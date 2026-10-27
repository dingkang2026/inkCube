"""python/tests/test_retrieval.py — 多通道检索装配单测（§3.3）。

覆盖：
  - data_sources 通道装配：仅 knowledge / knowledge+internet / wiki+db /
    web_search_cfg.key 空时跳过 internet / db 为 None 时跳过 wiki
  - data_sources=None 默认 ["knowledge"]
  - report_depth → 知识库 top_k（full 20 / brief 10），显式 top_k 覆盖
  - internet 通道收到 web_search_cfg（provider/key 归一产物，双供应商）
  - 空结果降级：optional→skip / 非 optional→is_degraded
  - usage 累计

通道函数与 LLM 相关调用全部 mock，不触网、不依赖向量库。
async 用例统一用 asyncio.run 驱动。
"""
import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import report_generator.retrieval as retrieval_mod  # noqa: E402
from report_generator.retrieval import (  # noqa: E402
    _search_knowledge_channel,
    _tavily_search,
    _zhipu_search,
    retrieve_for_section,
    web_search_cfg_from_settings,
)

_ZERO = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
_STRATEGY_USAGE = {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1}
_CARD_USAGE = {"prompt_tokens": 2, "completion_tokens": 0, "total_tokens": 2}

# web_search_cfg 归一对象（web_search_cfg_from_settings 产物形态）
_WS_CFG = {"provider": "tavily", "key": "sk-secret", "engine": "search_pro"}
_WS_CFG_EMPTY = {"provider": "", "key": "", "engine": "search_pro"}


def _patches(know_ret=None, inet_ret=None, wiki_ret=None, inet_warn=None):
    """返回 mocks dict。默认各通道各返回 1 条原始结果（通道契约：三元组 results/usage/warnings）。"""
    gen = AsyncMock(return_value=(["q1"], dict(_STRATEGY_USAGE)))
    know = AsyncMock(return_value=(
        know_ret if know_ret is not None
        else [{"source_type": "knowledge", "content": "k", "score": 1.0}],
        dict(_ZERO),
        [],
    ))
    inet = AsyncMock(return_value=(
        inet_ret if inet_ret is not None
        else [{"source_type": "internet", "content": "i", "score": 1.0}],
        dict(_ZERO),
        list(inet_warn or []),
    ))
    wiki = AsyncMock(return_value=(
        wiki_ret if wiki_ret is not None
        else [{"source_type": "wiki", "content": "w", "score": 1.0}],
        dict(_ZERO),
        [],
    ))
    cards = AsyncMock(return_value=([], {"level": "sufficient", "reason": ""}, dict(_CARD_USAGE)))
    mocks = {"gen": gen, "know": know, "inet": inet, "wiki": wiki, "cards": cards}
    return mocks


def _run(section=None, **kwargs):
    """在五路 patch 下运行 retrieve_for_section，返回 (result, usage, mocks)。"""
    mocks = _patches(
        know_ret=kwargs.pop("_know_ret", None),
        inet_ret=kwargs.pop("_inet_ret", None),
        wiki_ret=kwargs.pop("_wiki_ret", None),
        inet_warn=kwargs.pop("_inet_warn", None),
    )
    sec = section or {"section_id": "s1", "title": "T"}
    call_kwargs = dict(
        section=sec, search_tool=MagicMock(), registry=MagicMock(), project_id="p1",
        llm_client=MagicMock(), llm_settings={}, digests=[],
    )
    call_kwargs.update(kwargs)
    with patch("report_generator.retrieval.generate_search_queries", mocks["gen"]), \
         patch("report_generator.retrieval._search_knowledge_channel", mocks["know"]), \
         patch("report_generator.retrieval._search_wiki_channel", mocks["wiki"]), \
         patch("report_generator.retrieval._search_internet_channel", mocks["inet"]), \
         patch("report_generator.retrieval.build_knowledge_cards", mocks["cards"]):
        result, usage = asyncio.run(retrieve_for_section(**call_kwargs))
    return result, usage, mocks


# ── 通道装配 ────────────────────────────────────────────────


