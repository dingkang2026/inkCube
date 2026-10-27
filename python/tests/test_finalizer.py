"""python/tests/test_finalizer.py — 全局收尾单测（§3.7）。

回归覆盖：
  - B3：_build_toc 必须按装配后正文的 ##/### 标题出现顺序生成两级目录
    （章条目 + 缩进子节条目，2026-10-27 两级装配），
    而非大纲原序（post_body(front) 前置重排后目录 ≡ 正文顺序）。
  - B4：_smooth_transitions 不得破坏“## 标题独立成行”不变量——
    节尾 --- 分隔符与下一节标题粘连（---## xxx）会导致目录
    只剩首节且 Markdown 渲染破坏。
  - 原规则 2（节首重复标题剥离）已上移至装配层 _assemble_full_text，
    行为用例见 test_assemble_two_level.py。
"""
import os
import re
import sys

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from report_generator.finalizer import (  # noqa: E402
    _build_toc,
    _convert_subheads,
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

    def test_toc_h1_excluded_h3_as_indented_subsections(self):
        """# 不计入目录；### 计入为章条目下的缩进子节条目（两级目录，2026-10-27）。"""
        text = "# 大标题\n\n## 1 章节A\n\n### 1.1 小节\n\n## 2 章节B\n"
        toc = _build_toc(text)
        assert "1. 章节A" in toc
        assert "2. 章节B" in toc
        assert "大标题" not in toc
        assert "   - 1.1 小节" in toc

    def test_toc_two_levels_subsections_indented(self):
        """两级目录：章条目有序列表，子节缩进 - n.m 挂靠，顺序与正文一致。"""
        text = (
            "## 1 行业概述\n\n### 1.1 行业定义\n\n正文\n\n### 1.2 产业链\n\n正文\n\n"
            "## 2 市场规模\n\n正文\n"
        )
        toc = _build_toc(text)
        lines = [ln for ln in toc.split("\n") if ln.strip()]
        assert lines == [
            "## 目录",
            "1. 行业概述",
            "   - 1.1 行业定义",
            "   - 1.2 产业链",
            "2. 市场规模",
        ]

    def test_toc_empty_body(self):
        """无 ## 标题时仅返回目录头。"""
        assert _build_toc("纯正文无标题").strip() == "## 目录"

    def test_toc_strips_injected_number(self):
        """组装层注入的前导序号被剥去，目录行 idx. 与正文编号语义一致无双号。"""
        text = "## 1 执行摘要\n\n...\n\n## 2 市场分析\n\n...\n"
        toc = _build_toc(text)
        assert "1. 执行摘要" in toc
        assert "2. 市场分析" in toc
        assert "1. 1" not in toc
        assert "2. 2" not in toc


class TestConvertSubheads:
    """◆ 小节 → 三级标题（章号.节号，§3.5 层级：H2 章 / H3 小节）。"""

    def test_subheads_numbered_within_section(self):
        """◆ 小节转换三级标题并按章内递增编号。"""
        text = "## 2 产品与服务\n\n◆ 竞品对比\n\n内容甲\n\n◆ 价格体系\n\n内容乙"
        out = _convert_subheads(text)
        assert "### 2.1 竞品对比" in out
        assert "### 2.2 价格体系" in out
        assert "◆" not in out

    def test_sub_counter_resets_on_new_section(self):
        """遇新章节小节号重置，章号随标题更新。"""
        text = "## 2 产品\n\n◆ 甲\n\n## 3 市场\n\n◆ 乙\n\n◆ 丙"
        out = _convert_subheads(text)
        assert "### 2.1 甲" in out
        assert "### 3.1 乙" in out
        assert "### 3.2 丙" in out

    def test_tolerates_leading_whitespace_and_fullwidth_space(self):
        """行首空白与 ◆ 后全角空格均容忍。"""
        text = "## 1 章\n\n\u3000◆ 全角后标题\n\n ◆ 前导空白标题"
        out = _convert_subheads(text)
        assert "### 1.1 全角后标题" in out
        assert "### 1.2 前导空白标题" in out

    def test_unnumbered_sections_yield_zero_prefix(self):
        """无号章节（非 runner 组装输入）小节号前缀为 0.x，可见异常不静默。"""
        text = "## 裸章\n\n◆ 甲"
        assert "### 0.1 甲" in _convert_subheads(text)

    def test_plain_text_passthrough(self):
        """无 ◆ 输入原样返回。"""
        text = "## 1 章\n\n正文提到 ◆ 符号不在行首不影响"
        assert _convert_subheads(text) == text

    def test_subheads_under_subsection_context_become_h4(self):
        """子节上下文（### n.m）中的 ◆ 转 H4，编号 n.m.k 不与子节号冲突。"""
        text = "## 2 章\n\n### 2.1 节甲\n\n◆ 要点一\n\n内容\n\n◆ 要点二"
        out = _convert_subheads(text)
        assert "#### 2.1.1 要点一" in out
        assert "#### 2.1.2 要点二" in out
        assert "### 2.1 节甲" in out  # 子节标题本身原样保留

    def test_sub_counter_resets_on_new_subsection(self):
        """遇新子节（### n.m）H4 小节号重置、前缀随子节更新。"""
        text = "## 2 章\n\n### 2.1 节甲\n\n◆ 甲点\n\n### 2.2 节乙\n\n◆ 乙点"
        out = _convert_subheads(text)
        assert "#### 2.1.1 甲点" in out
        assert "#### 2.2.1 乙点" in out


class TestSmoothTransitions:
    """回归：_smooth_transitions 不得破坏“## 标题独立成行”不变量。

    原规则 2（节首重复标题剥离）已上移至装配层，用例见
    test_assemble_two_level.py 的 TestStripLeadingDuplicate 类。
    """

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
