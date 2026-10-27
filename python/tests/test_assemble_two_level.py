"""python/tests/test_assemble_two_level.py — 两级装配单测（2026-10-27 一级目录修复）。

背景：模板大纲为两级（章 + children 子节），旧 _assemble_full_text 按展平
执行节全部输出 `## n 标题`——章级（一级目录）在正文与目录中双双丢失。

覆盖：
  - 两级结构：`## n 章题` + `### n.m 节题`，编号按呈现顺序注入
  - 无 children 章：平铺 `## n 标题`（向后兼容）
  - post_body 前置/后置（章级）；post_body 子节独立成章（自定义模板边界形态）
  - 空内容子节/章跳过，编号连续
  - 节首重复标题剥离（原 _smooth_transitions 规则 2 上移，含裸标题/
    带号重复/不误伤三向回归）
"""
import os
import re
import sys

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from report_generator.runner import ReportRunner  # noqa: E402


def _make_runner(tmp_path) -> ReportRunner:
    """装配测试专用 runner：不触 LLM/检索，仅用 outline + outputs。"""
    return ReportRunner(
        template={"report_depth": "brief"},
        variables={}, project_id="p1",
        search_tool=None, llm_client=None, llm_settings={},
        output_dir=str(tmp_path),
    )


class TestAssembleTwoLevel:
    def test_two_level_structure_with_numbering(self, tmp_path):
        """章+子节 → `## n 章题` + `### n.m 节题`，子节号章内递增、跨章随章号。"""
        runner = _make_runner(tmp_path)
        runner.outline = [
            {"section_id": "ch1", "title": "行业概述", "children": [
                {"section_id": "s1", "title": "行业定义"},
                {"section_id": "s2", "title": "产业链"},
            ]},
            {"section_id": "ch2", "title": "市场规模", "children": [
                {"section_id": "s3", "title": "全球市场"},
            ]},
        ]
        runner.outputs = {"s1": "定义正文", "s2": "链条正文", "s3": "全球正文"}

        out = runner._assemble_full_text()
        assert "## 1 行业概述\n\n### 1.1 行业定义\n\n定义正文\n\n### 1.2 产业链\n\n链条正文" in out
        assert "## 2 市场规模\n\n### 2.1 全球市场\n\n全球正文" in out

    def test_childless_chapter_flat_h2(self, tmp_path):
        """无 children 章 → `## n 标题`（旧平铺形态向后兼容）。"""
        runner = _make_runner(tmp_path)
        runner.outline = [
            {"section_id": "ch1", "title": "技术演进", "children": []},
        ]
        runner.outputs = {"ch1": "演进正文"}
        assert runner._assemble_full_text() == "## 1 技术演进\n\n演进正文"

    def test_post_body_front_end_reorder(self, tmp_path):
        """post_body(front) 前置、(end) 后置，章号按最终呈现顺序。"""
        runner = _make_runner(tmp_path)
        runner.outline = [
            {"section_id": "ch0", "title": "摘要",
             "compose_mode": "post_body", "position": "front"},
            {"section_id": "ch1", "title": "正文章", "children": []},
            {"section_id": "ch9", "title": "免责声明",
             "compose_mode": "post_body", "position": "end"},
        ]
        runner.outputs = {"ch0": "摘要正文", "ch1": "正文", "ch9": "声明正文"}
        out = runner._assemble_full_text()
        assert out.index("## 1 摘要") < out.index("## 2 正文章") < out.index("## 3 免责声明")

    def test_post_body_child_detaches_from_chapter(self, tmp_path):
        """post_body 子节独立成章前后置（自定义模板边界形态），原章不空壳。"""
        runner = _make_runner(tmp_path)
        runner.outline = [
            {"section_id": "ch1", "title": "综述章", "children": [
                {"section_id": "s1", "title": "全文综述",
                 "compose_mode": "post_body", "position": "front"},
                {"section_id": "s2", "title": "背景"},
            ]},
        ]
        runner.outputs = {"s1": "综述正文", "s2": "背景正文"}
        out = runner._assemble_full_text()
        # front：综述独立成章置首；章块留剩余子节，不因子节移走而消失
        assert out.startswith("## 1 全文综述\n\n综述正文")
        assert "## 2 综述章\n\n### 2.1 背景\n\n背景正文" in out

    def test_empty_subsection_skipped_numbering_continuous(self, tmp_path):
        """无内容子节跳过、子节号连续；有内容子节的章正常出现。"""
        runner = _make_runner(tmp_path)
        runner.outline = [
            {"section_id": "ch1", "title": "章一", "children": [
                {"section_id": "s1", "title": "节甲"},
                {"section_id": "s2", "title": "节乙"},
            ]},
            {"section_id": "ch2", "title": "章二", "children": [
                {"section_id": "s3", "title": "节丙"},
            ]},
        ]
        runner.outputs = {"s1": "甲正文", "s3": "丙正文"}  # s2 未生成
        out = runner._assemble_full_text()
        assert "### 1.1 节甲" in out
        assert "节乙" not in out
        assert "## 1 章一" in out
        assert "## 2 章二" in out
        assert "### 2.1 节丙" in out

    def test_chapter_skipped_when_all_children_empty(self, tmp_path):
        """章的全部子节均无内容 → 整章不出现，后续章编号前移。"""
        runner = _make_runner(tmp_path)
        runner.outline = [
            {"section_id": "ch1", "title": "空章", "children": [
                {"section_id": "s1", "title": "节甲"},
            ]},
            {"section_id": "ch2", "title": "实章", "children": []},
        ]
        runner.outputs = {"ch2": "实章正文"}  # s1 无内容
        out = runner._assemble_full_text()
        assert "空章" not in out
        assert out.startswith("## 1 实章\n\n实章正文")

    def test_chapters_separated_by_hr(self, tmp_path):
        """章块之间以 --- 分隔（_smooth_transitions 依赖该形态剥离重建）。"""
        runner = _make_runner(tmp_path)
        runner.outline = [
            {"section_id": "ch1", "title": "章一", "children": []},
            {"section_id": "ch2", "title": "章二", "children": []},
        ]
        runner.outputs = {"ch1": "甲", "ch2": "乙"}
        assert runner._assemble_full_text() == "## 1 章一\n\n甲\n\n---\n\n## 2 章二\n\n乙"


