"""python/routers/ppts.py — PPT 交付物 API（2.3 §6.3 端点清单）。

执行态权威在 tasks（origin='ppt'），产物文件权威在 workspace，
本路由只做登记簿 CRUD、门禁应答与进度/文件只读聚合。
"""
import asyncio
import logging
import os
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import deps
import ppt_service
from readiness import ensure_ready

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ppts"])


class PptImageSelection(BaseModel):
    """图片来源复合选择（2.3 §4.1 E2）：钉选全收 + 范围圈池，物化在 ppt_service。"""
    categories: list[str] = []          # 圈池分类（空 = 不限）
    min_width: int | None = None        # 最小宽度像素阈值（None = 不限）
    pinned_ids: list[str] = []          # 钉选（必入池）


class PptCreateRequest(BaseModel):
    title: str
    source_report_id: str | None = None   # 可空=自由主题
    image_selection: PptImageSelection | None = None
    style_ref: str | None = None
    template_refs: str | None = None      # 向导模板选择标识串 "kind:id,..."（None = 不选）
    notes: str | None = None


class PptAnswerRequest(BaseModel):
    text: str | None = None


class PptEnhanceRequest(BaseModel):
    """从项目内已完成的 PPT 交付物发起成品增强（2.3 §4.3 E3）。"""
    notes: str | None = None


class PptEnhanceImportRequest(BaseModel):
    """导入本机 PPTX 发起成品增强（2.3 §4.3 E3；路径由文件选择对话框给出）。"""
    title: str
    source_path: str
    notes: str | None = None


@router.get("/projects/{project_id}/ppts")
async def list_ppts(
    project_id: str,
    limit: int = Query(50, ge=0, description="Page size; 0 = return all"),
    offset: int = Query(0, ge=0, description="Rows to skip"),
):
    """交付物列表（limit+1 探测 has_more，list_tasks 同款；total 供前端计数/分页）。"""
    db = deps.db
    ppts = db.list_ppts(
        project_id, limit=limit + 1 if limit > 0 else 0, offset=offset)
    has_more = limit > 0 and len(ppts) > limit
    if has_more:
        ppts = ppts[:limit]
    total = db.conn.execute(
        "SELECT COUNT(*) AS cnt FROM ppts WHERE project_id = ?", (project_id,)
    ).fetchone()["cnt"]
    return {"ppts": ppts, "has_more": has_more, "total": total}


@router.post("/projects/{project_id}/ppts")
async def create_ppt(project_id: str, req: PptCreateRequest):
    """创建并启动（准入校验同任务对话：缺 LLM 配置时结构化报错，0.4 §7.3）。"""
    ensure_ready("task")
    title = (req.title or "").strip()
    if not title:
        return JSONResponse(status_code=400,
                            content={"error": "标题不能为空", "code": "INVALID_TITLE"})
    sel = req.image_selection
    logger.info(
        "Creating ppt: project=%s title=%s report=%s pinned=%d cats=%s minWidth=%s",
        project_id, title, req.source_report_id,
        len(sel.pinned_ids) if sel else 0,
        sel.categories if sel else None, sel.min_width if sel else None,
    )
    try:
        # 素材就位含复合选择物化 + mermaid 重绘（headless Electron 同步子进程，
        # 秒级×块数），放线程池执行避免阻塞事件循环；launch 需事件循环
        # （create_task），拆两段
        ppt, message = await asyncio.to_thread(
            ppt_service.service.prepare, deps.db, project_id, title,
            source_report_id=req.source_report_id,
            image_selection=sel,
            style_ref=(req.style_ref or "").strip() or None,
            template_refs=(req.template_refs or "").strip() or None,
            notes=(req.notes or "").strip() or None,
        )
    except ppt_service.TemplateRefError as e:
        # 模板选择引用非法（格式/不存在/同 kind 重复）：创建前拦截，
        # 不让 agent 跑到 Stage 1 才发现路径无效
        return JSONResponse(status_code=400,
                            content={"error": str(e), "code": "INVALID_TEMPLATE_REF"})
    except Exception as e:
        logger.error("Create ppt failed: %s", e, exc_info=True)
        return JSONResponse(status_code=500,
                            content={"error": f"创建失败: {e}", "code": "CREATE_FAILED"})
    ppt, err = _launch_or_rollback(ppt, message)
    if err:
        return err
    return {"ppt": _detail(deps.db, ppt)}


