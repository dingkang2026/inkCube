"""Regression tests for wiki dedup merge (review P1-1).

merge_pages 此前在 LLM 合并失败时仍无条件删除 dup 页（独特内容静默丢失，
返回值还报成功）。修复后：合并失败保留 dup 页并通过 failed 上报；
超出 _MAX_MERGE_CHARS 的截断通过 truncated 上报；change log 只记实际合并数。

复审 Warning 2026-10-27 补口：merge 写回与全站链接重写接入内容级 CAS
（M-1 同族）——canonical 窗口内被并发改写 → 全组 content_changed 拒绝、
dup 保留待重试、零副作用；canonical 自身 [[dup]] 引用内存改写（修旧链路
死链残留）；tags/refs/sources 并集落库（修死计算）；逐页改写快照失配
重读重做一次，仍失配跳过不覆盖。
"""
import asyncio
import os
import sys

import pytest

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.database import Database
from wiki.dedup import _rewrite_wikilinks, merge_pages


def _run(coro):
    """Synchronously drive a coroutine (project uses plain pytest, no pytest-asyncio)."""
    return asyncio.run(coro)


class _FakeLLM:
    """Minimal LLM client stub with configurable reply / failure."""

    def __init__(self, reply: str = "merged body", fail: bool = False):
        self.reply = reply
        self.fail = fail
        self.calls = 0

    async def chat_full(self, messages):
        self.calls += 1
        if self.fail:
            raise RuntimeError("llm boom")
        return {"content": self.reply}


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
            db, project_id, "paos", ["poly_phosphate"], _FakeLLM(),
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
            db, project_id, "paos", ["poly_phosphate"], _FakeLLM(fail=True),
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
            db, project_id, "paos", ["long_dup"], _FakeLLM(),
        ))

        assert result["merged"] == 1
        assert result["truncated"] == ["long_dup"]

    def test_missing_canonical_is_noop(self, db, project_id):
        result = _run(merge_pages(
            db, project_id, "ghost", ["poly_phosphate"], _FakeLLM(),
        ))
        assert result["merged"] == 0
        assert result["failed"] == []
        assert result["truncated"] == []


class TestMergePagesCas:
    """复审 Warning 2026-10-27：merge 写回接入内容级 CAS（M-1 同族）。"""

    def test_canonical_changed_in_flight_rejects_whole_group(self, db, project_id, monkeypatch):
        """canonical 在 LLM 合并窗口内被并发改写 → 全组 content_changed：
        dup 保留待重试、canonical 保持用户内容、无 change log（零副作用）。"""
        db.create_or_update_wiki_page(
            project_id=project_id, page_key="paos", title="PAOs",
            content="canonical body", page_type="entity",
        )
        db.create_or_update_wiki_page(
            project_id=project_id, page_key="poly_phosphate", title="聚磷菌",
            content="unique dup content", page_type="entity",
        )
        stale = dict(db.get_wiki_page(project_id, "paos"))
        real_get = db.get_wiki_page
        # 他方写入先行：用户编辑改写 canonical 全文
        paos_id = db.get_wiki_page(project_id, "paos")["id"]
        db.update_wiki_page_content(
            page_id=paos_id, content="用户已改写的 canonical", status="user_edited")
        # 被测函数读到的是旧快照（仅 canonical；dup 走真实读取）
        monkeypatch.setattr(
            db, "get_wiki_page",
            lambda pid, key: stale if key == "paos" else real_get(pid, key))

        result = _run(merge_pages(
            db, project_id, "paos", ["poly_phosphate"], _FakeLLM(),
        ))

        assert result["merged"] == 0
        assert result["failed"] == [
            {"page_key": "poly_phosphate", "reason": "content_changed"},
        ]
        # dup 保留可重试；canonical 未被合并结果覆盖（副作用验证走 real_get，
        # monkeypatch 后的 getter 对 paos 仍返回 stale 快照）
        assert real_get(project_id, "poly_phosphate")["content"] == "unique dup content"
        assert real_get(project_id, "paos")["content"] == "用户已改写的 canonical"
        # 全组拒绝 → 不写 change log，也无链接改写副作用
        assert db.list_wiki_change_log(project_id) == []

    def test_merge_rewrites_canonical_self_links_and_persists_unions(self, db, project_id):
        """成功路径：canonical 自身 [[dup]] 引用同步改写（旧实现被 merged_content
        覆盖回去残留死链）；tags/refs/sources 并集落库（旧实现计算后未持久化）。"""
        db.create_or_update_wiki_page(
            project_id=project_id, page_key="paos", title="PAOs",
            content="canonical body", page_type="entity",
            tags=["tag_a"], refs=["ref_a"], sources=["src_a"],
        )
        db.create_or_update_wiki_page(
            project_id=project_id, page_key="poly_phosphate", title="聚磷菌",
            content="dup body", page_type="entity",
            tags=["tag_b"], refs=["ref_b"], sources=["src_b"],
        )

        result = _run(merge_pages(
            db, project_id, "paos", ["poly_phosphate"],
            _FakeLLM(reply="merged citing [[poly_phosphate]] and [[poly_phosphate|聚磷菌]]"),
        ))

        assert result["merged"] == 1
        final = db.get_wiki_page(project_id, "paos")
        assert final["content"] == "merged citing [[paos]] and [[paos|聚磷菌]]"
        assert final["tags"] == ["tag_a", "tag_b"]
        assert final["refs"] == ["ref_a", "ref_b"]
        assert final["sources"] == ["src_a", "src_b"]


