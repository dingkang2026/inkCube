"""python/routers/meeting_notes.py — 纪要端点（2.13 §3 + 2.14 纪要中心）。

POST   /projects/{p}/notes                 创建草稿（title 默认「拜访-YYYYMMDD」）
GET    /projects/{p}/notes                 pending 列表（待处理区/向导恢复）
GET    /projects/{p}/notes/archived        归档列表（分页契约 0.1 §16）
PATCH  /projects/{p}/notes/{id}            编辑输入（缺席=不改、显式 null=清除；archived 409）
PATCH  /projects/{p}/notes/{id}/content    归档正文编辑（行内 draft_md + 素材 md 联动，2.14 补）
DELETE /projects/{p}/notes/{id}            放弃草稿 / 删除归档纪要
POST   /projects/{p}/notes/{id}/structure  发起 LLM 结构化（202 受理；draft/structured 可受理）
POST   /projects/{p}/notes/{id}/confirm    确认保存（正文比对删旧传新 + draft_md 回写，完整置 archived）
POST   /projects/{p}/notes/{id}/materialize     正文补入素材（幂等，archived only）
DELETE /projects/{p}/notes/{id}/material        移出素材（完整级联，archived only）
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from datetime import date
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import deps
import meeting_notes_service as svc
from paths import get_data_dir
from readiness import ensure_ready
from routers.knowledge import FileUpload, _upload_core

logger = logging.getLogger(__name__)
router = APIRouter()


class NoteCreate(BaseModel):
    title: str | None = None
    occurred_at: str | None = None
    raw_text: str | None = None
    image_file_ids: list[str] | None = None


class NoteUpdate(BaseModel):
    title: str | None = None
    occurred_at: str | None = None
    raw_text: str | None = None
    image_file_ids: list[str] | None = None


class ConfirmItem(BaseModel):
    title: str
    selected: bool = True
    due_date: str | None = None


class NoteConfirm(BaseModel):
    content_md: str
    action_items: list[ConfirmItem]
    save_to_materials: bool = True  # 2.14：False = 正文仅存纪要列表（saved_refs.file_id 留空，事后可补）


class NoteContentUpdate(BaseModel):
    content_md: str


def _load_draft_content(note: dict) -> str:
    """行内 draft_md.content_md（容错解析，空/坏载荷回空串）——「已确认正文」事实源。"""
    try:
        return (json.loads(note.get("draft_md") or "{}") or {}).get("content_md") or ""
    except (ValueError, TypeError):
        return ""


def _draft_md_payload(content_md: str, action_items: list) -> str:
    return json.dumps({
        "content_md": content_md,
        "action_items": [{"title": it.title} for it in action_items],
    }, ensure_ascii=False)


async def _cleanup_note_file(db, file_id: str, project_id: str) -> None:
    """纪要关联素材文件完整级联删（chunks/向量/FTS/磁盘/DB 行，同 dematerialize
    链；素材 md 与现场图片通用）。

    _upload_core 无同名覆盖语义（新内容 = 新 file_id 新档），内容变化重传前必须
    显式删旧，否则素材面板同名重复 + RAG 旧内容残留。"""
    from knowledge.file_cleanup import cleanup_file_data
    from knowledge.fts_store import FTSStore
    from knowledge.vector_store import VectorStore
    data_dir = get_data_dir()
    await cleanup_file_data(
        db, VectorStore(str(Path(data_dir) / "chroma")), FTSStore(db),
        file_id, project_id, data_dir, remove_file_record=True)


def _db():
    return deps.db


def _get_owned(note_id: str, project_id: str) -> dict:
    n = _db().get_meeting_note(note_id)
    if not n or n["project_id"] != project_id:
        raise HTTPException(404, "note not found")
    return n


def _image_referenced_elsewhere(db, project_id: str, file_id: str,
                                exclude_note_id: str) -> bool:
    """图片 file 是否被同项目其他纪要行引用（删除行时保护共享图片不被误删；
    pending + archived limit=0 全量组合覆盖全部状态行，契约 0.1 §16）。
    check-then-act 跨行窗口（检查与删除间他行挂上同一图可误删）经裁决接受：
    向导每次上传生成全新 file_id，跨行共享仅可能经直连 API 构造，桌面单用户
    风险可忽略（review 2026-10-28）。"""
    rows = list(db.list_pending_meeting_notes(project_id))
    rows += db.list_meeting_notes_archived(project_id, 0, 0)["notes"]
    return any(row["id"] != exclude_note_id
               and file_id in (row.get("image_file_ids") or [])
               for row in rows)


@router.post("/projects/{project_id}/notes")
async def create_note(project_id: str, req: NoteCreate):
    db = _db()
    proj = db.get_project(project_id)
    if not proj or proj.get("kind") != "project":
        raise HTTPException(404, "project not found")
    title = (req.title or "").strip() or f"拜访-{date.today().strftime('%Y%m%d')}"
    note = db.create_meeting_note(project_id, title, req.occurred_at,
                                  req.raw_text, req.image_file_ids or [])
    logger.info("Meeting note created %s in %s", note["id"], project_id)
    return {"note": note}


@router.get("/projects/{project_id}/notes")
async def list_pending(project_id: str):
    """pending 列表（draft/structuring/structured 待确认；草稿提示条与向导恢复数据源）。"""
    return {"notes": _db().list_pending_meeting_notes(project_id)}


@router.get("/projects/{project_id}/notes/archived")
async def list_archived(project_id: str, limit: int = 50, offset: int = 0):
    """归档纪要分页（2.14 §4 已归档时间线数据源；契约 0.1 §16）。"""
    return _db().list_meeting_notes_archived(project_id, limit, offset)


@router.patch("/projects/{project_id}/notes/{note_id}")
async def update_note(project_id: str, note_id: str, req: NoteUpdate):
    """编辑输入；字段缺席 = 不改、显式 null = 清除（model_fields_set 分流，
    同 2.12 CR #1 语义）。title NOT NULL 例外：显式 null/空白按「不改」——
    NOT NULL 列无清除语义（可清除的仅可空列 occurred_at/raw_text）。
    archived 行不可编辑（2.14：历史记录防与素材 md 双源漂移）。"""
    note = _get_owned(note_id, project_id)
    if note["status"] == "archived":
        raise HTTPException(409, "note archived")
    fields = {k: getattr(req, k) for k in
              ("title", "occurred_at", "raw_text", "image_file_ids")
              if k in req.model_fields_set}
    if "title" in fields and not (fields["title"] or "").strip():
        fields.pop("title")
    note = _db().update_meeting_note(note_id, fields)
    if not note:
        raise HTTPException(404, "note not found")
    return {"note": note}


@router.delete("/projects/{project_id}/notes/{note_id}")
async def delete_note(project_id: str, note_id: str):
    """删除纪要（任何状态，实测裁决推翻 2.14「仅删行」）：级联清理行专属产物——
    素材 md（refs.file_id，cleanup 完整级联）+ 现场图片（image_file_ids，未被
    同项目其他纪要行引用时一并删，共享图片保护）；已建事务保留（事务面板管理）。
    confirm 进行中 409（与 confirm 共用内存占坑串行化同 note）——
    防「保存进行中删除」竞态：行删后 confirm 后续步骤把素材/事务写到已删行的下游。"""
    note = _get_owned(note_id, project_id)
    if not svc.try_acquire_confirm(note_id):
        raise HTTPException(409, "confirm in progress")
    db = _db()
    try:
        refs = json.loads(note.get("saved_refs") or "{}")
        if refs.get("file_id"):
            await _cleanup_note_file(db, refs["file_id"], project_id)
        for img_fid in note.get("image_file_ids") or []:
            if db.get_file(img_fid) and not _image_referenced_elsewhere(
                    db, project_id, img_fid, note_id):
                await _cleanup_note_file(db, img_fid, project_id)
        db.delete_meeting_note(note_id)
        logger.info("Meeting note deleted %s (assets cascaded)", note_id)
    finally:
        svc.release_confirm(note_id)
    return {"ok": True}


async def _requeue_note_images(db, project_id: str, note: dict) -> None:
    """定稿驱动解析（2.13）：受理时确保附图进入解析/描述流水线。

    pending 直接入队（即时性优于等扫描器，enqueue 内部去重）；error 文件走
    cleanup+reset 重解析（重试定稿自愈，同 retry_file 姿势）；done 文件的
    error 图重描述（force 跳过小图归档）；done/parsing 幂等跳过。"""
    if not deps.worker:
        return  # worker 离线时扫描器同样离线，等待循环超时自然暴露（不另起路径）
    from knowledge.file_cleanup import cleanup_file_data
    from knowledge.fts_store import FTSStore
    from knowledge.vector_store import VectorStore
    data_dir = get_data_dir()
    vs = VectorStore(str(Path(data_dir) / "chroma"))
    fts = FTSStore(db)
    for fid in note.get("image_file_ids") or []:
        f = db.get_file(fid)
        if not f:
            continue
        if f["status"] == "pending":
            await deps.worker.enqueue_file(fid)
        elif f["status"] == "error":
            await cleanup_file_data(db, vs, fts, fid, project_id, data_dir,
                                    remove_file_record=False)
            db.reset_file_for_retry(fid)
            await deps.worker.enqueue_file(fid)
        elif f["status"] == "done":
            for img in db.list_images(fid):
                if img.get("status") == "error":
                    await deps.worker.enqueue_image(img["id"], force=True)


@router.post("/projects/{project_id}/notes/{note_id}/structure")
async def structure_note(project_id: str, note_id: str):
    """异步受理（202；模式对齐 profile/extract：run_in_executor + status 守卫）。

    材料空 422 前置按「文本或图非空」判定（2.13 定稿驱动：受理时图尚未解析，
    描述属受理后产物，不能拿 _collect_materials 判空）；service 内 NO_MATERIAL
    为 executor 防御。受理时确保附图入解析流水线（等待就绪在 service 内）。"""
    ensure_ready("task")
    note = _get_owned(note_id, project_id)
    # structured 可重发（2.14 补「上一步」改输入链：确认步回输入步修改后重新生成，
    # 回 draft 重走——draft_md 旧产物被新产物覆盖，refs 未产生（confirm 才落）无副作用）
    if note["status"] not in ("draft", "structured"):
        raise HTTPException(409, "note not re-generatable")
    if not ((note.get("raw_text") or "").strip()
            or (note.get("image_file_ids") or [])):
        raise HTTPException(422, "no material to structure")
    db = _db()
    await _requeue_note_images(db, project_id, note)
    db.update_meeting_note(note_id, {"status": "structuring"})
    settings = db.get_settings()
    loop = asyncio.get_running_loop()
    loop.run_in_executor(
        None, lambda: svc.structure_meeting_note_sync(db, note_id, settings))
    return JSONResponse(status_code=202, content={"accepted": True})


@router.post("/projects/{project_id}/notes/{note_id}/confirm")
async def confirm_note(project_id: str, note_id: str, req: NoteConfirm):
    """确认保存（同步）：分步落 saved_refs 幂等重试——md（可选，2.14
    save_to_materials）→ 事务（逐条记 refs）→ 完整置 archived 终态（2.14 原删行：
    历史记录留存供归档列表回溯）；重试只补未完成步骤，杜绝事务重复创建。

    并发防护：confirm 是 md 上传+逐建事务的复合操作，无法单事务化——内存占坑
    串行化同 note（try_acquire 判负 409，模式同 profile_service）；status 保持
    structured，失败留存后「重试保存」天然放行。
    正文与同步态联动语义（勾选框 = 同步目标态，实测裁决）：行内 draft_md 是
    「已确认正文」事实源。save_to_materials=true（目标「在素材」）：内容变 →
    已有素材删旧传新（_upload_core 无同名覆盖，不删旧则素材面板同名重复 +
    RAG 旧内容残留）；无 file_id（上传失败后重试，内容未变）→ 补传，杜绝 refs
    永不完整的「重试保存」死循环。save_to_materials=false（目标「不在素材」）：
    已有素材一律删除（无论内容是否变化）。载荷与行内不一致（正文或行动项）→
    回写 draft_md（移出再入/详情浮层读 draft_md，不回写则编辑回退 LLM 原稿）。
    """
    ensure_ready("file_parsing")
    note = _get_owned(note_id, project_id)
    if note["status"] != "structured":
        raise HTTPException(409, "note not structured")
    if not req.content_md.strip():
        raise HTTPException(422, "content_md required")
    if not svc.try_acquire_confirm(note_id):
        raise HTTPException(409, "confirm already in progress")
    db = _db()
    try:
        proj = db.get_project(project_id)
        if not proj or proj.get("kind") != "project":
            raise HTTPException(404, "project not found")
        refs = json.loads(note.get("saved_refs") or "{}")
        file_id, task_ids = refs.get("file_id"), refs.get("task_ids", [])
        failed: list[str] = []
        created = 0
        # 步骤 1：正文与同步态对齐（勾选框 = 同步目标态，见 docstring 联动语义）
        content_changed = _load_draft_content(note).strip() != req.content_md.strip()
        refs_dirty = False
        if req.save_to_materials:
            if file_id and content_changed:
                await _cleanup_note_file(db, file_id, project_id)
                file_id = None
            if not file_id:
                fname = (f"纪要-{(note.get('occurred_at') or date.today().isoformat()).replace('-', '')}"
                         f"-{note['title']}.md")
                result = await _upload_core(
                    project_id, FileUpload(
                        projectId=project_id, filename=fname, fileType="text/markdown",
                        data=base64.b64encode(req.content_md.encode("utf-8")).decode()),
                    db, get_data_dir(), deps.worker)
                file_id = result["file_id"]
                refs_dirty = True
        elif file_id:
            # 目标「不在素材」：已有素材一律删除（内容未变也删）
            await _cleanup_note_file(db, file_id, project_id)
            file_id = None
            refs_dirty = True
        new_draft_md = _draft_md_payload(req.content_md, req.action_items)
        if refs_dirty or (note.get("draft_md") or "").strip() != new_draft_md:
            db.update_meeting_note(note_id, {
                "saved_refs": json.dumps({"file_id": file_id, "task_ids": task_ids}),
                "draft_md": new_draft_md})
        # 步骤 2：事务（幂等键 = 条目 title：重试改清单不再按下标误跳——旧逻辑
        # items=[A,B] A 成 B 败后删 A 留 B 重试，B 落 idx0 被误跳，从未建成却被
        # 判全归档。存量 int 元素（旧格式）仍按下标跳过，双格式同表达式兼容免
        # 迁移；同名条目视为同一条待办不重复创建）
        from affairs_service import phase_of
        phase = phase_of(proj.get("stage") or "none")
        for idx, item in enumerate(req.action_items):
            if not item.selected or idx in task_ids or item.title in task_ids:
                continue
            if not item.title.strip():
                raise HTTPException(422, "action item title required")
            try:
                db.create_phase_task(project_id=project_id, phase=phase,
                                     task_type="material", title=item.title,
                                     due_date=item.due_date)
                task_ids.append(item.title)  # 幂等键 = 条目 title（偏差 21，原 append idx）
                created += 1
                db.update_meeting_note(note_id, {"saved_refs": json.dumps(
                    {"file_id": file_id, "task_ids": task_ids})})
            except Exception as e:
                logger.warning("confirm task failed note=%s idx=%s: %s",
                               note_id, idx, e)
                failed.append(item.title)
        # 步骤 3：全部选中项完成（含全不选，spec §4 合法态）→ 置 archived 终态（2.14：
        # 原删行；行保留供归档列表回溯，正文留存行内）。int/str 双格式判全同表达式
        done = all(idx in task_ids or it.title in task_ids
                   for idx, it in enumerate(req.action_items) if it.selected)
        if done:
            db.update_meeting_note(note_id, {"status": "archived"})
        return {"file_id": file_id, "created_tasks": created,
                "failed_tasks": failed, "archived": done}  # 实际状态（review：恒 True 契约污染）
    finally:
        svc.release_confirm(note_id)


@router.patch("/projects/{project_id}/notes/{note_id}/content")
async def update_note_content(project_id: str, note_id: str, req: NoteContentUpdate):
    """正文保存（三态模型，实测裁决）：structured = 格式化态「暂存」（人工修改
    存回行内、保持待确认，不入素材不建事务不归档；重试残留 file_id 行照常联动
    删旧传新）；archived = 确认态人工修改确认（实测推翻「archived 只读」）。
    行内 draft_md 与素材 md 联动更新（按 refs.file_id 分流 = 行的素材归属事实）——
    已入素材行内容变化删旧传新（cleanup 完整级联 + 解析入队刷新 RAG，新 file_id
    落 refs，入库状态不变）；未入素材仅回写 draft_md，不上传不解析（review 裁决：
    原无条件上传静默违背目标态语义——structured 暂存/confirm 选不入素材的 archived
    行编辑一次即被静默入库）。内容未变幂等返回当前 file_id（前端 dirty 禁用为
    第一道防线，返回形态供前端局部回写防 clobber）。
    行动项清单不动（编辑仅正文；已建事务在事务面板管理，同删除语义）。
    占坑与 confirm/materialize/dematerialize 互斥（refs 并发写串行化）。"""
    note = _get_owned(note_id, project_id)
    if note["status"] not in ("structured", "archived"):
        raise HTTPException(409, "note not editable")
    if not req.content_md.strip():
        raise HTTPException(422, "content_md required")
    if not svc.try_acquire_confirm(note_id):
        raise HTTPException(409, "confirm in progress")
    db = _db()
    try:
        refs = json.loads(note.get("saved_refs") or "{}")
        file_id = refs.get("file_id")
        if _load_draft_content(note).strip() == req.content_md.strip():
            return {"ok": True, "file_id": file_id}  # 幂等：内容未变
        try:
            action_items = (json.loads(note.get("draft_md") or "{}") or {}).get("action_items") or []
        except (ValueError, TypeError):
            action_items = []
        if file_id:
            # 已入素材（含 structured 重试残留行）：删旧传新刷新 RAG
            ensure_ready("file_parsing")
            await _cleanup_note_file(db, file_id, project_id)
            fname = (f"纪要-{(note.get('occurred_at') or date.today().isoformat()).replace('-', '')}"
                     f"-{note['title']}.md")
            result = await _upload_core(
                project_id, FileUpload(
                    projectId=project_id, filename=fname, fileType="text/markdown",
                    data=base64.b64encode(req.content_md.encode("utf-8")).decode()),
                db, get_data_dir(), deps.worker)
            refs["file_id"] = result["file_id"]
        # 未入素材（目标态「不在素材」/暂存行）：仅回写 draft_md，refs 不动——
        # 不上传不解析（ensure_ready 仅在有素材分支，纯回写是本地操作）
        db.update_meeting_note(note_id, {
            "saved_refs": json.dumps(refs),
            "draft_md": json.dumps({"content_md": req.content_md,
                                    "action_items": action_items}, ensure_ascii=False)})
        logger.info("Meeting note content updated %s (material -> %s)",
                    note_id, refs.get("file_id"))
        return {"ok": True, "file_id": refs.get("file_id")}
    finally:
        svc.release_confirm(note_id)


@router.post("/projects/{project_id}/notes/{note_id}/materialize")
async def materialize_note(project_id: str, note_id: str):
    """正文补入素材（2.14）：归档纪要事后入库——复用 confirm 步骤 1 上传链
    （解析入队进 RAG），refs 落库；已有 file_id 幂等直接返回。archived only；
    与 confirm/DELETE 共用内存占坑（refs 并发写串行化，同 2.13 竞态防线）。"""
    ensure_ready("file_parsing")
    note = _get_owned(note_id, project_id)
    if note["status"] != "archived":
        raise HTTPException(409, "note not archived")
    if not svc.try_acquire_confirm(note_id):
        raise HTTPException(409, "confirm in progress")
    db = _db()
    try:
        refs = json.loads(note.get("saved_refs") or "{}")
        file_id = refs.get("file_id")
        if file_id:
            return {"ok": True, "file_id": file_id}  # 幂等：已入库不重传
        if not note.get("draft_md"):
            raise HTTPException(422, "no content to materialize")
        content_md = (json.loads(note["draft_md"]) or {}).get("content_md") or ""
        if not content_md.strip():
            raise HTTPException(422, "no content to materialize")
        fname = (f"纪要-{(note.get('occurred_at') or date.today().isoformat()).replace('-', '')}"
                 f"-{note['title']}.md")
        result = await _upload_core(
            project_id, FileUpload(
                projectId=project_id, filename=fname, fileType="text/markdown",
                data=base64.b64encode(content_md.encode("utf-8")).decode()),
            db, get_data_dir(), deps.worker)
        refs["file_id"] = result["file_id"]
        db.update_meeting_note(note_id, {"saved_refs": json.dumps(refs)})
        logger.info("Meeting note materialized %s -> %s", note_id, refs["file_id"])
        return {"ok": True, "file_id": refs["file_id"]}
    finally:
        svc.release_confirm(note_id)


@router.delete("/projects/{project_id}/notes/{note_id}/material")
async def dematerialize_note(project_id: str, note_id: str):
    """移出素材（2.14）：完整级联删素材文件（chunks/向量/FTS/Wiki 衍生/磁盘/DB 行，
    cleanup_file_data remove_file_record=True 同素材删除链）+ refs.file_id 置 null；
    纪要行保留（正文仍在，可重新加入）。无 file_id 幂等返回。"""
    note = _get_owned(note_id, project_id)
    if note["status"] != "archived":
        raise HTTPException(409, "note not archived")
    if not svc.try_acquire_confirm(note_id):
        raise HTTPException(409, "confirm in progress")
    db = _db()
    try:
        refs = json.loads(note.get("saved_refs") or "{}")
        file_id = refs.get("file_id")
        if file_id:
            from knowledge.file_cleanup import cleanup_file_data
            from knowledge.fts_store import FTSStore
            from knowledge.vector_store import VectorStore
            data_dir = get_data_dir()
            vs = VectorStore(str(Path(data_dir) / "chroma"))
            fts = FTSStore(db)
            await cleanup_file_data(db, vs, fts, file_id, project_id, data_dir,
                                    remove_file_record=True)
            refs["file_id"] = None
            db.update_meeting_note(note_id, {"saved_refs": json.dumps(refs)})
            logger.info("Meeting note dematerialized %s (file %s removed)",
                        note_id, file_id)
        return {"ok": True}
    finally:
        svc.release_confirm(note_id)
