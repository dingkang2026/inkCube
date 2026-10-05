"""python/tests/test_variable_resolver.py — 变量解析层单测（§3.1.1 研究 DAG）。

覆盖：
  - implicit_project_vars：项目字段映射 / None 兜底
  - _topo_sort：拓扑序、确定性、成环 → VariableResolutionError、忽略非 derived 依赖
  - _parse_list_value：JSON 数组 / 代码块 / 按行降级 / 空 → UNRESOLVED
  - _render_deps：渲染、list 连接、跳过 UNRESOLVED
  - _extract_value（async）：scalar/list 抽值、空 → UNRESOLVED、LLM 异常 → UNRESOLVED
  - resolve_variables（async）：隐式+user+derived 全链路、下游降级、证据、usage 累计

async 用例统一用 asyncio.run 驱动（不引入 pytest-asyncio 依赖）。
"""
import asyncio
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from report_generator.retrieval import RetrievalResult  # noqa: E402
from report_generator.variable_resolver import (  # noqa: E402
    UNRESOLVED,
    VariableResolutionError,
    _extract_value,
    _parse_list_value,
    _render_deps,
    _topo_sort,
    implicit_project_vars,
    resolve_variables,
)

_EXTRACT_USAGE = {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}
_RETRIEVE_USAGE = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}


class FakeLLM:
    """按 derive_hint 关键字匹配返回抽值内容；记录调用。"""

    def __init__(self, mapping=None, default="", usage=None):
        self.mapping = mapping or {}
        self.default = default
        self.usage = dict(usage or _EXTRACT_USAGE)
        self.calls = []

    async def chat_full(self, messages, settings):
        self.calls.append(messages)
        user = messages[-1]["content"]
        for key, val in self.mapping.items():
            if key in user:
                return {"content": val, "usage": dict(self.usage)}
        return {"content": self.default, "usage": dict(self.usage)}


class BoomLLM:
    async def chat_full(self, messages, settings):
        raise RuntimeError("boom")


def _fake_retrieve(tracker=None, cards=None):
    """构造 retrieve_for_section 替身，记录 kwargs 并返回固定卡片。"""
    _cards = cards if cards is not None else [
        {"content": "证据文本", "source_type": "knowledge",
         "source_title": "doc.pdf", "source_from": "doc.pdf p.1"},
    ]

    async def _inner(**kwargs):
        if tracker is not None:
            tracker.append(kwargs)
        return RetrievalResult(cards=list(_cards)), dict(_RETRIEVE_USAGE)

    return _inner


# ── implicit_project_vars ───────────────────────────────────


class TestImplicitProjectVars:
    def test_maps_project_fields(self):
        out = implicit_project_vars({
            "company_name": "华为", "credit_code": "91X", "name": "华为项目",
            "type": "尽调", "stage": "A轮",
        })
        assert out == {
            "company_name": "华为", "credit_code": "91X", "project_name": "华为项目",
            "project_type": "尽调", "stage": "A轮",
        }

    def test_none_project_all_empty(self):
        assert implicit_project_vars(None) == {
            "company_name": "", "credit_code": "", "project_name": "",
            "project_type": "", "stage": "",
        }

    def test_missing_fields_default_empty(self):
        out = implicit_project_vars({"company_name": "华为"})
        assert out["company_name"] == "华为"
        assert out["project_name"] == ""
        assert out["stage"] == ""


# ── _topo_sort ──────────────────────────────────────────────


