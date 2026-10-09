"""python/tests/test_iterative_retrieval.py — 迭代检索循环单元测试（2.5 §3.3）。

覆盖：
  - 轮 1 sufficient：单轮收尾，无补搜
  - 轮 1 insufficient + follow_up_queries：轮 2 以补搜 queries 检索、增量建卡
    （existing_cards / used_queries 传入建卡）、queries 汇总正确
  - 达轮次上限（settings report_retrieval_max_rounds，含钳制 0→1 / 99→5）停止
  - 补搜轮无新料（来源全部与已有轮次重复）→ 不建卡直接收尾
  - 轮 1 空结果：optional → skip；非 optional → degraded

通道函数 / 建卡 / 检索策略全部 monkeypatch，不触网不触 LLM。
"""
import asyncio
import os
import sys

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from report_generator import retrieval as retrieval_mod
from report_generator.registry import CitationRegistry

_ZERO = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
_SECTION = {"section_id": "s1", "title": "T", "writing_requirements": []}


def _raw(source_from: str, source_type: str = "knowledge") -> dict:
    return {
        "source_type": source_type,
        "source_title": source_from,
        "source_from": source_from,
        "source_date": "",
        "content": f"内容-{source_from}",
        "score": 1.0,
    }


class _Harness:
    """按轮次预设通道结果与建卡评估序列的测试夹具。"""

    def __init__(self, monkeypatch, round_raw: dict, sufficiency_seq: list[dict]):
        self.round_raw = round_raw  # {"queries tuple": [raw, ...]}
        self.sufficiency_seq = list(sufficiency_seq)
        self.round_queries: list[list[str]] = []
        self.build_calls: list[dict] = []

        async def fake_strategy(section, digests, llm_client, llm_settings):
            return ["q1"], dict(_ZERO)

        async def fake_channel_k(queries, *args, **kwargs):
            self.round_queries.append(list(queries))
            return list(self.round_raw.get(tuple(queries), [])), dict(_ZERO), []

        async def fake_channel_w(queries, db, project_id, top_k):
            return [], dict(_ZERO), []

        async def fake_channel_i(queries, *args, **kwargs):
            return [], dict(_ZERO), []

        async def fake_build(new_raw, section, llm_client, llm_settings,
                             existing_cards=None, used_queries=None):
            self.build_calls.append({
                "new_raw_from": [r["source_from"] for r in new_raw],
                "existing_count": len(existing_cards or []),
                "used_queries": list(used_queries or []),
            })
            suff = self.sufficiency_seq.pop(0)
            new_card = {
                "card_id": "Cx_new",
                "section_id": section["section_id"],
                "content": f"card-{new_raw[0]['source_from']}" if new_raw else "card",
                "source_type": new_raw[0]["source_type"] if new_raw else "knowledge",
                "source_title": "t",
                "source_from": new_raw[0]["source_from"] if new_raw else "fallback",
                "source_date": "",
            }
            return list(existing_cards or []) + [new_card], suff, dict(_ZERO)

        monkeypatch.setattr(retrieval_mod, "generate_search_queries", fake_strategy)
        monkeypatch.setattr(retrieval_mod, "_search_knowledge_channel", fake_channel_k)
        monkeypatch.setattr(retrieval_mod, "_search_wiki_channel", fake_channel_w)
        monkeypatch.setattr(retrieval_mod, "_search_internet_channel", fake_channel_i)
        monkeypatch.setattr(retrieval_mod, "build_knowledge_cards", fake_build)

    def run(self, settings: dict | None = None, section: dict | None = None):
        return asyncio.run(retrieval_mod.retrieve_for_section(
            section=section or dict(_SECTION),
            search_tool=None,
            registry=CitationRegistry(),
            project_id="p1",
            llm_client=None,
            llm_settings=settings or {},
            digests=[],
            data_sources=["knowledge"],
        ))


def _insuff(follow_up: list[str]) -> dict:
    return {"level": "insufficient", "reason": "缺数据", "follow_up_queries": follow_up}


