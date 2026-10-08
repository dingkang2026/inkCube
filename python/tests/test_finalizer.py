"""python/tests/test_finalizer.py — 全局收尾单测（§3.7）。

回归覆盖：
  - B3：_build_toc 必须按装配后正文的 ## 标题出现顺序生成目录，
    而非大纲原序（post_body(front) 前置重排后目录 ≡ 正文顺序）。
  - B4：_smooth_transitions 不得破坏“## 标题独立成行”不变量——
    节尾 --- 分隔符与下一节标题粘连（---## xxx）会导致目录
    只剩首节且 Markdown 渲染破坏。
"""
import os
import re
import sys

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from report_generator.finalizer import (  # noqa: E402
    _build_toc,
    _smooth_transitions,
    finalize_report,
)
from report_generator.registry import CitationRegistry  # noqa: E402


class TestBuildToc:
    def test_toc_follows_body_order(self):
        """目录顺序严格等于正文 ## 标题出现顺序。"""
        text = (
            "## 执行摘要\n\n摘要正文\n\n"
            "## 市场分析\n\n市场正文\n\n"
            "## 结论\n\n结论正文\n"
        )
        toc = _build_toc(text)
        lines = [ln for ln in toc.split("\n") if ln.strip()]
        assert lines[0] == "## 目录"
        assert lines[1:] == ["1. 执行摘要", "2. 市场分析", "3. 结论"]

    def test_toc_front_reorder(self):
        """post_body(front) 前置后摘要在正文最前，目录应随之在前。"""
        text = "## 摘要\n\n...\n\n## 第一章\n\n...\n"
        toc = _build_toc(text)
        assert toc.index("摘要") < toc.index("第一章")

    def test_toc_ignores_non_h2(self):
        """仅 ## 计入目录，# 与 ### 不计入。"""
        text = "# 大标题\n\n## 章节A\n\n### 小节\n\n## 章节B\n"
        toc = _build_toc(text)
        assert "1. 章节A" in toc
        assert "2. 章节B" in toc
        assert "大标题" not in toc
        assert "小节" not in toc

    def test_toc_empty_body(self):
        """无 ## 标题时仅返回目录头。"""
        assert _build_toc("纯正文无标题").strip() == "## 目录"


class TestSmoothTransitions:
    """回归：_smooth_transitions 不得破坏“## 标题独立成行”不变量。"""

    def test_separator_not_glued_to_next_heading(self):
        """节尾 --- 分隔符不得与下一节标题粘连成 ---## xxx。"""
        text = "## 节A\n\n正文A\n\n---\n\n## 节B\n\n正文B"
        out = _smooth_transitions(text)
        assert not re.search(r"^---#", out, flags=re.M)

    def test_all_headings_standalone_in_multi_sections(self):
        """多节 --- 拼接输入经润色后，每个 ## 标题独立成行。"""
        text = "\n\n---\n\n".join(f"## 节{i}\n\n正文{i}" for i in range(5))
        out = _smooth_transitions(text)
        assert re.findall(r"^## (.+)$", out, flags=re.M) == [
            "节0", "节1", "节2", "节3", "节4",
        ]

    def test_noop_is_identity(self):
        """无违规输入润色后逐字不变。"""
        text = "## 节A\n\n正文A\n\n---\n\n## 节B\n\n正文B"
        assert _smooth_transitions(text) == text

    def test_dangling_transition_word_fixed_without_glue(self):
        """规则1：节尾悬空过渡词改写为句号，且不与分隔符/标题粘连。"""
        text = "## 节A\n\n细节如下：\n\n---\n\n## 节B\n\n正文B"
        out = _smooth_transitions(text)
        assert "细节。" in out
        assert not re.search(r"如下：\s*$", out, flags=re.M)
        assert not re.search(r"^---#", out, flags=re.M)

    def test_leading_duplicate_plain_title_removed(self):
        """规则2：节首纯文本重复章节标题被清除。"""
        text = "## 基本信息\n\n基本信息\n\n正文内容"
        out = _smooth_transitions(text)
        assert out.count("## 基本信息") == 1
        assert not re.search(r"^基本信息$", out, flags=re.M)


class TestFinalizeReportE2E:
    """端到端：finalize_report 后标题独立成行、目录完整、尾注齐全。"""

    @staticmethod
    def _make_registry(n: int) -> CitationRegistry:
        reg = CitationRegistry()
        for i in range(n):
            reg.register_card({
                "card_id": f"c{i}",
                "section_id": "s",
                "content": f"内容{i}",
                "source_type": "knowledge",
                "source_title": f"来源{i}",
                "source_from": f"from{i}",
            })
        return reg

    def test_multi_section_toc_complete_and_no_glued_headings(self):
        """多节 + --- 分隔经 finalize_report 后目录条目数 = 章节数。"""
        template = {"template_name": "测试报告"}
        n = 4
        full_text = "\n\n---\n\n".join(
            f"## 章节{i}\n\n正文[{i + 1}]" for i in range(n)
        )
        out = finalize_report(full_text, self._make_registry(n), template)

        # 无 ---## 粘连
        assert not re.search(r"^---#", out, flags=re.M)
        # 目录条目数 = 章节数，且与正文顺序一致
        toc_block = re.search(r"## 目录\n(.*?)\n## ", out, flags=re.S).group(1)
        toc_items = [ln for ln in toc_block.split("\n") if re.match(r"^\d+\. ", ln)]
        assert [ln.split(". ", 1)[1] for ln in toc_items] == [
            f"章节{i}" for i in range(n)
        ]
        # 尾注与正文引用一一对应
        assert "[4] 《来源3》" in out
