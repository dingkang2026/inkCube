"""python/tests/test_retrieval.py — 多通道检索装配单测（§3.3）。

覆盖：
  - data_sources 通道装配：仅 knowledge / knowledge+internet / wiki+db /
    tavily_key 空时跳过 internet / db 为 None 时跳过 wiki
  - data_sources=None 默认 ["knowledge"]
  - report_depth → 知识库 top_k（full 20 / brief 10），显式 top_k 覆盖
  - internet 通道收到 tavily_api_key
  - 空结果降级：optional→skip / 非 optional→is_degraded
  - usage 累计

通道函数与 LLM 相关调用全部 mock，不触网、不依赖向量库。
async 用例统一用 asyncio.run 驱动。
"""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from report_generator.retrieval import retrieve_for_section  # noqa: E402

_ZERO = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
_STRATEGY_USAGE = {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1}
_CARD_USAGE = {"prompt_tokens": 2, "completion_tokens": 0, "total_tokens": 2}


def _patches(know_ret=None, inet_ret=None, wiki_ret=None):
    """返回 mocks dict。默认各通道各返回 1 条原始结果。"""
    gen = AsyncMock(return_value=(["q1"], dict(_STRATEGY_USAGE)))
    know = AsyncMock(return_value=(
        know_ret if know_ret is not None
        else [{"source_type": "knowledge", "content": "k", "score": 1.0}],
        dict(_ZERO),
    ))
    inet = AsyncMock(return_value=(
        inet_ret if inet_ret is not None
        else [{"source_type": "internet", "content": "i", "score": 1.0}],
        dict(_ZERO),
    ))
    wiki = AsyncMock(return_value=(
        wiki_ret if wiki_ret is not None
        else [{"source_type": "wiki", "content": "w", "score": 1.0}],
        dict(_ZERO),
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
        _, _, m = _run(data_sources=["knowledge"], tavily_api_key="key")
        assert m["know"].call_count == 1
        assert m["inet"].call_count == 0

    def test_knowledge_and_internet(self):
        _, _, m = _run(data_sources=["knowledge", "internet"], tavily_api_key="key")
        assert m["know"].call_count == 1
        assert m["inet"].call_count == 1

    def test_internet_skipped_without_key(self):
        """请求 internet 但 tavily_key 为空 → 优雅跳过，不调用 internet 通道。"""
        _, _, m = _run(data_sources=["knowledge", "internet"], tavily_api_key="")
        assert m["know"].call_count == 1
        assert m["inet"].call_count == 0

    def test_default_data_sources_is_knowledge(self):
        _, _, m = _run(data_sources=None, tavily_api_key="key")
        assert m["know"].call_count == 1
        assert m["inet"].call_count == 0

    def test_internet_receives_tavily_key(self):
        _, _, m = _run(data_sources=["knowledge", "internet"], tavily_api_key="sk-secret")
        # _search_internet_channel(queries, tavily_api_key, top_k, query_parallelism)
        assert m["inet"].call_args.args[1] == "sk-secret"


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


class TestUsage:
    def test_accumulates_strategy_and_cards(self):
        _, usage, _ = _run(data_sources=["knowledge"])
        # 策略(1) + 建卡(2)；通道 mock 返回 0
        assert usage["total_tokens"] == 3

    def test_skip_path_only_strategy_usage(self):
        _, usage, _ = _run(data_sources=["knowledge"], _know_ret=[])
        assert usage["total_tokens"] == 1  # 仅策略，未建卡
