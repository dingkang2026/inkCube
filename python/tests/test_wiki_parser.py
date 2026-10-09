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