class TestChannelAssembly:
    def test_knowledge_only(self):
        _, _, m = _run(data_sources=["knowledge"], web_search_cfg=_WS_CFG)
        assert m["know"].call_count == 1
        assert m["inet"].call_count == 0

    def test_knowledge_and_internet(self):
        _, _, m = _run(data_sources=["knowledge", "internet"], web_search_cfg=_WS_CFG)
        assert m["know"].call_count == 1
        assert m["inet"].call_count == 1

    def test_internet_skipped_without_key(self):
        """请求 internet 但 key 为空 → 优雅跳过，不调用 internet 通道。"""
        _, _, m = _run(data_sources=["knowledge", "internet"], web_search_cfg=_WS_CFG_EMPTY)
        assert m["know"].call_count == 1
        assert m["inet"].call_count == 0

    def test_default_data_sources_is_knowledge(self):
        _, _, m = _run(data_sources=None, web_search_cfg=_WS_CFG)
        assert m["know"].call_count == 1
        assert m["inet"].call_count == 0

    def test_internet_receives_cfg(self):
        _, _, m = _run(data_sources=["knowledge", "internet"], web_search_cfg=_WS_CFG)
        # _search_internet_channel(queries, web_search_cfg, top_k, query_parallelism)
        assert m["inet"].call_args.args[1] == _WS_CFG


# ── knowledge 通道检索范围（include_project_kb，§1.2）──────


class TestKnowledgeChannelScope:
    """include_project_kb=False 时检索范围仅含勾选的基础库；
    空范围直接返回空三元组（不发任何检索请求）。"""

    @staticmethod
    def _tool():
        tool = MagicMock()

        async def search(query, project_id, top_k):
            return [{"id": f"{project_id}-c1", "content": "c", "_score": 1.0,
                     "metadata": {"filename": "f.pdf"}}]

        tool.search = AsyncMock(side_effect=search)
        return tool

    def test_default_includes_project_and_base(self):
        tool = self._tool()
        results, _, _ = asyncio.run(_search_knowledge_channel(
            ["q"], tool, "proj-1", ["base-1"], 5))
        pids = {c.kwargs["project_id"] for c in tool.search.await_args_list}
        assert pids == {"proj-1", "base-1"}
        # 两范围各返回 1 chunk（id 含 pid 不去重）→ 2 条结果
        assert len(results) == 2

    def test_false_excludes_project_kb(self):
        """取消项目库（§1.2，2026-10-27）：仅检索勾选的基础库。"""
        tool = self._tool()
        asyncio.run(_search_knowledge_channel(
            ["q"], tool, "proj-1", ["base-1"], 5, include_project_kb=False))
        pids = {c.kwargs["project_id"] for c in tool.search.await_args_list}
        assert pids == {"base-1"}

    def test_false_without_base_empty_scope_early_return(self):
        """取消项目库且未勾基础库 → 空范围早返回空三元组，不发起任何检索。"""
        tool = self._tool()
        results, usage, warnings = asyncio.run(_search_knowledge_channel(
            ["q"], tool, "proj-1", None, 5, include_project_kb=False))
        assert results == []
        assert usage == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        assert warnings == []
        assert tool.search.await_count == 0


# ── wiki 通道 ────────────────────────────────────────────────


class TestWikiChannel:
    def test_wiki_with_db_invoked(self):
        db = MagicMock()
        _, _, m = _run(data_sources=["knowledge", "wiki"], db=db)
        assert m["wiki"].call_count == 1
        # _search_wiki_channel(queries, db, project_id, top_k)
        assert m["wiki"].call_args.args[1] is db
        assert m["know"].call_count == 1

    def test_wiki_skipped_without_db(self):
        """请求 wiki 但 db 为 None → 优雅跳过，不调用 wiki 通道。"""
        _, _, m = _run(data_sources=["knowledge", "wiki"], db=None)
        assert m["wiki"].call_count == 0
        assert m["know"].call_count == 1

    def test_wiki_not_requested(self):
        _, _, m = _run(data_sources=["knowledge"], db=MagicMock())
        assert m["wiki"].call_count == 0

    def test_wiki_empty_results_not_degrade_with_knowledge(self):
        """wiki 通道空结果但知识库有结果 → 不降级，正常建卡。"""
        result, _, m = _run(
            data_sources=["knowledge", "wiki"], db=MagicMock(), _wiki_ret=[],
        )
        assert result.is_degraded is False
        assert result.skip is False
        assert m["cards"].call_count == 1

    def test_wiki_channel_all_draft_pages_returns_triple(self):
        """回归（§7.1-1 修复）：项目页面全部为 draft（未完成编译）→
        三元组（空结果 / zero_usage / 空警告），不得返回二元组致上层解包 ValueError。"""
        from report_generator.retrieval import _search_wiki_channel
        db = MagicMock()
        db.list_wiki_pages.return_value = [{"page_key": "a", "status": "draft"}]
        results, usage, warnings = asyncio.run(
            _search_wiki_channel(["q"], db=db, project_id="p1", top_k=5)
        )
        assert results == []
        assert usage == dict(_ZERO)
        assert warnings == []

    def test_wiki_channel_list_pages_error_returns_triple(self):
        """list_wiki_pages 异常分支同样锁定三元组契约。"""
        from report_generator.retrieval import _search_wiki_channel
        db = MagicMock()
        db.list_wiki_pages.side_effect = RuntimeError("db down")
        results, usage, warnings = asyncio.run(
            _search_wiki_channel(["q"], db=db, project_id="p1", top_k=5)
        )
        assert results == []
        assert usage == dict(_ZERO)
        assert warnings == []


