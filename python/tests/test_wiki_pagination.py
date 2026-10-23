"""Wiki 列表端点分页回归（5 端点，0.1dev-guide 列表分页统一约定）。

回归背景（2026-10）：
- pages/reviews/changelog/versions 统一 limit/offset/has_more（+total）；
  reviews 响应追加 counts（按 type 分型计数，徽章消费端不再客户端全量计数）；
- pages 的 missing_pages 必须基于全量 page_key 集计算（key-only 轻量查询），
  不能基于当前页——分页后当前页缺 key 不代表项目缺页（关键契约）；
- ledger/entries 增 offset（保留 1≤limit≤100 校验，无 limit=0 全量语义）。
"""
import asyncio
import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import routers.wiki as wiki
from db.database import Database


@pytest.fixture
def db():
    database = Database(db_path=":memory:")
    database.initialize()
    return database


@pytest.fixture
def routed(db, monkeypatch):
    monkeypatch.setattr(wiki, "get_db", lambda: db)
    return db


def _insert_page(database, pid, key, seq):
    database.conn.execute(
        "INSERT INTO wiki_pages (id, project_id, page_key, title, content, status,"
        " created_at, updated_at) VALUES (?, ?, ?, ?, '', 'draft', ?, ?)",
        (f"wp{seq:03d}", pid, key, f"页-{key}", f"2026-01-01T00:00:{seq:02d}",
         f"2026-01-01T00:00:{seq:02d}"),
    )


def _seed_pages(database, pid, keys):
    for i, key in enumerate(keys):
        _insert_page(database, pid, key, i)
    database.conn.commit()


def _seed_schemas(database, keys):
    for i, key in enumerate(keys):
        database.conn.execute(
            "INSERT INTO wiki_schemas (id, project_type, page_key, title, position)"
            " VALUES (?, 'general', ?, ?, ?)",
            (f"ws{i:03d}", key, f"概要-{key}", i),
        )
    database.conn.commit()


def _seed_reviews(database, pid, spec):
    """spec: [(type, resolved), ...]；id 按序 wpvNNN。"""
    for i, (rtype, resolved) in enumerate(spec):
        database.conn.execute(
            "INSERT INTO wiki_reviews (id, project_id, type, title, resolved,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (f"rv{i:03d}", pid, rtype, f"发现-{i}", 1 if resolved else 0,
             f"2026-01-01T00:00:{i:02d}"),
        )
    database.conn.commit()


def _seed_changelog(database, pid, n):
    for i in range(n):
        database.conn.execute(
            "INSERT INTO wiki_change_log (id, project_id, action, summary,"
            " created_at) VALUES (?, ?, 'compiled', ?, ?)",
            (f"cl{i:03d}", pid, f"变更-{i}", f"2026-01-01T00:00:{i:02d}"),
        )
    database.conn.commit()


def _seed_versions(database, page_id, pid, key, n):
    for i in range(1, n + 1):
        database.conn.execute(
            "INSERT INTO wiki_page_versions (id, wiki_page_id, project_id,"
            " page_key, title, content, version, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (f"v{i:03d}", page_id, pid, key, f"页-{key}", "", i,
             f"2026-01-01T00:{i:02d}:00"),
        )
    database.conn.commit()


def _seed_ledger(database, pid, n):
    for i in range(n):
        database.conn.execute(
            "INSERT INTO ledger_entries (id, project_id, page_key, entry_date,"
            " entry_type, content, fingerprint, created_at, updated_at)"
            " VALUES (?, ?, 'milestones', ?, 'milestone', ?, ?, ?, ?)",
            (f"le{i:03d}", pid, f"2026-01-0{i + 1}", f"条目-{i}", f"fp{i}",
             f"2026-01-01T00:00:{i:02d}", f"2026-01-01T00:00:{i:02d}"),
        )
    database.conn.commit()


