"""python/routers/activities.py — Activity Hub 聚合端点（0.4lifecycle-design.md §8.1 #0）。

单一常驻轮询的数据源：活跃活动列表（按项目分组）+ 未读/最近通知 + summary。
前端 ActivityMonitor 每 5/15s 拉取一次，所有全局反馈面（Toast / 徽章 / 首页活动区）
从同一份数据取数（0.4 §3.6 聚合模型 + 前端派生）。

活跃侧统一经 activities_registry 取快照（0.4 §2.2）：本文件仅保留各 kind 的
fetch 实现（存量函数）+ 注册；新增长时活动禁止再往本文件加私有聚合函数，
一律实现 ActivitySource 后注册（coding-habits §2.7）。

后端零文案：entity_title 是用户数据，展示文案由前端 i18n 按 entity_type + kind 渲染。
"""
import asyncio
import json
import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import activities_registry as registry
import activity_events
import deps
import ppt_service
import scheduler
from routers import forms
from transfer import service as transfer_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["system"])


def _active_files(db) -> list[dict]:
    """跨项目活跃文件（GROUP BY project_id，file 活跃定义含图片描述期，0.4 §8.1）。

    停止态口径（0.4 §队列暂停修订）：排队中（pending）不算活跃，只有真正
    在飞的（parsing / describing）保留——活跃标识精确反映队列状态，停止后
    只剩收尾任务呼吸、跑完自然熄灭；恢复后心跳下一拍重新点亮。
    """
    cursor = db.conn.execute(
        """
        SELECT f.project_id, p.name AS project_name,
               CASE WHEN p.kind = 'base_lib' THEN 'baselib' ELSE 'project' END AS container,
               COUNT(*) AS cnt,
               SUM(CASE WHEN f.status = 'done'
                        AND (f.image_status = 'describing'
                             OR (f.image_status = 'pending' AND NOT :describe_paused))
                        THEN 1 ELSE 0 END) AS image_cnt
        FROM files f
        JOIN projects p ON p.id = f.project_id
        WHERE f.status = 'parsing'
           OR (f.status = 'pending' AND NOT :parsing_paused)
           OR (f.status = 'done' AND (
                f.image_status = 'describing'
                OR (f.image_status = 'pending' AND NOT :describe_paused)))
        GROUP BY f.project_id, p.name, p.kind
        """,
        {
            "parsing_paused": db.get_flag("parsing_paused"),
            "describe_paused": db.get_flag("describe_paused"),
        },
    )
    return [
        {
            "entity_type": "file",
            "project_id": row["project_id"],
            "project_name": row["project_name"],
            "container": row["container"] or "project",
            "count": row["cnt"],
            "image_describing": row["image_cnt"] or 0,
        }
        for row in cursor.fetchall()
    ]


def _active_compiles(db) -> list[dict]:
    """跨项目活跃 Wiki 编译（queued/ingesting/storing）。"""
    cursor = db.conn.execute(
        """
        SELECT wct.id, wct.project_id, wct.status,
               wct.pages_completed, wct.pages_total,
               p.name AS project_name,
               CASE WHEN p.kind = 'base_lib' THEN 'baselib' ELSE 'project' END AS container
        FROM wiki_compile_tasks wct
        JOIN projects p ON p.id = wct.project_id
        WHERE wct.status IN ('queued', 'ingesting', 'storing')
        ORDER BY wct.created_at DESC
        """
    )
    result = []
    for row in cursor.fetchall():
        progress_text = ""
        if row["pages_total"]:
            progress_text = f"{row['pages_completed'] or 0}/{row['pages_total']}"
        result.append({
            "entity_type": "wiki_compile",
            "entity_id": row["id"],
            "project_id": row["project_id"],
            "project_name": row["project_name"],
            "container": row["container"] or "project",
            "status": row["status"],
            "progress_text": progress_text,
        })
    return result


