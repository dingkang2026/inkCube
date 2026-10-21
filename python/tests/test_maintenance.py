"""python/tests/test_maintenance.py — 启动期存储维护单测（2026-10-20 存储审计）。

覆盖 maintenance.py 全部入口的真实 SQLite 行为：
- 孤儿任务目录清扫：DB 引用保留 / 无引用删除 / tasks 根缺失幂等
- 归档任务清盘：days=None 全清（清理端点共享语义）/ days>0 保留期窗口 /
  days<=0 跳过 / 活跃任务永不触碰 / DB 行保留 / 托管锚外目录不触碰
- llm_usage 保留期：过期行删除 / days<=0 跳过
- 订阅执行记录保留期：过期 run 连带影子任务（DB 行 + 托管工作区）删除 /
  running 行保留（启动补偿可能在飞）/ 未来 run 保留
- run_storage_maintenance：单项失败隔离，不阻断其余步骤

数据库用 tmp_path 真实文件库：maintenance 直接走 SQL 与 db.delete_task
真实链路；paths.get_data_dir monkeypatch 到 tmp_path——delete_task 的
managed_root 判据与 maintenance 的目录锚定都经它推导，单一锚点同时受控。
"""
import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import maintenance
from db.database import Database


@pytest.fixture
def env(tmp_path, monkeypatch):
    """tmp 数据目录 + 真实 Database（get_data_dir 锚到 tmp_path）。"""
    monkeypatch.setattr("paths.get_data_dir", lambda: str(tmp_path))
    database = Database(str(tmp_path / "inkcube.db"))
    database.initialize()
    yield database, tmp_path
    database.conn.close()


def _make_task_dir(data_dir, task_id):
    """模拟托管任务目录 data/tasks/{id}/workspace，返回 task_dir。"""
    task_dir = os.path.join(str(data_dir), "tasks", task_id)
    workspace = os.path.join(task_dir, "workspace")
    os.makedirs(workspace, exist_ok=True)
    with open(os.path.join(workspace, "keep.txt"), "w", encoding="utf-8") as f:
        f.write("x")
    return task_dir


def _insert_task(db, task_id, workspace, status="active", updated_at=None):
    now = datetime.now().isoformat()
    db.conn.execute(
        "INSERT INTO tasks (id, title, status, workspace_path, venv_path,"
        " mode, origin, created_at, updated_at)"
        " VALUES (?, 't', ?, ?, '', 'coding', 'chat', ?, ?)",
        (task_id, status, workspace, now, updated_at or now),
    )
    db.conn.commit()


def _insert_schedule_run(db, run_id, task_id, status, started_at,
                         report_path=None):
    """插入订阅 + 执行记录（FK：run.schedule_id → task_schedules）。"""
    now = datetime.now().isoformat()
    db.conn.execute(
        "INSERT INTO task_schedules (id, name, prompt, interval_type,"
        " interval_value, created_at, updated_at)"
        " VALUES ('sch_test', 's', 'p', 'daily', '09:00', ?, ?)", (now, now))
    db.conn.execute(
        "INSERT INTO task_schedule_runs (id, schedule_id, task_id, status,"
        " report_path, started_at) VALUES (?, 'sch_test', ?, ?, ?, ?)",
        (run_id, task_id, status, report_path, started_at))
    db.conn.commit()


def _make_report_copy(data_dir, run_id):
    """模拟订阅报告副本 data/schedules/{run_id}.md，返回文件路径。"""
    d = os.path.join(str(data_dir), "schedules")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{run_id}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("# report")
    return path


class TestOrphanTaskDirs:
    def test_orphan_removed_referenced_kept(self, env):
        db, data_dir = env
        keep_dir = _make_task_dir(data_dir, "task-keep")
        orphan = _make_task_dir(data_dir, "task-orphan")
        _insert_task(db, "task-keep", os.path.join(keep_dir, "workspace"))

        removed = maintenance.cleanup_orphan_task_dirs(db, str(data_dir))

        assert removed == 1
        assert os.path.isdir(keep_dir)
        assert not os.path.isdir(orphan)

    def test_missing_root_returns_zero(self, env):
        db, data_dir = env
        assert maintenance.cleanup_orphan_task_dirs(db, str(data_dir)) == 0


class TestArchivedWorkspaces:
    def test_none_purges_all_archived_keeps_active(self, env):
        """days=None（设置页清理端点语义）：全部归档清盘，活跃与 DB 行不动。"""
        db, data_dir = env
        arch_dir = _make_task_dir(data_dir, "task-arch")
        act_dir = _make_task_dir(data_dir, "task-act")
        _insert_task(db, "task-arch", os.path.join(arch_dir, "workspace"),
                     status="archived")
        _insert_task(db, "task-act", os.path.join(act_dir, "workspace"))

        removed = maintenance.cleanup_archived_task_workspaces(db, None)

        assert removed == 1
        assert not os.path.isdir(arch_dir)
        assert os.path.isdir(act_dir)
        # DB 行保留（历史可查）
        assert db.get_task("task-arch")["status"] == "archived"

    def test_days_window_only_purges_stale(self, env):
        """days>0：只清 updated_at 早于 cutoff 的归档任务。"""
        db, data_dir = env
        old_dir = _make_task_dir(data_dir, "task-old")
        new_dir = _make_task_dir(data_dir, "task-new")
        stale = (datetime.now() - timedelta(days=40)).isoformat()
        _insert_task(db, "task-old", os.path.join(old_dir, "workspace"),
                     status="archived", updated_at=stale)
        _insert_task(db, "task-new", os.path.join(new_dir, "workspace"),
                     status="archived")

        removed = maintenance.cleanup_archived_task_workspaces(db, 30)

        assert removed == 1
        assert not os.path.isdir(old_dir)
        assert os.path.isdir(new_dir)

    def test_days_zero_skips(self, env):
        """days=0 = 永不清理（设置语义）。"""
        db, data_dir = env
        arch_dir = _make_task_dir(data_dir, "task-arch")
        _insert_task(db, "task-arch", os.path.join(arch_dir, "workspace"),
                     status="archived")

        assert maintenance.cleanup_archived_task_workspaces(db, 0) == 0
        assert os.path.isdir(arch_dir)

    def test_outside_managed_root_untouched(self, env):
        """用户自选工作区（data/tasks 锚外）永不触碰。"""
        db, data_dir = env
        user_ws = os.path.join(str(data_dir), "user-ws", "workspace")
        os.makedirs(user_ws, exist_ok=True)
        _insert_task(db, "task-user", user_ws, status="archived")

        assert maintenance.cleanup_archived_task_workspaces(db, None) == 0
        assert os.path.isdir(user_ws)