class TestListWikiPagesPagination:
    def test_limit_zero_returns_all_backward_compatible(self, routed):
        pid = routed.create_project("P")["id"]
        _seed_pages(routed, pid, ["a", "b", "c"])
        result = asyncio.run(wiki.list_wiki_pages(pid, limit=0))
        assert len(result["pages"]) == 3
        assert result["has_more"] is False
        assert result["total"] == 3

    def test_limit_returns_page_and_has_more(self, routed):
        pid = routed.create_project("P")["id"]
        _seed_pages(routed, pid, ["a", "b", "c", "d", "e"])
        result = asyncio.run(wiki.list_wiki_pages(pid, limit=3, offset=0))
        assert len(result["pages"]) == 3
        assert result["has_more"] is True
        assert result["total"] == 5

    def test_offset_paginates_across_pages(self, routed):
        pid = routed.create_project("P")["id"]
        _seed_pages(routed, pid, [f"k{i}" for i in range(7)])
        page1 = asyncio.run(wiki.list_wiki_pages(pid, limit=3, offset=0))
        page2 = asyncio.run(wiki.list_wiki_pages(pid, limit=3, offset=3))
        keys1 = {p["page_key"] for p in page1["pages"]}
        keys2 = {p["page_key"] for p in page2["pages"]}
        assert keys1.isdisjoint(keys2)
        assert len(keys1 | keys2) == 6

    def test_missing_pages_based_on_full_key_set(self, routed):
        """关键回归：limit=1 只返回 1 页，missing_pages 仍基于全量 key 集计算。"""
        pid = routed.create_project("P")["id"]
        _seed_schemas(routed, ["a", "b", "c"])
        _seed_pages(routed, pid, ["a", "b"])
        result = asyncio.run(wiki.list_wiki_pages(pid, limit=1, offset=0))
        assert len(result["pages"]) == 1
        assert {s["page_key"] for s in result["missing_pages"]} == {"c"}
        # 全量形态下同样正确
        full = asyncio.run(wiki.list_wiki_pages(pid, limit=0))
        assert {s["page_key"] for s in full["missing_pages"]} == {"c"}

    def test_response_keys_complete(self, routed):
        """分页不丢附属键（侧栏/头部消费的摘要字段与列表同响应返回）。"""
        pid = routed.create_project("P")["id"]
        _seed_pages(routed, pid, ["a"])
        result = asyncio.run(wiki.list_wiki_pages(pid, limit=1, offset=0))
        for key in ("pages", "has_more", "total", "missing_pages",
                    "pending_compile_count", "wiki_purpose", "wiki_overview",
                    "source_pages"):
            assert key in result

    def test_pending_compile_count_matches_pending_files(self, routed):
        """listPages 的待编译数与编译消费的待编译集合同口径实时计算
        （2026-10-22 修复：不再消费 projects 列上的事件累加计数器）。"""
        pid = routed.create_project("P")["id"]
        routed.create_file(
            project_id=pid, filename="new.pdf", file_type=".pdf",
            file_size=1, file_hash="h1", file_id="f_new")
        routed.update_file_status("f_new", "done")
        routed.create_file(
            project_id=pid, filename="old.pdf", file_type=".pdf",
            file_size=1, file_hash="h2", file_id="f_old")
        routed.update_file_status("f_old", "done")
        routed.create_or_update_source_page(
            project_id=pid, file_id="f_old", status="ingested", pages_touched=[])

        result = asyncio.run(wiki.list_wiki_pages(pid, limit=50, offset=0))

        assert result["pending_compile_count"] == 1


class TestListReviewsPagination:
    def test_limit_zero_returns_all_backward_compatible(self, routed):
        pid = routed.create_project("P")["id"]
        _seed_reviews(routed, pid, [("contradiction", False), ("data-gap", False)])
        result = asyncio.run(wiki.list_reviews(pid, limit=0))
        assert len(result["reviews"]) == 2
        assert result["has_more"] is False
        assert result["total"] == 2

    def test_limit_returns_page_and_has_more(self, routed):
        pid = routed.create_project("P")["id"]
        _seed_reviews(routed, pid, [(f"t{i}", False) for i in range(5)])
        result = asyncio.run(wiki.list_reviews(pid, limit=3, offset=0))
        assert len(result["reviews"]) == 3
        assert result["has_more"] is True

    def test_offset_paginates_across_pages(self, routed):
        pid = routed.create_project("P")["id"]
        _seed_reviews(routed, pid, [(f"t{i}", False) for i in range(7)])
        page1 = asyncio.run(wiki.list_reviews(pid, limit=3, offset=0))
        page2 = asyncio.run(wiki.list_reviews(pid, limit=3, offset=3))
        ids1 = {r["id"] for r in page1["reviews"]}
        ids2 = {r["id"] for r in page2["reviews"]}
        assert ids1.isdisjoint(ids2)
        assert len(ids1 | ids2) == 6
        assert page1["total"] == 7

    def test_counts_by_type(self, routed):
        """counts 按 type 分型（pending/resolved/total），供 PulseBar 徽章。"""
        pid = routed.create_project("P")["id"]
        _seed_reviews(routed, pid, [
            ("contradiction", False), ("contradiction", False),
            ("data-gap", True),
        ])
        result = asyncio.run(wiki.list_reviews(pid, limit=0))
        assert result["counts"] == {
            "contradiction": {"pending": 2, "resolved": 0, "total": 2},
            "data-gap": {"pending": 0, "resolved": 1, "total": 1},
        }

    def test_counts_ignores_resolved_filter_and_pagination(self, routed):
        """counts 为全表分型口径：不随 resolved 筛选/分页变化（徽章恒正确）。"""
        pid = routed.create_project("P")["id"]
        _seed_reviews(routed, pid, [
            ("contradiction", False), ("data-gap", False), ("data-gap", True),
        ])
        result = asyncio.run(wiki.list_reviews(pid, resolved=True, limit=1, offset=0))
        assert all(r["resolved"] for r in result["reviews"])
        assert result["counts"]["data-gap"]["pending"] == 1
        assert result["counts"]["contradiction"]["pending"] == 1
        assert result["total"] == 3  # total 亦为全表口径


