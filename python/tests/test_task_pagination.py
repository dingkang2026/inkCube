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


def _create_n_tasks(db, n, status="active"):
    ids = []
    for i in range(n):
        t = db.create_task(f"任务-{i}", "/tmp", "", "general")
        ids.append(t["id"])
        if status != "active":
            db.conn.execute(
                "UPDATE tasks SET status = ? WHERE id = ?", (status, t["id"])
            )
    db.conn.commit()
    return ids


class TestListTasksPagination:
    def test_no_limit_returns_all_backward_compatible(self, db):
        """limit=0（默认）→ 全量 + has_more=False（create_task 配额检查等既有调用方不受影响）。"""
        _create_n_tasks(db, 3)
        tasks, has_more = db.list_tasks()
        assert len(tasks) == 3
        assert has_more is False

    def test_limit_returns_page_and_has_more(self, db):
        _create_n_tasks(db, 5)
        tasks, has_more = db.list_tasks(limit=3)
        assert len(tasks) == 3
        assert has_more is True

    def test_limit_beyond_total_no_more(self, db):
        _create_n_tasks(db, 3)
        tasks, has_more = db.list_tasks(limit=10)
        assert len(tasks) == 3
        assert has_more is False

    def test_offset_skips_rows(self, db):
        _create_n_tasks(db, 4)
        first, _ = db.list_tasks(limit=2)
        rest, has_more = db.list_tasks(limit=2, offset=2)
        assert has_more is False
        first_ids = [t["id"] for t in first]
        rest_ids = [t["id"] for t in rest]
        assert first_ids != rest_ids
        assert len(set(first_ids) | set(rest_ids)) == 4  # 两页无重叠、合起来覆盖全部

    def test_status_filter_combined_with_pagination(self, db):
        _create_n_tasks(db, 3)
        _create_n_tasks(db, 4, status="archived")
        tasks, has_more = db.list_tasks(status="archived", limit=2)
        assert len(tasks) == 2
        assert all(t["status"] == "archived" for t in tasks)
        assert has_more is True

    def test_message_count_still_aggregated(self, db):
        """分页不影响 message_count 聚合（LEFT JOIN 计数语义保留）。"""
        ids = _create_n_tasks(db, 1)
        db.save_task_message(ids[0], "user", "hello")
        tasks, _ = db.list_tasks(limit=10)
        assert tasks[0]["message_count"] == 1
