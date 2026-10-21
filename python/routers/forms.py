"""python/routers/forms.py — 填表系统 API（docs/2.4forms-design.md §4）。

模板管理（上传/列表/详情/重解析/删除，全局资产）+ 填表实例
（发起/列表/详情/人工修订/下载，项目作用域）。两个长时活动
（模板分析 / 填表执行）跑 BackgroundTasks / lifespan 拾取，
经统一活动模型注册（0.4 §3 / coding-habits §2.7），
终态经 activity_events 声明（notifications + SSE）。
"""
import asyncio
import hashlib
import json
import logging
import os
import uuid
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

import activity_events
from llm.usage import UsageContext, push_context  # 2.10 §4.3 用量环境
from paths import get_data_dir
from readiness import ensure_ready

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/forms", tags=["forms"])

# 预置模板 seed 清单，固定 id 即 seed 幂等锚点（2.4 §3.1）；
# 文件名纯 ASCII（同 electron-builder productName 决策：中文在脚本/工具链上有编码风险）；
# 子目录 forms/ 与报告模板 json 平铺区隔；每张 docx 配出厂预解析产物
# {stem}.schema.json——出厂预生成、随包分发，
# 新用户 seed 即 success、零 LLM 调用开箱即用
PRESET_FORM_TEMPLATES = [
    {"id": "form_tpl_investment_recommend", "name": "投资推荐项目情况表",
     "file": os.path.join("forms", "investment_recommend.docx")},
    {"id": "form_tpl_recruitment_project_info", "name": "招商引资项目信息表",
     "file": os.path.join("forms", "recruitment_project_info.docx")},
    {"id": "form_tpl_project_approval", "name": "项目立项审批表",
     "file": os.path.join("forms", "project_approval.docx")},
]

# lifespan create_task 的后台协程引用集（防 GC；done 回调自清）
_BG_TASKS: set = set()


def get_db():
    """Get database instance."""
    try:
        import deps
        return deps.db
    except ImportError:
        raise HTTPException(status_code=500, detail="Database not initialized")


def preset_form_templates() -> list[dict]:
    """预置模板 seed 清单（main.py lifespan 调用）。

    路径复用报告模板目录解析链路（loader.get_templates_dir），
    打包兼容同 renderer 的 reference-doc 模式；文件缺失的条目跳过
    （仅兜底——正常打包模板必然随包存在）。出厂预解析产物（2.4 §3.1）
    存在且与 docx hash 配对时直接携带 schema/mapping/hash（seed 即
    success，新用户零 LLM 调用开箱即用）；否则回退 pending 由运行时
    分析兜住，绝不落与文件错位的 schema。
    """
    from report_generator.loader import get_templates_dir
    base = get_templates_dir()
    seeded = []
    for t in PRESET_FORM_TEMPLATES:
        path = os.path.join(base, t["file"])
        if not os.path.exists(path):
            logger.warning("Preset form template missing: %s", path)
            continue
        entry = {
            "id": t["id"],
            "name": t["name"],
            "source": "preset",
            "file_path": path,
        }
        preset = _load_preset_schema(path)
        if preset:
            entry.update(preset)
        seeded.append(entry)
    return seeded


def _load_preset_schema(docx_path: str) -> dict | None:
    """读出厂预解析产物（{stem}.schema.json，2.4 §3.1）。

    校验版本（不认识即回退）与形状（preset/template_schema/
    llm_mapping 须为 dict——hash 校验的是 docx 而非产物本身，语法
    合法但形状错的产物必须在此拦截），再校验 file_hash 与 docx
    配对——schema 的 target 是文件格子索引，docx 变更后产物错位会
    在填表时写错格。缺失/损坏/不配对返回 None（seed 回退 pending，
    运行时分析产出与当前文件匹配的 schema）。
    """
    schema_path = os.path.splitext(docx_path)[0] + ".schema.json"
    try:
        with open(schema_path, encoding="utf-8") as f:
            preset = json.load(f)
        if (not isinstance(preset, dict)
                or preset.get("preset_schema_version") != 1
                or not isinstance(preset.get("template_schema"), dict)
                or not isinstance(preset.get("llm_mapping"), dict)):
            logger.warning(
                "Preset form schema malformed or unknown version, fallback "
                "to runtime analysis: %s", schema_path)
            return None
        if str(preset["file_hash"]) != sha256_file(docx_path):
            logger.warning(
                "Preset form schema hash mismatch (docx changed without "
                "re-preset?), fallback to runtime analysis: %s", schema_path)
            return None
        return {
            "template_schema": json.dumps(
                preset["template_schema"], ensure_ascii=False),
            "llm_mapping": json.dumps(
                preset["llm_mapping"], ensure_ascii=False),
            "field_count": int(preset["field_count"]),
            "file_hash": str(preset["file_hash"]),
        }
    except (OSError, ValueError, KeyError, TypeError):
        logger.warning(
            "Preset form schema missing/corrupt, fallback to runtime "
            "analysis: %s", schema_path, exc_info=True)
        return None


