"""知识体检 review 修复回归（2026-10-27：M-1 CAS / m-1 dedup 档 / s-2 409 门 / s-3 步骤隔离）。

覆盖五条链路：
1. M-1 内容级 CAS：replace_wiki_page_text / fix_wiki_page_wikilink 读-改-写
   窗口内被并发写入 → content_changed 拒绝写入且零副作用
2. m-1 档位口径：auto_governance._build_dedup_client 走 get_task_settings("dedup")
   （与手动 dedup 端点同档，不再误用 standard）；对照 standard 档仍归 sweep
3. s-2 合并互斥：merge_duplicate_pages 编译进行中（queued/ingesting/storing）
   → 409 COMPILING_IN_PROGRESS；终态放行 + 携 group_id 时组状态回写 merged
4. s-3 步骤隔离：治理链后段（dedup）异常不吞前段动作，部分汇总照常落 task 行
5. 复审 Major 补口：update_wiki_page_content 可选内容级 CAS + enrich 端点
   编译 409 门（与 fix-wikilink 同口径）+ LLM 补链窗口竞态拒写零副作用
"""
import asyncio
import json
import os
import sys
from unittest.mock import MagicMock

import pytest

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.database import Database


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


# ── 1. M-1 内容级 CAS ──


class TestReplaceTextContentCas:
    def test_replace_rejects_when_content_changed_in_flight(self, db, project_id, monkeypatch):
        """读-改-写窗口内他方先行写入 → content_changed 拒写，零副作用。

        M-1 竞态模拟：get_wiki_page 读到快照后、UPDATE 前，用户编辑先行落库
        （UPDATE ... WHERE content = 快照 不命中）——治理链与用户写路径并存
        时谁后到谁让路，而不是互相覆盖。
        """
        page = db.create_or_update_wiki_page(
            project_id, "p_cert", "证书页", content="公司成立于 2008 年。", status="compiled",
        )
        stale = dict(db.get_wiki_page(project_id, "p_cert"))
        real_get = db.get_wiki_page  # 保存真实函数：副作用验证不能用 stale 快照
        # 他方写入先行：用户编辑改写全文（old_text 在新内容中已不存在）
        db.update_wiki_page_content(
            page_id=page["id"], content="用户已改写的全新内容", status="user_edited")
        # 被测函数读到的是旧快照
        monkeypatch.setattr(db, "get_wiki_page", lambda pid, key: stale)

        result = db.replace_wiki_page_text(project_id, "p_cert", "成立于 2008 年", "成立于 2009 年")

        assert result == {"success": False, "reason": "content_changed", "page_key": "p_cert"}
        # 用户内容未被覆盖，无版本快照
        final = real_get(project_id, "p_cert")
        assert final["content"] == "用户已改写的全新内容"
        assert db.list_wiki_page_versions(page["id"]) == []


class TestFixWikilinkContentCas:
    def test_fix_rejects_when_content_changed_in_flight(self, db, project_id, monkeypatch):
        """死链修复同款 CAS：窗口内内容被改 → content_changed，不产生写操作。"""
        page = db.create_or_update_wiki_page(
            project_id, "p_report", "报告页", content="见 [[dead_link]] 说明。", status="compiled",
        )
        stale = dict(db.get_wiki_page(project_id, "p_report"))
        real_get = db.get_wiki_page
        db.update_wiki_page_content(
            page_id=page["id"], content="已被改写，死链已不存在。", status="user_edited")
        monkeypatch.setattr(db, "get_wiki_page", lambda pid, key: stale)

        result = db.fix_wiki_page_wikilink(project_id, "p_report", "dead_link", "target_page")

        assert result == {"success": False, "reason": "content_changed", "page_key": "p_report"}
        final = real_get(project_id, "p_report")
        assert final["content"] == "已被改写，死链已不存在。"
        assert db.list_wiki_page_versions(page["id"]) == []


# ── 2. m-1 dedup 档位 ──


