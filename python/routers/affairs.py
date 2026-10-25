"""python/routers/affairs.py — 时期任务路由(3.0 二期)

挂 /projects/{project_id}/tasks(与 knowledge.py 的 /knowledge/projects 无冲突)。
IPC 通道前缀 affairs:*(裁决:tasks:* 已被 TaskAgent 长任务占用)。
"""
import logging
from datetime import date

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

import deps
import affairs_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/projects", tags=["affairs"])


def _db():
    return deps.db


class TaskIn(BaseModel):
    title: str
    task_type: str                      # material | report | milestone | recurring
    description: str = ""
    due_date: str | None = None
    recurrence_rule: dict | None = None
    auto_complete: dict | None = None


_TASK_TYPES = {"material", "report", "milestone", "recurring"}
# 手动可设状态;pending_activation 是系统态(阶段切换时机),不可手动指定
_PATCHABLE_STATUS = {"open", "done", "skipped"}
# 手动可改时期(Task 10 开放编辑);'none' 是项目 stage 值,任务 phase 无此取值
_PATCHABLE_PHASES = {"before", "during", "after"}


@router.get("/due")
async def list_due_tasks(horizon_days: int = 7):
    """首页今日工作台 + 侧栏色点数据源(3.0 三期 §7.2):跨项目一次返回,防 N+1。

    声明在 /{project_id}/tasks 之前,防未来新增单段项目路由遮蔽 /due。
    """
    if not 1 <= horizon_days <= 31:
        raise HTTPException(status_code=422, detail="horizon_days must be 1-31")
    db = _db()
    return {
        "tasks": db.list_due_phase_tasks(horizon_days),
        "reviews_counts": db.count_pending_reviews_by_project(),
    }


@router.get("/{project_id}/tasks")
async def list_phase_tasks(
    project_id: str,
    limit: int = Query(50, ge=0, description="Page size; 0 = return all"),
    offset: int = Query(0, ge=0, description="Rows to skip"),
):
    """时期任务列表（看板/节奏分页；日历视图前端传 limit=0 全量，分页不截断日历）。"""
    db = _db()
    # limit+1 探测 has_more（list_tasks 同款，免 COUNT）
    tasks = db.list_phase_tasks(
        project_id, limit=limit + 1 if limit > 0 else 0, offset=offset)
    has_more = limit > 0 and len(tasks) > limit
    if has_more:
        tasks = tasks[:limit]
    total = db.conn.execute(
        "SELECT COUNT(*) AS cnt FROM phase_tasks WHERE project_id = ?",
        (project_id,),
    ).fetchone()["cnt"]
    return {"tasks": tasks, "has_more": has_more, "total": total}


@router.post("/{project_id}/tasks")
async def create_phase_task(project_id: str, body: TaskIn):
    db = _db()
    if body.task_type not in _TASK_TYPES:
        raise HTTPException(status_code=422, detail="invalid task_type")
    if not body.title.strip():
        raise HTTPException(status_code=422, detail="title required")
    project = db.get_project(project_id)
    if not project or project.get("kind") != "project":
        raise HTTPException(status_code=404, detail="project not found")
    phase = affairs_service.phase_of(project.get("stage") or "none")
    # 自建 recurring:rule 白名单校验(与 roll/activate 同口径)并铺首个
    # 未过期期次实例——occ/due 由 rule 推导(当期已过 → 下一期,2026-10-24 裁决:
    # 自建不产出出生即逾期实例),用户传的 due_date 对 recurring 无意义
    # (随期次滚动,单期可后续 PATCH);非 recurring 沿用用户 given due
    occ = None
    due = body.due_date
    if body.task_type == "recurring":
        rule = affairs_service.parse_rule(body.recurrence_rule)
        if not rule:
            raise HTTPException(status_code=422, detail="invalid recurrence_rule")
        occ, due = affairs_service.first_open_occurrence(rule, date.today())
    task = db.create_phase_task(
        project_id=project_id, phase=phase, task_type=body.task_type,
        title=body.title.strip(), description=body.description,
        due_date=due, recurrence_rule=body.recurrence_rule,
        auto_complete=body.auto_complete, occurrence_key=occ,
    )
    return {"task": task}