class TestRewriteWikilinksCas:
    """复审 Warning 2026-10-27：全站链接重写逐页 CAS + 幂等重读重试。"""

    def _seed(self, db, project_id):
        db.create_or_update_wiki_page(
            project_id=project_id, page_key="poly_phosphate", title="聚磷菌",
            content="dup body", page_type="entity")
        db.create_or_update_wiki_page(
            project_id=project_id, page_key="reader", title="Reader",
            content="see [[poly_phosphate]] and [[poly_phosphate|PP]]",
            page_type="topic")
        return db.get_wiki_page(project_id, "reader")["id"]

    def test_retry_after_stale_snapshot_succeeds(self, db, project_id, monkeypatch):
        """首次写回快照失配 → 重读最新内容重做（链接替换幂等，重读安全）。"""
        reader_id = self._seed(db, project_id)
        real_update = db.update_wiki_page_content
        rejected = {"done": False}

        def flaky_update(**kw):
            if not rejected["done"] and kw.get("page_id") == reader_id:
                rejected["done"] = True
                return {"success": False, "reason": "content_changed"}
            return real_update(**kw)

        monkeypatch.setattr(db, "update_wiki_page_content", flaky_update)

        rewritten = _rewrite_wikilinks(db, project_id, "poly_phosphate", "paos")

        assert rewritten == 1
        final = db.get_wiki_page(project_id, "reader")["content"]
        assert "[[paos]]" in final and "[[paos|PP]]" in final

    def test_gives_up_after_second_mismatch_keeps_page(self, db, project_id, monkeypatch):
        """两次失配（持续并发编辑）→ 跳过该页不覆盖，内容保留原样。"""
        self._seed(db, project_id)
        real_update = db.update_wiki_page_content

        def always_stale(**kw):
            if kw.get("expected_content") is not None:
                return {"success": False, "reason": "content_changed"}
            return real_update(**kw)

        monkeypatch.setattr(db, "update_wiki_page_content", always_stale)

        rewritten = _rewrite_wikilinks(db, project_id, "poly_phosphate", "paos")

        assert rewritten == 0
        assert db.get_wiki_page(project_id, "reader")["content"] == \
            "see [[poly_phosphate]] and [[poly_phosphate|PP]]"
