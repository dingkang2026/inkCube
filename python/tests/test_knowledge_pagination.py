"""项目 / 基础库列表分页回归（knowledge，0.1dev-guide 列表分页统一约定）。

回归背景（2026-10）：
- GET /projects、GET /base-libs 此前全量加载；统一 limit/offset/has_more/total；
  projects 响应保留 archived_count（侧边栏归档 tab 徽记，一次聚合查询），
  total 与列表同口径（archived 过滤后）。
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import routers.knowledge as knowledge
from db.database import Database


@pytest.fixture
def db():
    database = Database(db_path=":memory:")
    database.initialize()
    return database


@pytest.fixture
def routed(db, monkeypatch):
    monkeypatch.setattr(knowledge, "get_db", lambda: db)
    return db


def _archive(database, pid):
    database.conn.execute(
        "UPDATE projects SET archived_at = '2026-01-15T00:00:00' WHERE id = ?",
        (pid,),
    )
    database.conn.commit()


def _ensure_lib(database, lib_id):
    """基础库行直插最小 projects 记录（kind='base_lib'，对齐既有测试）。"""
    now = database._now()
    database.conn.execute(
        "INSERT INTO projects (id, name, description, type, stage, company_name,"
        " credit_code, kind, created_at, updated_at)"
        " VALUES (?, 'T', '', 'general', 'none', '', '', 'base_lib', ?, ?)",
        (lib_id, now, now),
    )
    database.conn.commit()


class TestListProjectsPagination:
    def test_limit_zero_returns_all_backward_compatible(self, routed):
        for i in range(3):
            routed.create_project(name=f"项目-{i}")
        result = asyncio.run(knowledge.list_projects(limit=0))
        assert len(result["projects"]) == 3
        assert result["has_more"] is False
        assert result["total"] == 3

    def test_limit_returns_page_and_has_more(self, routed):
        for i in range(5):
            routed.create_project(name=f"项目-{i}")
        result = asyncio.run(knowledge.list_projects(limit=3, offset=0))
        assert len(result["projects"]) == 3
        assert result["has_more"] is True
        assert result["total"] == 5

    def test_offset_paginates_across_pages(self, routed):
        for i in range(7):
            routed.create_project(name=f"项目-{i}")
        page1 = asyncio.run(knowledge.list_projects(limit=3, offset=0))
        page2 = asyncio.run(knowledge.list_projects(limit=3, offset=3))
        ids1 = {p["id"] for p in page1["projects"]}
        ids2 = {p["id"] for p in page2["projects"]}
        assert ids1.isdisjoint(ids2)
        assert len(ids1 | ids2) == 6
        assert page2["has_more"] is True

    def test_total_scoped_to_archived_flag(self, routed):
        active = [routed.create_project(name=f"活跃-{i}")["id"] for i in range(2)]
        archived = [routed.create_project(name=f"归档-{i}")["id"] for i in range(3)]
        for pid in archived:
            _archive(routed, pid)
        live = asyncio.run(knowledge.list_projects(limit=0))
        assert live["total"] == 2
        assert all(p["id"] in active for p in live["projects"])
        assert live["archived_count"] == 3  # 徽记与 archived 参数无关
        gone = asyncio.run(knowledge.list_projects(archived=True, limit=0, offset=0))
        assert gone["total"] == 3
        assert all(p["id"] in archived for p in gone["projects"])

    def test_base_lib_rows_excluded(self, routed):
        routed.create_project(name="项目")
        _ensure_lib(routed, "bl-x")
        result = asyncio.run(knowledge.list_projects(limit=0))
        assert result["total"] == 1
        assert all(p["id"] != "bl-x" for p in result["projects"])


class TestListBaseLibsPagination:
    def test_limit_zero_returns_all_backward_compatible(self, routed):
        for i in range(3):
            _ensure_lib(routed, f"bl{i}")
        result = asyncio.run(knowledge.list_base_libs(limit=0))
        assert len(result["base_libs"]) == 3
        assert result["has_more"] is False
        assert result["total"] == 3

    def test_limit_returns_page_and_has_more(self, routed):
        for i in range(5):
            _ensure_lib(routed, f"bl{i}")
        result = asyncio.run(knowledge.list_base_libs(limit=3, offset=0))
        assert len(result["base_libs"]) == 3
        assert result["has_more"] is True
        assert result["total"] == 5

    def test_offset_paginates_across_pages(self, routed):
        for i in range(7):
            _ensure_lib(routed, f"bl{i}")
        page1 = asyncio.run(knowledge.list_base_libs(limit=3, offset=0))
        page2 = asyncio.run(knowledge.list_base_libs(limit=3, offset=3))
        ids1 = {b["id"] for b in page1["base_libs"]}
        ids2 = {b["id"] for b in page2["base_libs"]}
        assert ids1.isdisjoint(ids2)
        assert len(ids1 | ids2) == 6

    def test_project_rows_excluded(self, routed):
        routed.create_project(name="项目")
        _ensure_lib(routed, "bl-a")
        result = asyncio.run(knowledge.list_base_libs(limit=0))
        assert result["total"] == 1
        assert result["base_libs"][0]["id"] == "bl-a"
