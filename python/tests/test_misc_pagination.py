"""技能 / 网页登录态列表分页回归（skills + web_logins，统一约定）。

回归背景（2026-10）：
- GET /skills、GET /web-logins 此前全量加载；统一 limit/offset/has_more/total；
  归一化（enabled/needs_confirm bool 化）只作用于当前页行。
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import deps
import routers.skills as skills
import routers.web_logins as web_logins
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


def _seed_skills(database, n):
    ids = []
    for i in range(n):
        row = database.create_skill(
            f"skill-{i}", f"描述-{i}", "local", f"C:/src/{i}", f"C:/skills/{i}")
        ids.append(row["id"])
        database.conn.execute(
            "UPDATE skills SET created_at = ? WHERE id = ?",
            (f"2026-01-01T00:00:{i:02d}", row["id"]),
        )
    database.conn.commit()
    return ids


class TestListSkillsPagination:
    def test_limit_zero_returns_all_backward_compatible(self, deps_db):
        """limit=0 → 全量：install 查重/delete/toggle 内部调用不受影响。"""
        _seed_skills(deps_db, 3)
        result = asyncio.run(skills.list_skills(limit=0))
        assert len(result["skills"]) == 3
        assert result["has_more"] is False
        assert result["total"] == 3

    def test_limit_returns_page_and_has_more(self, deps_db):
        _seed_skills(deps_db, 5)
        result = asyncio.run(skills.list_skills(limit=3, offset=0))
        assert len(result["skills"]) == 3
        assert result["has_more"] is True
        assert result["total"] == 5

    def test_offset_paginates_across_pages(self, deps_db):
        _seed_skills(deps_db, 7)
        page1 = asyncio.run(skills.list_skills(limit=3, offset=0))
        page2 = asyncio.run(skills.list_skills(limit=3, offset=3))
        ids1 = {s["id"] for s in page1["skills"]}
        ids2 = {s["id"] for s in page2["skills"]}
        assert ids1.isdisjoint(ids2)
        assert len(ids1 | ids2) == 6

    def test_limit_offset_binding_order(self, deps_db):
        """LIMIT ?/OFFSET ? 占位符顺序与参数 append 顺序一致（绑定颠倒防回归）。"""
        _seed_skills(deps_db, 5)
        page2 = asyncio.run(skills.list_skills(limit=2, offset=2))
        assert [s["name"] for s in page2["skills"]] == ["skill-2", "skill-1"]

    def test_row_normalization_applies(self, deps_db):
        """归一化（enabled/needs_confirm bool、permissions 列表）作用于页行。"""
        _seed_skills(deps_db, 2)
        result = asyncio.run(skills.list_skills(limit=1, offset=0))
        row = result["skills"][0]
        assert row["enabled"] is True
        # create_skill 不写 needs_confirm 列（表默认值 1）：断言归一化 bool 化语义
        assert isinstance(row["needs_confirm"], bool)
        assert row["permissions"] == []


class TestListWebLoginsPagination:
    def test_limit_zero_returns_all_backward_compatible(self, deps_db):
        for i in range(3):
            deps_db.add_web_login(f"site{i}.com", None)
        result = asyncio.run(web_logins.list_web_logins(limit=0))
        assert len(result["logins"]) == 3
        assert result["has_more"] is False
        assert result["total"] == 3

    def test_limit_returns_page_and_has_more(self, deps_db):
        for i in range(5):
            deps_db.add_web_login(f"site{i}.com", None)
        result = asyncio.run(web_logins.list_web_logins(limit=3, offset=0))
        assert len(result["logins"]) == 3
        assert result["has_more"] is True
        assert result["total"] == 5

    def test_offset_paginates_across_pages(self, deps_db):
        for i in range(7):
            deps_db.add_web_login(f"site{i}.com", None)
        page1 = asyncio.run(web_logins.list_web_logins(limit=3, offset=0))
        page2 = asyncio.run(web_logins.list_web_logins(limit=3, offset=3))
        domains1 = {l["domain"] for l in page1["logins"]}
        domains2 = {l["domain"] for l in page2["logins"]}
        assert domains1.isdisjoint(domains2)
        assert len(domains1 | domains2) == 6
