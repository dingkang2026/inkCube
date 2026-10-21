"""python/tests/test_transfer_service.py — 导出/导入活动状态与终态事件（2.9 §7）。

run 状态是进程内存态（同 ledger_service._RUNNING 先例）；
终态通知经 activity_events 统一入口（无行查询——run 不落库，显式传上下文）。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from db.database import Database
from transfer import service
import activity_events


@pytest.fixture(autouse=True)
def clean_runs():
    service._RUNS.clear()
    yield
    service._RUNS.clear()


def test_start_update_finish_lifecycle():
    run_id = service.try_start_run("project_export", "p1", "Alpha")
    assert run_id
    items = service.fetch_running("project_export")
    assert len(items) == 1
    assert items[0]["entity_type"] == "project_export"
    assert items[0]["project_id"] == "p1"
    assert items[0]["entity_title"] == "Alpha"
    assert items[0]["progress"] == 0

    service.update_run(run_id, progress=150, message="zipping")   # 100 封顶
    assert service.fetch_running("project_export")[0]["progress"] == 100
    assert service.fetch_running("project_export")[0]["progress_text"] == "zipping"

    service.finish_run(run_id)
    assert service.fetch_running("project_export") == []
    # 终态后同项目可再次登记（run 表已清）
    assert service.try_start_run("project_export", "p1", "Alpha")


def test_unknown_run_update_is_silent():
    service.update_run("no-such-run", progress=50)   # 不抛


def test_try_start_run_project_dedupe():
    """同 kind+project 原子防重入（CodeReview M1）：check+start 同临界区，
    FastAPI 线程池并发导出不再有 is_running/start_run 分离窗口。"""
    assert service.try_start_run("project_export", "p1", "Alpha")
    assert service.try_start_run("project_export", "p1", "Alpha2") is None   # 同项目拒绝
    assert service.try_start_run("project_export", "p2", "Beta")             # 异项目放行
    assert service.try_start_run("project_import", "p1", "Alpha")             # 异 kind 放行


def test_try_start_run_kind_dedupe():
    """导入全局单飞（2.9 §6.1）：dedupe="kind" 时同 kind 任一 run 在飞即拒。"""
    assert service.try_start_run("project_import", "p9", "Beta", dedupe="kind")
    assert service.try_start_run("project_import", "p8", "Gamma", dedupe="kind") is None
    assert service.try_start_run("project_export", "p9", "Beta", dedupe="kind")   # 异 kind 放行


def test_complete_transfer_writes_notification():
    db = Database(db_path=":memory:")
    db.initialize()
    activity_events.complete_transfer(
        db, "project_export", "run-1", project_id="p1", entity_title="Alpha")
    unread = db.list_unread_notifications()
    assert len(unread) == 1
    assert unread[0]["entity_type"] == "project_export"
    assert unread[0]["kind"] == "completed"
    assert unread[0]["container"] == "project"
    assert unread[0]["entity_title"] == "Alpha"


def test_fail_transfer_truncates_detail():
    db = Database(db_path=":memory:")
    db.initialize()
    activity_events.fail_transfer(
        db, "project_import", "run-2", project_id=None,
        entity_title="Beta", error_message="x" * 300)
    unread = db.list_unread_notifications()
    assert len(unread) == 1
    assert unread[0]["kind"] == "failed"
    assert len(unread[0]["detail"]) == 200
