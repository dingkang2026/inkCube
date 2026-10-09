"""python/tests/test_validator.py — 校验层单测（§3.6）。

覆盖：
  - Rule 3：目标字数由 report_depth 推算（full=1200），±20% 偏差触发；
    brief 深度豁免、post_body 节豁免；v1 的 word_count/must_cite 字段不再被读取
  - Rule 1/2/4/5/7 基本触发
  - validate_global：幻觉引用 / 未引用 / 编号不连续
  - fix_hallucinated_refs / _extract_refs
"""
import os
import sys
from unittest.mock import MagicMock

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from report_generator.validator import (  # noqa: E402
    _extract_refs,
    fix_hallucinated_refs,
    validate_global,
    validate_section,
)


def _benign(n: int) -> str:
    """构造长度为 n、不触发其它规则的纯正文。"""
    return "数" * n


_STD = {"section_id": "s1", "title": "T", "compose_mode": "standard"}


def _rules(output, section, depth="full", given=None, sparse=False, floor=0.8):
    return [v.rule for v in validate_section(
        output, given or set(), section,
        report_depth=depth, is_sparse=sparse, word_floor_ratio=floor,
    )]


# ── Rule 3：depth 化目标字数 ────────────────────────────────


class TestRule3DepthTarget:
    def test_full_at_target_no_violation(self):
        assert 3 not in _rules(_benign(1200), _STD, "full")

    def test_full_too_short(self):
        assert 3 in _rules(_benign(100), _STD, "full")

    def test_full_too_long(self):
        assert 3 in _rules(_benign(2000), _STD, "full")

    def test_full_lower_boundary(self):
        # 0.8 * 1200 = 960 → 恰好达标；959 → 偏差
        assert 3 not in _rules(_benign(960), _STD, "full")
        assert 3 in _rules(_benign(959), _STD, "full")

    def test_full_upper_boundary(self):
        # 1.2 * 1200 = 1440 → 恰好达标；1441 → 偏差
        assert 3 not in _rules(_benign(1440), _STD, "full")
        assert 3 in _rules(_benign(1441), _STD, "full")

    def test_brief_depth_exempt(self):
        """brief 深度不启用 Rule 3（§3.5 200-500 字与 §3.6 目标冲突）。"""
        assert 3 not in _rules(_benign(100), _STD, "brief")

    def test_post_body_exempt(self):
        """post_body（摘要/结论）天然短小，豁免 Rule 3。"""
        pb = {"section_id": "sum", "title": "摘要", "compose_mode": "post_body"}
        assert 3 not in _rules(_benign(100), pb, "full")

    def test_default_depth_is_full(self):
        v = validate_section(_benign(100), set(), _STD)  # 不传 report_depth
        assert 3 in [x.rule for x in v]

    def test_v1_word_count_field_ignored(self):
        """Rule 3 目标由 depth 推算，不读取 section 的 v1 word_count。"""
        sec_v1 = {"section_id": "s", "title": "T", "compose_mode": "standard", "word_count": 500}
        # 若误用 word_count=500，1200/500=240% 会触发；实际用 1200 → 不触发
        assert 3 not in _rules(_benign(1200), sec_v1, "full")

    def test_v1_must_cite_field_ignored(self):
        """must_cite（v1 死字段）不再产生任何违规：达标正文 + must_cite 应零违规。"""
        sec_v1 = {"section_id": "s", "title": "T", "compose_mode": "standard", "must_cite": [99]}
        violations = validate_section(_benign(1200), set(), sec_v1, "full")
        assert violations == []
        assert all("must_cite" not in v.message for v in violations)


# ── Rule 2 豁免区（表格行 / 围栏代码块）─────────────────────


class TestRule2Exemptions:
    def test_bold_in_table_cell_exempt(self):
        """表格单元格内粗体是合法强调形式（§3.5 形式规范），不触发规则 2。"""
        out = _benign(1200) + "\n| 指标 | 数值 |\n|---|---|\n| **营收** | 100亿 |\n"
        assert 2 not in _rules(out, _STD, "full")

    def test_bold_in_fenced_code_exempt(self):
        """围栏代码块（mermaid/LaTeX 源码等）内的 ** 不参与加粗判定。"""
        out = _benign(1200) + "\n```\n**not bold in code**\n```\n"
        assert 2 not in _rules(out, _STD, "full")

    def test_heading_in_fenced_code_exempt(self):
        """围栏代码块内的 # 注释行不参与标题判定（与加粗检查共用豁免区）。"""
        out = _benign(1200) + "\n```\n# not a heading in code\n```\n"
        assert 2 not in _rules(out, _STD, "full")

    def test_bold_outside_table_still_triggers(self):
        """豁免区之外的正文加粗仍触发规则 2。"""
        out = _benign(600) + "**重点**" + _benign(600) + "\n| a | b |\n"
        assert 2 in _rules(out, _STD, "full")


