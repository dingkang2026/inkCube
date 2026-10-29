"""python/routers/sticky_notes.py — 便签（2.12）：全局速记收件箱。

六端点：GET/POST /sticky/notes、PATCH/DELETE /sticky/notes/{id}、
POST /sticky/notes/{id}/archive（归档落项目素材，复用 _upload_core 上传链）、
POST /sticky/notes/{id}/to-affair（转 material 事务，便签原地留存）。

并发防护：archive 是「md 上传+解析入队 + 行落定」复合操作（await 窗口真存在），
内存占坑串行化同 note——防双归档双素材、防「归档进行中删除」竞态（行删后素材
md 落盘成孤儿产物）；DELETE 与 archive 共用占坑（进行中 409）。
"""
from __future__ import annotations

import base64
import logging
import threading
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

import deps
from db.database import STICKY_UNSET
from paths import get_data_dir
from readiness import ensure_ready
from routers.knowledge import FileUpload, _upload_core

logger = logging.getLogger(__name__)
router = APIRouter()


class StickyCreate(BaseModel):
    content: str
    color: str | None = None


class StickyUpdate(BaseModel):
    content: str | None = None
    color: str | None = None


class StickyArchive(BaseModel):
    project_id: str


class StickyToAffair(BaseModel):
    project_id: str
    due_date: str | None = None


def _db():
    return deps.db


def _value_error_422(e: ValueError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(e))


# archive 复合操作占坑（模式同 meeting_notes_service/profile_service）：
# db 层条件更新只能防「行落定」重复，防不了「素材 md 已落盘、行判负」的孤儿。
_OP_LOCK = threading.Lock()
_ARCHIVING: set[str] = set()


def _try_acquire(note_id: str) -> bool:
    with _OP_LOCK:
        if note_id in _ARCHIVING:
            return False
        _ARCHIVING.add(note_id)
        return True


def _release(note_id: str) -> None:
    with _OP_LOCK:
        _ARCHIVING.discard(note_id)


@router.get("/sticky/notes")
async def list_notes(view: str = "inbox",
                     limit: int = Query(50, ge=0), offset: int = Query(0, ge=0)):
    """收件箱/已归档二分列表；limit=0 全量（选择器场景），limit>0 时
    传 limit+1 探测 has_more（0.1dev-guide §16 分页契约）。"""
    if view not in ("inbox", "archived"):
        raise HTTPException(422, "invalid view")
    db = _db()
    rows = db.list_sticky_notes(view, limit=limit + 1 if limit > 0 else 0, offset=offset)
    has_more = limit > 0 and len(rows) > limit
    total = db.conn.execute(
        "SELECT COUNT(*) AS n FROM sticky_notes WHERE archived_at IS "
        + ("NULL" if view == "inbox" else "NOT NULL")).fetchone()["n"]
    return {"notes": rows[:limit] if has_more else rows,
            "has_more": has_more, "total": total}


@router.post("/sticky/notes")
async def create_note(req: StickyCreate):
    content = req.content.strip()
    if not content:
        raise HTTPException(422, "content required")
    try:
        note = _db().create_sticky_note(content, req.color)
    except ValueError as e:
        raise _value_error_422(e)
    logger.info("Sticky note created %s", note["id"])
    return {"note": note}


@router.patch("/sticky/notes/{note_id}")
async def update_note(note_id: str, req: StickyUpdate):
    """content/color 可选更新；显式 null = 清除（如选「无色」），
    字段缺席 = 不改（model_fields_set 区分，2.12 §4 色板单选即存语义）。"""
    if req.content is not None and not req.content.strip():
        raise HTTPException(422, "content required")
    color = req.color if "color" in req.model_fields_set else STICKY_UNSET
    try:
        note = _db().update_sticky_note(
            note_id,
            req.content.strip() if req.content is not None else None,
            color)
    except ValueError as e:
        raise _value_error_422(e)
    if not note:
        raise HTTPException(404, "note not found")
    return {"note": note}


@router.delete("/sticky/notes/{note_id}")
async def delete_note(note_id: str):
    """删除；归档进行中 409（与 archive 共用占坑）——防「归档进行中删除」竞态：
    行删后素材 md 照常落盘，成无主孤儿产物。"""
    if not _try_acquire(note_id):
        raise HTTPException(409, "archive in progress")
    try:
        if not _db().delete_sticky_note(note_id):
            raise HTTPException(404, "note not found")
    finally:
        _release(note_id)
    return {"ok": True}


@router.post("/sticky/notes/{note_id}/archive")
async def archive_note(note_id: str, req: StickyArchive):
    """归档：便签 md 经 _upload_core 落项目素材（复用上传+解析链），
    成功后行落 project_id/archived_at；未落盘成功前不改行（失败即留在收件箱）。"""
    ensure_ready("file_parsing")
    db = _db()
    note = db.get_sticky_note(note_id)
    if not note:
        raise HTTPException(404, "note not found")
    if note["archived_at"]:
        raise HTTPException(409, "note already archived")
    proj = db.get_project(req.project_id)
    if not proj or proj.get("kind") != "project":
        raise HTTPException(404, "project not found")
    if not _try_acquire(note_id):
        raise HTTPException(409, "archive in progress")
    try:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        upload = FileUpload(projectId=req.project_id, filename=f"便签-{ts}.md",
                            fileType="text/markdown",
                            data=base64.b64encode(note["content"].encode("utf-8")).decode())
        result = await _upload_core(req.project_id, upload, db, get_data_dir(), deps.worker)
        archived = db.archive_sticky_note(note_id, req.project_id)
        if archived is None:
            # 占坑后其它 archive 入口已串行化，此判负为 db 层双保险（防未来新入口）
            raise HTTPException(409, "note already archived")
    finally:
        _release(note_id)
    logger.info("Sticky note %s archived to project %s (file %s)",
                note_id, req.project_id, result.get("file_id"))
    return {"note": archived, "file": result}


@router.post("/sticky/notes/{note_id}/to-affair")
async def to_affair(note_id: str, req: StickyToAffair):
    """转事务：material 类，title=首行 40 字截断，description=全文；
    便签原地留存（转事务不消耗便签，2.12 §3）。全程同步无 await 窗口
    （事件循环内原子），不占坑；与 archive 并发的产物均有效无孤儿。"""
    db = _db()
    note = db.get_sticky_note(note_id)
    if not note:
        raise HTTPException(404, "note not found")
    proj = db.get_project(req.project_id)
    if not proj or proj.get("kind") != "project":
        raise HTTPException(404, "project not found")
    from affairs_service import phase_of
    task = db.create_phase_task(
        project_id=req.project_id, phase=phase_of(proj.get("stage") or "none"),
        task_type="material",
        # 防御：存量库可能有空正文行（update 422 上线前写入），splitlines() 对空串返回 []
        title=(note["content"].splitlines() or [""])[0][:40],
        description=note["content"], due_date=req.due_date)
    logger.info("Sticky note %s -> affair %s (project %s)", note_id, task["id"], req.project_id)
    return {"task": task}
