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

    async def chat_full(self, messages):
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
        raw_results, {"section_id": "s1", "title": "T"}, client,
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
            async def chat_full(self, messages):
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


# ── 迭代检索循环增量行为（§3.3）────────────────────────────


class TestIterativeIncremental:
    def test_follow_up_queries_parsed_when_insufficient(self):
        """insufficient 评估时 follow_up_queries 透传（补搜 queries 来源，§3.3）。"""
        content = (
            '{"cards": [{"content": "知识点", "source_type": "knowledge",'
            ' "source_title": "t", "source_from": "a.pdf p.1", "source_date": ""}],'
            ' "sufficiency": {"level": "insufficient", "reason": "缺对比数据",'
            ' "follow_up_queries": ["q1", "q2"]}}'
        )
        cards, sufficiency, _ = _build([_raw("knowledge")], _FakeLLM(content=content))
        assert sufficiency == {
            "level": "insufficient", "reason": "缺对比数据",
            "follow_up_queries": ["q1", "q2"],
        }

    def test_sufficient_forces_empty_follow_up_queries(self):
        """sufficient 时强制清空 queries（prompt 已约束，防御归一双保险）。"""
        content = (
            '{"cards": [{"content": "知识点", "source_type": "knowledge",'
            ' "source_title": "t", "source_from": "a.pdf p.1", "source_date": ""}],'
            ' "sufficiency": {"level": "sufficient", "reason": "ok",'
            ' "follow_up_queries": ["多余"]}}'
        )
        _, sufficiency, _ = _build([_raw("knowledge")], _FakeLLM(content=content))
        assert sufficiency["follow_up_queries"] == []

    def test_malformed_follow_up_queries_normalized(self):
        """非字符串项剔除、截断至 4 组。"""
        content = (
            '{"cards": [{"content": "k", "source_type": "knowledge",'
            ' "source_title": "t", "source_from": "a.pdf p.1", "source_date": ""}],'
            ' "sufficiency": {"level": "insufficient", "reason": "r",'
            ' "follow_up_queries": ["q1", "", 3, null, "q2", "q3", "q4", "q5"]}}'
        )
        _, sufficiency, _ = _build([_raw("knowledge")], _FakeLLM(content=content))
        assert sufficiency["follow_up_queries"] == ["q1", "q2", "q3", "q4"]

    def test_existing_cards_kept_and_new_card_ids_continue(self):
        """补搜轮：existing_cards 原样保留（含 ref_number 不漂移），新卡 card_id 接续编号。"""
        content = (
            '{"cards": [{"content": "新知识点", "source_type": "knowledge",'
            ' "source_title": "t", "source_from": "b.pdf p.2", "source_date": ""}],'
            ' "sufficiency": {"level": "sufficient", "reason": "ok"}}'
        )
        existing = [{
            "card_id": "Cs1_001", "section_id": "s1", "content": "旧知识点",
            "source_type": "wiki", "source_title": "旧", "source_from": "wiki:m",
            "source_date": "", "ref_number": 7,
        }]
        cards, sufficiency, _ = asyncio.run(build_knowledge_cards(
            [_raw("knowledge")], {"section_id": "s1", "title": "T"},
            _FakeLLM(content=content),
            existing_cards=existing, used_queries=["q1"],
        ))
        assert len(cards) == 2
        assert cards[0]["card_id"] == "Cs1_001"  # 原样保留
        assert cards[0]["ref_number"] == 7  # 编号不漂移
        assert cards[1]["card_id"] == "Cs1_002"  # 新卡序号接续
        assert sufficiency["level"] == "sufficient"

    def test_existing_cards_and_used_queries_injected_into_prompt(self):
        """补搜轮 user 消息注入已有卡片轻量清单与已用 queries 硬约束。"""
        client = _FakeLLM(content='{"cards": [], "sufficiency": {}}')
        existing = [{
            "card_id": "Cs1_001", "section_id": "s1", "content": "旧知识点内容",
            "source_type": "wiki", "source_title": "旧", "source_from": "wiki:m",
            "source_date": "",
        }]
        asyncio.run(build_knowledge_cards(
            [_raw("knowledge")], {"section_id": "s1", "title": "T"},
            client, existing_cards=existing, used_queries=["旧查询"],
        ))
        user_msg = client.captured[1]["content"]
        assert "已有知识卡片" in user_msg
        assert "旧知识点内容" in user_msg
        assert "已执行的检索" in user_msg
        assert "旧查询" in user_msg
        assert "不得与之语义重复" in user_msg

    def test_fallback_keeps_existing_cards(self):
        """建卡 LLM 失败时 fallback 仍保留 existing_cards（无 follow_up_queries）。"""
        existing = [{
            "card_id": "Cs1_001", "section_id": "s1", "content": "旧",
            "source_type": "wiki", "source_title": "旧", "source_from": "wiki:m",
            "source_date": "",
        }]
        cards, sufficiency, _ = asyncio.run(build_knowledge_cards(
            [_raw("knowledge")], {"section_id": "s1", "title": "T"},
            _FakeLLM(content="not-json"), existing_cards=existing,
        ))
        assert len(cards) == 2  # existing + fallback(1 raw)
        assert cards[0]["card_id"] == "Cs1_001"
        assert sufficiency["follow_up_queries"] == []
