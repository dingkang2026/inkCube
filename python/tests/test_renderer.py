"""python/tests/test_renderer.py — Markdown→docx 导出单测（§3.7）。

覆盖（pandoc 通道，renderer 已由手写 python-docx 映射重写为内置 pandoc 调用）：
  - pandoc 缺失（安装损坏）→ RuntimeError 引导，不静默降级
  - pandoc 非零退出 → RuntimeError 带 stderr
  - 成功路径 → 产出非空 docx，临时 md 清理
  - pandoc 超时 → .tmp 半写产物清理、最终路径无残留（M6 修复回归——
    此前直接写最终路径，超时在原路径留下损坏 zip 残留）
  - mermaid 桥集成：pandoc 输入为桥产出文件 + 桥临时目录清理；
    Electron 不可用时降级保留代码块导出（§3.7 渲染规则）
"""
import os
import subprocess
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

    def test_pandoc_timeout_cleans_partial_output(self, tmp_path):
        """pandoc 超时（TimeoutExpired）：.tmp 半写产物被清理，最终路径无残留。"""
        out = tmp_path / "r.docx"

        def fake_run(cmd, **kwargs):
            # 模拟 pandoc 半写后被 kill：.tmp 上有残留字节
            with open(cmd[3], "wb") as f:
                f.write(b"PK\x03\x04 partial")
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=60)

        with patch("report_generator.renderer.resolve_tool", return_value="pandoc"), \
             patch("report_generator.renderer.subprocess.run", side_effect=fake_run):
            with pytest.raises(subprocess.TimeoutExpired):
                render_markdown_to_docx(MD, str(out), title="T")

        assert not out.exists()                              # 最终路径无残留
        assert not (tmp_path / "r.docx.tmp").exists()        # 半写产物已清理
        assert not list(tmp_path.glob("*.tmp.md"))           # 临时 md 已清理（finally）

    def test_pandoc_success_no_tmp_docx_residue(self, tmp_path):
        """成功路径：产物经 .tmp 原子落位，无 .docx.tmp 残留。"""
        out = tmp_path / "r.docx"

        def fake_run(cmd, **kwargs):
            with open(cmd[3], "wb") as f:  # pandoc 写 .tmp 隔离路径
                f.write(b"PK\x03\x04 fake docx")

            class R:
                returncode = 0
                stderr = ""
            return R()

        with patch("report_generator.renderer.resolve_tool", return_value="pandoc"), \
             patch("report_generator.renderer.subprocess.run", side_effect=fake_run):
            render_markdown_to_docx(MD, str(out), title="T")

        assert out.exists()
        assert not (tmp_path / "r.docx.tmp").exists()


# ── Mermaid 桥集成（§3.7 渲染规则）─────────────────────────


class TestMermaidBridgeIntegration:
    def test_mermaid_preprocessed_before_pandoc(self, tmp_path):
        """含 mermaid 报告：pandoc 输入为桥产出文件，桥临时目录在结束后清理。"""
        out = tmp_path / "r.docx"
        pre_md = tmp_path / "preprocessed.md"
        pre_md.write_text("# T\n\n![diagram](svgs/diagram_0.svg)", encoding="utf-8")
        mmd_tmp = str(tmp_path / "mmd_tmp")
        cleanup_calls: list[str] = []

        def fake_run(cmd, **kwargs):
            # pandoc 收到的是桥产出文件，而非 renderer 的 .tmp.md
            assert cmd[1] == str(pre_md)
            with open(cmd[3], "wb") as f:
                f.write(b"PK\x03\x04 fake docx")

            class R:
                returncode = 0
                stderr = ""
            return R()

        with patch("report_generator.renderer.resolve_tool", return_value="pandoc"), \
             patch("report_generator.renderer.preprocess_mermaid",
                   return_value=(pre_md, mmd_tmp)), \
             patch("report_generator.renderer.cleanup_temp_dir",
                   side_effect=cleanup_calls.append), \
             patch("report_generator.renderer.subprocess.run", side_effect=fake_run):
            result = render_markdown_to_docx(
                "# T\n\n```mermaid\ngraph TD\nA-->B\n```", str(out), title="T"
            )

        assert os.path.exists(result)
        # 桥临时目录恰清理一次（mmd_tmp 非 None 才清）
        assert cleanup_calls == [mmd_tmp]

    def test_mermaid_kept_as_code_when_electron_missing(self, tmp_path):
        """含 mermaid 但 Electron 不可用：降级保留代码块原样导出，不阻断 docx。"""
        md = "# T\n\n```mermaid\ngraph TD\nA-->B\n```\n"
        out = tmp_path / "r.docx"

        def fake_run(cmd, **kwargs):
            src = cmd[1]
            assert src.endswith(".tmp.md")
            content = open(src, encoding="utf-8").read()
            # 渲染降级：代码块原样保留（未被替换为图片引用）
            assert "```mermaid" in content
            assert "![diagram]" not in content
            with open(cmd[3], "wb") as f:
                f.write(b"PK\x03\x04 fake docx")

            class R:
                returncode = 0
                stderr = ""
            return R()

        with patch("report_generator.renderer.resolve_tool", return_value="pandoc"), \
             patch("tools.mermaid_bridge.find_electron", return_value=None), \
             patch("report_generator.renderer.subprocess.run", side_effect=fake_run):
            result = render_markdown_to_docx(md, str(out), title="T")

        assert os.path.exists(result)