class TestTopoSort:
    def test_respects_dependency_order(self):
        derived = [
            {"name": "c", "depends_on": ["a", "b"]},
            {"name": "a", "depends_on": []},
            {"name": "b", "depends_on": ["a"]},
        ]
        names = [v["name"] for v in _topo_sort(derived)]
        assert names.index("a") < names.index("b") < names.index("c")

    def test_deterministic_sorted_output(self):
        derived = [{"name": "z", "depends_on": []}, {"name": "y", "depends_on": []}]
        names = [v["name"] for v in _topo_sort(derived)]
        assert names == ["y", "z"]  # 入度同为 0，按名排序

    def test_cycle_raises(self):
        with pytest.raises(VariableResolutionError):
            _topo_sort([
                {"name": "a", "depends_on": ["b"]},
                {"name": "b", "depends_on": ["a"]},
            ])

    def test_ignores_non_derived_deps(self):
        # company_name 是隐式变量，不在 derived 集内 → 不构成边
        order = _topo_sort([{"name": "x", "depends_on": ["company_name"]}])
        assert [v["name"] for v in order] == ["x"]


# ── _parse_list_value ───────────────────────────────────────


class TestParseListValue:
    def test_json_array(self):
        assert _parse_list_value('["甲", "乙"]') == ["甲", "乙"]

    def test_json_array_filters_empty_items(self):
        assert _parse_list_value('["甲", "", "乙"]') == ["甲", "乙"]

    def test_markdown_code_block(self):
        assert _parse_list_value('```json\n["甲", "乙"]\n```') == ["甲", "乙"]

    def test_line_split_fallback_dash(self):
        assert _parse_list_value("- 甲\n- 乙") == ["甲", "乙"]

    def test_line_split_fallback_numbered(self):
        assert _parse_list_value("1. 甲\n2. 乙") == ["甲", "乙"]

    def test_empty_returns_unresolved(self):
        assert _parse_list_value("") is UNRESOLVED
        assert _parse_list_value("   ") is UNRESOLVED

    def test_empty_array_returns_unresolved(self):
        assert _parse_list_value("[]") is UNRESOLVED


# ── _render_deps ────────────────────────────────────────────


class TestRenderDeps:
    def test_empty(self):
        assert _render_deps({}) == ""

    def test_scalar(self):
        assert _render_deps({"a": "华为"}) == "a: 华为"

    def test_multiple_preserves_order(self):
        assert _render_deps({"a": "华为", "b": "通信"}) == "a: 华为；b: 通信"

    def test_list_joined(self):
        assert _render_deps({"a": ["x", "y"]}) == "a: x、y"

    def test_unresolved_skipped(self):
        assert _render_deps({"a": None, "b": "通信"}) == "b: 通信"


# ── _extract_value（async）──────────────────────────────────


class TestExtractValue:
    def test_scalar_strips(self):
        llm = FakeLLM(default="  通信设备  ")
        val, usage = asyncio.run(_extract_value("h", {}, "ev", "scalar", llm, {}))
        assert val == "通信设备"
        assert usage["total_tokens"] == 8

    def test_scalar_empty_unresolved(self):
        llm = FakeLLM(default="   ")
        val, _ = asyncio.run(_extract_value("h", {}, "ev", "scalar", llm, {}))
        assert val is UNRESOLVED

    def test_list_parses(self):
        llm = FakeLLM(default='["甲", "乙"]')
        val, _ = asyncio.run(_extract_value("h", {}, "ev", "list", llm, {}))
        assert val == ["甲", "乙"]

    def test_llm_exception_unresolved(self):
        val, usage = asyncio.run(_extract_value("h", {}, "ev", "scalar", BoomLLM(), {}))
        assert val is UNRESOLVED
        assert usage["total_tokens"] == 0


# ── resolve_variables（async）───────────────────────────────


