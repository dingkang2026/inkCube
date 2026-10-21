"""python/tests/test_activity_events.py — E2/E3 终态事件框架单测（0.4lifecycle-design.md §6.4）。

覆盖（file 顺序 3 / report · wiki_compile 顺序 4 / task · model_download E2 收尾）：
complete / fail 写通知（container / title / detail 截断）、实体不存在静默跳过、
防御性双写幂等（未读查重）、事件写入失败不影响业务主流程；DB 钩子移除回归
（纯状态更新不再自动发通知）；E3 事件投递（subscribe / publish / 查重跳过不投递）。
"""
import asyncio
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


def _insert_report(db, project_id, report_id, status="generating"):
    db.conn.execute(
        "INSERT INTO reports (id, project_id, template_id, title, output_format, status, "
        "progress, content_path, created_at, updated_at) "
        "VALUES (?, ?, 'tpl', ?, 'Word', ?, 40, '/tmp/x', datetime('now'), datetime('now'))",
        (report_id, project_id, f"报告-{report_id}", status),
    )
    db.conn.commit()


def _insert_compile_task(db, project_id, task_id, status="ingesting"):
    db.conn.execute(
        "INSERT INTO wiki_compile_tasks (id, project_id, trigger_type, status, pages_total, "
        "pages_completed, created_at) VALUES (?, ?, 'manual', ?, 10, 3, datetime('now'))",
        (task_id, project_id, status),
    )
    db.conn.commit()


def _insert_base_lib(db, lib_id: str, name: str = "共享资料库",
                     lib_type: str = "preset_test") -> str:
    """直插 base_lib 行（原 create_base_lib 方法的测试替身，2.9 D4 后方法已删）。"""
    db.conn.execute(
        "INSERT INTO projects (id, name, description, kind, lib_type, created_at, updated_at) "
        "VALUES (?, ?, '', 'base_lib', ?, datetime('now'), datetime('now'))",
        (lib_id, name, lib_type),
    )
    db.conn.commit()
    return lib_id


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
        lib_id = _insert_base_lib(db, "lib-test-1")
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


class TestReportTerminalEvents:
    """report 终态显式声明（顺序 4）：旧 update_report_status 内嵌钩子已移除。"""

    def test_complete_report_writes_notification(self, db, project_id):
        _insert_report(db, project_id, "r1")
        activity_events.complete_report(db, "r1")

        notifications = db.list_unread_notifications()
        assert len(notifications) == 1
        n = notifications[0]
        assert n["entity_type"] == "report"
        assert n["entity_id"] == "r1"
        assert n["kind"] == "completed"
        assert n["entity_title"] == "报告-r1"
        assert n["project_id"] == project_id
        assert n["container"] == "project"

    def test_fail_report_carries_error_message(self, db, project_id):
        """error_message 显式传参（顺序 4 修复）：旧钩子在 update_report_error 写入前
        读列恒为 NULL，失败通知 detail 从未带出过错误信息。"""
        _insert_report(db, project_id, "r2")
        activity_events.fail_report(db, "r2", "x" * 300)

        n = db.list_unread_notifications()[0]
        assert n["kind"] == "failed"
        assert len(n["detail"]) == 200  # 截 200 字符（§3.2）

    def test_baselib_container(self, db):
        """基础库报告终态通知携带 container（旧 main.py 崩溃恢复直调缺此字段）。"""
        lib_id = _insert_base_lib(db, "lib-test-1")
        _insert_report(db, lib_id, "r_lib")
        activity_events.complete_report(db, "r_lib")
        assert db.list_unread_notifications()[0]["container"] == "baselib"

    def test_missing_report_silent(self, db):
        activity_events.complete_report(db, "no-such-report")
        activity_events.fail_report(db, "no-such-report", "boom")
        assert db.list_unread_notifications() == []


class TestCompileTerminalEvents:
    """wiki_compile 终态显式声明（顺序 4）：旧 update_compile_task_status 内嵌钩子已移除。"""

    def test_complete_compile_uses_project_name(self, db, project_id):
        _insert_compile_task(db, project_id, "wc1")
        activity_events.complete_compile(db, "wc1")

        n = db.list_unread_notifications()[0]
        assert n["entity_type"] == "wiki_compile"
        assert n["kind"] == "completed"
        assert n["entity_title"] == "Event Test"  # project_name
        assert n["detail"] is None

    def test_fail_compile_truncates_detail(self, db, project_id):
        _insert_compile_task(db, project_id, "wc3")
        activity_events.fail_compile(db, "wc3", "y" * 300)

        n = db.list_unread_notifications()[0]
        assert n["kind"] == "failed"
        assert len(n["detail"]) == 200

    def test_deleted_project_cascade_silent(self, db, project_id):
        """项目删除：wiki_compile_tasks 级联删除（PRAGMA foreign_keys=ON）→
        fail_compile 查无任务行静默跳过。用户主动删除项目，通知静默符合规则二（§1.5）
        ——task_queue「Project deleted」失败分支仅在删项目的竞态窗口内任务行尚存，
        LEFT JOIN 回退 entity_title="Wiki" 为纯防御。"""
        _insert_compile_task(db, project_id, "wc5")
        db.conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        db.conn.commit()
        assert db.get_wiki_compile_task("wc5") is None  # 级联删除生效
        activity_events.fail_compile(db, "wc5", "Project deleted")
        assert db.list_unread_notifications() == []

    def test_baselib_container(self, db):
        lib_id = _insert_base_lib(db, "lib-test-1")
        _insert_compile_task(db, lib_id, "wc_lib")
        activity_events.complete_compile(db, "wc_lib")
        assert db.list_unread_notifications()[0]["container"] == "baselib"

    def test_missing_task_silent(self, db):
        activity_events.complete_compile(db, "no-such-task")
        activity_events.fail_compile(db, "no-such-task", "boom")
        assert db.list_unread_notifications() == []


