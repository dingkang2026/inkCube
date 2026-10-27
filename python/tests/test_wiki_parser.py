"""Regression tests for V2 parser metadata robustness (review P2-3).

块首/元数据行之间的空行曾导致整块 metadata（type/title/refs/tags）静默丢失：
refs 是图谱边与 backlinks 的数据源，丢失后图谱断边、broken-link lint 漏检。
修复后 metadata 区对空行漂移健壮（遇空行向后看，后继仍是 meta 行则继续收集）。
"""
import os
import sys

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from wiki.parser import parse_compile_output_v2


def _wrap(key: str, inner: str) -> str:
    return f"---PAGE: {key}---\n{inner}---END PAGE---"


class TestV2PageMetadata:
    def test_leading_blank_line_keeps_metadata(self):
        """块首空行（LLM 格式漂移常态）不得丢弃 metadata。"""
        output = _wrap(
            "enterprise_profile",
            "\ntype: entity\ntitle: 企业画像\nrefs: [paos, vfa]\n\n正文第一段。\n",
        )
        pages, _, _ = parse_compile_output_v2(output)
        assert len(pages) == 1
        p = pages[0]
        assert p["page_type"] == "entity"
        assert p["title"] == "企业画像"
        assert p["refs"] == ["paos", "vfa"]
        assert "正文第一段" in p["content"]

    def test_blank_line_between_meta_lines_keeps_metadata(self):
        """元数据行之间的空行不得提前截断 metadata 区。"""
        output = _wrap(
            "vfa",
            "type: concept\n\ntitle: VFA\n\ntags: [water]\n\n正文。\n",
        )
        pages, _, _ = parse_compile_output_v2(output)
        p = pages[0]
        assert p["page_type"] == "concept"
        assert p["title"] == "VFA"
        assert p["tags"] == ["water"]
        assert "正文" in p["content"]

    def test_normal_block_unchanged(self):
        """常规格式回归：metadata 采集 + 空行后 body 起点。"""
        output = _wrap("k", "type: entity\ntitle: T\nrefs: [a]\n\n# Heading\nBody\n")
        pages, _, _ = parse_compile_output_v2(output)
        p = pages[0]
        assert p["title"] == "T"
        assert p["refs"] == ["a"]
        assert p["content"] == "# Heading\nBody"

    def test_body_starts_at_first_non_meta_line(self):
        """metadata 后空行接正文：正文原样保留，不被吞入 metadata。"""
        output = _wrap("k", "type: entity\n\nHello world\nmore\n")
        pages, _, _ = parse_compile_output_v2(output)
        assert pages[0]["content"] == "Hello world\nmore"

    def test_no_metadata_block(self):
        """无 metadata 的块：title 退回 page_key，正文完整。"""
        output = _wrap("k", "Just body text\n")
        pages, _, _ = parse_compile_output_v2(output)
        assert pages[0]["title"] == "k"
        assert pages[0]["content"] == "Just body text"


class TestV2FullwidthColon:
    """全角冒号容忍（2026-10-26 事故回归）：中文 LLM 输出 ---PAGE： key---，
    半角-only 正则全盘漏析 → 0 页静默成功（wiki_pages 零写入仍报 completed）。
    修复后所有块分隔符与 meta 行正则均接受 [:：]。"""

    def test_fullwidth_page_delimiter(self):
        """---PAGE： key--- 全角分隔符能解析出页面。"""
        output = "---PAGE： company_overview---\ntype: entity\ntitle: 企业概况\n\n正文内容。\n---END PAGE---"
        pages, _, _ = parse_compile_output_v2(output)
        assert len(pages) == 1
        assert pages[0]["page_key"] == "company_overview"
        assert pages[0]["title"] == "企业概况"
        assert "正文内容" in pages[0]["content"]

    def test_fullwidth_review_delimiter(self):
        """---REVIEW： type | title--- 全角分隔符能解析出发现项。"""
        output = "---REVIEW：contradiction | 认证时间矛盾---\n证书显示 2008 年，BP 显示 2009 年\n---END REVIEW---"
        _, reviews, _ = parse_compile_output_v2(output)
        assert len(reviews) == 1
        assert reviews[0]["type"] == "contradiction"
        assert reviews[0]["title"] == "认证时间矛盾"

    def test_fullwidth_meta_lines(self):
        """块内 meta 行全角冒号（type： entity）能采集 metadata。"""
        output = "---PAGE: k---\ntype：concept\ntitle：VFA\n\n正文。\n---END PAGE---"
        pages, _, _ = parse_compile_output_v2(output)
        p = pages[0]
        assert p["page_type"] == "concept"
        assert p["title"] == "VFA"

    def test_incident_output_shape(self):
        """真实事故形态端到端：全角 PAGE 块 + 全角 REVIEW 块混合输出全部解析。"""
        output = (
            "---PAGE： exit_path---\n"
            "type: entity\n"
            "title: 退出路径\n"
            "\n"
            "IPO 与并购两条路径。\n"
            "---END PAGE---\n"
            "\n"
            "---REVIEW：contradiction | 成立时间矛盾---\n"
            "EVIDENCE: [[exit_path]] 「2011 年成立」\n"
            "工商登记与官网表述不一致。\n"
            "---END REVIEW---"
        )
        pages, reviews, _ = parse_compile_output_v2(output)
        assert len(pages) == 1
        assert pages[0]["page_key"] == "exit_path"
        assert len(reviews) == 1
        assert reviews[0]["evidence"][0]["page_key"] == "exit_path"
