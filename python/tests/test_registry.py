"""python/tests/test_registry.py — 全局引用注册器单元测试。

覆盖：
  - wiki 卡片的 Prompt / 来源列表渲染为「项目Wiki」标签（回归）
  - knowledge / internet 标签
  - 未知 source_type 回退为原字符串
  - 按 (source_type, source_from, content_hash) 去重
"""
import os
import sys

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from report_generator.registry import CitationRegistry  # noqa: E402


def _card(source_type, source_from="x", content="c"):
    return {
        "card_id": "C001",
        "section_id": "s1",
        "content": content,
        "source_type": source_type,
        "source_title": f"{source_type} 标题",
        "source_from": source_from,
        "source_date": "",
    }


# ── 来源标签 ────────────────────────────────────────────────


class TestTypeLabels:
    def test_wiki_card_renders_wiki_label(self):
        """wiki 卡片在 Prompt 呈现与来源列表中标为「项目Wiki」（回归）。"""
        reg = CitationRegistry()
        n = reg.register_card(_card("wiki", source_from="wiki:market"))
        assert "项目Wiki" in reg.format_for_prompt([n])

        reg2 = CitationRegistry()
        reg2.register_card(_card("wiki", source_from="wiki:market"))
        assert "项目Wiki" in reg2.format_source_list()

    def test_knowledge_and_internet_labels(self):
        reg = CitationRegistry()
        nk = reg.register_card(_card("knowledge", source_from="a.pdf p.1"))
        ni = reg.register_card(_card("internet", source_from="https://x"))
        text = reg.format_for_prompt([nk, ni])
        assert "知识库" in text
        assert "互联网" in text

    def test_unknown_type_falls_back_to_raw(self):
        reg = CitationRegistry()
        n = reg.register_card(_card("feed"))
        assert "feed" in reg.format_for_prompt([n])


# ── 去重 ────────────────────────────────────────────────────


class TestDedup:
    def test_same_key_dedups(self):
        reg = CitationRegistry()
        n1 = reg.register_card(_card("wiki", source_from="wiki:a", content="同内容"))
        n2 = reg.register_card(_card("wiki", source_from="wiki:a", content="同内容"))
        assert n1 == n2 == 1
        assert len(reg) == 1

    def test_different_source_type_not_dedup(self):
        """同 source_from 但 source_type 不同 → 不去重（key 含 source_type）。"""
        reg = CitationRegistry()
        n1 = reg.register_card(_card("wiki", source_from="same", content="c"))
        n2 = reg.register_card(_card("knowledge", source_from="same", content="c"))
        assert n1 != n2
        assert len(reg) == 2