class TestDbHooksRemoved:
    """DB 钩子移除回归（E2，顺序 4）：状态变更方法纯更新，不再自动发通知。"""

    def test_update_report_status_no_notification(self, db, project_id):
        _insert_report(db, project_id, "r_hook", "generating")
        db.update_report_status("r_hook", "done", 100)
        db.update_report_status("r_hook", "error")
        assert db.get_report("r_hook")["status"] == "error"  # 状态确实更新
        assert db.list_unread_notifications() == []  # 但不再自动发通知

    def test_update_compile_task_status_no_notification(self, db, project_id):
        _insert_compile_task(db, project_id, "wc_hook")
        db.update_compile_task_status("wc_hook", "completed", pages_total=5, pages_completed=5)
        db.update_compile_task_status("wc_hook", "ingesting")
        task = db.get_wiki_compile_task("wc_hook")
        assert task["status"] == "ingesting"
        assert db.list_unread_notifications() == []


class TestTaskTerminalEvents:
    """task 终态显式声明（E2 收尾，v3.9）：task_agent.py 旧直调已迁入。"""

    def test_complete_task_writes_notification(self, db, tmp_path):
        task_id = db.create_task("重构调研", str(tmp_path))["id"]
        activity_events.complete_task(db, task_id)

        n = db.list_unread_notifications()[0]
        assert n["entity_type"] == "task"
        assert n["entity_id"] == task_id
        assert n["kind"] == "completed"
        assert n["entity_title"] == "重构调研"
        assert n["project_id"] is None  # 任务不隶属项目（导航固定任务页）

    def test_fail_task_detail_truncated(self, db, tmp_path):
        task_id = db.create_task("失败任务", str(tmp_path))["id"]
        activity_events.fail_task(db, task_id, "z" * 300)

        n = db.list_unread_notifications()[0]
        assert n["kind"] == "failed"
        assert len(n["detail"]) == 200  # 截 200 字符（§3.2）

    def test_missing_task_silent(self, db):
        """任务不存在 / 已删除 → 静默跳过（与旧直调 if task 行为一致）。"""
        activity_events.complete_task(db, "no-such-task")
        activity_events.fail_task(db, "no-such-task", "boom")
        assert db.list_unread_notifications() == []


class TestDownloadTerminalEvents:
    """model_download 终态显式声明（E2 收尾，v3.9）：model_manager / main.py 旧直调已迁入。"""

    def test_complete_download(self, db):
        activity_events.complete_download(db, "bge-m3")

        n = db.list_unread_notifications()[0]
        assert n["entity_type"] == "model_download"
        assert n["entity_id"] == "bge-m3"
        assert n["entity_title"] == "bge-m3"  # 模型名即标题（用户可识）
        assert n["kind"] == "completed"
        assert n["project_id"] is None

    def test_fail_download_detail(self, db):
        activity_events.fail_download(db, "tableformer-v2", "所有下载源均失败")

        n = db.list_unread_notifications()[0]
        assert n["kind"] == "failed"
        assert n["detail"] == "所有下载源均失败"


class TestNotify:
    """notify 通用入口（2.11 §3.1）：信息类 kind，上下文显式传入。"""

    def test_notify_writes_notification(self, db):
        activity_events.notify(
            db, entity_type="form_template", entity_id="tpl-1",
            project_id=None, container="project",
            kind="file_changed", entity_title="投资推荐表",
        )

        n = db.list_unread_notifications()[0]
        assert n["entity_type"] == "form_template"
        assert n["entity_id"] == "tpl-1"
        assert n["kind"] == "file_changed"
        assert n["entity_title"] == "投资推荐表"
        assert n["project_id"] is None
        assert n["container"] == "project"
        assert n["detail"] is None

    def test_notify_detail_truncated(self, db):
        activity_events.notify(
            db, entity_type="affair", entity_id="a1",
            project_id="p1", container="project",
            kind="overdue", entity_title="周报", detail="x" * 300,
        )
        assert len(db.list_unread_notifications()[0]["detail"]) == 200  # 截 200 字符

    def test_notify_dedup_unread(self, db):
        """未读查重（2.11 D6）：同三元组重复通知只落一条。"""
        for _ in range(2):
            activity_events.notify(
                db, entity_type="form_template", entity_id="tpl-1",
                project_id=None, container="project",
                kind="file_changed", entity_title="投资推荐表",
            )
        assert len(db.list_unread_notifications()) == 1

    def test_notify_failure_does_not_raise(self, db, monkeypatch):
        """事件写入异常不影响业务主流程（与终态 emit 同约定）。"""
        def boom(*args, **kwargs):
            raise RuntimeError("db exploded")

        monkeypatch.setattr(db, "add_notification", boom)
        activity_events.notify(
            db, entity_type="affair", entity_id="a1",
            project_id="p1", container="project",
            kind="due", entity_title="周报",
        )  # 不抛


