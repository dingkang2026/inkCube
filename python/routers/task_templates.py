"""Task templates router (0.5 §5, E2) — 自定义任务模板 CRUD。

内置模板不落库（前端 i18n 常量为唯一事实源）：GET 返回自定义行 + 置顶内置名
列表；内置置顶态是 builtin=1 记录行（id='tpl_builtin_<name>'，仅存置顶态，
category/title/prompt 为占位空串不消费），DELETE 该行即取消置顶。
错误契约（D1）：400 用 JSONResponse {error, code} 体，经 handlePythonResponse
解析为 err.errorCode。
"""
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import deps

logger = logging.getLogger(__name__)

router = APIRouter()


class TaskTemplateBody(BaseModel):
    id: str | None = None           # None=新建（'tpl_custom_<hex8>'），否则更新
    scope: str = "global"           # 'global' | 'project'
    projectId: str | None = None    # scope='project' 时必填
    category: str                   # info_gather|monitor|industry|report_invest|office
    title: str
    prompt: str                     # 含 {占位符}
    pinned: bool = False


class PinBody(BaseModel):
    pinned: bool


@router.get("/task-templates")
async def list_task_templates(scope: str | None = None, projectId: str | None = None):
    """自定义行 + 置顶内置名；scope/projectId 透传 db 过滤。"""
    rows = deps.db.list_task_templates(scope=scope, project_id=projectId)
    return {
        "templates": [r for r in rows if not r["builtin"]],
        "builtinPinned": [r["id"] for r in rows if r["builtin"] and r["pinned"]],
    }


@router.post("/task-templates")
async def save_task_template(body: TaskTemplateBody):
    """新建/更新自定义模板；scope='project' 且无 projectId → 400 INVALID_SCOPE。"""
    if body.scope == "project" and not body.projectId:
        return JSONResponse(status_code=400, content={
            "error": "scope='project' 需要提供 projectId", "code": "INVALID_SCOPE"})
    tpl = deps.db.upsert_task_template(
        body.id, body.scope,
        body.projectId if body.scope == "project" else None,
        body.category, body.title, body.prompt, body.pinned)
    return {"template": tpl}


@router.delete("/task-templates/{tpl_id}")
async def delete_task_template(tpl_id: str):
    """删除自定义模板；builtin 记录行也可删（= 取消置顶）。"""
    deps.db.delete_task_template(tpl_id)
    return {"ok": True}


@router.post("/task-templates/{tpl_id}/pin")
async def pin_task_template(tpl_id: str, body: PinBody):
    """置顶开关：自定义行 UPDATE；内置 id（'builtin_*'）落/删置顶记录行。"""
    deps.db.set_task_template_pinned(tpl_id, body.pinned)
    return {"ok": True}