class TestLlmUsageRetention:
    def test_old_rows_deleted_new_kept(self, env):
        db, _ = env
        old = (datetime.now() - timedelta(days=100)).isoformat()
        now = datetime.now().isoformat()
        for ts in (old, now):
            db.conn.execute(
                "INSERT INTO llm_usage (ts, source, model, mode)"
                " VALUES (?, 'task', 'm', 'full')", (ts,))
        db.conn.commit()

        removed = maintenance.cleanup_old_llm_usage(db, 90)

        assert removed == 1
        remain = db.conn.execute(
            "SELECT COUNT(*) AS c FROM llm_usage").fetchone()["c"]
        assert remain == 1

    def test_zero_skips(self, env):
        db, _ = env
        assert maintenance.cleanup_old_llm_usage(db, 0) == 0


class TestScheduleRunsRetention:
    def test_expired_done_run_purges_shadow_task(self, env):
        """过期 done run：run 行 + 影子任务行 + 托管工作区一并清除。"""
        db, data_dir = env
        task_dir = _make_task_dir(data_dir, "shadow-1")
        _insert_task(db, "shadow-1", os.path.join(task_dir, "workspace"))
        stale = (datetime.now() - timedelta(days=100)).isoformat()
        _insert_schedule_run(db, "run-old", "shadow-1", "done", stale)

        removed = maintenance.cleanup_expired_schedule_runs(db, 90)

        assert removed == 1
        assert db.get_task("shadow-1") is None
        assert not os.path.isdir(task_dir)
        remain = db.conn.execute(
            "SELECT COUNT(*) AS c FROM task_schedule_runs").fetchone()["c"]
        assert remain == 0

    def test_running_expired_run_kept(self, env):
        """过期但 running 的 run 保留：启动补偿可能在飞，收尾后下轮再清。"""
        db, _ = env
        stale = (datetime.now() - timedelta(days=100)).isoformat()
        _insert_schedule_run(db, "run-run", None, "running", stale)

        assert maintenance.cleanup_expired_schedule_runs(db, 90) == 0
        remain = db.conn.execute(
            "SELECT COUNT(*) AS c FROM task_schedule_runs").fetchone()["c"]
        assert remain == 1

    def test_recent_run_kept(self, env):
        db, _ = env
        recent = (datetime.now() - timedelta(days=1)).isoformat()
        _insert_schedule_run(db, "run-new", None, "done", recent)

        assert maintenance.cleanup_expired_schedule_runs(db, 90) == 0

    def test_expired_run_report_copy_removed(self, env):
        """B5：过期 run 的磁盘报告副本随行清除（影子任务同清）。"""
        db, data_dir = env
        stale = (datetime.now() - timedelta(days=100)).isoformat()
        copy = _make_report_copy(data_dir, "run-old")
        _insert_schedule_run(db, "run-old", None, "done", stale,
                             report_path=copy)

        assert maintenance.cleanup_expired_schedule_runs(db, 90) == 1
        assert not os.path.exists(copy)

    def test_retained_run_report_copy_kept(self, env):
        """保留期内 run 的副本不动。"""
        db, data_dir = env
        recent = (datetime.now() - timedelta(days=1)).isoformat()
        copy = _make_report_copy(data_dir, "run-new")
        _insert_schedule_run(db, "run-new", None, "done", recent,
                             report_path=copy)

        assert maintenance.cleanup_expired_schedule_runs(db, 90) == 0
        assert os.path.exists(copy)

    def test_report_copy_outside_schedules_kept(self, env):
        """锚外路径不删（逃逸防护，与 delete_task managed_root 判据同构）。"""
        db, data_dir = env
        stale = (datetime.now() - timedelta(days=100)).isoformat()
        outside = os.path.join(str(data_dir), "elsewhere.md")
        with open(outside, "w", encoding="utf-8") as f:
            f.write("x")
        _insert_schedule_run(db, "run-old", None, "done", stale,
                             report_path=outside)

        assert maintenance.cleanup_expired_schedule_runs(db, 90) == 1
        assert os.path.exists(outside)


class TestRunStorageMaintenance:
    def test_step_failure_isolated(self, env, monkeypatch):
        """单项失败不阻断其余：usage 步骤抛错，孤儿清扫仍执行。"""
        db, data_dir = env
        orphan = _make_task_dir(data_dir, "task-orphan")

        def _boom(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(maintenance, "cleanup_old_llm_usage", _boom)

        maintenance.run_storage_maintenance(
            db, str(data_dir), archived_days=30, usage_days=90, runs_days=90)

        assert not os.path.isdir(orphan)