class TestStripLeadingDuplicate:
    """节首重复标题剥离（原 _smooth_transitions 规则 2 上移装配层的三向回归）。"""

    def test_plain_duplicate_removed(self, tmp_path):
        """节首裸文本重复节题被清除（章级）。"""
        runner = _make_runner(tmp_path)
        runner.outline = [{"section_id": "ch1", "title": "基本信息", "children": []}]
        runner.outputs = {"ch1": "基本信息\n\n正文内容"}
        out = runner._assemble_full_text()
        assert out.count("## 1 基本信息") == 1
        assert not re.search(r"^基本信息$", out, flags=re.M)
        assert "正文内容" in out

    def test_numbered_duplicate_removed(self, tmp_path):
        """节首带 n.m 编号的重复行剥号比对后清除（子节级）。"""
        runner = _make_runner(tmp_path)
        runner.outline = [{"section_id": "ch1", "title": "章", "children": [
            {"section_id": "s1", "title": "行业定义"},
        ]}]
        runner.outputs = {"s1": "1.1 行业定义\n\n定义正文"}
        out = runner._assemble_full_text()
        assert not re.search(r"^1\.1 行业定义$", out, flags=re.M)
        assert "定义正文" in out

    def test_different_first_line_kept(self, tmp_path):
        """节首行剥号后与节题不同 → 不误伤。"""
        runner = _make_runner(tmp_path)
        runner.outline = [{"section_id": "ch1", "title": "基本信息", "children": []}]
        runner.outputs = {"ch1": "公司概况说明\n\n正文内容"}
        out = runner._assemble_full_text()
        assert "公司概况说明" in out