# ── report_depth → top_k ────────────────────────────────────


class TestTopKByDepth:
    def test_full_depth_top_k_20(self):
        _, _, m = _run(data_sources=["knowledge"], report_depth="full")
        # _search_knowledge_channel(queries, search_tool, project_id, base_project_ids, top_k, ...)
        assert m["know"].call_args.args[4] == 20

    def test_brief_depth_top_k_10(self):
        _, _, m = _run(data_sources=["knowledge"], report_depth="brief")
        assert m["know"].call_args.args[4] == 10

    def test_explicit_top_k_overrides_depth(self):
        _, _, m = _run(data_sources=["knowledge"], report_depth="full", top_k=5)
        assert m["know"].call_args.args[4] == 5


# ── 空结果降级 ──────────────────────────────────────────────


class TestDegradation:
    def test_optional_empty_skips(self):
        result, _, m = _run(
            section={"section_id": "opt", "title": "T", "optional": True},
            data_sources=["knowledge"], _know_ret=[],
        )
        assert result.skip is True
        assert m["cards"].call_count == 0  # 提前返回，不建卡

    def test_non_optional_empty_degrades(self):
        result, _, _ = _run(data_sources=["knowledge"], _know_ret=[])
        assert result.is_degraded is True
        assert result.skip is False


# ── usage 累计 ──────────────────────────────────────────────


class TestWarnings:
    def test_channel_warnings_propagate(self):
        """internet 通道的显式降级警告 → RetrievalResult.warnings（0.4 §7.3 v3.6）。"""
        warn = "互联网检索未生效：智谱 API Key 无效或已失效，本次报告仅使用项目内材料。请前往 设置 > 基础配置 > 网络搜索 更新 Key。"
        result, _, _ = _run(
            data_sources=["knowledge", "internet"], web_search_cfg=_WS_CFG,
            _inet_warn=[warn],
        )
        assert result.warnings == [warn]

    def test_no_warnings_by_default(self):
        result, _, _ = _run(data_sources=["knowledge", "internet"], web_search_cfg=_WS_CFG)
        assert result.warnings == []


class TestUsage:
    def test_accumulates_strategy_and_cards(self):
        _, usage, _ = _run(data_sources=["knowledge"])
        # 策略(1) + 建卡(2)；通道 mock 返回 0
        assert usage["total_tokens"] == 3

    def test_skip_path_only_strategy_usage(self):
        _, usage, _ = _run(data_sources=["knowledge"], _know_ret=[])
        assert usage["total_tokens"] == 1  # 仅策略，未建卡


# ── internet 通道 HTTP 直测（MockTransport）────────────────────
# 上方装配用例 mock 掉了通道函数；此处直测 retrieval 内 HTTP 实现与
# 归一入口（review 🟡-5 盲区补齐）：请求体 / 429 退避 / 序号加权 /
# answer 伪 URL 语义 / 白名单归一逐字段锁定，不触网。


def _direct_client(handler):
    """构造注入 MockTransport 的 AsyncClient（retrieval 函数接收外部 client）。"""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=30.0)


class TestWebSearchCfgNormalization:
    """web_search_cfg_from_settings：provider→key 归一（唯一入口）。"""

    def test_no_provider_key_empty_even_with_keys(self):
        cfg = web_search_cfg_from_settings({
            "web_search_provider": "", "tavily_key": "tvly", "zhipu_search_key": "zp",
        })
        assert cfg == {"provider": "", "key": "", "engine": "search_pro"}

    def test_unknown_provider_key_empty(self):
        cfg = web_search_cfg_from_settings({"web_search_provider": "bogus"})
        assert cfg["key"] == ""

    def test_tavily_provider_and_key_stripped(self):
        cfg = web_search_cfg_from_settings({
            "web_search_provider": " tavily ", "tavily_key": "  tvly-1  ",
        })
        assert cfg == {"provider": "tavily", "key": "tvly-1", "engine": "search_pro"}

    def test_zhipu_provider_key_and_engine(self):
        cfg = web_search_cfg_from_settings({
            "web_search_provider": "zhipu", "zhipu_search_key": "zp-1",
            "zhipu_search_engine": "search_std",
        })
        assert cfg == {"provider": "zhipu", "key": "zp-1", "engine": "search_std"}

    def test_engine_defaults_to_search_pro(self):
        cfg = web_search_cfg_from_settings(
            {"web_search_provider": "zhipu", "zhipu_search_key": "k"})
        assert cfg["engine"] == "search_pro"


