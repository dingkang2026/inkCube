"""python/tests/test_normalizer.py — 归一化层单测（§3.1.1 / §3.2）。

覆盖：
  - instantiate：v2 结构化变量不报错、list 顿号连接注入、None→空串、
    未声明变量报错、含引号/换行值转义、大纲结构不因 list 增删节
  - normalize_outline / _fold_node：多级折叠、prompt_hint 附加、默认写作要求
  - validate_execution_outline：section_id 校验、缺失/重复/非法 compose_mode/post_body children
  - get_execution_sections：两级/无子节提取
"""
import os
import sys

import pytest

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from report_generator.normalizer import (  # noqa: E402
    _fold_node,
    get_execution_sections,
    instantiate,
    normalize_outline,
    validate_execution_outline,
)


# ── instantiate ─────────────────────────────────────────────


class TestInstantiate:
    def test_v2_structured_variables_no_typeerror(self):
        """v2 variables 为结构化 dict 列表，instantiate 不得 TypeError（旧 set(list[dict]) bug）。"""
        template = {
            "template_name": "尽调报告",
            "variables": [
                {"name": "company_name", "source": "user", "label": "公司名"},
                {"name": "industry", "source": "derived", "label": "行业",
                 "depends_on": ["company_name"], "derive_hint": "判断行业",
                 "data_sources": ["knowledge"], "cardinality": "scalar"},
            ],
            "outline": [
                {"section_id": "ch1", "title": "{{company_name}}概况", "children": [
                    {"section_id": "s1", "title": "基本信息",
                     "description": "介绍{{company_name}}与{{industry}}"},
                ]},
            ],
        }
        resolved = {"company_name": "华为", "industry": "通信"}
        out = instantiate(template, resolved)
        assert out["outline"][0]["title"] == "华为概况"
        assert out["outline"][0]["children"][0]["description"] == "介绍华为与通信"
        # 原模板不被修改（深拷贝）
        assert template["outline"][0]["title"] == "{{company_name}}概况"

    def test_list_value_joined_with_dunhao(self):
        """list 派生变量（如竞对名单）以顿号「、」连接注入正文（§2.1.7）。"""
        template = {"outline": [{"section_id": "s1", "title": "竞争：{{peers}}"}]}
        resolved = {"peers": ["A", "B", "C"]}
        out = instantiate(template, resolved)
        assert out["outline"][0]["title"] == "竞争：A、B、C"
        # 不得注入 Python repr
        assert "['A'" not in out["outline"][0]["title"]

    def test_list_filters_none_and_empty_items(self):
        """list 连接时过滤 None/空串项，避免顿号堆叠。"""
        template = {"outline": [{"section_id": "s1", "title": "{{peers}}"}]}
        resolved = {"peers": ["A", None, "", "B"]}
        out = instantiate(template, resolved)
        assert out["outline"][0]["title"] == "A、B"

    def test_empty_list_becomes_empty_string(self):
        """空 list 注入为空串（降级，正文不残留 {{}}）。"""
        template = {"outline": [{"section_id": "s1", "title": "竞争：{{peers}}。"}]}
        resolved = {"peers": []}
        out = instantiate(template, resolved)
        assert out["outline"][0]["title"] == "竞争：。"

    def test_list_injection_keeps_outline_structure(self):
        """list 注入不增删大纲节点：节点数恒定（不做 foreach 展开）。"""
        template = {"outline": [
            {"section_id": "ch4", "title": "竞对分析：{{peers}}"},
            {"section_id": "ch5", "title": "结论"},
        ]}
        resolved = {"peers": ["A", "B", "C", "D"]}
        out = instantiate(template, resolved)
        assert len(out["outline"]) == 2
        assert [n["section_id"] for n in out["outline"]] == ["ch4", "ch5"]
        assert out["outline"][0]["title"] == "竞对分析：A、B、C、D"

    def test_none_becomes_empty_string(self):
        """UNRESOLVED（None）替换为空串，避免正文残留 {{}}。"""
        template = {"outline": [{"section_id": "s1", "title": "阶段：{{stage}}。"}]}
        resolved = {"stage": None}
        out = instantiate(template, resolved)
        assert out["outline"][0]["title"] == "阶段：。"

    def test_undeclared_variable_raises(self):
        template = {"outline": [{"section_id": "s1", "title": "{{unknown}}"}]}
        with pytest.raises(ValueError, match="未声明"):
            instantiate(template, {})

    def test_value_with_quotes_and_newlines_escaped(self):
        """含引号/换行的值经 JSON 转义后 round-trip 不破坏结构。"""
        template = {"outline": [{"section_id": "s1", "title": "{{q}}"}]}
        value = 'he said "hi"\nsecond line'
        out = instantiate(template, {"q": value})
        assert out["outline"][0]["title"] == value


