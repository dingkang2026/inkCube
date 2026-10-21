"""Regression tests for sweep_reviews Stage1 orphan matching (review P1-2).

修复前：page_inbound_counts 以原始 page_key 为 key，而 Stage1 提取侧已做
_normalize_key，两侧口径错位导致 orphan 永远无法自动解决（违反 2.6 §5.3
「已入链 → auto_resolved」承诺，全部落入 Stage2 耗 LLM 成本）。
"""
import asyncio
import os
import sys

import pytest

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.database import Database
from wiki.sweep_reviews import _stage1_rule_match, sweep_reviews


def _run(coro):
    """Synchronously drive a coroutine (project uses plain pytest, no pytest-asyncio)."""
    return asyncio.run(coro)


@pytest.fixture
def db():
    """Create a fresh in-memory database with all tables."""
    database = Database(db_path=":memory:")
    database.initialize()
    return database


@pytest.fixture
def project_id(db):
    proj = db.create_project(name="Test Project", project_type="general")
    return proj["id"]


class TestStage1Orphan:
    def test_normalized_counts_key_matches_extracted_ref(self):
        """核心回归：counts key 原始 snake_case，提取侧归一化后应命中。"""
        reviews = [{
            "id": "r1", "type": "orphan",
            "title": "孤立页面: [[enterprise_profile]] (企业画像)",
            "description": "",
        }]
        resolved, remaining = _stage1_rule_match(
            reviews, {"enterprise_profile"}, {"enterprise_profile": 3},
        )
        assert resolved == ["r1"]
        assert remaining == []

    def test_orphan_with_inbound_link_resolves_end_to_end(self, db, project_id):
        db.create_or_update_wiki_page(
            project_id=project_id, page_key="paos", title="PAOs", content="x",
        )
        db.create_or_update_wiki_page(
            project_id=project_id, page_key="report", title="Report",
            content="see [[paos]]", refs=["paos"],
        )
        db.create_or_update_wiki_review(
            "rev1", project_id, "orphan", "孤立页面: [[paos]] (PAOs)", "desc",
        )

        # 无 LLM client → 仅跑 Stage1 规则匹配
        result = _run(sweep_reviews(db, project_id))

        assert result["stage1_resolved"] == 1
        assert result["remaining"] == 0
        assert db.list_wiki_reviews(project_id, resolved=False) == []

    def test_counts_key_normalization_e2e(self, db, project_id):
        """refs 存储值带大小写/分隔符漂移时，归一化后仍应匹配。"""
        db.create_or_update_wiki_page(
            project_id=project_id, page_key="enterprise_profile", title="企业画像",
            content="x",
        )
        db.create_or_update_wiki_page(
            project_id=project_id, page_key="report", title="Report",
            content="see [[enterprise_profile]]", refs=["Enterprise-Profile"],
        )
        db.create_or_update_wiki_review(
            "rev1", project_id, "orphan",
            "孤立页面: [[enterprise_profile]] (企业画像)", "desc",
        )

        result = _run(sweep_reviews(db, project_id))
        assert result["stage1_resolved"] == 1

    def test_orphan_without_inbound_stays_pending(self, db, project_id):
        db.create_or_update_wiki_page(
            project_id=project_id, page_key="lonely", title="Lonely", content="x",
        )
        db.create_or_update_wiki_review(
            "rev1", project_id, "orphan", "孤立页面: [[lonely]] (Lonely)", "desc",
        )

        result = _run(sweep_reviews(db, project_id))
        assert result["stage1_resolved"] == 0
        assert result["remaining"] == 1
        assert len(db.list_wiki_reviews(project_id, resolved=False)) == 1


class TestSweepEndpointSignature:
    """router → sweep_reviews 签名漂移回归（P1 漏网：函数级单测的调用
    形态掩盖了端点层四参调用，端点必 500 而单测全绿——端点层必须有
    直接覆盖）"""

    def test_endpoint_invokes_sweep_with_three_args(self, monkeypatch):
        import routers.wiki as wiki_router

        class _FakeDB:
            def get_project(self, pid):
                return {"id": pid, "name": "P"}

            def get_settings(self):
                raise RuntimeError("no llm configured")  # 走 llm_client=None 分支

        captured = []

        async def fake_sweep(db, project_id, llm_client):
            captured.append((project_id, llm_client))
            return {"stage1_resolved": 0, "remaining": 0}

        monkeypatch.setattr(wiki_router, "get_db", lambda: _FakeDB())
        monkeypatch.setattr("wiki.sweep_reviews.sweep_reviews", fake_sweep)

        result = _run(wiki_router.sweep_project_reviews("p1"))

        assert result == {"success": True, "stage1_resolved": 0, "remaining": 0}
        assert captured == [("p1", None)]
