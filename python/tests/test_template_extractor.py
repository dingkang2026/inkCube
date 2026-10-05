"""python/tests/test_template_extractor.py — 模板提取器 variables 草稿清洗单测（§2.3.2）。

覆盖 _sanitize_variables：
  - 非列表 / 空 → []
  - name 缺失/空/重复 → 过滤去重
  - source 非法 → 默认 user
  - user：required 默认 True、placeholder 仅留非空字符串
  - derived：depends_on 剔除悬空依赖（仅留已声明 + 隐式）、排除自依赖、去重保序
  - derived：data_sources 非法/为空 → ["knowledge"]、去重；cardinality 非法 → scalar
  - 仅输出 schema 允许字段（additionalProperties=false）
"""
import os
import sys

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from report_generator.template_extractor import (  # noqa: E402
    IMPLICIT_VARS,
    _sanitize_variables,
)


class TestSanitizeVariablesBasic:
    def test_non_list_returns_empty(self):
        assert _sanitize_variables(None) == []
        assert _sanitize_variables("x") == []
        assert _sanitize_variables({"name": "a"}) == []

    def test_empty_list(self):
        assert _sanitize_variables([]) == []

    def test_drops_non_dict_items(self):
        assert _sanitize_variables([None, "x", 42]) == []

    def test_drops_missing_or_blank_name(self):
        assert _sanitize_variables([{"source": "user"}, {"name": "  ", "source": "user"}]) == []

    def test_dedup_keeps_first(self):
        out = _sanitize_variables([
            {"name": "a", "source": "user", "label": "第一"},
            {"name": "a", "source": "user", "label": "第二"},
        ])
        assert len(out) == 1
        assert out[0]["label"] == "第一"

    def test_invalid_source_defaults_user(self):
        out = _sanitize_variables([{"name": "a", "source": "weird"}])
        assert out[0]["source"] == "user"

    def test_label_defaults_to_name(self):
        out = _sanitize_variables([{"name": "行业", "source": "user"}])
        assert out[0]["label"] == "行业"


class TestSanitizeUserVariable:
    def test_required_defaults_true(self):
        out = _sanitize_variables([{"name": "a", "source": "user"}])
        assert out[0]["required"] is True

    def test_required_false_preserved(self):
        out = _sanitize_variables([{"name": "a", "source": "user", "required": False}])
        assert out[0]["required"] is False

    def test_placeholder_kept_when_nonempty(self):
        out = _sanitize_variables([{"name": "a", "source": "user", "placeholder": "如：光伏"}])
        assert out[0]["placeholder"] == "如：光伏"

    def test_placeholder_dropped_when_blank(self):
        out = _sanitize_variables([{"name": "a", "source": "user", "placeholder": "   "}])
        assert "placeholder" not in out[0]

    def test_no_derived_fields_on_user(self):
        out = _sanitize_variables([{"name": "a", "source": "user"}])
        for key in ("depends_on", "derive_hint", "data_sources", "cardinality"):
            assert key not in out[0]


class TestSanitizeDerivedVariable:
    def test_depends_on_keeps_declared_and_implicit(self):
        out = _sanitize_variables([
            {"name": "产业领域", "source": "derived", "depends_on": ["company_name"],
             "derive_hint": "判定领域", "data_sources": ["knowledge"], "cardinality": "scalar"},
            {"name": "竞争对手", "source": "derived", "depends_on": ["产业领域"],
             "derive_hint": "列出竞对", "data_sources": ["knowledge"], "cardinality": "list"},
        ])
        by_name = {v["name"]: v for v in out}
        assert by_name["产业领域"]["depends_on"] == ["company_name"]
        assert by_name["竞争对手"]["depends_on"] == ["产业领域"]

    def test_depends_on_drops_dangling(self):
        out = _sanitize_variables([
            {"name": "x", "source": "derived", "depends_on": ["不存在的变量", "company_name"],
             "derive_hint": "h", "data_sources": ["knowledge"]},
        ])
        assert out[0]["depends_on"] == ["company_name"]

    def test_depends_on_drops_self_reference(self):
        out = _sanitize_variables([
            {"name": "x", "source": "derived", "depends_on": ["x"],
             "derive_hint": "h", "data_sources": ["knowledge"]},
        ])
        assert out[0]["depends_on"] == []

    def test_depends_on_dedup_preserves_order(self):
        out = _sanitize_variables([
            {"name": "x", "source": "derived",
             "depends_on": ["company_name", "stage", "company_name"],
             "derive_hint": "h", "data_sources": ["knowledge"]},
        ])
        assert out[0]["depends_on"] == ["company_name", "stage"]

    def test_derive_hint_missing_becomes_empty(self):
        out = _sanitize_variables([
            {"name": "x", "source": "derived", "data_sources": ["knowledge"]},
        ])
        assert out[0]["derive_hint"] == ""

    def test_data_sources_empty_defaults_knowledge(self):
        out = _sanitize_variables([
            {"name": "x", "source": "derived", "derive_hint": "h", "data_sources": []},
        ])
        assert out[0]["data_sources"] == ["knowledge"]

    def test_data_sources_invalid_filtered(self):
        out = _sanitize_variables([
            {"name": "x", "source": "derived", "derive_hint": "h",
             "data_sources": ["knowledge", "web", "internet"]},
        ])
        assert out[0]["data_sources"] == ["knowledge", "internet"]

    def test_data_sources_all_invalid_defaults_knowledge(self):
        out = _sanitize_variables([
            {"name": "x", "source": "derived", "derive_hint": "h", "data_sources": ["web"]},
        ])
        assert out[0]["data_sources"] == ["knowledge"]

    def test_cardinality_invalid_defaults_scalar(self):
        out = _sanitize_variables([
            {"name": "x", "source": "derived", "derive_hint": "h",
             "data_sources": ["knowledge"], "cardinality": "tuple"},
        ])
        assert out[0]["cardinality"] == "scalar"

    def test_cardinality_list_preserved(self):
        out = _sanitize_variables([
            {"name": "x", "source": "derived", "derive_hint": "h",
             "data_sources": ["knowledge"], "cardinality": "list"},
        ])
        assert out[0]["cardinality"] == "list"

    def test_no_user_fields_on_derived(self):
        out = _sanitize_variables([
            {"name": "x", "source": "derived", "derive_hint": "h", "data_sources": ["knowledge"]},
        ])
        for key in ("required", "placeholder"):
            assert key not in out[0]


class TestSanitizeSchemaCompliance:
    """清洗输出仅含 _schema.json $defs/variable 允许的字段（additionalProperties=false）。"""

    _ALLOWED = {
        "name", "source", "label", "required", "placeholder",
        "depends_on", "derive_hint", "data_sources", "cardinality",
    }

    def test_only_allowed_keys_emitted(self):
        out = _sanitize_variables([
            {"name": "u", "source": "user", "bogus": 1, "label": "U"},
            {"name": "d", "source": "derived", "derive_hint": "h",
             "data_sources": ["knowledge"], "extra": "x"},
        ])
        for v in out:
            assert set(v.keys()) <= self._ALLOWED

    def test_implicit_vars_constant(self):
        assert "company_name" in IMPLICIT_VARS
        assert "stage" in IMPLICIT_VARS