class TestIterativeLoop:
    def test_sufficient_first_round_stops(self, monkeypatch):
        h = _Harness(monkeypatch, {("q1",): [_raw("a.pdf p.1")]},
                     [{"level": "sufficient", "reason": "ok", "follow_up_queries": []}])
        result, _ = h.run()

        assert len(result.rounds) == 1
        assert h.round_queries == [["q1"]]
        assert result.sufficiency["level"] == "sufficient"
        assert result.queries == ["q1"]
        assert [c["ref_number"] for c in result.cards] == [1]

    def test_followup_second_round_incremental_build(self, monkeypatch):
        h = _Harness(
            monkeypatch,
            {("q1",): [_raw("a.pdf p.1")], ("f1",): [_raw("b.pdf p.2")]},
            [_insuff(["f1"]), {"level": "sufficient", "reason": "ok", "follow_up_queries": []}],
        )
        result, _ = h.run()

        assert len(result.rounds) == 2
        assert h.round_queries == [["q1"], ["f1"]]  # 轮 2 用补搜 queries 检索
        assert result.sufficiency["level"] == "sufficient"
        assert result.queries == ["q1", "f1"]  # 全量已用 queries 汇总
        # 增量建卡：轮 2 existing=1（轮 1 卡片）、used_queries 含轮 1 全量 + 本轮 f1
        # （f1 已在轮 2 执行，「不得语义重复」约束自然涵盖本轮 queries）
        assert [c["existing_count"] for c in h.build_calls] == [0, 1]
        assert h.build_calls[1]["used_queries"] == ["q1", "f1"]
        assert len(result.cards) == 2
        assert all(c.get("ref_number") for c in result.cards)  # 全部注册

    def test_max_rounds_limit_stops_iterations(self, monkeypatch):
        seq = [_insuff([f"f{n}"]) for n in range(1, 5)]  # 永不 sufficient
        raw = {("q1",): [_raw("a.pdf p.1")]}
        raw.update({(f"f{n}",): [_raw(f"s{n}.pdf")] for n in range(1, 5)})
        h = _Harness(monkeypatch, raw, seq)
        result, _ = h.run(settings={"report_retrieval_max_rounds": 3})

        assert len(result.rounds) == 3  # 达上限停止
        assert result.sufficiency["level"] == "insufficient"

    def test_max_rounds_clamped(self, monkeypatch):
        # 0 → 钳为 1（insufficient 也只跑一轮）
        seq = [_insuff(["f1"])]
        h = _Harness(monkeypatch, {("q1",): [_raw("a.pdf p.1")]}, seq)
        result, _ = h.run(settings={"report_retrieval_max_rounds": 0})
        assert len(result.rounds) == 1

        # 99 → 钳为 5
        seq5 = [_insuff([f"f{n}"]) for n in range(1, 6)]
        raw = {("q1",): [_raw("a.pdf p.1")]}
        raw.update({(f"f{n}",): [_raw(f"s{n}.pdf")] for n in range(1, 6)})
        h2 = _Harness(monkeypatch, raw, seq5)
        result2, _ = h2.run(settings={"report_retrieval_max_rounds": 99})
        assert len(result2.rounds) == 5

    def test_no_new_sources_skips_rebuild(self, monkeypatch):
        # 轮 2 补搜撞回已搜来源 → 不建卡，以轮 1 材料收尾
        h = _Harness(
            monkeypatch,
            {("q1",): [_raw("a.pdf p.1")], ("f1",): [_raw("a.pdf p.1")]},
            [_insuff(["f1"])],
        )
        result, _ = h.run()

        assert len(result.rounds) == 2
        assert len(h.build_calls) == 1  # 轮 2 未建卡
        assert "card_count" not in result.rounds[1]
        assert result.cards  # 轮 1 卡片保留

    def test_empty_first_round_optional_skips(self, monkeypatch):
        h = _Harness(monkeypatch, {("q1",): []}, [])
        section = dict(_SECTION, optional=True)
        result, _ = h.run(section=section)
        assert result.skip is True

    def test_empty_first_round_not_optional_degrades(self, monkeypatch):
        h = _Harness(monkeypatch, {("q1",): []}, [])
        result, _ = h.run()
        assert result.is_degraded is True
