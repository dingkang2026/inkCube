"""python/routers/usage.py — Token 用量查询 API（docs/2.10token-usage-design.md §4.6）。

聚合读端点：总览（summary：today/total/range 三组 + by_day/by_source/
by_model/by_project 分组）与任务轮次（task/{id}）。后端实时 GROUP BY
无缓存——任何重取天然正确（§5.4 刷新自愈的根因）。
"""
import logging

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/usage", tags=["usage"])


def get_db():
    """Get database instance."""
    try:
        import deps
        return deps.db
    except ImportError:
        raise HTTPException(status_code=500, detail="Database not initialized")


@router.get("/summary")
async def usage_summary(range_key: str = "all", project_id: str | None = None):
    """用量总览：today/total 两组固定（状态栏一次请求拿全）+ range 窗口
    聚合与 by_* 分组（设置-用量统计 tab 数据源）；project_id 传参时为
    项目下钻视图（项目详情徽章 hover 分拆同源）。"""
    return get_db().get_usage_summary(range_key=range_key, project_id=project_id)


@router.get("/task/{task_id}")
async def usage_task_rounds(
    task_id: str,
    limit: int = Query(50, ge=0, description="Page size; 0 = return all"),
    offset: int = Query(0, ge=0, description="Rows to skip"),
):
    """任务轮次聚合：每轮 = 同 round_id 的全部调用（任务详情轮次下钻）。

    limit=0 → 全量；total = 去重轮次数（COUNT DISTINCT round_id）。"""
    db = get_db()
    rows = db.get_usage_task_rounds(
        task_id, limit=limit + 1 if limit > 0 else 0, offset=offset)
    has_more = limit > 0 and len(rows) > limit
    if has_more:
        rows = rows[:limit]
    total = db.conn.execute(
        "SELECT COUNT(DISTINCT round_id) AS n FROM llm_usage "
        "WHERE task_id = ? AND round_id IS NOT NULL",
        (task_id,),
    ).fetchone()["n"]
    return {"rounds": rows, "has_more": has_more, "total": total}
