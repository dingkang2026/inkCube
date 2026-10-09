"""python/tests/test_runner_assemble.py — 章节组装编号单测（§3.5 标题序号）。

组装层是标题序号的单一事实源：按最终呈现顺序（post_body front 前置 →
standard → post_body end 后置）注入十进制序号，网页预览 / docx 导出 /
手写目录三方同源一致。
"""
import os
import re
import sys

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from report_generator.runner import ReportRunner  # noqa: E402


def _make_runner(sections: list[dict], outputs: dict[str, str]) -> ReportRunner:
    """跳过 __init__（LLM/检索依赖），仅装配组装所需字段。"""
    r = ReportRunner.__new__(ReportRunner)
    r.sections = sections
    r.outputs = outputs
    return r


class TestAssembleFullTextNumbering:
    def test_sections_numbered_in_order(self):
        """standard 节按执行顺序编号 1..N。"""
        runner = _make_runner(
            sections=[{"section_id": "a", "title": "基本信息"},
                      {"section_id": "b", "title": "财务分析"}],
            outputs={"a": "内容A", "b": "内容B"},
        )
        out = runner._assemble_full_text()
        assert "## 1 基本信息" in out
        assert "## 2 财务分析" in out

    def test_post_body_front_numbered_first(self):
        """post_body front 前置节拿 1 号，standard 顺延。"""
        runner = _make_runner(
            sections=[
                {"section_id": "sum", "title": "尽调摘要",
                 "compose_mode": "post_body", "position": "front"},
                {"section_id": "a", "title": "基本信息"},
            ],
            outputs={"sum": "摘要", "a": "内容A"},
        )
        out = runner._assemble_full_text()
        assert out.index("## 1 尽调摘要") < out.index("## 2 基本信息")

    def test_post_body_end_numbered_last(self):
        """post_body end 后置节编号最后。"""
        runner = _make_runner(
            sections=[
                {"section_id": "a", "title": "基本信息"},
                {"section_id": "ref", "title": "附录",
                 "compose_mode": "post_body", "position": "end"},
            ],
            outputs={"a": "内容A", "ref": "附录内容"},
        )
        out = runner._assemble_full_text()
        assert out.index("## 1 基本信息") < out.index("## 2 附录")

    def test_empty_output_section_skipped_without_consuming_number(self):
        """无产出章节跳过且不占号。"""
        runner = _make_runner(
            sections=[{"section_id": "a", "title": "甲"},
                      {"section_id": "b", "title": "乙"}],
            outputs={"a": "内容A"},  # b 无产出
        )
        out = runner._assemble_full_text()
        assert "## 1 甲" in out
        assert "乙" not in out
        assert re.search(r"^## 2 ", out, flags=re.M) is None

    def test_mixed_full_order_numbering(self):
        """front + standard ×2 + end 完整顺序编号 1-4。"""
        runner = _make_runner(
            sections=[
                {"section_id": "sum", "title": "摘要",
                 "compose_mode": "post_body", "position": "front"},
                {"section_id": "a", "title": "甲"},
                {"section_id": "b", "title": "乙"},
                {"section_id": "end", "title": "风险提示",
                 "compose_mode": "post_body", "position": "end"},
            ],
            outputs={"sum": "s", "a": "A", "b": "B", "end": "E"},
        )
        out = runner._assemble_full_text()
        nums = re.findall(r"^## (\d+) (.+)$", out, flags=re.M)
        assert nums == [("1", "摘要"), ("2", "甲"), ("3", "乙"), ("4", "风险提示")]

    def test_section_separator_preserved(self):
        """编号注入不破坏节间 --- 分隔符结构。"""
        runner = _make_runner(
            sections=[{"section_id": "a", "title": "甲"},
                      {"section_id": "b", "title": "乙"}],
            outputs={"a": "A", "b": "B"},
        )
        out = runner._assemble_full_text()
        assert "\n\n---\n\n" in out
        assert not re.search(r"^---#", out, flags=re.M)