class TestNotificationDaily:
    """add_notification_daily 按天查重（2.11 D9/D1）：未读优先于按天，
    已读后跨天重发（逾期提醒每天提醒一次）。"""

    def _write(self, db, entity_id="a1", kind="overdue", title="周报"):
        return db.add_notification_daily(
            "affair", entity_id, "p1", kind, title, container="project",
        )

    def test_same_day_read_still_deduped(self, db):
        """同日已读不重发：date(created_at) = today 命中当天窗口。"""
        nid = self._write(db)
        assert nid is not None
        db.mark_notifications_read([nid])
        assert self._write(db) is None

    def test_unread_blocks_cross_day(self, db):
        """未读优先于按天：昨天写入仍未读 → 今天不重发（不轰炸）。"""
        nid = self._write(db)
        db.conn.execute(
            "UPDATE notifications SET created_at = datetime('now', 'localtime', '-1 day') "
            "WHERE id = ?", (nid,))
        db.conn.commit()
        assert self._write(db) is None

    def test_read_resend_next_day(self, db):
        """已读 + 昨天写入 → 今天重发（D1 逾期提醒跨天重发）。"""
        nid = self._write(db)
        db.mark_notifications_read([nid])
        db.conn.execute(
            "UPDATE notifications SET created_at = datetime('now', 'localtime', '-1 day') "
            "WHERE id = ?", (nid,))
        db.conn.commit()
        nid2 = self._write(db)
        assert nid2 is not None and nid2 != nid
        assert len(db.list_unread_notifications()) == 1

    def test_kinds_independent(self, db):
        """查重三元组含 kind：due 与 overdue 互不互斥（同实体可并存）。"""
        self._write(db, kind="due")
        nid = self._write(db, kind="overdue")
        assert nid is not None


class TestEventPublish:
    """E3 事件投递（v3.9）：_store_and_publish 写入成功即发布，查重跳过不投递。"""

    def test_publish_reaches_subscriber(self, db, project_id):
        """订阅者收到完整通知行（含 id）；查重跳过（同实体未读已存在）不投递。"""
        async def scenario():
            q = activity_events.subscribe()
            fid = db.create_file(project_id, "s.pdf", ".pdf", 100, "h9")["id"]
            activity_events.complete_file(db, fid)
            await asyncio.sleep(0)  # 让 call_soon_threadsafe 调度的一轮跑完

            payload = q.get_nowait()
            assert payload["entity_type"] == "file"
            assert payload["kind"] == "completed"
            assert payload["entity_title"] == "s.pdf"
            assert payload["id"] > 0

            # 同 kind 重复声明：未读 completed 已存在 → 查重跳过写入，也不投递
            # （查重三元组含 kind，completed / failed 互不互斥，与 §3.2 一致）
            activity_events.complete_file(db, fid)
            await asyncio.sleep(0)
            assert q.empty()
            activity_events.unsubscribe(q)

        asyncio.run(scenario())

    def test_unsubscribe_stops_delivery(self, db, project_id):
        async def scenario():
            q = activity_events.subscribe()
            activity_events.unsubscribe(q)
            fid = db.create_file(project_id, "t.pdf", ".pdf", 100, "h10")["id"]
            activity_events.complete_file(db, fid)
            await asyncio.sleep(0)
            assert q.empty()

        asyncio.run(scenario())

    def test_publish_without_subscribers_noop(self, db, project_id):
        """无订阅者时发布不抛错（SSE 未连接是常态，心跳轮询兜底）。"""
        fid = db.create_file(project_id, "u.pdf", ".pdf", 100, "h11")["id"]
        activity_events.complete_file(db, fid)
        assert len(db.list_unread_notifications()) == 1

    def test_publish_failure_does_not_raise(self, db, project_id, monkeypatch):
        """投递链路异常不影响事件存储主流程（存储在投递前完成）。"""
        def boom(payload):
            raise RuntimeError("publish exploded")

        monkeypatch.setattr(activity_events, "_publish", boom)
        fid = db.create_file(project_id, "v.pdf", ".pdf", 100, "h12")["id"]
        activity_events.complete_file(db, fid)  # 不抛
        assert len(db.list_unread_notifications()) == 1  # 存储已完成
