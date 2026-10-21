"""python/maintenance.py — 启动期存储维护收口（2026-10-20 数据管理审计落地）。

启动清扫统一入口（main.py lifespan 后台 task 调用，不阻塞启动）：
- 孤儿任务目录清扫：data_dir/tasks/ 下不被 DB 任一任务引用的目录（布局迁移、
  删除时 rmtree 失败、DB 重置等都会产生孤儿，此前无任何清扫机制）；
- 归档任务清盘：归档超 N 天的任务删除磁盘工作区（含 .venv 与任务日志），
  保留 DB 行可查历史——auto_archive 只改状态不释放磁盘的后续闭环；
- llm_usage / task_schedule_runs 保留期：append-only 表此前无界增长。

时间口径：各表时间列均由 Python 本地时间写入（_now() / datetime.now()），
cutoff 统一用 datetime.now() 计算，同格式 ISO 字符串字典序 = 时间序。
"""

import logging
import os
import shutil
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _is_under(child: str, root: str) -> bool:
    """child 是否位于 root 目录之内（normcase 归一，防大小写/斜杠差异误判）。"""
    return _norm(child).startswith(_norm(root) + os.sep)


def cleanup_orphan_task_dirs(db, data_dir: str) -> int:
    """清扫 data_dir/tasks/ 下不被任何任务行引用的孤儿目录。

    只认锚定在 data_dir/tasks/ 内的 workspace（用户自选工作区不归本清扫管，
    与 delete_task 的 managed_root 判据一致）。返回删除的目录数。
    """
    tasks_root = os.path.join(data_dir, "tasks")
    if not os.path.isdir(tasks_root):
        return 0

    managed = set()
    for row in db.conn.execute(
            "SELECT workspace_path FROM tasks WHERE workspace_path != ''"):
        task_dir = os.path.dirname(row["workspace_path"])
        if task_dir and _is_under(task_dir, tasks_root):
            managed.add(_norm(task_dir))

    removed = 0
    for name in os.listdir(tasks_root):
        d = os.path.join(tasks_root, name)
        if not os.path.isdir(d):
            continue
        if _norm(d) in managed:
            continue
        shutil.rmtree(d, ignore_errors=True)
        removed += 1
        logger.info("Removed orphan task dir: %s", d)
    if removed:
        logger.info("Orphan task dir cleanup done: %d removed", removed)
    return removed


def cleanup_archived_task_workspaces(db, days: int | None = 0) -> int:
    """归档任务删除磁盘工作区（保留 DB 行）。

    days=None 清全部归档任务（设置页「清理」端点用）；days>0 只清归档超
    N 天的（启动保留期清盘用）；days<=0 跳过。"""
    if days is not None and days <= 0:
        return 0
    if days is None:
        rows = db.conn.execute(
            "SELECT id, workspace_path FROM tasks"
            " WHERE status = 'archived'").fetchall()
    else:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        rows = db.conn.execute(
            "SELECT id, workspace_path FROM tasks"
            " WHERE status = 'archived' AND updated_at < ?", (cutoff,)).fetchall()
    if not rows:
        return 0
    from paths import get_data_dir
    tasks_root = os.path.join(get_data_dir(), "tasks")
    removed = 0
    for row in rows:
        task_dir = os.path.dirname(row["workspace_path"] or "")
        if not task_dir or not _is_under(task_dir, tasks_root):
            continue
        if not os.path.isdir(task_dir):
            continue
        shutil.rmtree(task_dir, ignore_errors=True)
        removed += 1
        logger.info("Purged archived task workspace: task=%s dir=%s",
                    row["id"], task_dir)
    if removed:
        logger.info("Archived task workspace purge done: %d removed (days=%s)",
                    removed, days)
    return removed


def cleanup_old_llm_usage(db, days: int) -> int:
    """llm_usage 保留期：删除 ts 早于 cutoff 的行。days<=0 跳过。"""
    if days <= 0:
        return 0
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    cur = db.conn.execute("DELETE FROM llm_usage WHERE ts < ?", (cutoff,))
    db.conn.commit()
    if cur.rowcount:
        logger.info("Purged %d llm_usage rows older than %dd", cur.rowcount, days)
    return cur.rowcount


def cleanup_expired_schedule_runs(db, days: int) -> int:
    """订阅执行记录保留期：过期 run 连同影子任务（消息/状态/managed 工作区）
    与磁盘报告副本（report_path，data/schedules/{run_id}.md）一并清除。
    只清非 running 行——启动补偿可能在飞，running 行由
    fail_stale_running_runs 收尾后下轮再清。days<=0 跳过。"""
    if days <= 0:
        return 0
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    rows = db.conn.execute(
        "SELECT task_id, report_path FROM task_schedule_runs"
        " WHERE started_at < ? AND status != 'running'",
        (cutoff,)).fetchall()
    from paths import get_data_dir
    schedules_root = os.path.join(get_data_dir(), "schedules")
    for row in rows:
        if row["task_id"]:
            try:
                db.delete_task(row["task_id"])
            except Exception:
                logger.warning("Retention: delete shadow task failed: %s",
                               row["task_id"], exc_info=True)
        # 磁盘报告副本与 run 行同生命周期（行删后文件成孤儿——2026-10-20
        # 审计缺口）；只删锚在 data_dir/schedules 内的路径，防 DB 值异常
        # 时误删任意文件（与 delete_task 的 managed_root 判据同构）
        report_path = row["report_path"]
        if report_path and _is_under(report_path, schedules_root):
            try:
                os.remove(report_path)
            except FileNotFoundError:
                pass  # 已删（幂等）
            except OSError:
                logger.warning("Retention: remove report copy failed: %s",
                               report_path, exc_info=True)
    cur = db.conn.execute(
        "DELETE FROM task_schedule_runs WHERE started_at < ?"
        " AND status != 'running'", (cutoff,))
    db.conn.commit()
    if cur.rowcount:
        logger.info("Purged %d expired schedule runs (>%dd)",
                    cur.rowcount, days)
    return cur.rowcount


def run_storage_maintenance(db, data_dir: str, *, archived_days: int,
                            usage_days: int, runs_days: int) -> None:
    """启动维护总入口：各项独立隔离，单项失败不阻断其余。"""
    steps = (
        ("orphan task dirs", lambda: cleanup_orphan_task_dirs(db, data_dir)),
        ("archived workspaces",
         lambda: cleanup_archived_task_workspaces(db, archived_days)),
        ("llm usage", lambda: cleanup_old_llm_usage(db, usage_days)),
        ("schedule runs",
         lambda: cleanup_expired_schedule_runs(db, runs_days)),
    )
    for name, fn in steps:
        try:
            fn()
        except Exception:
            logger.error("Storage maintenance step failed: %s", name,
                         exc_info=True)
