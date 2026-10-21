"""时期任务列表分页回归（affairs，0.1dev-guide 列表分页统一约定）。

回归背景（2026-10）：
- GET /affairs/{pid}/tasks 此前全量加载；增 limit/offset/has_more/total。
  日历视图由前端传 limit=0 全量拉取，分页不得截断日历（关键契约）。
- GET /projects/{pid}/suggestions 同批审计（2026-10-20）补齐分页契约——
  此前 db 无 LIMIT SELECT * 全量拉取，与 §16「20 端点全量改造」宣称不符。
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import routers.affairs as affairs
from db.database import Database


@pytest.fixture
def db():
    database = Database(db_path=":memory:")
    database.initialize()
    return database


@pytest.fixture
def routed(db, monkeypatch):
    monkeypatch.setattr(affairs, "_db", lambda: db)
    return db


def _seed_tasks(database, pid, n):
    """db 层 create_phase_task（router 层 task_type 校验不适用于直测）；
    created_at 为 DEFAULT CURRENT_TIMESTAMP，列表断言不依赖行序。"""
    ids = []
    for i in range(n):
        row = database.create_phase_task(pid, "before", "milestone", f"任务-{i}")
        ids.append(row["id"])
    return ids


class TestListPhaseTasksPagination:
    def test_limit_zero_returns_all_calendar_safe(self, routed):
        """limit=0 → 全量：日历视图依赖该语义，分页不得截断日历。"""
        pid = routed.create_project("P")["id"]
        _seed_tasks(routed, pid, 3)
        result = asyncio.run(affairs.list_phase_tasks(pid, limit=0))
        assert len(result["tasks"]) == 3
        assert result["has_more"] is False
        assert result["total"] == 3

    def test_limit_returns_page_and_has_more(self, routed):
        pid = routed.create_project("P")["id"]
        _seed_tasks(routed, pid, 5)
        result = asyncio.run(affairs.list_phase_tasks(pid, limit=3, offset=0))
        assert len(result["tasks"]) == 3
        assert result["has_more"] is True
        assert result["total"] == 5

    def test_limit_beyond_total_no_more(self, routed):
        pid = routed.create_project("P")["id"]
        _seed_tasks(routed, pid, 3)
        result = asyncio.run(affairs.list_phase_tasks(pid, limit=10, offset=0))
        assert len(result["tasks"]) == 3
        assert result["has_more"] is False
        assert result["total"] == 3

    def test_offset_paginates_across_pages(self, routed):
        pid = routed.create_project("P")["id"]
        _seed_tasks(routed, pid, 7)
        page1 = asyncio.run(affairs.list_phase_tasks(pid, limit=3, offset=0))
        page2 = asyncio.run(affairs.list_phase_tasks(pid, limit=3, offset=3))
        page3 = asyncio.run(affairs.list_phase_tasks(pid, limit=3, offset=6))
        ids1 = {t["id"] for t in page1["tasks"]}
        ids2 = {t["id"] for t in page2["tasks"]}
        ids3 = {t["id"] for t in page3["tasks"]}
        assert ids1.isdisjoint(ids2) and ids2.isdisjoint(ids3)
        assert len(ids1 | ids2 | ids3) == 7
        assert page3["has_more"] is False

    def test_total_is_project_scoped(self, routed):
        pid_a = routed.create_project("A")["id"]
        pid_b = routed.create_project("B")["id"]
        _seed_tasks(routed, pid_a, 2)
        _seed_tasks(routed, pid_b, 1)
        result_a = asyncio.run(affairs.list_phase_tasks(pid_a, limit=0))
        result_b = asyncio.run(affairs.list_phase_tasks(pid_b, limit=0))
        assert result_a["total"] == 2
        assert result_b["total"] == 1
        assert all(t["project_id"] == pid_a for t in result_a["tasks"])


def _seed_suggestions(database, pid, n, kind="create_task"):
    ids = []
    for i in range(n):
        row = database.create_suggestion(
            pid, kind, f"建议-{i}", entry_fingerprint=f"fp-{kind}-{i}")
        assert row is not None
        ids.append(row["id"])
    return ids


class TestListSuggestionsPagination:
    def test_limit_zero_returns_all_backward_compatible(self, routed):
        """limit=0 → 全量：脉搏徽章/既有调用方零改动兼容。"""
        pid = routed.create_project("P")["id"]
        _seed_suggestions(routed, pid, 3)
        result = asyncio.run(affairs.list_suggestions(pid, limit=0))
        assert len(result["suggestions"]) == 3
        assert result["has_more"] is False
        assert result["total"] == 3

    def test_limit_returns_page_and_has_more(self, routed):
        pid = routed.create_project("P")["id"]
        _seed_suggestions(routed, pid, 5)
        result = asyncio.run(affairs.list_suggestions(pid, limit=3, offset=0))
        assert len(result["suggestions"]) == 3
        assert result["has_more"] is True
        assert result["total"] == 5

    def test_offset_paginates_across_pages(self, routed):
        pid = routed.create_project("P")["id"]
        _seed_suggestions(routed, pid, 7)
        page1 = asyncio.run(affairs.list_suggestions(pid, limit=3, offset=0))
        page2 = asyncio.run(affairs.list_suggestions(pid, limit=3, offset=3))
        page3 = asyncio.run(affairs.list_suggestions(pid, limit=3, offset=6))
        ids1 = {s["id"] for s in page1["suggestions"]}
        ids2 = {s["id"] for s in page2["suggestions"]}
        ids3 = {s["id"] for s in page3["suggestions"]}
        assert ids1.isdisjoint(ids2) and ids2.isdisjoint(ids3)
        assert len(ids1 | ids2 | ids3) == 7
        assert page3["has_more"] is False

    def test_total_is_status_scoped(self, routed):
        """total 与列表同口径（status 过滤后）：pending 页不被已决建议夸大。"""
        pid = routed.create_project("P")["id"]
        _seed_suggestions(routed, pid, 3, kind="create_task")
        _seed_suggestions(routed, pid, 2, kind="complete_task")
        pending = asyncio.run(affairs.list_suggestions(pid, limit=0))
        assert pending["total"] == 5
        # 决掉 2 条 → pending 列表与 total 同步收缩；dismissed 独立计数
        rows = routed.list_suggestions(pid)
        routed.decide_suggestion(rows[0]["id"], "dismissed", only_pending=True)
        routed.decide_suggestion(rows[1]["id"], "dismissed", only_pending=True)
        pending2 = asyncio.run(affairs.list_suggestions(pid, limit=0))
        assert len(pending2["suggestions"]) == 3
        assert pending2["total"] == 3
        dismissed = asyncio.run(
            affairs.list_suggestions(pid, status="dismissed", limit=0))
        assert dismissed["total"] == 2

    def test_project_scoped_total(self, routed):
        pid_a = routed.create_project("A")["id"]
        pid_b = routed.create_project("B")["id"]
        _seed_suggestions(routed, pid_a, 2)
        _seed_suggestions(routed, pid_b, 1)
        assert asyncio.run(affairs.list_suggestions(pid_a, limit=0))["total"] == 2
        assert asyncio.run(affairs.list_suggestions(pid_b, limit=0))["total"] == 1
