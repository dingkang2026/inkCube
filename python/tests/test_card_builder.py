"""python/tests/test_card_builder.py — 知识卡片构建单元测试。

覆盖：
  - 用户消息来源标签：wiki → [项目Wiki]（不误标为互联网）、knowledge/internet 标签
  - LLM 响应不可解析 / 调用失败 → fallback 卡片保留 source_type
  - LLM 响应正常解析 → 卡片字段透传（含 wiki）
  - 空原始结果短路

不触网：llm_client 用本地 fake 对象替代。
async 用例统一用 asyncio.run 驱动。
"""
import asyncio
import os
import sys

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from report_generator.card_builder import build_knowledge_cards  # noqa: E402

_ZERO = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


class _FakeLLM:
    """捕获 messages 并返回预设 content 的假 LLM 客户端。"""

    def __init__(self, content="not-json"):
        self.content = content
        self.captured = None

    async def chat_full(self, messages, settings):
        self.captured = messages
        return {"content": self.content, "usage": dict(_ZERO)}


def _raw(source_type):
    return {
        "source_type": source_type,
        "source_title": f"{source_type} 来源",
        "source_from": "wiki:market" if source_type == "wiki" else "a.pdf p.1",
        "source_date": "2026-01-01",
        "content": "内容",
        "score": 1.0,
    }


def _build(raw_results, client):
    return asyncio.run(build_knowledge_cards(
        raw_results, {"section_id": "s1", "title": "T"}, client, {},
    ))


# ── 来源标签 ────────────────────────────────────────────────


class TestSourceLabels:
    def test_wiki_labeled_as_wiki(self):
        """wiki 原始结果标注为 [项目Wiki]，不误标为互联网（回归）。"""
        client = _FakeLLM()
        _build([_raw("wiki")], client)
        user_msg = client.captured[1]["content"]
        assert "[项目Wiki]" in user_msg
        assert "[互联网]" not in user_msg

    def test_knowledge_and_internet_labels(self):
        client = _FakeLLM()
        _build([_raw("knowledge"), _raw("internet")], client)
        user_msg = client.captured[1]["content"]
        assert "[知识库]" in user_msg
        assert "[互联网]" in user_msg

    def test_unknown_type_falls_back_to_internet_label(self):
        """未知 source_type 维持默认标签（互联网）。"""
        client = _FakeLLM()
        _build([_raw("feed")], client)
        assert "[互联网]" in client.captured[1]["content"]


# ── 降级路径 ────────────────────────────────────────────────


class TestFallback:
    def test_unparseable_response_falls_back_keeping_source_type(self):
        """LLM 响应不可解析 → fallback 卡片保留 source_type=wiki（回归）。"""
        client = _FakeLLM(content="这不是 JSON")
        cards, sufficiency, _ = _build([_raw("wiki")], client)
        assert len(cards) == 1
        assert cards[0]["source_type"] == "wiki"
        assert sufficiency["level"] == "insufficient"

    def test_llm_failure_falls_back(self):
        class _Boom:
            async def chat_full(self, messages, settings):
                raise RuntimeError("boom")

        cards, _, usage = _build([_raw("knowledge")], _Boom())
        assert len(cards) == 1
        assert usage["total_tokens"] == 0


# ── 正常解析 ────────────────────────────────────────────────


class TestParsedResponse:
    def test_parsed_cards_pass_through_wiki_type(self):
        content = (
            '{"cards": [{"content": "知识点", "source_type": "wiki",'
            ' "source_title": "市场分析", "source_from": "wiki:market", "source_date": ""}],'
            ' "sufficiency": {"level": "sufficient", "reason": "ok"}}'
        )
        client = _FakeLLM(content=content)
        cards, sufficiency, _ = _build([_raw("wiki")], client)
        assert cards[0]["source_type"] == "wiki"
        assert cards[0]["card_id"].startswith("Cs1_")
        assert sufficiency["level"] == "sufficient"

    def test_empty_raw_results_short_circuits(self):
        cards, sufficiency, usage = _build([], _FakeLLM())
        assert cards == []
        assert sufficiency["level"] == "insufficient"
        assert usage["total_tokens"] == 0