class TestDedupClientTier:
    def test_build_dedup_client_uses_dedup_tier(self, db, monkeypatch):
        """治理链 dedup 客户端走 get_task_settings(dedup)（与手动端点同口径）。

        m-1 回归：原实现误用 standard 档 client，注释却称「dedup 档独立路由」。
        """
        import llm
        import llm.routing
        captured = {}

        def fake_get_task_settings(settings, tier):
            captured["tier"] = tier
            return {"model": f"{tier}-model"}

        def fake_client(settings):
            captured["model"] = settings.get("model")
            return object()

        monkeypatch.setattr(llm.routing, "get_task_settings", fake_get_task_settings)
        monkeypatch.setattr(llm, "LLMClient", fake_client)

        from wiki.auto_governance import _build_dedup_client
        assert _build_dedup_client(db) is not None
        assert captured == {"tier": "dedup", "model": "dedup-model"}

    def test_build_llm_client_keeps_standard_tier(self, db, monkeypatch):
        """对照：standard 档 client 仍走 resolve_model(standard)（sweep 用）。"""
        import llm
        import llm.routing
        captured = {}

        def fake_resolve_model(settings, tier):
            captured["tier"] = tier
            return f"{tier}-model"

        def fake_client(settings):
            captured["model"] = settings.get("model")
            return object()

        monkeypatch.setattr(llm.routing, "resolve_model", fake_resolve_model)
        monkeypatch.setattr(llm, "LLMClient", fake_client)

        from wiki.auto_governance import _build_llm_client
        assert _build_llm_client(db) is not None
        assert captured == {"tier": "standard", "model": "standard-model"}


# ── 3. s-2 合并 409 门 ──


class TestMergeCompileGate:
    def _gate_setup(self, db, project_id, monkeypatch):
        monkeypatch.setattr("routers.wiki.get_db", lambda: db)
        monkeypatch.setattr("routers.wiki.ensure_ready", lambda *a, **k: None)

    @pytest.mark.parametrize("status", ["queued", "ingesting", "storing"])
    def test_merge_rejected_while_compiling(self, db, project_id, monkeypatch, status):
        """编译进行中合并 → 409 COMPILING_IN_PROGRESS（s-2：最重写操作补门）。"""
        from routers import wiki as wiki_router

        self._gate_setup(db, project_id, monkeypatch)
        task = db.create_wiki_compile_task(project_id, "manual")
        if status != "queued":
            db.update_compile_task_status(task["id"], status)

        resp = _run(wiki_router.merge_duplicate_pages(
            project_id,
            wiki_router.DedupMergeRequest(canonical_key="corp_a", duplicate_keys=["corp_b"]),
        ))

        assert resp.status_code == 409
        assert json.loads(resp.body)["code"] == "COMPILING_IN_PROGRESS"

    def test_merge_proceeds_after_compile_completed_and_marks_group(self, db, project_id, monkeypatch):
        """终态放行 → merge_pages 执行（mock）→ 携 group_id 时组状态回写 merged。"""
        import llm
        import wiki.dedup as dedup_mod
        from routers import wiki as wiki_router

        self._gate_setup(db, project_id, monkeypatch)
        task = db.create_wiki_compile_task(project_id, "manual")
        db.update_compile_task_status(task["id"], "completed")
        db.replace_dedup_groups(project_id, [
            {"slugs": ["corp_a", "corp_b"], "reason": "同一公司主页", "confidence": "high"},
        ])
        group = db.list_dedup_groups(project_id)[0]

        merge_calls = []

        async def fake_merge_pages(db_, pid, canonical_key, duplicate_keys, llm_client):
            merge_calls.append((canonical_key, list(duplicate_keys)))
            return {"merged": 1, "rewritten_links": 0, "failed": [], "truncated": []}

        monkeypatch.setattr(dedup_mod, "merge_pages", fake_merge_pages)
        monkeypatch.setattr(llm, "LLMClient", lambda settings: object())

        result = _run(wiki_router.merge_duplicate_pages(
            project_id,
            wiki_router.DedupMergeRequest(
                canonical_key="corp_a", duplicate_keys=["corp_b"], group_id=group["id"]),
        ))

        assert result["merged"] == 1
        assert merge_calls == [("corp_a", ["corp_b"])]
        row = db.conn.execute(
            "SELECT status FROM wiki_dedup_groups WHERE id = ?", (group["id"],)
        ).fetchone()
        assert row["status"] == "merged"


# ── 4. s-3 治理链步骤隔离 ──