def _launch_or_rollback(ppt: dict, message: str):
    """launch + 失败回滚（create/enhance 两入口共用）：ppts 行已以 running 落库
    （prepare 段），launch 失败回滚为 failed——否则无泵僵尸行永久占据活动中心
    与前端轮询，重试创建再双份资源。返回 (ppt, error_response|None)。"""
    try:
        return ppt_service.service.launch(deps.db, ppt, message), None
    except RuntimeError as e:
        code = str(e)
        deps.db.update_ppt_status(
            ppt["id"], "failed",
            error=("任务正在执行中，无法重复启动" if code == "TASK_RUNNING"
                   else "已达到最大并发任务数限制，启动失败"))
        if code == "TASK_RUNNING":
            return None, JSONResponse(status_code=409,
                                      content={"error": "任务正在执行中", "code": "TASK_RUNNING"})
        return None, JSONResponse(status_code=429,
                                  content={"error": "已达到最大并发任务数限制",
                                           "code": "TASK_LIMIT_REACHED"})
    except Exception as e:
        # 非 RuntimeError（如并发写库锁冲突）也会发生在置位/消息落库后：
        # 不处置则行卡在无泵状态，永久占据活动中心与前端轮询
        logger.error("PPT launch failed: ppt=%s", ppt["id"], exc_info=True)
        deps.db.update_ppt_status(ppt["id"], "failed", error=f"启动失败: {e}")
        return None, JSONResponse(status_code=500,
                                  content={"error": "任务启动失败，请重试",
                                           "code": "LAUNCH_FAILED"})


# 增强同源去重锁（per-project，防 TOCTOU）：检查与 prepare 之间的 await 会让出
# 事件循环，enhance / enhance-import 两入口交叉可双过 409——按项目串行化
# 「检查 → 建行 → 入槽」全程。锁对象随项目常驻（量级 = 项目数，不回收）：
# 回收会引入「A 持锁中条目被删、C 重建新锁」的新竞态
_enhance_locks: dict[str, asyncio.Lock] = {}


async def _run_enhance(db, project_id: str, title: str,
                       source_abs: str, notes: str | None):
    """enhance 两入口共用执行链：同源去重 → prepare（staging+建行）→ launch（失败回滚）。"""
    async with _enhance_locks.setdefault(project_id, asyncio.Lock()):
        # 同源去重：同项目已有进行中的增强（running/awaiting 门禁）时拒绝重复入队——
        # 否则两个增强 agent 并发跑，双倍 LLM/音频成本且产出两张几乎相同的交付物
        active = [r for r in db.list_ppts(project_id)
                  if r.get("kind") == "enhanced"
                  and r.get("status") in ("running", "awaiting_input")]
        if active:
            return JSONResponse(status_code=409, content={
                "error": "该项目已有进行中的增强任务，请等待其完成或先取消",
                "code": "ENHANCE_IN_PROGRESS"})
        try:
            # 源拷贝为同步轻活，与 create 段对齐放线程池，避免阻塞事件循环
            ppt, message = await asyncio.to_thread(
                ppt_service.service.prepare_enhance, db,
                project_id=project_id, title=title,
                source_pptx=source_abs, notes=notes,
            )
        except Exception as e:
            logger.error("Enhance ppt failed: %s", e, exc_info=True)
            return JSONResponse(status_code=500,
                                content={"error": f"创建增强任务失败: {e}",
                                         "code": "ENHANCE_CREATE_FAILED"})
        # launch 在事件循环线程执行，锁内仅串行化同项目创建，不阻塞他项
        ppt, err = _launch_or_rollback(ppt, message)
        if err:
            return err
        return {"ppt": _detail(db, ppt)}


@router.post("/ppts/{ppt_id}/enhance")
async def enhance_ppt(ppt_id: str, req: PptEnhanceRequest):
    """从项目内已有 PPT 交付物发起成品增强（源 = 该交付物 export 产物）。"""
    ensure_ready("task")
    src = deps.db.get_ppt(ppt_id)
    if not src:
        return JSONResponse(status_code=404,
                            content={"error": "PPT 不存在", "code": "PPT_NOT_FOUND"})
    if (src.get("kind") != "deck" or src.get("status") != "completed"
            or not src.get("export_path")):
        return JSONResponse(
            status_code=400,
            content={"error": "仅已完成的 PPT 交付物可发起增强",
                     "code": "NOT_ENHANCEABLE"})
    source_abs = str(Path(src["workspace_path"]) / src["export_path"])
    # 与 enhance-import 入口对齐的源文件预检：workspace 被清理/文件移动后
    # 缺失时归 400 SOURCE_NOT_FOUND，而非穿透到拷贝阶段的 500
    if not os.path.isfile(source_abs):
        return JSONResponse(status_code=400,
                            content={"error": "源 PPTX 文件不存在",
                                     "code": "SOURCE_NOT_FOUND"})
    logger.info("Enhancing ppt from deliverable: src=%s project=%s",
                ppt_id, src["project_id"])
    return await _run_enhance(
        deps.db, src["project_id"], f"{src['title']} · 增强", source_abs,
        (req.notes or "").strip() or None)