@router.patch("/{project_id}/tasks/{task_id}")
async def update_phase_task(project_id: str, task_id: str, body: dict):
    db = _db()
    task = db.get_phase_task(task_id)
    if not task or task["project_id"] != project_id:
        raise HTTPException(status_code=404, detail="task not found")
    fields = body or {}
    if "status" in fields and fields["status"] not in _PATCHABLE_STATUS:
        raise HTTPException(status_code=422, detail="invalid status")
    if "phase" in fields and fields["phase"] not in _PATCHABLE_PHASES:
        raise HTTPException(status_code=422, detail="invalid phase")
    # rule 与 POST 同口径校验:伪日期(02-30)等非法值若落库,roll 时整组
    # 静默停滚;显式 null(清除规则)保留(Task 10 复审 Major)
    if fields.get("recurrence_rule") is not None and not affairs_service.parse_rule(fields["recurrence_rule"]):
        raise HTTPException(status_code=422, detail="invalid recurrence_rule")
    updated = db.update_phase_task(task_id, fields)
    return {"task": updated}


@router.delete("/{project_id}/tasks/{task_id}")
async def delete_phase_task(project_id: str, task_id: str):
    db = _db()
    task = db.get_phase_task(task_id)
    if not task or task["project_id"] != project_id:
        raise HTTPException(status_code=404, detail="task not found")
    db.delete_phase_task(task_id)
    return {"ok": True}


# ── 3.0 四期:事件驱动任务建议(3.1 §4.3;采纳/忽略是人类裁决口)──


@router.get("/{project_id}/suggestions")
async def list_suggestions(
    project_id: str,
    status: str = "pending",
    limit: int = Query(50, ge=0, description="Page size; 0 = return all"),
    offset: int = Query(0, ge=0, description="Rows to skip"),
):
    """建议列表（建议卡分页拉取；脉搏徽章传 limit=1 消费 total，与 reviews counts 桶同款最小负载）。"""
    db = _db()
    # limit+1 探测 has_more（list_tasks 同款，免 COUNT）
    suggestions = db.list_suggestions(
        project_id, status, limit=limit + 1 if limit > 0 else 0, offset=offset)
    has_more = limit > 0 and len(suggestions) > limit
    if has_more:
        suggestions = suggestions[:limit]
    total = db.conn.execute(
        "SELECT COUNT(*) AS cnt FROM task_suggestions"
        " WHERE project_id = ? AND status = ?",
        (project_id, status),
    ).fetchone()["cnt"]
    return {"suggestions": suggestions, "has_more": has_more, "total": total}


@router.post("/{project_id}/suggestions/{suggestion_id}/accept")
async def accept_suggestion(project_id: str, suggestion_id: str):
    """采纳:create_task → 建任务回填;complete_task → 目标打 done。
    目标失效 → 404 且建议转 dismissed(失效永不再提,3.1 §4.4)。
    原子性(整体 review W2):pending 守卫 + 动作 + 回填在 db 层单事务,
    accept×dismiss 并发/双击由行级守卫串行化,后到者 409。"""
    db = _db()
    sug = db.get_suggestion(suggestion_id)
    if not sug or sug["project_id"] != project_id or sug["status"] != "pending":
        raise HTTPException(status_code=404, detail="suggestion not found")
    result = db.accept_suggestion_atomic(suggestion_id)
    if result is None:
        raise HTTPException(status_code=409, detail="suggestion already decided")
    if result.get("reason") == "target_missing":
        raise HTTPException(status_code=404, detail="target_task_missing")
    return {"task": result["task"]}


@router.post("/{project_id}/suggestions/{suggestion_id}/dismiss")
async def dismiss_suggestion(project_id: str, suggestion_id: str):
    db = _db()
    sug = db.get_suggestion(suggestion_id)
    if not sug or sug["project_id"] != project_id or sug["status"] != "pending":
        raise HTTPException(status_code=404, detail="suggestion not found")
    # only_pending 条件更新(整体 review W2):与 accept 并发时后到者不得覆盖已决状态
    decided = db.decide_suggestion(suggestion_id, "dismissed", only_pending=True)
    if decided is None:
        raise HTTPException(status_code=409, detail="suggestion already decided")
    return {"suggestion": decided}
