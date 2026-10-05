"""python/tests/test_finalizer.py — 全局收尾单测（§3.7）。

回归覆盖：
  - B3：_build_toc 必须按装配后正文的 ## 标题出现顺序生成目录，
    而非大纲原序（post_body(front) 前置重排后目录 ≡ 正文顺序）。
"""
import os
import sys

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from report_generator.finalizer import _build_toc  # noqa: E402


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