@router.post("/projects/{project_id}/ppts/enhance-import")
async def enhance_import_ppt(project_id: str, req: PptEnhanceImportRequest):
    """导入本机 PPTX 发起成品增强（路径由前端 dialog:openFile 直传）。"""
    ensure_ready("task")
    title = (req.title or "").strip()
    source = (req.source_path or "").strip()
    if not title:
        return JSONResponse(status_code=400,
                            content={"error": "标题不能为空", "code": "INVALID_TITLE"})
    if not source.lower().endswith(".pptx"):
        return JSONResponse(status_code=400,
                            content={"error": "仅支持 .pptx 文件",
                                     "code": "UNSUPPORTED_PPTX_FORMAT"})
    if not os.path.isfile(source):
        return JSONResponse(status_code=400,
                            content={"error": "源 PPTX 文件不存在", "code": "SOURCE_NOT_FOUND"})
    logger.info("Enhancing ppt from import: project=%s source=%s", project_id, source)
    return await _run_enhance(
        deps.db, project_id, title, source,
        (req.notes or "").strip() or None)


def _detail(db, ppt: dict) -> dict:
    """详情聚合：任务态 + confirm_ui 端口 + 创作页清单（SVG 预览网格数据源）。
    页面内容前端复用 tasks:readFile（task_id 随详情返回），不另设转发端点。"""
    task = db.get_task(ppt.get("task_id") or "") or {}
    port = None
    pages: list[str] = []
    if ppt.get("workspace_path"):
        project = Path(ppt["workspace_path"])
        port = ppt_service.confirm_ui_port(project)
        pages = ppt_service.list_pages(project)
    return {**ppt, "task_status": task.get("status"),
            "confirm_ui_port": port, "pages": pages}


@router.get("/ppts/{ppt_id}")
async def get_ppt(ppt_id: str):
    ppt = deps.db.get_ppt(ppt_id)
    if not ppt:
        return JSONResponse(status_code=404,
                            content={"error": "PPT 不存在", "code": "PPT_NOT_FOUND"})
    ppt_service.sync_progress(deps.db, ppt)
    # 泵随回合结束退出，回执续跑由本详情页轮询接棒（提交后 3s 内响应）
    ppt_service.service.auto_resume_if_receipt(deps.db, ppt)
    return {"ppt": _detail(deps.db, deps.db.get_ppt(ppt_id) or ppt)}


@router.delete("/ppts/{ppt_id}")
async def delete_ppt(ppt_id: str):
    logger.info("Deleting ppt: %s", ppt_id)
    return await ppt_service.service.delete(deps.db, ppt_id)


@router.post("/ppts/{ppt_id}/answer")
async def answer_ppt(ppt_id: str, req: PptAnswerRequest):
    """门禁应答 → 续跑（常规路径由自动续跑接管；此处兜底/补充意见）。"""
    logger.info("PPT answer: ppt=%s text_len=%d", ppt_id, len(req.text or ""))
    return ppt_service.service.answer(deps.db, ppt_id, (req.text or "").strip() or None)


@router.post("/ppts/{ppt_id}/pause")
async def pause_ppt(ppt_id: str):
    logger.info("Pausing ppt: %s", ppt_id)
    return ppt_service.service.pause(deps.db, ppt_id)


@router.post("/ppts/{ppt_id}/resume")
async def resume_ppt(ppt_id: str):
    """手动继续（paused / awaiting_input 状态的用户显式推进）。"""
    logger.info("Resuming ppt: %s", ppt_id)
    return ppt_service.service.answer(deps.db, ppt_id, None)


@router.post("/ppts/{ppt_id}/cancel")
async def cancel_ppt(ppt_id: str):
    logger.info("Cancelling ppt: %s", ppt_id)
    return await ppt_service.service.cancel(deps.db, ppt_id)
