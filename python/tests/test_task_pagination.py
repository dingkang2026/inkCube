"""python/tests/test_task_pagination.py — 任务列表分页回归测试。

回归背景：GET /tasks 此前全量加载无上限，前端改为「加载更多」分页
（list_tasks 增加 limit/offset；limit>0 时取 limit+1 行探测 has_more，
避免额外 COUNT 查询）。limit=0 保持全量语义，配额检查等既有调用方不受影响。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.database import Database


@pytest.fixture
def db():
    database = Database(db_path=":memory:")
    database.initialize()
    return database


def _create_n_tasks(db, n, data_dir, status="active"):
    ids = []
    for i in range(n):
        t = db.create_task(f"任务-{i}", data_dir, "", "general")
        ids.append(t["id"])
        if status != "active":
            db.conn.execute(
                "UPDATE tasks SET status = ? WHERE id = ?", (status, t["id"])
            )
    db.conn.commit()
    return ids


class TestListTasksPagination:
    def test_no_limit_returns_all_backward_compatible(self, db, tmp_path):
        """limit=0（默认）→ 全量 + has_more=False（create_task 配额检查等既有调用方不受影响）。"""
        _create_n_tasks(db, 3, str(tmp_path))
        tasks, has_more = db.list_tasks()
        assert len(tasks) == 3
        assert has_more is False

    def test_limit_returns_page_and_has_more(self, db, tmp_path):
        _create_n_tasks(db, 5, str(tmp_path))
        tasks, has_more = db.list_tasks(limit=3)
        assert len(tasks) == 3
        assert has_more is True

    def test_limit_beyond_total_no_more(self, db, tmp_path):
        _create_n_tasks(db, 3, str(tmp_path))
        tasks, has_more = db.list_tasks(limit=10)
        assert len(tasks) == 3
        assert has_more is False

    def test_offset_skips_rows(self, db, tmp_path):
        _create_n_tasks(db, 4, str(tmp_path))
        first, _ = db.list_tasks(limit=2)
        rest, has_more = db.list_tasks(limit=2, offset=2)
        assert has_more is False
        first_ids = [t["id"] for t in first]
        rest_ids = [t["id"] for t in rest]
        assert first_ids != rest_ids
        assert len(set(first_ids) | set(rest_ids)) == 4  # 两页无重叠、合起来覆盖全部

    def test_status_filter_combined_with_pagination(self, db, tmp_path):
        _create_n_tasks(db, 3, str(tmp_path))
        _create_n_tasks(db, 4, str(tmp_path), status="archived")
        tasks, has_more = db.list_tasks(status="archived", limit=2)
        assert len(tasks) == 2
        assert all(t["status"] == "archived" for t in tasks)
        assert has_more is True

    def test_message_count_still_aggregated(self, db, tmp_path):
        """分页不影响 message_count 聚合（LEFT JOIN 计数语义保留）。"""
        ids = _create_n_tasks(db, 1, str(tmp_path))
        db.save_task_message(ids[0], "user", "hello")
        tasks, _ = db.list_tasks(limit=10)
        assert tasks[0]["message_count"] == 1


class TestListTasksOriginFilter:
    """origin 过滤（2.3 §3.1 形态分离，2026-10-16）。

    回归背景：db.list_tasks 支持 origin 参数但 GET /tasks 未透传、
    tasks:listFiltered IPC 未传 'chat' —— PPT 任务会泄漏进任务 Tab
    （违反「PPT 任务永不进任务 Tab」不变量）。
    """

    def test_default_no_filter_includes_ppt(self, db, tmp_path):
        """origin=None（崩溃恢复等内部扫描）→ 全量含 ppt 任务。"""
        chat = db.create_task("chat", str(tmp_path), "", "coding")
        ppt = db.create_task("ppt-runner", "", "", "coding", origin="ppt")
        tasks, _ = db.list_tasks()
        ids = [t["id"] for t in tasks]
        assert chat["id"] in ids and ppt["id"] in ids

    def test_origin_chat_excludes_ppt(self, db, tmp_path):
        """任务 Tab（origin='chat'）→ PPT 任务不出现。"""
        db.create_task("chat", str(tmp_path), "", "coding")
        db.create_task("ppt-runner", "", "", "coding", origin="ppt")
        tasks, _ = db.list_tasks(origin="chat")
        assert [t["title"] for t in tasks] == ["chat"]

    def test_origin_ppt_only_ppt(self, db, tmp_path):
        db.create_task("chat", str(tmp_path), "", "coding")
        db.create_task("ppt-runner", "", "", "coding", origin="ppt")
        tasks, _ = db.list_tasks(origin="ppt")
        assert [t["title"] for t in tasks] == ["ppt-runner"]

    def test_origin_combined_with_pagination(self, db, tmp_path):
        """origin 与分页组合（任务 Tab 实际形态）。"""
        for i in range(3):
            db.create_task(f"c{i}", str(tmp_path), "", "coding")
        for i in range(2):
            db.create_task(f"p{i}", "", "", "coding", origin="ppt")
        tasks, has_more = db.list_tasks(origin="chat", limit=2)
        assert len(tasks) == 2
        assert has_more is True
        assert all(t["origin"] == "chat" for t in tasks)
