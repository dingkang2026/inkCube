# -*- coding: utf-8 -*-
"""search_strategy.generate_search_queries 回归（P1 形参瘦身批次）。

根因修复回归：D3 签名收窄后 L94 残留 `chat_full(messages, llm_settings)` 二参
调用，TypeError 被 except 吞掉 → 检索策略永远降级为标题单查询（静默失效）。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report_generator.search_strategy import generate_search_queries  # noqa: E402


class _FakeLLM:
    def __init__(self, content):
        self.content = content
        self.calls = []

    async def chat_full(self, messages):
        self.calls.append(messages)
        return {"content": self.content,
                "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                          "total_tokens": 2}}


def test_generate_queries_parses_llm_json():
    """正常路径：LLM JSON 解析出多组关键词（修复前此处 TypeError 静默降级）"""
    llm = _FakeLLM('{"queries": ["行业规模", "竞争格局"]}')
    queries, usage = asyncio.run(generate_search_queries(
        {"section_id": "s1", "title": "市场分析"}, [], llm))
    assert queries == ["行业规模", "竞争格局"]
    assert usage["total_tokens"] == 2
    assert len(llm.calls) == 1


def test_generate_queries_falls_back_to_title_on_bad_json():
    """响应不可解析 → 降级为标题单查询（该降级只应发生在解析失败时）"""
    llm = _FakeLLM("not json")
    queries, _ = asyncio.run(generate_search_queries(
        {"section_id": "s1", "title": "市场分析"}, [], llm))
    assert queries == ["市场分析"]
