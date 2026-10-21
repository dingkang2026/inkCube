"""订阅（schedules）列表端点分页回归（3 端点，0.1dev-guide 列表分页统一约定）。

回归背景（2026-10）：
- GET /schedules 已有 category/enabled/q 服务端筛选，补 limit/offset/has_more/total；
- GET /schedules/runs/inbox、GET /schedules/{id}/runs 增 offset + has_more/total。
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import deps
import routers.schedules as schedules
from db.database import Database


@pytest.fixture
def db():
    database = Database(db_path=":memory:")
    database.initialize()
    return database


@pytest.fixture
def deps_db(db, monkeypatch):
    monkeypatch.setattr(deps, "db", db, raising=False)
    return db


def _seed_schedule(database, name, category="market", enabled=1, seq=0):
    row = database.create_schedule(
        name, "prompt", "general", category, "daily", "09:00", enabled=enabled)
    ts = f"2026-01-01T00:00:{seq:02d}"
    database.conn.execute(
        "UPDATE task_schedules SET created_at = ?, updated_at = ? WHERE id = ?",
        (ts, ts, row["id"]),
    )
    database.conn.commit()
    return row["id"]


def _seed_run(database, run_id, schedule_id, status="done", seq=0, read=False):
    database.insert_schedule_run(run_id, schedule_id, None, status)
    if status != "running":
        database.update_schedule_run(run_id, status)
    ts = f"2026-02-01T00:00:{seq:02d}"
    sets = "started_at = ?, finished_at = ?" if status != "running" else "started_at = ?"
    params = [ts, ts] if status != "running" else [ts]
    if read:
        sets += ", read_at = ?"
        params.append(ts)
    params.append(run_id)
    database.conn.execute(
        f"UPDATE task_schedule_runs SET {sets} WHERE id = ?", params)
    database.conn.commit()


class TestListSchedulesPagination:
    def test_limit_zero_returns_all_backward_compatible(self, deps_db):
        for i in range(3):
            _seed_schedule(deps_db, f"订阅-{i}", seq=i)
        result = asyncio.run(schedules.list_schedules(limit=0))
        assert len(result["schedules"]) == 3
        assert result["has_more"] is False
        assert result["total"] == 3

    def test_limit_returns_page_and_has_more(self, deps_db):
        for i in range(5):
            _seed_schedule(deps_db, f"订阅-{i}", seq=i)
        result = asyncio.run(schedules.list_schedules(limit=3, offset=0))
        assert len(result["schedules"]) == 3
        assert result["has_more"] is True
        assert result["total"] == 5

    def test_offset_paginates_across_pages(self, deps_db):
        for i in range(7):
            _seed_schedule(deps_db, f"订阅-{i}", seq=i)
        page1 = asyncio.run(schedules.list_schedules(limit=3, offset=0))
        page2 = asyncio.run(schedules.list_schedules(limit=3, offset=3))
        ids1 = {s["id"] for s in page1["schedules"]}
        ids2 = {s["id"] for s in page2["schedules"]}
        assert ids1.isdisjoint(ids2)
        assert len(ids1 | ids2) == 6

    def test_limit_offset_binding_order(self, deps_db):
        """LIMIT ?/OFFSET ? 占位符顺序与参数 append 顺序一致（绑定颠倒防回归）。"""
        for i in range(5):
            _seed_schedule(deps_db, f"订阅-{i}", seq=i)
        page2 = asyncio.run(schedules.list_schedules(limit=2, offset=2))
        assert [s["name"] for s in page2["schedules"]] == ["订阅-2", "订阅-1"]

    def test_category_filter_combined_with_pagination(self, deps_db):
        for i in range(25):
            _seed_schedule(deps_db, f"市场-{i}", category="market", seq=i)
        for i in range(20):
            _seed_schedule(deps_db, f"风险-{i}", category="risk", seq=25 + i)
        page2 = asyncio.run(
            schedules.list_schedules(category="market", limit=20, offset=20))
        assert len(page2["schedules"]) == 5
        assert page2["total"] == 25
        assert all(s["category"] == "market" for s in page2["schedules"])

    def test_enabled_filter_with_total(self, deps_db):
        for i in range(3):
            _seed_schedule(deps_db, f"启用-{i}", enabled=1, seq=i)
        for i in range(2):
            _seed_schedule(deps_db, f"停用-{i}", enabled=0, seq=3 + i)
        result = asyncio.run(schedules.list_schedules(enabled=0, limit=0, offset=0))
        assert len(result["schedules"]) == 2
        assert result["total"] == 2

    def test_q_filter_with_total(self, deps_db):
        _seed_schedule(deps_db, "合同巡检", seq=0)
        _seed_schedule(deps_db, "发票巡检", seq=1)
        _seed_schedule(deps_db, "舆情监控", seq=2)
        result = asyncio.run(schedules.list_schedules(q="巡检", limit=0, offset=0))
        assert result["total"] == 2
        assert {s["name"] for s in result["schedules"]} == {"合同巡检", "发票巡检"}


class TestScheduleInboxPagination:
    def test_only_done_runs_in_inbox(self, deps_db):
        sid = _seed_schedule(deps_db, "订阅", seq=0)
        _seed_run(deps_db, "r-running", sid, status="running", seq=0)
        _seed_run(deps_db, "r-failed", sid, status="failed", seq=1)
        _seed_run(deps_db, "r-done-1", sid, status="done", seq=2)
        _seed_run(deps_db, "r-done-2", sid, status="done", seq=3)
        result = asyncio.run(schedules.schedule_inbox(limit=50, offset=0))
        assert {r["id"] for r in result["runs"]} == {"r-done-1", "r-done-2"}
        assert result["total"] == 2

    def test_unread_filter_with_scoped_total(self, deps_db):
        sid = _seed_schedule(deps_db, "订阅", seq=0)
        _seed_run(deps_db, "r-read", sid, status="done", seq=0, read=True)
        _seed_run(deps_db, "r-unread", sid, status="done", seq=1)
        result = asyncio.run(schedules.schedule_inbox(unread=1, limit=50, offset=0))
        assert {r["id"] for r in result["runs"]} == {"r-unread"}
        assert result["total"] == 1

    def test_limit_returns_page_and_has_more(self, deps_db):
        sid = _seed_schedule(deps_db, "订阅", seq=0)
        for i in range(5):
            _seed_run(deps_db, f"r{i}", sid, status="done", seq=i)
        result = asyncio.run(schedules.schedule_inbox(limit=3, offset=0))
        assert len(result["runs"]) == 3
        assert result["has_more"] is True
        assert result["total"] == 5

    def test_offset_paginates_across_pages(self, deps_db):
        sid = _seed_schedule(deps_db, "订阅", seq=0)
        for i in range(7):
            _seed_run(deps_db, f"r{i}", sid, status="done", seq=i)
        page1 = asyncio.run(schedules.schedule_inbox(limit=3, offset=0))
        page2 = asyncio.run(schedules.schedule_inbox(limit=3, offset=3))
        ids1 = {r["id"] for r in page1["runs"]}
        ids2 = {r["id"] for r in page2["runs"]}
        assert ids1.isdisjoint(ids2)
        assert len(ids1 | ids2) == 6


class TestListScheduleRunsPagination:
    def test_default_limit_20_slice(self, deps_db):
        sid = _seed_schedule(deps_db, "订阅", seq=0)
        for i in range(25):
            _seed_run(deps_db, f"r{i:02d}", sid, status="done", seq=i)
        result = asyncio.run(schedules.list_schedule_runs(sid, limit=20, offset=0))
        assert len(result["runs"]) == 20
        assert result["has_more"] is True
        assert result["total"] == 25

    def test_offset_paginates_across_pages(self, deps_db):
        sid = _seed_schedule(deps_db, "订阅", seq=0)
        for i in range(7):
            _seed_run(deps_db, f"r{i}", sid, status="done", seq=i)
        page1 = asyncio.run(schedules.list_schedule_runs(sid, limit=3, offset=0))
        page2 = asyncio.run(schedules.list_schedule_runs(sid, limit=3, offset=3))
        ids1 = {r["id"] for r in page1["runs"]}
        ids2 = {r["id"] for r in page2["runs"]}
        assert ids1.isdisjoint(ids2)
        assert len(ids1 | ids2) == 6
        assert page2["total"] == 7

    def test_schedule_404(self, deps_db):
        res = asyncio.run(schedules.list_schedule_runs("nope", limit=20, offset=0))
        assert getattr(res, "status_code", None) == 404