class TestGovernanceStepIsolation:
    def test_partial_summary_persisted_when_dedup_fails(self, db, project_id, monkeypatch):
        """治理链后段（dedup）异常 → 前段动作照常落库（s-3：不留无痕迹）。

        sweep 核销 2 条（mock）+ 死链修复 1 条（真实写路径），dedup 检测抛
        异常——governance_summary 仍带 swept=2/link_fixed=1/dedup_groups=0
        落 task 行，通知照常发出。
        """
        import activity_events
        import wiki.dedup as dedup_mod
        import wiki.sweep_reviews as sweep_mod
        from wiki.auto_governance import on_compile_complete_sync

        task = db.create_wiki_compile_task(project_id, "manual")
        task_id = task["id"]
        db.append_ingest_summary(task_id, {"file_id": "f1", "pages_created": 2, "pages_updated": 0})

        # 造一条可机械修复的死链发现项（真实走 fix_wiki_page_wikilink）
        db.create_or_update_wiki_page(
            project_id, "src_report", "报告摘要页", content="引用 [[dead_link]]。", status="compiled",
        )
        db.create_or_update_wiki_review(
            review_id="rev_dead", project_id=project_id,
            review_type="broken-link", title="死链: [[dead_link]]",
            description=(
                "页面 'src_report' 引用了不存在的页面。\n"
                "PAGES: src_report\n建议链接到: [[target_page]]"
            ),
        )

        async def fake_sweep(db_, pid, llm_client):
            return {"stage1_resolved": 2, "stage2_resolved": 0}

        async def boom(*args, **kwargs):
            raise RuntimeError("dedup exploded")

        monkeypatch.setattr(sweep_mod, "sweep_reviews", fake_sweep)
        monkeypatch.setattr("wiki.auto_compile._readiness_ready", lambda kind: True)
        monkeypatch.setattr("wiki.auto_governance._build_llm_client", lambda db_: object())
        monkeypatch.setattr("wiki.auto_governance._build_dedup_client", lambda db_: object())
        monkeypatch.setattr(
            dedup_mod, "extract_entity_summaries",
            lambda db_, pid: [{"k": "a"}, {"k": "b"}])
        monkeypatch.setattr(dedup_mod, "detect_duplicates", boom)
        notify = MagicMock()
        monkeypatch.setattr(activity_events, "governance_wiki", notify)

        on_compile_complete_sync(db, project_id, task_id, {})

        persisted = db.get_wiki_compile_task(task_id)
        assert json.loads(persisted["governance_summary"]) == {
            "swept": 2, "link_fixed": 1, "dedup_groups": 0,
        }
        # 死链发现项真实核销（修复成功 → resolved）
        assert db.get_wiki_review("rev_dead")["resolved"] == 1
        # 有动作 → 通知仍发出（部分失败不影响通知链）
        assert notify.called


# ── 5. 复审 Major 补口：update_wiki_page_content CAS + enrich 端点 ──


class TestUpdatePageContentCas:
    def test_cas_write_succeeds_when_snapshot_matches(self, db, project_id):
        """expected_content 命中现行内容 → 正常写入 + 版本快照 + success。"""
        page = db.create_or_update_wiki_page(
            project_id, "p_doc", "文档页", content="原始正文。", status="compiled",
        )
        result = db.update_wiki_page_content(
            page_id=page["id"], content="补链后正文。", status="compiled",
            compile_version=3, expected_content="原始正文。",
        )
        assert result == {"success": True}
        assert db.get_wiki_page(project_id, "p_doc")["content"] == "补链后正文。"
        versions = db.list_wiki_page_versions(page["id"])
        assert len(versions) == 1 and versions[0]["version"] == 3

    def test_cas_write_rejected_when_content_changed_in_flight(self, db, project_id):
        """窗口内他方先行写入 → content_changed 拒绝整页覆盖，零副作用。"""
        page = db.create_or_update_wiki_page(
            project_id, "p_doc", "文档页", content="原始正文。", status="compiled",
        )
        # 他方先行写入：快照「原始正文。」自此过期
        db.update_wiki_page_content(
            page_id=page["id"], content="用户已改写的全新内容。", status="user_edited")
        result = db.update_wiki_page_content(
            page_id=page["id"], content="LLM 补链后的旧底稿。", status="compiled",
            compile_version=3, expected_content="原始正文。",
        )

        assert result == {"success": False, "reason": "content_changed"}
        final = db.get_wiki_page(project_id, "p_doc")
        assert final["content"] == "用户已改写的全新内容。"
        assert final["status"] == "user_edited"
        assert db.list_wiki_page_versions(page["id"]) == []

    def test_without_expected_content_keeps_legacy_unconditional_write(self, db, project_id):
        """expected_content=None 保持原语义（编辑器保存等全量调用方零改动）。"""
        page = db.create_or_update_wiki_page(
            project_id, "p_doc", "文档页", content="旧内容。", status="user_edited",
        )
        result = db.update_wiki_page_content(
            page_id=page["id"], content="编辑器保存的最新全文。", status="user_edited")
        assert result == {"success": True}
        assert db.get_wiki_page(project_id, "p_doc")["content"] == "编辑器保存的最新全文。"


