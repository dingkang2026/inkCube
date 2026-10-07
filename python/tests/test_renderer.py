"""python/tests/test_renderer.py — Markdown→docx 导出单测（§3.7）。

覆盖（pandoc 通道，renderer 已由手写 python-docx 映射重写为内置 pandoc 调用）：
  - pandoc 缺失（安装损坏）→ RuntimeError 引导，不静默降级
  - pandoc 非零退出 → RuntimeError 带 stderr
  - 成功路径 → 产出非空 docx，临时 md 清理
"""
import os
import sys
import tempfile
from unittest.mock import patch

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from report_generator.renderer import render_markdown_to_docx  # noqa: E402

# 含表格 / 加粗的样例（pandoc 原生支持，旧手写映射不支持）
MD = (
    "# 标题\n\n## 章节一\n\n正文内容[1]，**加粗**。\n\n"
    "| 指标 | 数值 |\n|---|---|\n| 营收 | 100亿 |\n"
)


class TestRenderMarkdownToDocx:
    def test_pandoc_missing_raises_with_guidance(self):
        """pandoc 不可用（安装损坏/缺失）应报错并给出安装引导。"""
        with patch("report_generator.renderer.resolve_tool", return_value=None):
            with pytest.raises(RuntimeError, match="pandoc"):
                render_markdown_to_docx(
                    MD, os.path.join(tempfile.gettempdir(), "inkcube_test_x.docx")
                )

    def test_pandoc_failure_raises_runtime_error(self, tmp_path):
        """pandoc 非零退出码应抛 RuntimeError（带 stderr 详情）。"""
        def fake_run(cmd, **kwargs):
            class R:
                returncode = 1
                stderr = "parse error: bad markdown"
            return R()

        with patch("report_generator.renderer.resolve_tool", return_value="pandoc"), \
             patch("report_generator.renderer.subprocess.run", side_effect=fake_run):
            with pytest.raises(RuntimeError, match="parse error"):
                render_markdown_to_docx(MD, str(tmp_path / "r.docx"), title="T")

    def test_pandoc_success_produces_file_and_cleans_tmp(self, tmp_path):
        """成功路径：产出非空 docx；临时 md 清理；输入路径为 .tmp.md。"""
        out = tmp_path / "r.docx"

        def fake_run(cmd, **kwargs):
            src, dst = cmd[1], cmd[3]
            # 输入为写好的临时 md，输出为 docx 路径
            assert src.endswith(".tmp.md")
            assert os.path.exists(src)
            with open(dst, "wb") as f:
                f.write(b"PK\x03\x04 fake docx")

            class R:
                returncode = 0
                stderr = ""
            return R()

        with patch("report_generator.renderer.resolve_tool", return_value="pandoc"), \
             patch("report_generator.renderer.subprocess.run", side_effect=fake_run):
            result = render_markdown_to_docx(MD, str(out), title="T")

        assert os.path.exists(result)
        assert os.path.getsize(result) > 0
        # 临时 md 已清理
        assert not list(tmp_path.glob("*.tmp.md"))