def sha256_file(path: str) -> str:
    """模板文件 SHA-256（流式读取；模板 docx 量级下开销可忽略）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _file_changed(row: dict) -> bool:
    """模板文件内容变更判定（2.4 §2.4）：file_hash 与实时 hash 比对。

    file_hash 为 NULL（存量/历史失败）或文件缺失时返回 False——
    缺失有独立的 failed 表现，不掺和变更提示。
    """
    if not row.get("file_hash") or not row.get("file_path"):
        return False
    if not os.path.exists(row["file_path"]):
        return False
    return sha256_file(row["file_path"]) != row["file_hash"]


def _with_file_changed(row: dict) -> dict:
    """file_hash + 实时内容 → file_changed bool；file_path/file_hash 不出路由层
    （列表视图带 file_path 仅为比对所需，响应保持轻量形状）。"""
    changed = _file_changed(row)
    row.pop("file_hash", None)
    row.pop("file_path", None)
    row["file_changed"] = changed
    return row


def _get_search_tool(db, data_dir: str):
    """知识检索工具（与 reports.py 同款构造；检索范围由 fill_service
    按 [project_id]+base_project_ids 联合，不含互联网）。"""
    from knowledge.knowledge_search import KnowledgeSearchTool
    chroma_dir = os.path.join(data_dir, "chroma")
    return KnowledgeSearchTool(db=db, chroma_persist_dir=chroma_dir)


def _spawn(coro) -> None:
    """事件循环内启动后台协程并持有引用（防 GC），done 后自清。"""
    task = asyncio.get_running_loop().create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)


def resume_pending_work(db, data_dir: str) -> None:
    """启动拾取（2.4 §2.4）：pending 模板 → 后台分析；pending 实例 → 后台填表。

    main.py lifespan 调用（事件循环内）。分析彼此独立并发；填表的新增请求
    由 form_fill_max_concurrent 准入约束，重启重跑的残留是用户已发起过的
    作业，照常拾取不重复计数。
    """
    for tid in db.list_form_template_ids_by_status("pending"):
        _spawn(_run_template_analysis(tid))
    for fid in db.list_form_fill_ids_by_status("pending"):
        _spawn(_run_form_fill(fid, data_dir))


# ── 活动快照（ActivitySource fetch，注册点 activities.py，2.4 §3.4）──


def active_form_templates(db) -> list[dict]:
    """分析中/待分析填表模板（全局资产无项目实体，导航固定模板管理页）。"""
    cursor = db.conn.execute(
        "SELECT id, name, status FROM form_templates "
        "WHERE status IN ('pending', 'analyzing')"
    )
    return [
        {
            "entity_type": "form_template",
            "entity_id": row["id"],
            "entity_title": row["name"],
            "status": row["status"],
        }
        for row in cursor.fetchall()
    ]


def active_form_fills(db) -> list[dict]:
    """排队/填写中的填表实例（JOIN projects 补 container，基础库项目同报告语义）。"""
    cursor = db.conn.execute(
        """SELECT f.id, f.project_id, f.title, f.progress, p.name AS project_name,
                  CASE WHEN p.kind = 'base_lib' THEN 'baselib' ELSE 'project' END AS container
           FROM form_fills f
           JOIN projects p ON p.id = f.project_id
           WHERE f.status IN ('pending', 'filling')
           ORDER BY f.created_at DESC"""
    )
    return [
        {
            "entity_type": "form_fill",
            "entity_id": row["id"],
            "project_id": row["project_id"],
            "project_name": row["project_name"],
            "container": row["container"] or "project",
            "entity_title": row["title"],
            "progress": row["progress"] or 0,
        }
        for row in cursor.fetchall()
    ]


# ── 模板文件变更扫描（2.11 §3.3）：每日一次，挂心跳入口（activities.py）──


_last_change_scan_date: str | None = None


def maybe_scan_template_changes(db) -> None:
    """模板源文件变更扫描（每日一次，同日零开销；服务层自行兑底异常）。

    遍历分析成功且带 file_hash 的模板 → _file_changed 实时比对 → 命中发
    kind='file_changed' 通知（notify 通用入口；detail=None，后端零文案，
    语义在 i18n 主键完整表达）。查重走 add_notification 未读查重（2.11 D6）：
    每日一扫 + 未读查重 = 每天最多一条；重新分析更新 file_hash 后自然消停。
    单模板 hash 失败（文件被占用等瞬时态）静默跳过，下次扫描重试，不上抛。
    首查 DB 异常不置当日标记，当日后续心跳可重试（同 reminders 口径）。
    """
    global _last_change_scan_date
    today = date.today().isoformat()
    if _last_change_scan_date == today:
        return
    try:
        rows = db.conn.execute(
            "SELECT id, name, file_path, file_hash FROM form_templates "
            "WHERE status = 'success' AND file_hash IS NOT NULL"
        ).fetchall()
    except Exception:
        logger.warning("Form template change scan failed", exc_info=True)
        return
    _last_change_scan_date = today
    for row in rows:
        try:
            if not _file_changed(dict(row)):
                continue
            activity_events.notify(
                db,
                entity_type="form_template", entity_id=row["id"],
                project_id=None, container="project",
                kind="file_changed", entity_title=row["name"],
            )
            logger.info("Form template [%s] file_changed notification sent", row["id"])
        except Exception:
            logger.info("Form template [%s] change scan skipped (hash failed?)",
                        row["id"], exc_info=True)


# ── 后台任务：模板分析（2.4 §2.1 写路径 A）─────────────────


async def _run_template_analysis(template_id: str) -> None:
    """后台模板分析：结构提取 → LLMClient 语义映射 → 校验组装落库。

    全链路自行兜底：任何异常落到 failed/invalid 终态（含通知），
    不悬挂 pending/analyzing。启动拾取与 upload/reanalyze 端点共用。
    """
    db = get_db()
    row = db.get_form_template(template_id)
    if not row:
        return
    if not os.path.exists(row["file_path"]):
        msg = f"模板文件不存在: {row['file_path']}"
        db.update_form_template_status(template_id, "failed", error=msg)
        activity_events.fail_form_template(db, template_id, msg)
        return
    db.update_form_template_status(template_id, "analyzing")
    # hash 在分析开始时计算（与 analyzer 读取的内容窗口最小）；分析期间文件
    # 被改属可接受边界——下次列表请求/重启仍会以实时内容重新比对
    file_hash = sha256_file(row["file_path"])
    try:
        llm_settings = db.get_settings()
        from llm.client import LLMClient
        from llm.routing import get_task_settings
        llm_settings = get_task_settings(llm_settings, "form_analyze")
        llm_client = LLMClient(llm_settings)
        prev_mapping = None
        if row.get("llm_mapping"):
            try:
                prev_mapping = json.loads(row["llm_mapping"])
            except (json.JSONDecodeError, TypeError):
                prev_mapping = None
        from form_fill.template_analyzer import InvalidTemplateError, analyze_template
        # 2.10 §4.3: 用量环境（source=form / purpose=analyze；模板为全局
        # 资产无 project_id，entity_id=template_id）
        with push_context(UsageContext(
                source="form", purpose="analyze", entity_id=template_id)):
            schema, llm_mapping = await analyze_template(
                row["file_path"], row["name"], llm_client,
                user_hint=row.get("user_hint") or None,
                prev_mapping=prev_mapping,
            )
        field_count = len(schema.get("fields", []))
        db.update_form_template_schema(
            template_id, schema, llm_mapping, field_count,
            file_hash=file_hash)
        logger.info("Form template [%s] analyzed: %d fields", template_id, field_count)
        activity_events.complete_form_template(
            db, template_id, detail=f"分析完成，共识别 {field_count} 个字段")
    except ImportError as e:
        # 延迟 import 提前失败（docx 依赖缺失等）：与 InvalidTemplateError
        # 区分开——环境问题按 failed 而非 invalid（提示用户而非模板问题）。
        # 必须落终态：否则每次重启被 resume_pending_work 拾取 → 再次失败 →
        # 无限循环白跑（m-1：原 raise 使模板永久卡 analyzing）
        msg = f"分析组件加载失败（环境缺失）: {e}"
        logger.exception("Form template [%s] analysis ImportError", template_id)
        db.update_form_template_status(template_id, "failed", error=msg)
        activity_events.fail_form_template(db, template_id, msg)
    except Exception as e:
        from form_fill.template_analyzer import InvalidTemplateError
        if isinstance(e, InvalidTemplateError):
            logger.warning("Form template [%s] invalid: %s", template_id, e)
            db.update_form_template_status(template_id, "invalid", error=str(e))
            activity_events.fail_form_template(db, template_id, f"模板格式不符：{e}")
            return
        from errors import actionable
        msg = actionable(e)
        logger.exception("Form template analysis failed for %s: %s", template_id, e)
        db.update_form_template_status(template_id, "failed", error=msg)
        activity_events.fail_form_template(db, template_id, msg)


# ── 后台任务：填表执行（2.4 §2.2 写路径 B）─────────────────


def _project_context(db, project_id: str) -> dict:
    """rule_fill 直取的项目基础信息（键与 field_key/rule_hint 对应）。"""
    project = db.get_project(project_id) or {}
    return {
        "company_name": project.get("company_name") or "",
        "credit_code": project.get("credit_code") or "",
        "today": date.today().isoformat(),
    }


async def _run_form_fill(fill_id: str, data_dir: str) -> None:
    """后台填表：批次循环（检索→生成→写入）→ 产物落盘 + 值集/报告落库。"""
    db = get_db()
    fill = db.get_form_fill(fill_id)
    if not fill:
        return
    template = db.get_form_template(fill["template_id"])
    schema = None
    if template and template.get("template_schema"):
        try:
            schema = json.loads(template["template_schema"])
        except (json.JSONDecodeError, TypeError):
            schema = None
    if not template or template["status"] != "success" or not schema:
        msg = "模板不存在或尚未分析完成"
        db.update_form_fill_status(fill_id, "failed", error=msg)
        activity_events.fail_form_fill(db, fill_id, msg)
        return
    if not os.path.exists(template["file_path"]):
        msg = f"模板文件不存在: {template['file_path']}"
        db.update_form_fill_status(fill_id, "failed", error=msg)
        activity_events.fail_form_fill(db, fill_id, msg)
        return
    db.update_form_fill_status(fill_id, "filling")
    try:
        llm_settings = db.get_settings()
        from llm.client import LLMClient
        from llm.routing import get_task_settings
        llm_settings = get_task_settings(llm_settings, "form_fill")
        llm_client = LLMClient(llm_settings)
        search_tool = _get_search_tool(db, data_dir)
        base_ids = [b["id"] for b in db.get_project_base_libs(fill["project_id"])]

        def _on_progress(done: int, total: int) -> None:
            # 批次阶段封顶 95，完成 100 由 update_form_fill_result 写
            # （进度单调递增由 SQL MAX 保护，2.4 §5.4 不变量 4）
            db.update_form_fill_status(
                fill_id, "filling", progress=int(done / max(total, 1) * 95))

        from form_fill.fill_service import fill_template
        # 2.10 §4.3: 用量环境（source=form / purpose=fill，归属项目与填表实例）
        with push_context(UsageContext(
                source="form", purpose="fill",
                project_id=fill["project_id"], entity_id=fill_id)):
            docx_bytes, report, filled = await fill_template(
                schema, template["file_path"], _project_context(db, fill["project_id"]),
                search_tool, fill["project_id"], base_ids,
                llm_client, llm_settings, progress_cb=_on_progress)

        out_dir = os.path.join(data_dir, "form_fills")
        os.makedirs(out_dir, exist_ok=True)
        output_path = os.path.join(out_dir, f"{fill_id}.docx")
        with open(output_path, "wb") as f:
            f.write(docx_bytes)
        filled_n = sum(1 for r in report if r.get("status") == "filled")
        db.update_form_fill_result(fill_id, report, filled, output_path)
        logger.info("Form fill [%s] done: %d/%d filled",
                    fill_id, filled_n, len(report))
        activity_events.complete_form_fill(
            db, fill_id, detail=f"已填 {filled_n}/{len(report)} 个字段")
    except Exception as e:
        from errors import actionable
        msg = actionable(e)
        logger.exception("Form fill failed for %s: %s", fill_id, e)
        db.update_form_fill_status(fill_id, "failed", error=msg)
        activity_events.fail_form_fill(db, fill_id, msg)


# ── 模板 API（全局资产，2.4 §5 职责划分）───────────────────


@router.post("/templates/upload")
async def upload_form_template(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    """上传自定义填表模板（.docx）→ 落盘 + INSERT(pending) → 后台分析。"""
    filename = file.filename or "template"
    ext = Path(filename).suffix.lower()
    if ext != ".docx":
        return JSONResponse(
            status_code=400,
            content={
                "error": f"不支持的文件格式: {ext}。填表模板仅支持 Word (.docx)。",
                "code": "UNSUPPORTED_FORM_TEMPLATE_FORMAT",
            },
        )
    ensure_ready("form_analysis")
    db = get_db()
    template_id = str(uuid.uuid4())
    upload_dir = os.path.join(get_data_dir(), "form_templates")
    os.makedirs(upload_dir, exist_ok=True)
    save_path = os.path.join(upload_dir, f"{template_id}.docx")
    content = await file.read()
    with open(save_path, "wb") as f:
        f.write(content)
    # 文件名去后缀作模板名（用户可在重解析/展示语境辨认；重名不冲突——id 唯一）
    name = Path(filename).stem or filename
    db.create_form_template(template_id, name, "upload", save_path)
    background_tasks.add_task(_run_template_analysis, template_id)
    return {"template_id": template_id, "status": "pending"}


@router.get("/templates")
async def list_form_templates(
    q: str | None = Query(None, description="Name fuzzy filter"),
    status: str | None = Query(None, description="Exact status filter"),
    limit: int = Query(50, ge=0, description="Page size; 0 = return all"),
    offset: int = Query(0, ge=0, description="Rows to skip"),
):
    """模板列表（轻量视图 + file_changed 动态比对 + q/status 服务端筛选 + 分页，2.4 §2.4）。"""
    db = get_db()
    # limit+1 探测 has_more（list_tasks 同款，免 COUNT）
    templates = db.list_form_templates(
        q=q, status=status, limit=limit + 1 if limit > 0 else 0, offset=offset)
    has_more = limit > 0 and len(templates) > limit
    if has_more:
        templates = templates[:limit]
    total = db.count_form_templates(q=q, status=status)
    return {
        "templates": [_with_file_changed(t) for t in templates],
        "has_more": has_more,
        "total": total,
    }


@router.get("/templates/{template_id}")
async def get_form_template_detail(template_id: str):
    """模板详情：schema/mapping 解析为对象（损坏容错为 None）。"""
    db = get_db()
    row = db.get_form_template(template_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Form template not found: {template_id}")
    for col in ("template_schema", "llm_mapping"):
        raw = row.get(col)
        if raw:
            try:
                row[col] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                row[col] = None
    return {"template": _with_file_changed(row)}


class ReanalyzeRequest(BaseModel):
    """重解析请求：补充说明必填（2.4 §2.3）。"""
    user_hint: str


@router.post("/templates/{template_id}/reanalyze")
async def reanalyze_form_template(
    template_id: str, req: ReanalyzeRequest, background_tasks: BackgroundTasks
):
    """重解析（修正 Schema）：携带上次 llm_mapping 基线整体送 LLM 局部修正。"""
    ensure_ready("form_analysis")
    db = get_db()
    row = db.get_form_template(template_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Form template not found: {template_id}")
    # 守门与 delete 同口径：分析中一律 409。不看 user_hint 是否已存——
    # 首次解析后 hint 永非空，以它短路会让 analyzing 中重复入队双跑（m-2）
    if row["status"] in ("pending", "analyzing"):
        return JSONResponse(
            status_code=409,
            content={"error": "模板正在分析中，请等待完成后再重解析", "code": "FORM_TEMPLATE_ANALYZING"},
        )
    db.update_form_template_hint(template_id, req.user_hint)
    background_tasks.add_task(_run_template_analysis, template_id)
    return {"template_id": template_id, "status": "pending"}


@router.delete("/templates/{template_id}")
async def delete_form_template(template_id: str):
    """删除模板（本期物理删除）。被实例引用时实例悬空存活（2.4 不变量 11）；
    上传模板同步清理源文件。"""
    db = get_db()
    row = db.get_form_template(template_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Form template not found: {template_id}")
    if row["status"] in ("pending", "analyzing"):
        return JSONResponse(
            status_code=409,
            content={"error": "模板正在分析中，请等待完成后再删除", "code": "FORM_TEMPLATE_ANALYZING"},
        )
    db.delete_form_template(template_id)
    if row["source"] == "upload" and row["file_path"] and os.path.exists(row["file_path"]):
        try:
            os.remove(row["file_path"])
        except OSError:
            logger.warning("Failed to remove form template file: %s", row["file_path"])
    return {"success": True}


# ── 填表实例 API（项目作用域）──────────────────────────────


class FormFillCreateRequest(BaseModel):
    """发起填表请求（2.4 §4.2 写路径 B）。"""
    project_id: str
    template_id: str
    title: Optional[str] = None  # 缺省「{模板名} {YYYY-MM-DD}」（2.4 §3.2）


class FormFillUpdateRequest(BaseModel):
    """人工修订请求：完整值集（FormSchemaForm 收集，形状与 LLM 值集同构）。"""
    values: dict


@router.post("/fills")
async def start_form_fill(
    req: FormFillCreateRequest, background_tasks: BackgroundTasks
):
    """发起填表：准入校验 → 并发限制 → INSERT(pending) → 后台执行。"""
    # 准入前置校验（0.4 §7.3）：缺 LLM/嵌入模型时结构化报错
    ensure_ready("form_fill")
    db = get_db()
    template = db.get_form_template(req.template_id)
    if not template:
        raise HTTPException(status_code=404, detail=f"Form template not found: {req.template_id}")
    if template["status"] != "success":
        return JSONResponse(
            status_code=409,
            content={
                "error": "模板尚未分析完成或分析失败，无法发起填表",
                "code": "FORM_TEMPLATE_NOT_READY",
            },
        )
    if _file_changed(template):
        # 旧 schema 是旧文件的格子索引，续用会写错位——阻断并引导重解析（2.4 §2.4）
        return JSONResponse(
            status_code=409,
            content={
                "error": "模板文件已更新，请先重新解析后再发起填表",
                "code": "FORM_TEMPLATE_CHANGED",
            },
        )
    if not db.get_project(req.project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {req.project_id}")
    # 并发限制检查（对齐报告模式，2.4 §4.2）
    settings = db.get_settings()
    max_concurrent = settings.get("form_fill_max_concurrent", 2)
    active_count = db.count_active_form_fills()
    if active_count >= max_concurrent:
        return JSONResponse(
            status_code=429,
            content={
                "error": f"当前已有 {active_count} 个填表任务进行中（上限 {max_concurrent}），请稍后再试。",
                "code": "FORM_FILL_CONCURRENT_LIMIT",
            },
        )
    title = (req.title or "").strip() or f"{template['name']} {date.today().isoformat()}"
    fill = db.create_form_fill(req.project_id, req.template_id, title)
    background_tasks.add_task(_run_form_fill, fill["id"], get_data_dir())
    return {"fill_id": fill["id"], "status": "pending"}


@router.get("/fills")
async def list_form_fills(
    project_id: str,
    limit: int = Query(50, ge=0, description="Page size; 0 = return all"),
    offset: int = Query(0, ge=0, description="Rows to skip"),
):
    """项目填表实例列表（轻量视图 + 模板名 + 该次填表 token 消耗，2.10 §4.5）。"""
    db = get_db()
    # limit+1 探测 has_more（list_tasks 同款，免 COUNT）；usage 只算当前页行
    fills = db.list_form_fills(
        project_id, limit=limit + 1 if limit > 0 else 0, offset=offset)
    has_more = limit > 0 and len(fills) > limit
    if has_more:
        fills = fills[:limit]
    usage_map = db.get_usage_by_entities([f["id"] for f in fills])
    for f in fills:
        f["usage"] = usage_map.get(f["id"])
    total = db.conn.execute(
        "SELECT COUNT(*) AS cnt FROM form_fills WHERE project_id = ?", (project_id,)
    ).fetchone()["cnt"]
    return {"fills": fills, "has_more": has_more, "total": total}


@router.get("/fills/{fill_id}")
async def get_form_fill_detail(fill_id: str):
    """实例详情：schema + 值集 + 报告（动态表单回填所需，2.4 §5 preview 视图）。"""
    db = get_db()
    fill = db.get_form_fill(fill_id)
    if not fill:
        raise HTTPException(status_code=404, detail=f"Form fill not found: {fill_id}")
    for col in ("report", "filled_values"):
        raw = fill.get(col)
        if raw:
            try:
                fill[col] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                fill[col] = None
    template = db.get_form_template(fill["template_id"])
    schema = None
    if template and template.get("template_schema"):
        try:
            schema = json.loads(template["template_schema"])
        except (json.JSONDecodeError, TypeError):
            schema = None
    return {"fill": fill, "schema": schema}


@router.post("/fills/{fill_id}/update")
async def update_form_fill_values(fill_id: str, req: FormFillUpdateRequest):
    """人工修订（2.4 §4.2 写路径 C）：值集归一化 → apply_field 全量重放模板
    → 秒级重新生成产物。同步、不走 LLM；AI 初填与人工修订共用值集通道。"""
    db = get_db()
    fill = db.get_form_fill(fill_id)
    if not fill:
        raise HTTPException(status_code=404, detail=f"Form fill not found: {fill_id}")
    if fill["status"] in ("pending", "filling"):
        return JSONResponse(
            status_code=409,
            content={"error": "填表进行中，请等待完成后再修订", "code": "FORM_FILL_RUNNING"},
        )
    template = db.get_form_template(fill["template_id"])
    schema = None
    if template and template.get("template_schema"):
        try:
            schema = json.loads(template["template_schema"])
        except (json.JSONDecodeError, TypeError):
            schema = None
    if not schema or not template or not os.path.exists(template["file_path"]):
        return JSONResponse(
            status_code=409,
            content={"error": "模板不可用（已删除或分析产物缺失），无法重新生成",
                     "code": "FORM_TEMPLATE_MISSING"},
        )
    from docx import Document
    from form_fill.docx_filler import apply_field
    from form_fill.fill_service import _entry, _normalize_value

    doc = Document(template["file_path"])
    fields = {f["field_key"]: f for f in schema.get("fields", [])}
    report = []
    clean_values: dict = {}
    for key, field in fields.items():
        v = _normalize_value(field, (req.values or {}).get(key))
        if v is None:
            # 未填/清空：不写入该格（重放语义下模板原样留白），报告记 no_data
            report.append(_entry(field, "no_data"))
            continue
        r = apply_field(doc, field, v)
        if r["ok"]:
            clean_values[key] = v
        report.append(_entry(field, "filled" if r["ok"] else "failed", v, r["missed"]))
    output_path = fill.get("output_path") or os.path.join(
        get_data_dir(), "form_fills", f"{fill_id}.docx")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    doc.save(output_path)
    db.update_form_fill_values(fill_id, clean_values, report, output_path)
    logger.info("Form fill [%s] manually revised: %d/%d filled",
                fill_id, len(clean_values), len(fields))
    fill = db.get_form_fill(fill_id)
    fill["report"] = report
    fill["filled_values"] = clean_values
    return {"fill": fill, "report": report}


@router.get("/fills/{fill_id}/download")
async def download_form_fill(fill_id: str):
    """产物下载（文件流 → Electron dialog.showSaveDialog 写出）。"""
    db = get_db()
    fill = db.get_form_fill(fill_id)
    if not fill:
        raise HTTPException(status_code=404, detail=f"Form fill not found: {fill_id}")
    if not fill.get("output_path") or not os.path.exists(fill["output_path"]):
        raise HTTPException(status_code=404, detail="产物文件不存在（尚未生成或已被清理）")
    # 复用报告导出的文件名净化（跨 router 复用私有实现，避免双实现漂移）
    from routers.reports import _sanitize_filename
    filename = f"{_sanitize_filename(fill['title'])}.docx"
    return FileResponse(
        fill["output_path"],
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@router.delete("/fills/{fill_id}")
async def delete_form_fill(fill_id: str):
    """删除实例并级联清理产物文件。"""
    db = get_db()
    fill = db.get_form_fill(fill_id)
    if not fill:
        raise HTTPException(status_code=404, detail=f"Form fill not found: {fill_id}")
    if fill["status"] in ("pending", "filling"):
        # 进行中后台任务持有 DB 引用/将写产物：删行与任务写盘竞争、LLM 继续烧 token
        return JSONResponse(
            status_code=409,
            content={"error": "填表进行中，请等待完成后再删除", "code": "FORM_FILL_RUNNING"},
        )
    if fill.get("output_path") and os.path.exists(fill["output_path"]):
        try:
            os.remove(fill["output_path"])
        except OSError:
            logger.warning("Failed to remove form fill output: %s", fill["output_path"])
    db.delete_form_fill(fill_id)
    return {"success": True}