def _active_reports(db) -> list[dict]:
    """跨项目生成中的报告。"""
    cursor = db.conn.execute(
        """
        SELECT r.id, r.project_id, r.title, r.progress, p.name AS project_name,
               CASE WHEN p.kind = 'base_lib' THEN 'baselib' ELSE 'project' END AS container
        FROM reports r
        JOIN projects p ON p.id = r.project_id
        WHERE r.status = 'generating'
        ORDER BY r.created_at DESC
        """
    )
    return [
        {
            "entity_type": "report",
            "entity_id": row["id"],
            "project_id": row["project_id"],
            "project_name": row["project_name"],
            "container": row["container"] or "project",
            "entity_title": row["title"],
            "progress": row["progress"] or 0,
        }
        for row in cursor.fetchall()
    ]


def _active_tasks(db) -> list[dict]:
    """运行中任务（复用 task_agent 内存 running 集，补标题）。

    PPT 借道任务（origin='ppt'）不入 task 源：PPT 活动已由 ppt 源
    （fetch_active，entity_type='ppt'）呈现，双源上报会造成首页
    双条目与 running_tasks 假角标（2.3 审核项 #2）。
    影子任务（schedule_id 非空，定时订阅执行）同口径排除：订阅执行由
    schedule 源（scheduler.fetch_active）呈现（2.11 D2 复核）——影子
    任务不进人工任务列表（服务端排除），混入 task 源会造成任务假角标
    与点击落任务页扑空。
    """
    agent = getattr(deps, "task_agent", None)
    if not agent:
        return []
    result = []
    for task_id in list(agent._running_task_ids):
        task = db.get_task(task_id)
        if (task and task.get("origin") != "ppt"
                and not task.get("schedule_id")):
            result.append({
                "entity_type": "task",
                "entity_id": task_id,
                "entity_title": task.get("title") or task_id,
            })
    return result


def _active_model_downloads() -> list[dict]:
    """下载中模型（ModelManager 内存态，仅 CPU 后端，无 DB 查询，0.4 §3.3）。"""
    mgr = getattr(deps, "model_manager", None)
    if not mgr:
        return []
    try:
        return [
            {
                "entity_type": "model_download",
                "entity_id": dl["entity_id"],
                "entity_title": dl["entity_title"],
                "progress": dl.get("progress", 0),
            }
            for dl in mgr.get_active_downloads()
        ]
    except Exception as e:
        logger.warning("get_active_downloads failed: %s", e)
        return []


def _active_ledger_parses() -> list[dict]:
    """台账解析中(3.1 §4.2:编译后直调,进程内存态源;0.4 §8.1 台账新增 kind)。"""
    from wiki.ledger_service import fetch_running
    return fetch_running()


# form_template / form_fill（2.4 §3.4）：fetch 实现在 routers/forms.py
# （业务模块归属，本文件不再新增私有聚合函数）


# ── 统一活动注册表（0.4 §2.2）：聚合端点的唯一活跃数据源 ──
# fetch 均以 lambda 引用模块级函数（运行时名字查找），
# 保证测试 monkeypatch _active_* 后注册表取到替换实现。
# 全部 kind 均已收编（0.4 §6.1 / §8.2）；新增长时活动一律实现 ActivitySource 后注册。
registry.register(registry.FunctionSource("file", lambda: _active_files(deps.db)))
registry.register(registry.FunctionSource("wiki_compile", lambda: _active_compiles(deps.db)))
registry.register(registry.FunctionSource("report", lambda: _active_reports(deps.db)))
registry.register(registry.FunctionSource("task", lambda: _active_tasks(deps.db)))
registry.register(registry.FunctionSource("model_download", lambda: _active_model_downloads()))
registry.register(registry.FunctionSource("ledger_parse", lambda: _active_ledger_parses()))
registry.register(registry.FunctionSource("form_template",
                                          lambda: forms.active_form_templates(deps.db)))
registry.register(registry.FunctionSource("form_fill",
                                          lambda: forms.active_form_fills(deps.db)))
# project_export / project_import（2.9 §7）：fetch 实现在 transfer/service.py
registry.register(registry.FunctionSource("project_export",
                                          lambda: transfer_service.fetch_running("project_export")))
registry.register(registry.FunctionSource("project_import",
                                          lambda: transfer_service.fetch_running("project_import")))