# ── normalize_outline / _fold_node ──────────────────────────


class TestNormalizeOutline:
    def test_fold_children_into_writing_requirements(self):
        outline = [{"section_id": "ch1", "title": "第一章", "children": [
            {"section_id": "s1", "title": "市场", "children": [
                {"section_id": "ss1", "title": "规模", "prompt_hint": "给出TAM/SAM"},
                {"section_id": "ss2", "title": "增速"},
            ]},
        ]}]
        out = normalize_outline(outline)
        sec = out[0]["children"][0]
        reqs = sec["writing_requirements"]
        assert "须覆盖：规模（给出TAM/SAM）" in reqs
        assert "须覆盖：增速" in reqs
        # 三级 children 已折叠移除
        assert "children" not in sec

    def test_leaf_section_default_requirement(self):
        outline = [{"section_id": "ch", "title": "章",
                    "children": [{"section_id": "s", "title": "节"}]}]
        out = normalize_outline(outline)
        assert out[0]["children"][0]["writing_requirements"] == ["须覆盖：节"]

    def test_chapter_without_children_unchanged(self):
        outline = [{"section_id": "ch", "title": "章"}]
        out = normalize_outline(outline)
        assert out[0] == {"section_id": "ch", "title": "章"}
        assert "writing_requirements" not in out[0]

    def test_fold_node_deep_chain(self):
        node = {"title": "A", "children": [{"title": "B", "children": [{"title": "C"}]}]}
        assert _fold_node(node) == "须覆盖：A——B——C"

    def test_fold_node_with_prompt_hint(self):
        node = {"title": "规模", "prompt_hint": "给出TAM"}
        assert _fold_node(node) == "须覆盖：规模（给出TAM）"


# ── validate_execution_outline ──────────────────────────────


class TestValidateExecutionOutline:
    def test_valid_outline_passes(self):
        outline = [{"section_id": "ch1", "title": "章", "children": [
            {"section_id": "s1", "title": "节1", "compose_mode": "standard"},
            {"section_id": "s2", "title": "节2"},  # 默认 standard
        ]}]
        assert validate_execution_outline(outline) == []

    def test_chapter_missing_section_id(self):
        outline = [{"title": "章", "children": [{"section_id": "s1", "title": "节"}]}]
        errs = validate_execution_outline(outline)
        assert any("Chapter missing 'section_id'" in e for e in errs)

    def test_section_missing_section_id(self):
        outline = [{"section_id": "ch1", "title": "章", "children": [{"title": "节"}]}]
        errs = validate_execution_outline(outline)
        assert any("Section missing 'section_id'" in e for e in errs)

    def test_section_missing_title(self):
        outline = [{"section_id": "ch1", "title": "章",
                    "children": [{"section_id": "s1"}]}]
        errs = validate_execution_outline(outline)
        assert any("missing 'title'" in e for e in errs)

    def test_duplicate_section_id(self):
        outline = [{"section_id": "ch1", "title": "章", "children": [
            {"section_id": "dup", "title": "a"},
            {"section_id": "dup", "title": "b"},
        ]}]
        errs = validate_execution_outline(outline)
        assert any("Duplicate section id: dup" in e for e in errs)

    def test_invalid_compose_mode(self):
        outline = [{"section_id": "ch1", "title": "章", "children": [
            {"section_id": "s1", "title": "节", "compose_mode": "weird"},
        ]}]
        errs = validate_execution_outline(outline)
        assert any("invalid compose_mode: weird" in e for e in errs)

    def test_post_body_must_not_have_children(self):
        outline = [{"section_id": "ch1", "title": "章", "children": [
            {"section_id": "s1", "title": "摘要", "compose_mode": "post_body",
             "children": [{"section_id": "x", "title": "y"}]},
        ]}]
        errs = validate_execution_outline(outline)
        assert any("must not have children" in e for e in errs)


# ── get_execution_sections ──────────────────────────────────


class TestGetExecutionSections:
    def test_mixed_chapters(self):
        outline = [
            {"section_id": "ch1", "title": "章1",
             "children": [{"section_id": "s1", "title": "节1"}]},
            {"section_id": "ch2", "title": "章2"},  # 无子节 → 自身即节
        ]
        secs = get_execution_sections(outline)
        assert len(secs) == 2
        assert secs[0]["section_id"] == "s1"
        assert secs[0]["_chapter_title"] == "章1"
        assert secs[1]["section_id"] == "ch2"
        assert secs[1]["_chapter_title"] == "章2"
