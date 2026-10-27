"""python/tests/test_renderer_citations.py — 视觉尾注引用上标单测（2026-10-27）。

覆盖 renderer.apply_endnote_superscript 的转换契约（与前端
ReportPreview.applyCitationSuperscript 同规则、按引擎各转一次）：
  - 正文 [n] → pandoc 上标 ^[n]^
  - 行首 [n]（文末「参考来源」列表条目自身）不转
  - [n]( 链接形态不转；[[n]] 嵌套不转
  - 相邻引用 [40][41] 逐个转换
"""
import os
import sys

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from report_generator.renderer import apply_endnote_superscript  # noqa: E402


class TestApplyEndnoteSuperscript:
    def test_inline_citation_to_superscript(self):
        assert apply_endnote_superscript("增速领先的预测[25]，口径二[26]。") == (
            "增速领先的预测^[25]^，口径二^[26]^。"
        )

    def test_line_start_citation_kept(self):
        """「参考来源」列表条目（行首 [n]）保持原样。"""
        text = "## 参考来源\n\n[1] 《来源A》: 内容\n\n[2] 《来源B》: 内容"
        assert apply_endnote_superscript(text) == text

    def test_adjacent_citations_both_converted(self):
        assert apply_endnote_superscript("份额约31.6%[39][18]。") == "份额约31.6%^[39]^^[18]^。"

    def test_markdown_link_not_converted(self):
        """[n](url) 链接形态非引用，保持原样。"""
        text = "见[1](https://example.com)说明"
        assert apply_endnote_superscript(text) == text

    def test_nested_brackets_not_converted(self):
        """[[n]] 嵌套方括号（wiki 链接 key 形态）不转。"""
        text = "参见[[123]]页"
        assert apply_endnote_superscript(text) == text

    def test_multiline_mixed(self):
        """同文中正文转换、行首列表不转（MULTILINE 行首判定）。"""
        text = "结论依据材料[3]与[4]。\n\n[3] 《来源C》: 摘要\n[4] 《来源D》: 摘要"
        out = apply_endnote_superscript(text)
        assert "依据材料^[3]^与^[4]^。" in out
        assert "\n[3] 《来源C》" in out
        assert "\n[4] 《来源D》" in out

    def test_no_citation_passthrough(self):
        text = "无引用的普通正文。"
        assert apply_endnote_superscript(text) == text