# ppt（2.3 §6）：fetch 实现在 ppt_service（业务模块归属）
registry.register(registry.FunctionSource("ppt",
                                          lambda: ppt_service.service.fetch_active(deps.db)))
# schedule（2.11 §5）：fetch 实现在 scheduler（业务模块归属）
registry.register(registry.FunctionSource("schedule",
                                          lambda: scheduler.fetch_active(deps.db)))


@router.get("/activities/status")
async def activities_status():
    """全局活动状态聚合（0.4 §8.1 #0 / §2.2）：active（注册表快照）+ notifications + summary。"""
    db = deps.db

    # 3.0 §5.4 滚动生成器裁决:心跳入口 check-date roll(跨日即滚,同日零开销;
    # 复用既有心跳,不新增轮询端点;服务层自行兜底异常)
    import affairs_service
    affairs_service.maybe_roll_daily(db)

    # 2.11 §3.3 模板文件变更扫描(每日一次,同日零开销;服务层自行兑底异常)
    forms.maybe_scan_template_changes(db)

    # 2.11 §3.2 事务到期/逾期提醒扫描(每日一次,同日零开销;服务层自行兑底异常)
    affairs_service.maybe_scan_reminders(db)

    active = registry.snapshot()

    def _count(entity_type: str) -> int:
        return sum(1 for a in active if a["entity_type"] == entity_type)

    summary = {
        "active_files": sum(
            a["count"] for a in active if a["entity_type"] == "file"
        ),
        "active_compiles": _count("wiki_compile"),
        "generating_reports": _count("report"),
        "running_tasks": _count("task"),
        "downloading_models": _count("model_download"),
        "analyzing_form_templates": _count("form_template"),
        "filling_forms": _count("form_fill"),
        "active_ppts": _count("ppt"),
        "running_schedules": _count("schedule"),
    }

    return {
        "active": active,
        "notifications": {
            "unread": db.list_unread_notifications(),
            "recent": db.list_recent_notifications(10),
        },
        "summary": summary,
    }


class MarkReadRequest(BaseModel):
    ids: list[int]


@router.post("/activities/notifications/read")
async def mark_notifications_read(req: MarkReadRequest):
    """批量标记通知已读（弹出即已读，0.4 §5.5）。"""
    count = deps.db.mark_notifications_read(req.ids)
    return {"ok": True, "marked": count}


@router.get("/activities/notifications")
async def list_notifications(offset: int = 0, limit: int = 50):
    """分页全量通知（含已读，通知中心历史面，2.11 §3.4）；limit 上限 200。"""
    items, total = deps.db.list_notifications(max(0, offset), min(max(1, limit), 200))
    return {"items": items, "total": total}


@router.post("/activities/notifications/read-all")
async def read_all_notifications():
    """全部标记已读（通知中心一键操作，2.11 §3.4）。"""
    marked = deps.db.mark_all_notifications_read()
    return {"ok": True, "marked": marked}


@router.delete("/activities/notifications/{notification_id}")
async def delete_notification(notification_id: int):
    """单条删除通知（通知中心行内删除，2.11 §3.4）。幂等：不存在时 deleted=0。"""
    deleted = deps.db.delete_notification(notification_id)
    return {"ok": True, "deleted": deleted}


# SSE 保活间隔：无事件时发注释行，防中间层掐空闲连接（同 task:stream 语义）
_STREAM_KEEPALIVE_S = 15.0


@router.get("/activities/stream")
async def activities_stream():
    """全局终态事件流（0.4 §6.4 E3）：activity_events 发布 → SSE 推送，推为主 / 心跳轮询兜底。

    事件体 = notifications 行（含 id / entity_type / kind / detail…）；前端以
    其为「立即心跳」信号（触发一次聚合拉取，Toast 即时化），不做第二套解析。
    客户端断开由 uvicorn 取消生成器 → finally 反注册；15s 注释行保活。
    """
    q = activity_events.subscribe()

    async def gen():
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=_STREAM_KEEPALIVE_S)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield f"event: activity\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        finally:
            activity_events.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream")