class TestZhipuSearchDirect:
    def test_request_shape_scoring_and_date(self):
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content.decode("utf-8")))
            return httpx.Response(200, json={"search_result": [
                {"title": "S1", "content": "K1", "link": "https://b/1",
                 "publish_date": "2026-10-01"},
                {"title": "S2", "content": "K2", "link": "https://b/2"},
            ]})

        cfg = {"provider": "zhipu", "key": "zp-x", "engine": "search_std"}

        async def run():
            async with _direct_client(handler) as client:
                return await _zhipu_search("智能制造 招商", cfg, client, max_results=5)

        results = asyncio.run(run())
        body = seen[0]
        assert body["search_query"] == "智能制造 招商"
        assert body["search_engine"] == "search_std"
        assert body["search_intent"] is False
        assert body["count"] == 5
        assert body["content_size"] == "high"
        # 序号倒序加权（引擎已排序保序）+ publish_date 映射 + link→url
        assert results[0]["score"] == 2.0
        assert results[1]["score"] == 1.0
        assert results[0]["published_date"] == "2026-10-01"
        assert results[0]["url"] == "https://b/1"

    def test_query_truncated_to_70_chars(self):
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content.decode("utf-8")))
            return httpx.Response(200, json={"search_result": []})

        async def run():
            async with _direct_client(handler) as client:
                return await _zhipu_search(
                    "长" * 100, {"key": "k", "engine": "search_pro"}, client)

        asyncio.run(run())
        assert seen[0]["search_query"] == "长" * 70

    def test_engine_whitelist_fallback(self):
        """脏 engine（settings:save 可透传任意值）→ 白名单回落 search_pro。"""
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content.decode("utf-8")))
            return httpx.Response(200, json={"search_result": []})

        async def run():
            async with _direct_client(handler) as client:
                return await _zhipu_search(
                    "q", {"key": "k", "engine": "bogus"}, client)

        asyncio.run(run())
        assert seen[0]["search_engine"] == "search_pro"

    def test_429_backoff_then_success(self, monkeypatch):
        """429 指数退避（2s / 4s，最多 2 次）后成功；等待不真实 sleep。"""
        waits = []

        async def fake_sleep(sec):
            waits.append(sec)

        monkeypatch.setattr(retrieval_mod.asyncio, "sleep", fake_sleep)
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(429 if len(calls) <= 2 else 200,
                                  json={"search_result": []})

        async def run():
            async with _direct_client(handler) as client:
                return await _zhipu_search(
                    "q", {"key": "k", "engine": "search_pro"}, client)

        asyncio.run(run())
        assert len(calls) == 3
        assert waits == [2, 4]

    def test_429_exhausted_raises(self, monkeypatch):
        """退避耗尽仍 429 → raise_for_status 上抛（通道层捕获转警告文案）。"""
        async def fake_sleep(sec):
            pass

        monkeypatch.setattr(retrieval_mod.asyncio, "sleep", fake_sleep)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={})

        async def run():
            async with _direct_client(handler) as client:
                return await _zhipu_search(
                    "q", {"key": "k", "engine": "search_pro"}, client)

        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(run())


class TestTavilySearchDirect:
    def test_cjk_answer_off_and_pseudo_url_and_depth_fallback(self):
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content.decode("utf-8")))
            return httpx.Response(200, json={
                "answer": "AI summary",
                "results": [{"title": "T1", "content": "C1",
                             "url": "https://a/1", "score": 0.9}],
            })

        async def run():
            async with _direct_client(handler) as client:
                return await _tavily_search(
                    "智能制造 招商政策", "tvly-x", client,
                    max_results=5, search_depth="bogus")

        results = asyncio.run(run())
        body = seen[0]
        # CJK 查询关闭 include_answer（与工具层同判，c2f24e5c）；非法 depth 回落 basic
        assert body["include_answer"] is False
        assert body["language"] == "zh-cn"
        assert body["search_depth"] == "basic"
        assert body["max_results"] == 5
        # answer 段以伪 URL 首条进入且 score=1.0（恒置顶语义）
        assert results[0]["url"].startswith("tavily://answer/")
        assert results[0]["score"] == 1.0
        assert results[1]["url"] == "https://a/1"

    def test_non_cjk_query_includes_answer(self):
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content.decode("utf-8")))
            return httpx.Response(200, json={"answer": None, "results": []})

        async def run():
            async with _direct_client(handler) as client:
                return await _tavily_search("python news", "tvly-x", client)

        asyncio.run(run())
        assert seen[0]["include_answer"] is True