class TestResolveVariables:
    def _template(self):
        return {
            "report_depth": "full",
            "variables": [
                {"name": "focus", "source": "user", "label": "关注点"},
                {"name": "industry", "source": "derived", "label": "行业",
                 "depends_on": ["company_name"], "derive_hint": "判断所属行业",
                 "data_sources": ["knowledge"], "cardinality": "scalar"},
                {"name": "peers", "source": "derived", "label": "竞对",
                 "depends_on": ["industry"], "derive_hint": "列出竞争对手",
                 "data_sources": ["knowledge"], "cardinality": "list"},
            ],
        }

    def _project(self):
        return {"id": "p1", "company_name": "华为", "name": "华为项目",
                "type": "尽调", "stage": "A轮", "credit_code": "91X"}

    def test_full_flow(self):
        tracker = []
        llm = FakeLLM(mapping={"判断所属行业": "通信设备", "列出竞争对手": '["中兴", "爱立信"]'})
        with patch("report_generator.variable_resolver.retrieve_for_section",
                   _fake_retrieve(tracker)):
            resolved, evidence, usage = asyncio.run(resolve_variables(
                self._template(), self._project(), {"focus": "技术壁垒"},
                search_tool=MagicMock(), llm_client=llm, llm_settings={}, db=None,
            ))
        # 隐式变量
        assert resolved["company_name"] == "华为"
        assert resolved["project_name"] == "华为项目"
        assert resolved["stage"] == "A轮"
        # user 变量
        assert resolved["focus"] == "技术壁垒"
        # derived 变量（scalar / list）
        assert resolved["industry"] == "通信设备"
        assert resolved["peers"] == ["中兴", "爱立信"]
        # 拓扑序：industry 先于 peers
        assert [c["section"]["section_id"] for c in tracker] == ["industry", "peers"]
        # 检索参数透传
        assert tracker[0]["data_sources"] == ["knowledge"]
        assert tracker[0]["report_depth"] == "full"
        # 证据留存
        assert set(evidence.keys()) == {"industry", "peers"}
        assert evidence["industry"][0]["source_title"] == "doc.pdf"
        # usage 累计：2 检索(2) + 2 抽值(8) = 20
        assert usage["total_tokens"] == 20

    def test_downstream_degradation_on_missing_user_dep(self):
        """声明的 user 变量缺失 → UNRESOLVED；依赖它的 derived 连锁降级，且不触发检索。"""
        template = {"variables": [
            {"name": "region", "source": "user"},
            {"name": "city", "source": "derived", "depends_on": ["region"],
             "derive_hint": "分析城市", "cardinality": "scalar", "data_sources": ["knowledge"]},
        ]}
        tracker = []
        llm = FakeLLM(default="不应被调用")
        with patch("report_generator.variable_resolver.retrieve_for_section",
                   _fake_retrieve(tracker)):
            resolved, evidence, usage = asyncio.run(resolve_variables(
                template, {}, {},  # region 未提供
                search_tool=MagicMock(), llm_client=llm, llm_settings={},
            ))
        assert resolved["region"] is UNRESOLVED
        assert resolved["city"] is UNRESOLVED
        assert tracker == []  # 降级路径不检索
        assert usage["total_tokens"] == 0

    def test_derived_value_from_evidence_extract(self):
        """抽值取自 LLM，证据文本进入 extract prompt。"""
        template = {"variables": [
            {"name": "sector", "source": "derived", "depends_on": [],
             "derive_hint": "判断赛道", "cardinality": "scalar", "data_sources": ["knowledge"]},
        ]}
        llm = FakeLLM(default="半导体")
        with patch("report_generator.variable_resolver.retrieve_for_section",
                   _fake_retrieve(None, cards=[{"content": "芯片证据", "source_type": "knowledge",
                                                "source_title": "t", "source_from": "f"}])):
            resolved, evidence, _ = asyncio.run(resolve_variables(
                template, {}, {}, search_tool=MagicMock(), llm_client=llm, llm_settings={},
            ))
        assert resolved["sector"] == "半导体"
        # extract prompt 的 user 消息含检索证据
        assert any("芯片证据" in m[-1]["content"] for m in llm.calls)
        assert evidence["sector"][0]["content"] == "芯片证据"

    def test_retrieve_failure_degrades(self):
        """检索抛异常 → 该 derived 变量降级 UNRESOLVED，不中断整体解析。"""
        template = {"variables": [
            {"name": "x", "source": "derived", "depends_on": [],
             "derive_hint": "h", "cardinality": "scalar", "data_sources": ["knowledge"]},
        ]}

        async def _boom(**kwargs):
            raise RuntimeError("network down")

        llm = FakeLLM(default="unused")
        with patch("report_generator.variable_resolver.retrieve_for_section", _boom):
            resolved, _, _ = asyncio.run(resolve_variables(
                template, {}, {}, search_tool=MagicMock(), llm_client=llm, llm_settings={},
            ))
        assert resolved["x"] is UNRESOLVED

    def test_cycle_raises_through_resolve(self):
        template = {"variables": [
            {"name": "a", "source": "derived", "depends_on": ["b"],
             "derive_hint": "x", "cardinality": "scalar", "data_sources": ["knowledge"]},
            {"name": "b", "source": "derived", "depends_on": ["a"],
             "derive_hint": "y", "cardinality": "scalar", "data_sources": ["knowledge"]},
        ]}
        with pytest.raises(VariableResolutionError):
            asyncio.run(resolve_variables(
                template, {}, {}, search_tool=MagicMock(), llm_client=FakeLLM(), llm_settings={},
            ))

    def test_all_declared_present_in_ctx(self):
        """未提供值的声明变量也须存在于 ctx（instantiate 声明校验依赖完整性）。"""
        template = {"variables": [
            {"name": "u1", "source": "user"},
            {"name": "u2", "source": "user"},
        ]}
        with patch("report_generator.variable_resolver.retrieve_for_section", _fake_retrieve(None)):
            resolved, _, _ = asyncio.run(resolve_variables(
                template, {}, {"u1": "v1"},
                search_tool=MagicMock(), llm_client=FakeLLM(), llm_settings={},
            ))
        assert resolved["u1"] == "v1"
        assert "u2" in resolved
        assert resolved["u2"] is UNRESOLVED


