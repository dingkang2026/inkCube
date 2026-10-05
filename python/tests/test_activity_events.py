"""python/tests/test_activity_events.py — E2 file 终态事件框架单测（0.4lifecycle-design.md §6.4）。

覆盖：complete / fail 写通知（container / title / detail 截断）、文件不存在静默跳过、
防御性双写幂等（未读查重）、事件写入失败不影响业务主流程。
"""
import os
import sys

import pytest

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.database import Database
import activity_events


@pytest.fixture
def db():
    database = Database(db_path=":memory:")
    database.initialize()
    return database


@pytest.fixture
def project_id(db):
    return db.create_project(name="Event Test")["id"]


class TestFileTerminalEvents:
    def test_complete_file_writes_notification(self, db, project_id):
        fid = db.create_file(project_id, "a.pdf", ".pdf", 100, "h1")["id"]
        activity_events.complete_file(db, fid)

        notifications = db.list_unread_notifications()
        assert len(notifications) == 1
        n = notifications[0]
        assert n["entity_type"] == "file"
        assert n["kind"] == "completed"
        assert n["entity_title"] == "a.pdf"
        assert n["container"] == "project"
        assert n["detail"] is None

    def test_fail_file_truncates_detail(self, db, project_id):
        fid = db.create_file(project_id, "b.pdf", ".pdf", 100, "h2")["id"]
        activity_events.fail_file(db, fid, "x" * 300)

        n = db.list_unread_notifications()[0]
        assert n["kind"] == "failed"
        assert len(n["detail"]) == 200  # 截 200 字符（§3.2）

    def test_baselib_container(self, db, project_id):
        """基础库容器事实随事件携带（0.4 §3.6 导航分流取数）。"""
        lib_id = db.create_base_lib(name="共享资料库")["id"]
        fid = db.create_file(lib_id, "c.pdf", ".pdf", 100, "h3")["id"]
        activity_events.complete_file(db, fid)
        assert db.list_unread_notifications()[0]["container"] == "baselib"

    def test_missing_file_silent(self, db):
        """文件不存在 / 已删除 → 静默跳过，不写通知不抛错。"""
        activity_events.complete_file(db, "no-such-file")
        activity_events.fail_file(db, "no-such-file", "boom")
        assert db.list_unread_notifications() == []

    def test_double_fail_deduped(self, db, project_id):
        """防御性双写（pipeline + task_queue 各一次）只产生一条通知（§3.2 未读查重）。"""
        fid = db.create_file(project_id, "d.pdf", ".pdf", 100, "h4")["id"]
        activity_events.fail_file(db, fid, "boom")
        activity_events.fail_file(db, fid, "boom again")
        assert len(db.list_unread_notifications()) == 1

    def test_event_failure_does_not_raise(self, db, project_id, monkeypatch):
        """事件写入异常不影响业务主流程（状态已落库，通知是增益）。"""
        def boom(*args, **kwargs):
            raise RuntimeError("db exploded")

        monkeypatch.setattr(db, "add_notification", boom)
        fid = db.create_file(project_id, "e.pdf", ".pdf", 100, "h5")["id"]
        activity_events.complete_file(db, fid)  # 不抛
