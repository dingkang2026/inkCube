"""报告 / PPT 交付物列表分页回归（0.1dev-guide 列表分页统一约定）。

回归背景（2026-10）：
- GET /reports、GET /projects/{id}/ppts 此前全量加载无上限，前端改为
  「加载更多」分页；两端点统一 limit/offset/has_more/total 契约，
  limit=0 保持全量语义（既有调用方不受影响）；
- enrichment（template_name/stage/usage）只作用于当前页行。
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import deps
import routers.ppts as ppts
import routers.reports as reports
from db.database import Database


@pytest.fixture
def db():
    database = Database(db_path=":memory:")
    database.initialize()
    return database


@pytest.fixture
def routed(db, monkeypatch):
    """reports 走模块级 get_db，ppts 走 deps.db；两端点共用同一内存库。"""
    monkeypatch.setattr(reports, "get_db", lambda: db)
    monkeypatch.setattr(deps, "db", db, raising=False)
    return db


def _seed_reports(database, pid, n):
    """create_report 落库后回写秒级递增 created_at，保证 DESC 排序确定。"""
    ids = []
    for i in range(n):
        row = database.create_report(pid, "", f"报告-{i}")
        ids.append(row["id"])
        ts = f"2026-01-01T00:00:{i:02d}"
        database.conn.execute(
            "UPDATE reports SET created_at = ?, updated_at = ? WHERE id = ?",
            (ts, ts, row["id"]),
        )
    database.conn.commit()
    return ids


def _seed_ppts(database, pid, n):
    ids = []
    for i in range(n):
        row = database.create_ppt(pid, f"task-{i}", f"PPT-{i}")
        ids.append(row["id"])
        ts = f"2026-01-02T00:00:{i:02d}"
        database.conn.execute(
            "UPDATE ppts SET created_at = ?, updated_at = ? WHERE id = ?",
            (ts, ts, row["id"]),
        )
    database.conn.commit()
    return ids


class TestListReportsPagination:
    def test_limit_zero_returns_all_backward_compatible(self, routed):
        pid = routed.create_project("P")["id"]
        _seed_reports(routed, pid, 3)
        result = asyncio.run(reports.list_reports(pid, limit=0))
        assert len(result["reports"]) == 3
        assert result["has_more"] is False
        assert result["total"] == 3

    def test_limit_returns_page_and_has_more(self, routed):
        pid = routed.create_project("P")["id"]
        _seed_reports(routed, pid, 5)
        result = asyncio.run(reports.list_reports(pid, limit=3, offset=0))
        assert len(result["reports"]) == 3
        assert result["has_more"] is True
        assert result["total"] == 5

    def test_limit_beyond_total_no_more(self, routed):
        pid = routed.create_project("P")["id"]
        _seed_reports(routed, pid, 3)
        result = asyncio.run(reports.list_reports(pid, limit=10, offset=0))
        assert len(result["reports"]) == 3
        assert result["has_more"] is False
        assert result["total"] == 3

    def test_offset_paginates_across_pages(self, routed):
        pid = routed.create_project("P")["id"]
        _seed_reports(routed, pid, 25)
        page1 = asyncio.run(reports.list_reports(pid, limit=20, offset=0))
        page2 = asyncio.run(reports.list_reports(pid, limit=20, offset=20))
        assert len(page1["reports"]) == 20
        assert page1["has_more"] is True
        assert len(page2["reports"]) == 5
        assert page2["has_more"] is False
        ids1 = {r["id"] for r in page1["reports"]}
        ids2 = {r["id"] for r in page2["reports"]}
        assert ids1.isdisjoint(ids2)
        assert len(ids1 | ids2) == 25
        assert page1["total"] == 25 and page2["total"] == 25

    def test_limit_offset_binding_order(self, routed):
        """LIMIT ?/OFFSET ? 占位符顺序与参数 append 顺序一致（绑定颠倒防回归）。"""
        pid = routed.create_project("P")["id"]
        _seed_reports(routed, pid, 5)
        # created_at DESC：rpt(004) > rpt(003) > ...；第 2 页(2,2) = 中间两行
        page2 = asyncio.run(reports.list_reports(pid, limit=2, offset=2))
        assert [r["title"] for r in page2["reports"]] == ["报告-2", "报告-1"]

    def test_enrichment_applies_to_page_rows(self, routed):
        """模板缺失 → template_name=Unknown / stage=none，usage 键齐备。"""
        pid = routed.create_project("P")["id"]
        _seed_reports(routed, pid, 2)
        result = asyncio.run(reports.list_reports(pid, limit=1, offset=0))
        row = result["reports"][0]
        assert row["template_name"] == "Unknown"
        assert row["stage"] == "none"
        assert "usage" in row

    def test_project_isolation(self, routed):
        pid_a = routed.create_project("A")["id"]
        pid_b = routed.create_project("B")["id"]
        _seed_reports(routed, pid_a, 2)
        _seed_reports(routed, pid_b, 1)
        result = asyncio.run(reports.list_reports(pid_a, limit=0))
        assert result["total"] == 2
        assert all(r["project_id"] == pid_a for r in result["reports"])


class TestListPptsPagination:
    def test_limit_zero_returns_all_backward_compatible(self, routed):
        pid = routed.create_project("P")["id"]
        _seed_ppts(routed, pid, 3)
        result = asyncio.run(ppts.list_ppts(pid, limit=0))
        assert len(result["ppts"]) == 3
        assert result["has_more"] is False
        assert result["total"] == 3

    def test_limit_returns_page_and_has_more(self, routed):
        pid = routed.create_project("P")["id"]
        _seed_ppts(routed, pid, 5)
        result = asyncio.run(ppts.list_ppts(pid, limit=3, offset=0))
        assert len(result["ppts"]) == 3
        assert result["has_more"] is True
        assert result["total"] == 5

    def test_offset_paginates_across_pages(self, routed):
        pid = routed.create_project("P")["id"]
        _seed_ppts(routed, pid, 7)
        page1 = asyncio.run(ppts.list_ppts(pid, limit=3, offset=0))
        page2 = asyncio.run(ppts.list_ppts(pid, limit=3, offset=3))
        ids1 = {p["id"] for p in page1["ppts"]}
        ids2 = {p["id"] for p in page2["ppts"]}
        assert ids1.isdisjoint(ids2)
        assert len(ids1 | ids2) == 6
        assert page2["has_more"] is True
        assert page1["total"] == 7

    def test_limit_offset_binding_order(self, routed):
        pid = routed.create_project("P")["id"]
        _seed_ppts(routed, pid, 5)
        page2 = asyncio.run(ppts.list_ppts(pid, limit=2, offset=2))
        assert [p["title"] for p in page2["ppts"]] == ["PPT-2", "PPT-1"]