# ── 报告级互联网总开关 gating（§1.1）────────────────


class TestInternetEnabledGating:
    """internet_enabled=False 时，派生变量强制仅走 knowledge，无视 data_sources 声明。"""

    _TPL = {"variables": [
        {"name": "sector", "source": "derived", "depends_on": [],
         "derive_hint": "判断赛道", "cardinality": "scalar",
         "data_sources": ["knowledge", "internet"]},
    ]}

    def test_master_off_forces_knowledge_only(self):
        tracker = []
        llm = FakeLLM(default="半导体")
        with patch("report_generator.variable_resolver.retrieve_for_section",
                   _fake_retrieve(tracker)):
            asyncio.run(resolve_variables(
                self._TPL, {}, {}, search_tool=MagicMock(), llm_client=llm,
                llm_settings={}, internet_enabled=False,
            ))
        assert len(tracker) == 1
        assert tracker[0]["data_sources"] == ["knowledge"]

    def test_master_on_passes_declared_sources(self):
        tracker = []
        llm = FakeLLM(default="半导体")
        with patch("report_generator.variable_resolver.retrieve_for_section",
                   _fake_retrieve(tracker)):
            asyncio.run(resolve_variables(
                self._TPL, {}, {}, search_tool=MagicMock(), llm_client=llm,
                llm_settings={}, internet_enabled=True,
            ))
        assert len(tracker) == 1
        assert tracker[0]["data_sources"] == ["knowledge", "internet"]

    def test_default_is_enabled(self):
        """不传 internet_enabled 时默认 True（保持向后兼容）。"""
        tracker = []
        llm = FakeLLM(default="半导体")
        with patch("report_generator.variable_resolver.retrieve_for_section",
                   _fake_retrieve(tracker)):
            asyncio.run(resolve_variables(
                self._TPL, {}, {}, search_tool=MagicMock(), llm_client=llm,
                llm_settings={},
            ))
        assert tracker[0]["data_sources"] == ["knowledge", "internet"]
