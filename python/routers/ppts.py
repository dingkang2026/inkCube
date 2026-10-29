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
    except ppt_service.SourceReportError as e:
        # 来源报告正文不可读（content.md 缺失/已清理）：创建前拦截，禁止
        # 静默降级「自由主题」跑题生成（2026-10-28 复盘）
        return JSONResponse(status_code=400,
                            content={"error": str(e), "code": "SOURCE_REPORT_UNREADABLE"})
    except Exception as e:
        logger.error("Create ppt failed: %s", e, exc_info=True)
        return JSONResponse(status_code=500,
                            content={"error": f"创建失败: {e}", "code": "CREATE_FAILED"})
    ppt, err = _launch_or_rollback(ppt, message)
    if err:
        return err
    return {"ppt": _detail(deps.db, ppt)}


def _launch_or_rollback(ppt: dict, message: str):
    """launch + 失败回滚：ppts 行已以 running 落库
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


def _detail(db, ppt: dict) -> dict:
    """详情聚合：任务态 + confirm_ui 端口 + 创作页清单（SVG 预览网格数据源）。
    页面内容前端复用 tasks:readFile（task_id 随详情返回），不另设转发端点。"""
    task = db.get_task(ppt.get("task_id") or "") or {}
    port = None
    pages: list[str] = []
    if ppt.get("workspace_path"):
        project = Path(ppt["workspace_path"])
        port = ppt_service.confirm_ui_port(project)
        # workspace 相对路径：前端复用 tasks:readFile 直读（基准对齐）
        pages = ppt_service.pages_for_workspace(project, task.get("workspace_path"))
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
    """手动继续（paused / awaiting_input / failed 重试的用户显式推进）。"""
    logger.info("Resuming ppt: %s", ppt_id)
    return ppt_service.service.answer(deps.db, ppt_id, None)


@router.post("/ppts/{ppt_id}/retry-collect")
async def retry_collect_ppt(ppt_id: str):
    """模板收录重试（P2-3）：包已产出但收录失败的行，幂等重收录
    （纯本地 IO，不跑 agent、不耗模型；前端仅在工作区包完整时暴露入口）。"""
    logger.info("Retrying ppt template collect: ppt=%s", ppt_id)
    # 收录含工作区包拷贝（images/exports 大文件可达 30s+），线程池执行
    # 防阻塞事件循环（对齐 create 的 prepare 线程化惯例）
    return await asyncio.to_thread(
        ppt_service.service.retry_collect, deps.db, ppt_id)


@router.post("/ppts/{ppt_id}/cancel")
async def cancel_ppt(ppt_id: str):
    logger.info("Cancelling ppt: %s", ppt_id)
    return await ppt_service.service.cancel(deps.db, ppt_id)