class TestEnrichPageWikilinks:
    """复审 Major：enrich 补链与用户写路径同构读-改-写——补 409 门 + CAS 写回。"""

    def _setup(self, db, project_id, monkeypatch):
        monkeypatch.setattr("routers.wiki.get_db", lambda: db)
        monkeypatch.setattr("routers.wiki.ensure_ready", lambda *a, **k: None)

    @pytest.mark.parametrize("status", ["queued", "ingesting", "storing"])
    def test_enrich_rejected_while_compiling(self, db, project_id, monkeypatch, status):
        """编译进行中补链 → 409 COMPILING_IN_PROGRESS（与 fix-wikilink 同口径）。"""
        from routers import wiki as wiki_router

        self._setup(db, project_id, monkeypatch)
        db.create_or_update_wiki_page(
            project_id, "p_page", "页面", content="正文。", status="compiled")
        task = db.create_wiki_compile_task(project_id, "manual")
        if status != "queued":
            db.update_compile_task_status(task["id"], status)

        resp = _run(wiki_router.enrich_page_wikilinks(
            project_id, wiki_router.EnrichWikilinksRequest(page_key="p_page")))

        assert resp.status_code == 409
        assert json.loads(resp.body)["code"] == "COMPILING_IN_PROGRESS"

    def test_enrich_rejects_write_when_content_changed_during_llm(self, db, project_id, monkeypatch):
        """LLM 补链期间（秒级窗口）用户编辑先行 → 拒绝整页覆盖，LLM 建议整批丢弃。"""
        import llm
        import wiki.wiki_enrich as enrich_mod
        from routers import wiki as wiki_router

        self._setup(db, project_id, monkeypatch)
        db.create_or_update_wiki_page(
            project_id, "p_page", "页面", content="原始正文。", status="compiled")
        task = db.create_wiki_compile_task(project_id, "manual")
        db.update_compile_task_status(task["id"], "completed")

        async def fake_enrich(content, page_key, all_pages, llm_client):
            # 竞态注入：页面读取之后、写回之前，用户编辑先行落库
            db.update_wiki_page_content(
                page_id=db.get_wiki_page(project_id, "p_page")["id"],
                content="用户已改写的全新内容。", status="user_edited")
            return content + "\n补链结果（基于过期快照）", 1

        monkeypatch.setattr(enrich_mod, "enrich_wikilinks", fake_enrich)
        monkeypatch.setattr(llm, "LLMClient", lambda settings: object())

        result = _run(wiki_router.enrich_page_wikilinks(
            project_id, wiki_router.EnrichWikilinksRequest(page_key="p_page")))

        assert result == {"success": False, "reason": "content_changed", "links_added": 0}
        final = db.get_wiki_page(project_id, "p_page")
        assert final["content"] == "用户已改写的全新内容。"
        assert final["status"] == "user_edited"
        # 无 enriched 时间线（写被拒绝，不产生任何痕迹假象）
        cnt = db.conn.execute(
            "SELECT COUNT(*) AS cnt FROM wiki_change_log WHERE project_id = ? AND action = 'enriched'",
            (project_id,),
        ).fetchone()["cnt"]
        assert cnt == 0

    def test_enrich_writes_back_when_no_race(self, db, project_id, monkeypatch):
        """无竞态正路：补链写回成功 + enriched 时间线。"""
        import llm
        import wiki.wiki_enrich as enrich_mod
        from routers import wiki as wiki_router

        self._setup(db, project_id, monkeypatch)
        db.create_or_update_wiki_page(
            project_id, "p_page", "页面", content="原始正文。", status="compiled")
        task = db.create_wiki_compile_task(project_id, "manual")
        db.update_compile_task_status(task["id"], "completed")

        async def fake_enrich(content, page_key, all_pages, llm_client):
            return content + "\n参见 [[other_page]]。", 1

        monkeypatch.setattr(enrich_mod, "enrich_wikilinks", fake_enrich)
        monkeypatch.setattr(llm, "LLMClient", lambda settings: object())

        result = _run(wiki_router.enrich_page_wikilinks(
            project_id, wiki_router.EnrichWikilinksRequest(page_key="p_page")))

        assert result == {"success": True, "links_added": 1}
        assert db.get_wiki_page(project_id, "p_page")["content"].endswith("参见 [[other_page]]。")
        cnt = db.conn.execute(
            "SELECT COUNT(*) AS cnt FROM wiki_change_log WHERE project_id = ? AND action = 'enriched'",
            (project_id,),
        ).fetchone()["cnt"]
        assert cnt == 1
