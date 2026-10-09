"""Regression tests for wiki dedup merge (review P1-1).

merge_pages 此前在 LLM 合并失败时仍无条件删除 dup 页（独特内容静默丢失，
返回值还报成功）。修复后：合并失败保留 dup 页并通过 failed 上报；
超出 _MAX_MERGE_CHARS 的截断通过 truncated 上报；change log 只记实际合并数。
"""
import asyncio
import os
import sys

import pytest

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.database import Database
from wiki.dedup import merge_pages


def _run(coro):
    """Synchronously drive a coroutine (project uses plain pytest, no pytest-asyncio)."""
    return asyncio.run(coro)


class _FakeLLM:
    """Minimal LLM client stub with configurable reply / failure."""

    def __init__(self, reply: str = "merged body", fail: bool = False):
        self.reply = reply
        self.fail = fail
        self.calls = 0

    async def chat(self, messages, settings):
        self.calls += 1
        if self.fail:
            raise RuntimeError("llm boom")
        return self.reply


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


class TestMergePages:
    def test_merge_success_deletes_dup_and_rewrites_links(self, db, project_id):
        db.create_or_update_wiki_page(
            project_id=project_id, page_key="paos", title="PAOs",
            content="canonical body", page_type="entity", tags=["a"],
        )
        db.create_or_update_wiki_page(
            project_id=project_id, page_key="poly_phosphate", title="聚磷菌",
            content="dup body", page_type="entity", tags=["b"],
        )
        db.create_or_update_wiki_page(
            project_id=project_id, page_key="reader", title="Reader",
            content="see [[poly_phosphate]]", page_type="topic",
        )

        result = _run(merge_pages(
            db, project_id, "paos", ["poly_phosphate"], _FakeLLM(), {},
        ))

        assert result["merged"] == 1
        assert result["failed"] == []
        assert result["truncated"] == []
        assert db.get_wiki_page(project_id, "poly_phosphate") is None
        assert db.get_wiki_page(project_id, "paos")["content"] == "merged body"
        assert "[[paos]]" in db.get_wiki_page(project_id, "reader")["content"]
        logs = db.list_wiki_change_log(project_id)
        assert any(l["action"] == "dedup_merge" and "1 个" in l["summary"] for l in logs)

    def test_llm_failure_keeps_duplicate_page(self, db, project_id):
        """P1-1 核心：LLM 合并失败时 dup 页必须保留，内容不丢，failed 上报。"""
        db.create_or_update_wiki_page(
            project_id=project_id, page_key="paos", title="PAOs",
            content="canonical body", page_type="entity",
        )
        db.create_or_update_wiki_page(
            project_id=project_id, page_key="poly_phosphate", title="聚磷菌",
            content="unique dup content", page_type="entity",
        )

        result = _run(merge_pages(
            db, project_id, "paos", ["poly_phosphate"], _FakeLLM(fail=True), {},
        ))

        assert result["merged"] == 0
        assert result["failed"] == [
            {"page_key": "poly_phosphate", "reason": "llm_merge_failed"},
        ]
        # dup 页保留，独特内容未丢
        assert db.get_wiki_page(project_id, "poly_phosphate") is not None
        assert db.get_wiki_page(project_id, "poly_phosphate")["content"] == "unique dup content"
        # canonical 内容未被破坏
        assert db.get_wiki_page(project_id, "paos")["content"] == "canonical body"
        # 无实际合并 → 不写 change log
        assert db.list_wiki_change_log(project_id) == []

    def test_truncation_reported(self, db, project_id):
        db.create_or_update_wiki_page(
            project_id=project_id, page_key="paos", title="PAOs",
            content="short", page_type="entity",
        )
        db.create_or_update_wiki_page(
            project_id=project_id, page_key="long_dup", title="Long",
            content="x" * 5000, page_type="entity",
        )

        result = _run(merge_pages(
            db, project_id, "paos", ["long_dup"], _FakeLLM(), {},
        ))

        assert result["merged"] == 1
        assert result["truncated"] == ["long_dup"]

    def test_missing_canonical_is_noop(self, db, project_id):
        result = _run(merge_pages(
            db, project_id, "ghost", ["poly_phosphate"], _FakeLLM(), {},
        ))
        assert result["merged"] == 0
        assert result["failed"] == []
        assert result["truncated"] == []