class TestListLedgerEntriesPagination:
    def test_limit_bounds_enforced(self, routed):
        """1≤limit≤100 校验保留：0/101 → 422（本端点无 limit=0 全量语义）。"""
        pid = routed.create_project("P")["id"]
        _seed_ledger(routed, pid, 3)
        for bad in (0, 101):
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(wiki.list_ledger_entries(pid, limit=bad, offset=0))
            assert exc_info.value.status_code == 422

    def test_offset_paginates_across_pages(self, routed):
        pid = routed.create_project("P")["id"]
        _seed_ledger(routed, pid, 5)
        page1 = asyncio.run(wiki.list_ledger_entries(pid, limit=3, offset=0))
        page2 = asyncio.run(wiki.list_ledger_entries(pid, limit=3, offset=3))
        assert page1["has_more"] is True
        ids1 = {e["id"] for e in page1["entries"]}
        ids2 = {e["id"] for e in page2["entries"]}
        assert ids1.isdisjoint(ids2)
        assert len(ids1 | ids2) == 5
        assert page2["has_more"] is False

    def test_limit_offset_binding_order(self, routed):
        """LIMIT ?/OFFSET ? 占位符顺序与参数 append 顺序一致（绑定颠倒防回归）。"""
        pid = routed.create_project("P")["id"]
        _seed_ledger(routed, pid, 5)
        # entry_date DESC：01-05 最新在前；第 2 页(3,3) 应为 01-02、01-01
        page2 = asyncio.run(wiki.list_ledger_entries(pid, limit=2, offset=3))
        assert [e["entry_date"] for e in page2["entries"]] == [
            "2026-01-02", "2026-01-01"]

    def test_no_total_key(self, routed):
        """台账时间轴契约：{entries, has_more}，无 total 键。"""
        pid = routed.create_project("P")["id"]
        _seed_ledger(routed, pid, 2)
        result = asyncio.run(wiki.list_ledger_entries(pid, limit=1, offset=0))
        assert set(result.keys()) == {"entries", "has_more"}


class TestGetProjectChangelogPagination:
    def test_limit_zero_returns_all_backward_compatible(self, routed):
        pid = routed.create_project("P")["id"]
        _seed_changelog(routed, pid, 3)
        result = asyncio.run(wiki.get_project_changelog(pid, limit=0))
        assert len(result["changelog"]) == 3
        assert result["has_more"] is False
        assert result["total"] == 3

    def test_limit_returns_page_and_has_more(self, routed):
        pid = routed.create_project("P")["id"]
        _seed_changelog(routed, pid, 5)
        result = asyncio.run(wiki.get_project_changelog(pid, limit=3, offset=0))
        assert len(result["changelog"]) == 3
        assert result["has_more"] is True
        assert result["total"] == 5

    def test_offset_paginates_across_pages(self, routed):
        pid = routed.create_project("P")["id"]
        _seed_changelog(routed, pid, 7)
        page1 = asyncio.run(wiki.get_project_changelog(pid, limit=3, offset=0))
        page2 = asyncio.run(wiki.get_project_changelog(pid, limit=3, offset=3))
        ids1 = {c["id"] for c in page1["changelog"]}
        ids2 = {c["id"] for c in page2["changelog"]}
        assert ids1.isdisjoint(ids2)
        assert len(ids1 | ids2) == 6

    def test_project_404(self, routed):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(wiki.get_project_changelog("nope", limit=50, offset=0))
        assert exc_info.value.status_code == 404


class TestListPageVersionsPagination:
    def test_default_limit_20_slice(self, routed):
        pid = routed.create_project("P")["id"]
        _insert_page(routed, pid, "a", 0)
        routed.conn.commit()
        _seed_versions(routed, "wp000", pid, "a", 25)
        result = asyncio.run(wiki.list_page_versions(pid, "a", limit=20, offset=0))
        assert len(result["versions"]) == 20
        assert result["has_more"] is True
        assert result["total"] == 25

    def test_offset_paginates_across_pages(self, routed):
        pid = routed.create_project("P")["id"]
        _insert_page(routed, pid, "a", 0)
        routed.conn.commit()
        _seed_versions(routed, "wp000", pid, "a", 7)
        page1 = asyncio.run(wiki.list_page_versions(pid, "a", limit=3, offset=0))
        page2 = asyncio.run(wiki.list_page_versions(pid, "a", limit=3, offset=3))
        ids1 = {v["id"] for v in page1["versions"]}
        ids2 = {v["id"] for v in page2["versions"]}
        assert ids1.isdisjoint(ids2)
        assert len(ids1 | ids2) == 6
        assert page2["has_more"] is True

    def test_current_version_exposed(self, routed):
        pid = routed.create_project("P")["id"]
        _insert_page(routed, pid, "a", 0)
        routed.conn.commit()
        _seed_versions(routed, "wp000", pid, "a", 2)
        result = asyncio.run(wiki.list_page_versions(pid, "a", limit=1, offset=0))
        assert "current_version" in result

    def test_page_404(self, routed):
        pid = routed.create_project("P")["id"]
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(wiki.list_page_versions(pid, "ghost", limit=20, offset=0))
        assert exc_info.value.status_code == 404