# ── Rule 3：sparse / target_words / 下限放宽 ────────────────


class TestRule3SparseAndTargetWords:
    def test_sparse_skips_rule3(self):
        """sparse 降级节（零材料分析推理模式）字数不校验（§3.6）。"""
        assert 3 not in _rules(_benign(100), _STD, "full", sparse=True)

    def test_target_words_overrides_default(self):
        """节级 target_words 覆盖全局 1200（模板篇幅护栏，§2.1.3/§3.6）。"""
        sec = {**_STD, "target_words": 600}
        # 600±20% → 480-720：700 达标、1200 超标
        assert 3 not in _rules(_benign(700), sec, "full")
        assert 3 in _rules(_benign(1200), sec, "full")

    def test_insufficient_floor_relaxed(self):
        """insufficient 材料有限：下限放宽至 0.6（720 达标；默认 0.8 下触发）。"""
        assert 3 not in _rules(_benign(720), _STD, "full", floor=0.6)
        assert 3 in _rules(_benign(720), _STD, "full")


# ── 其它单节规则 ────────────────────────────────────────────


class TestOtherRules:
    def test_rule1_hallucinated_ref(self):
        assert 1 in _rules("某结论[5]。", _STD, "full", given={1, 2})

    def test_rule1_subset_ok(self):
        assert 1 not in _rules("某结论[1]。", _STD, "full", given={1, 2})

    def test_rule2_markdown_heading(self):
        assert 2 in _rules("## 小标题\n" + _benign(1200), _STD, "full")

    def test_rule2_bold(self):
        assert 2 in _rules(_benign(600) + "**重点**" + _benign(600), _STD, "full")

    def test_rule4_subhead_too_long(self):
        out = _benign(500) + "\n◆ 这是一个非常长的小标题超过十五字\n" + _benign(500)
        assert 4 in _rules(out, _STD, "full")

    def test_rule4_subhead_ok(self):
        out = _benign(600) + "\n◆ 短标题\n" + _benign(600)
        assert 4 not in _rules(out, _STD, "full")

    def test_rule5_ending_cliche(self):
        assert 5 in _rules(_benign(1190) + "综上所述。", _STD, "full")

    def test_rule7_first_line_heading(self):
        assert 7 in _rules("# 大标题\n" + _benign(1200), _STD, "full")

    def test_rule7_preamble(self):
        assert 7 in _rules("本节将介绍市场规模。\n" + _benign(1200), _STD, "full")


# ── 全局校验 ────────────────────────────────────────────────


class TestValidateGlobal:
    def test_uncited_refs(self):
        reg = MagicMock()
        reg.all_ref_numbers.return_value = [1, 2]
        issues = validate_global("引用[1]。", reg)
        assert any(i["type"] == "uncited_refs" for i in issues)

    def test_hallucinated_refs(self):
        reg = MagicMock()
        reg.all_ref_numbers.return_value = [1]
        issues = validate_global("引用[1][5]。", reg)
        assert any(i["type"] == "hallucinated_refs" for i in issues)

    def test_gap_in_numbering(self):
        reg = MagicMock()
        reg.all_ref_numbers.return_value = [1, 3]
        issues = validate_global("[1][3]", reg)
        assert any(i["type"] == "gap_in_numbering" for i in issues)

    def test_clean_passes(self):
        reg = MagicMock()
        reg.all_ref_numbers.return_value = [1, 2]
        assert validate_global("[1][2]", reg) == []


# ── 辅助函数 ────────────────────────────────────────────────


class TestHelpers:
    def test_extract_refs(self):
        assert _extract_refs("见[1]与[2][3]。") == [1, 2, 3]

    def test_extract_refs_empty(self):
        assert _extract_refs("无引用") == []

    def test_fix_hallucinated_refs(self):
        out = fix_hallucinated_refs("结论[1]与[9]。", valid_refs={1})
        assert "[1]" in out
        assert "[9]" not in out
