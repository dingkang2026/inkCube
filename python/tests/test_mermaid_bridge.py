"""python/tests/test_mermaid_bridge.py — 图形块 → PNG 离线渲染桥单测（§3.7）。

覆盖：
  - MERMAID_RE / INLINE_SVG_RE / LOCAL_SVG_REF_RE：各形态匹配与不识别边界
  - render_mermaid_to_png / render_svg_to_png：非零退出 / 非法 base64 /
    非 PNG 内容 / 超时 → None；合法 PNG 原样返回；输入经临时文件传递
    （Windows 下管道 stdin 读 0 字节，回归用例）且后缀即渲染器模式开关
  - preprocess_mermaid 多条路径：无图形块 / Electron 不可用 / 成功
    （mermaid + 内联 svg 替换 + PNG 落盘）/ 全部失败（保留原文件），
    调用方契约 (actual_input, temp_dir_or_None)；本地 .svg 引用改写与缺失降级
  - cleanup_temp_dir：None 安全、删目录
  - _resolve_renderer_script：env 优先（打包路径 C1 修复回归）/ 缺失回退仓库路径
  - TestElectronIntegration：真实 Electron 子进程冒烟（无 electron 环境自动跳过）——
    mock 层无法覆盖渲染器进程内部（历史：注入求值值不可克隆 + stdin 读 0 字节
    两 bug 使线上全线失败而单测全绿）
"""
import base64
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.mermaid_bridge import (  # noqa: E402
    INLINE_SVG_RE,
    LOCAL_SVG_REF_RE,
    MERMAID_RE,
    _resolve_renderer_script,
    cleanup_temp_dir,
    preprocess_mermaid,
    render_mermaid_to_png,
    render_svg_to_png,
)

_PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 24


def _png_b64(data: bytes = _PNG) -> str:
    return base64.b64encode(data).decode("ascii")


# ── MERMAID_RE ──────────────────────────────────────────────


class TestMermaidRe:
    def test_matches_mermaid_fence(self):
        text = "前文\n\n```mermaid\ngraph TD\nA-->B\n```\n\n后文"
        m = MERMAID_RE.search(text)
        assert m is not None
        assert m.group(1) == "graph TD\nA-->B\n"

    def test_ignores_plain_fence(self):
        assert MERMAID_RE.search("```python\nprint(1)\n```") is None

    def test_matches_multiple_blocks(self):
        text = (
            "```mermaid\ngraph TD\nA-->B\n```\n中间\n"
            "```mermaid\npie\ntitle x\n```"
        )
        assert len(MERMAID_RE.findall(text)) == 2


# ── INLINE_SVG_RE / LOCAL_SVG_REF_RE ─────────────────────


_SVG_SRC = (
    '<svg viewBox="0 0 900 980" xmlns="http://www.w3.org/2000/svg">\n'
    '  <rect x="10" y="10" width="80" height="80" fill="blue"/>\n'
    '  <text x="50" y="55">层一</text>\n'
    '</svg>'
)


class TestInlineSvgRe:
    def test_matches_viewbox_only_block(self):
        text = f"前文\n\n{_SVG_SRC}\n\n后文"
        m = INLINE_SVG_RE.search(text)
        assert m is not None
        # 命名组提取纯 svg 源（不含前后空行）
        assert m.group("svg").startswith("<svg")
        assert m.group("svg").endswith("</svg>")

    def test_matches_with_xml_declaration(self):
        text = '<?xml version="1.0"?>\n<svg viewBox="0 0 10 10"><rect/></svg>'
        m = INLINE_SVG_RE.search(text)
        assert m is not None
        assert m.group("svg").startswith("<svg")  # xml 声明不进渲染输入

    def test_case_insensitive_close_tag(self):
        text = '<svg viewBox="0 0 2 2"><rect/></SVG>'
        assert INLINE_SVG_RE.search(text) is not None

    def test_mid_paragraph_inline_not_matched(self):
        """段中内联（非行首）不是图形块，不转（保文字降级路径）。"""
        assert INLINE_SVG_RE.search(f"文字 {_SVG_SRC} 尾注") is None

    def test_matches_multiple_blocks(self):
        text = f"{_SVG_SRC}\n\n中间\n\n{_SVG_SRC}"
        assert len(INLINE_SVG_RE.findall(text)) == 2


class TestLocalSvgRefRe:
    def test_matches_relative_ref(self):
        assert LOCAL_SVG_REF_RE.search("![](diagrams/arch.svg)") is not None

    def test_url_ref_not_matched(self):
        """URL 形式无本地文件可读，不进改写（保留原引用交 pandoc）。"""
        assert LOCAL_SVG_REF_RE.search("![](https://x.com/a.svg)") is None

    def test_png_ref_not_matched(self):
        assert LOCAL_SVG_REF_RE.search("![](a.png)") is None


# ── render_mermaid_to_png ───────────────────────────────────


def _run_result(returncode=0, stdout="", stderr=""):
    class R:
        pass

    r = R()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


class TestRenderMermaidToPng:
    def test_nonzero_exit_returns_none(self):
        with patch("tools.mermaid_bridge.subprocess.run",
                   return_value=_run_result(returncode=1, stderr="boom")):
            assert render_mermaid_to_png("electron", "graph TD\nA-->B") is None

    def test_invalid_base64_returns_none(self):
        """stdout 不是合法 base64（渲染器异常输出）不当作成功。"""
        with patch("tools.mermaid_bridge.subprocess.run",
                   return_value=_run_result(stdout="!!! not base64 !!!")):
            assert render_mermaid_to_png("electron", "graph TD\nA-->B") is None

    def test_base64_but_not_png_returns_none(self):
        """stdout 可 base64 解码但内容非 PNG（如残留 SVG 文本）不当作成功。"""
        with patch("tools.mermaid_bridge.subprocess.run",
                   return_value=_run_result(stdout=base64.b64encode(b"<svg>x</svg>").decode())):
            assert render_mermaid_to_png("electron", "graph TD\nA-->B") is None

    def test_valid_png_returned(self):
        with patch("tools.mermaid_bridge.subprocess.run",
                   return_value=_run_result(stdout=_png_b64())):
            assert render_mermaid_to_png("electron", "graph TD\nA-->B") == _PNG

    def test_timeout_returns_none(self):
        with patch("tools.mermaid_bridge.subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd="electron", timeout=30)):
            assert render_mermaid_to_png("electron", "graph TD\nA-->B") is None

    def test_input_passed_via_temp_file(self):
        """输入经临时文件传递（argv 路径），不走 stdin；用后即清。

        回归：曾因 [electron, script] + input=code 的 stdin 通道在 Windows
        Electron 子进程读到 0 字节而全线渲染失败。
        """
        captured = {}

        def fake_run(argv, **kwargs):
            captured['argv'] = argv
            captured['kwargs'] = kwargs
            captured['content'] = Path(argv[2]).read_text(encoding='utf-8')
            return _run_result(stdout=_png_b64())

        with patch("tools.mermaid_bridge.subprocess.run", side_effect=fake_run):
            png = render_mermaid_to_png("electron", "graph TD\nA-->B")

        assert png == _PNG
        assert captured['argv'][0] == "electron"
        assert captured['argv'][1].endswith("mermaid-renderer.js")
        assert captured['content'] == "graph TD\nA-->B"
        assert "input" not in captured['kwargs']
        # 渲染结束后临时输入文件已清理
        assert not os.path.exists(captured['argv'][2])


class TestRenderSvgToPng:
    """内联 SVG 与 mermaid 同桥：后缀即渲染器模式开关（.svg 分流）。"""

    def test_svg_suffix_passed_as_mode_switch(self):
        captured = {}

        def fake_run(argv, **kwargs):
            captured['suffix'] = Path(argv[2]).suffix
            return _run_result(stdout=_png_b64())

        with patch("tools.mermaid_bridge.subprocess.run", side_effect=fake_run):
            assert render_svg_to_png("electron", "<svg><rect/></svg>") == _PNG
        assert captured['suffix'] == '.svg'

    def test_nonzero_exit_returns_none(self):
        with patch("tools.mermaid_bridge.subprocess.run",
                   return_value=_run_result(returncode=1, stderr="boom")):
            assert render_svg_to_png("electron", "<svg/>") is None

    def test_non_png_output_returns_none(self):
        with patch("tools.mermaid_bridge.subprocess.run",
                   return_value=_run_result(stdout=base64.b64encode(b"not png").decode())):
            assert render_svg_to_png("electron", "<svg/>") is None

    def test_timeout_returns_none(self):
        with patch("tools.mermaid_bridge.subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd="electron", timeout=30)):
            assert render_svg_to_png("electron", "<svg/>") is None


# ── preprocess_mermaid ──────────────────────────────────────


class TestPreprocessMermaid:
    def test_no_mermaid_returns_original(self, tmp_path):
        src = tmp_path / "doc.md"
        src.write_text("# 标题\n\n正文段落。", encoding="utf-8")
        actual, tmp_dir = preprocess_mermaid(src)
        assert actual == src
        assert tmp_dir is None

    def test_electron_missing_returns_original(self, tmp_path):
        src = tmp_path / "doc.md"
        src.write_text("```mermaid\ngraph TD\nA-->B\n```", encoding="utf-8")
        with patch("tools.mermaid_bridge.find_electron", return_value=None):
            actual, tmp_dir = preprocess_mermaid(src)
        assert actual == src
        assert tmp_dir is None
        # 原文件未被改动
        assert "```mermaid" in src.read_text(encoding="utf-8")

    def test_success_replaces_blocks_and_writes_png(self, tmp_path):
        src = tmp_path / "doc.md"
        src.write_text("前文\n\n```mermaid\ngraph TD\nA-->B\n```\n\n后文",
                       encoding="utf-8")
        with patch("tools.mermaid_bridge.find_electron", return_value="electron"), \
             patch("tools.mermaid_bridge.render_mermaid_to_png",
                   return_value=_PNG):
            actual, tmp_dir = preprocess_mermaid(src)
        try:
            assert tmp_dir is not None
            assert actual != src
            out = actual.read_text(encoding="utf-8")
            assert "![diagram](" in out
            assert "```mermaid" not in out
            # PNG 落盘且引用路径一致
            img_ref = out.split("![diagram](")[1].split(")")[0]
            assert Path(img_ref).exists()
            assert Path(img_ref).read_bytes() == _PNG
        finally:
            cleanup_temp_dir(tmp_dir)

    def test_all_failed_returns_original(self, tmp_path):
        """全部块渲染失败：返回原文件与 None（临时目录已被桥内部清理）。"""
        src = tmp_path / "doc.md"
        src.write_text("```mermaid\ngraph TD\nA-->B\n```", encoding="utf-8")
        with patch("tools.mermaid_bridge.find_electron", return_value="electron"), \
             patch("tools.mermaid_bridge.render_mermaid_to_png", return_value=None):
            actual, tmp_dir = preprocess_mermaid(src)
        assert actual == src
        assert tmp_dir is None
        # 代码块原样保留
        assert "```mermaid" in src.read_text(encoding="utf-8")

    def test_io_error_degrades_to_original(self, tmp_path):
        """mkdtemp 后写盘 IO 异常（磁盘满/权限/杀软锁）：清理后降级返回原文件。"""
        src = tmp_path / "doc.md"
        src.write_text("```mermaid\ngraph TD\nA-->B\n```", encoding="utf-8")
        with patch("tools.mermaid_bridge.find_electron", return_value="electron"), \
             patch("tools.mermaid_bridge._render_blocks_to_file",
                   side_effect=OSError("disk full")):
            actual, tmp_dir = preprocess_mermaid(src)
        assert actual == src
        assert tmp_dir is None
        assert "```mermaid" in src.read_text(encoding="utf-8")

    def test_image_dir_persists_png_in_place(self, tmp_path):
        """指定 image_dir：PNG 持久化到该目录且 md 原地改写（ppt 素材就位模式）。

        M3 回归：缺省临时目录模式下 staged md 的引用指向 %TEMP% 绝对路径，
        临时目录被系统清理后引用断裂，且每次创建泄漏一个临时目录。
        """
        src = tmp_path / "staging" / "report.md"
        src.parent.mkdir(parents=True)
        src.write_text("前文\n\n```mermaid\ngraph TD\nA-->B\n```\n\n后文",
                       encoding="utf-8")
        image_dir = tmp_path / "staging" / "mermaid"
        with patch("tools.mermaid_bridge.find_electron", return_value="electron"), \
             patch("tools.mermaid_bridge.render_mermaid_to_png",
                   return_value=_PNG):
            actual, tmp_dir = preprocess_mermaid(src, image_dir=image_dir)
        assert actual == src          # 原地改写，无临时 md
        assert tmp_dir is None        # 无临时目录需清理
        out = src.read_text(encoding="utf-8")
        assert "```mermaid" not in out
        img_ref = out.split("![diagram](")[1].split(")")[0]
        assert Path(img_ref).is_relative_to(image_dir)  # 引用落在工作区内
        assert Path(img_ref).read_bytes() == _PNG

    def test_image_dir_all_failed_keeps_original(self, tmp_path):
        """指定 image_dir 且全部渲染失败：md 原样保留（降级纪律）。"""
        src = tmp_path / "report.md"
        src.write_text("```mermaid\ngraph TD\nA-->B\n```", encoding="utf-8")
        image_dir = tmp_path / "mermaid"
        with patch("tools.mermaid_bridge.find_electron", return_value="electron"), \
             patch("tools.mermaid_bridge.render_mermaid_to_png", return_value=None):
            actual, tmp_dir = preprocess_mermaid(src, image_dir=image_dir)
        assert actual == src
        assert tmp_dir is None
        assert "```mermaid" in src.read_text(encoding="utf-8")


class TestPreprocessInlineSvg:
    """内联 <svg> 块与本地 .svg 引用的预处理（导出丢图根因回归）。"""

    def test_svg_block_replaced_with_png_ref(self, tmp_path):
        src = tmp_path / "doc.md"
        src.write_text(f"前文\n\n{_SVG_SRC}\n\n后文", encoding="utf-8")
        with patch("tools.mermaid_bridge.find_electron", return_value="electron"), \
             patch("tools.mermaid_bridge.render_svg_to_png", return_value=_PNG):
            actual, tmp_dir = preprocess_mermaid(src)
        try:
            assert tmp_dir is not None
            out = actual.read_text(encoding="utf-8")
            assert "![diagram](" in out
            assert "<svg" not in out
            img_ref = out.split("![diagram](")[1].split(")")[0]
            assert Path(img_ref).read_bytes() == _PNG
        finally:
            cleanup_temp_dir(tmp_dir)

    def test_svg_render_failed_keeps_block(self, tmp_path):
        """SVG 渲染失败：保留原块（降级纪律），mermaid 块不受牵连。"""
        src = tmp_path / "doc.md"
        src.write_text(
            f"{_SVG_SRC}\n\n```mermaid\ngraph TD\nA-->B\n```", encoding="utf-8")
        with patch("tools.mermaid_bridge.find_electron", return_value="electron"), \
             patch("tools.mermaid_bridge.render_svg_to_png", return_value=None), \
             patch("tools.mermaid_bridge.render_mermaid_to_png", return_value=_PNG):
            actual, tmp_dir = preprocess_mermaid(src)
        try:
            out = actual.read_text(encoding="utf-8")
            assert "<svg" in out          # svg 降级保留
            assert "![diagram](" in out   # mermaid 正常替换
        finally:
            cleanup_temp_dir(tmp_dir)

    def test_mixed_blocks_all_replaced(self, tmp_path):
        """mermaid + 内联 svg 混合：两类块都替换，PNG 计数连续不互踩。"""
        src = tmp_path / "doc.md"
        src.write_text(
            f"```mermaid\ngraph TD\nA-->B\n```\n\n{_SVG_SRC}", encoding="utf-8")
        with patch("tools.mermaid_bridge.find_electron", return_value="electron"), \
             patch("tools.mermaid_bridge.render_mermaid_to_png", return_value=_PNG), \
             patch("tools.mermaid_bridge.render_svg_to_png", return_value=_PNG):
            actual, tmp_dir = preprocess_mermaid(src)
        try:
            out = actual.read_text(encoding="utf-8")
            refs = [seg.split(")")[0] for seg in out.split("![diagram](")[1:]]
            assert len(refs) == 2
            assert len(set(refs)) == 2    # diagram_0 / diagram_1，不互踩
        finally:
            cleanup_temp_dir(tmp_dir)

    def test_local_svg_ref_rewritten(self, tmp_path):
        """本地 .svg 文件引用（相对 md 目录）：读文件同桥转 PNG。"""
        (tmp_path / "arch.svg").write_text(_SVG_SRC, encoding="utf-8")
        src = tmp_path / "doc.md"
        src.write_text("![](arch.svg)", encoding="utf-8")
        with patch("tools.mermaid_bridge.find_electron", return_value="electron"), \
             patch("tools.mermaid_bridge.render_svg_to_png", return_value=_PNG):
            actual, tmp_dir = preprocess_mermaid(src)
        try:
            out = actual.read_text(encoding="utf-8")
            assert "arch.svg" not in out
            img_ref = out.split("![diagram](")[1].split(")")[0]
            assert Path(img_ref).read_bytes() == _PNG
        finally:
            cleanup_temp_dir(tmp_dir)

    def test_local_svg_ref_missing_kept(self, tmp_path):
        """引用的 svg 文件不存在：保留原引用（降级），不算失败也不阻断。"""
        src = tmp_path / "doc.md"
        src.write_text("![](nope.svg)", encoding="utf-8")
        with patch("tools.mermaid_bridge.find_electron", return_value="electron"):
            actual, tmp_dir = preprocess_mermaid(src)
        assert actual == src
        assert tmp_dir is None
        assert "nope.svg" in src.read_text(encoding="utf-8")

    def test_png_ref_only_passes_through(self, tmp_path):
        """仅 png/jpg 引用：无图形块，直通不起子进程。"""
        src = tmp_path / "doc.md"
        src.write_text("![](a.png) ![](b.jpg)", encoding="utf-8")
        actual, tmp_dir = preprocess_mermaid(src)
        assert actual == src
        assert tmp_dir is None


# ── cleanup_temp_dir ────────────────────────────────────────


class TestCleanupTempDir:
    def test_none_is_safe(self):
        # None 不抛异常（renderer 无 mermaid 路径每次都会以 None 调用）
        cleanup_temp_dir(None)

    def test_removes_dir(self):
        d = tempfile.mkdtemp(prefix="inkcube_mmd_test_")
        assert os.path.isdir(d)
        cleanup_temp_dir(d)
        assert not os.path.isdir(d)


# ── _resolve_renderer_script（C1 打包路径修复回归）──────────


class TestResolveRendererScript:
    """打包版渲染脚本定位：app.asar 对 Python 不透明（isfile 无法穿透），
    脚本经 extraResources 分发并由主进程注入 env；dev 回退仓库相对路径。"""

    def test_env_override_takes_priority(self, tmp_path, monkeypatch):
        """env 指向的脚本存在 → 优先使用（打包路径唯一入口）。"""
        script = tmp_path / "mermaid-renderer.js"
        script.write_text("// renderer", encoding="utf-8")
        monkeypatch.setenv("INKCUBE_MERMAID_RENDERER_SCRIPT", str(script))
        assert _resolve_renderer_script() == str(script)

    def test_env_missing_file_falls_back_to_repo_path(self, monkeypatch):
        """env 指向不存在的文件（配置残留/被杀软清理）→ 回退仓库路径，不报错。"""
        monkeypatch.setenv("INKCUBE_MERMAID_RENDERER_SCRIPT", "Z:/nope/renderer.js")
        resolved = _resolve_renderer_script()
        assert resolved.endswith("mermaid-renderer.js")
        assert "electron" in resolved

    def test_no_env_falls_back_to_repo_path(self, monkeypatch):
        """dev（无 env）→ 仓库 electron/mermaid-renderer.js。"""
        monkeypatch.delenv("INKCUBE_MERMAID_RENDERER_SCRIPT", raising=False)
        resolved = _resolve_renderer_script()
        assert Path(resolved) == (
            Path(__file__).resolve().parents[2] / "electron" / "mermaid-renderer.js"
        )


# ── 真实 Electron 集成（无 electron 环境自动跳过）──────────


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ELECTRON = _PROJECT_ROOT / 'node_modules' / 'electron' / 'dist' / (
    'electron.exe' if sys.platform == 'win32' else 'electron')
_MERMAID_JS = _PROJECT_ROOT / 'node_modules' / 'mermaid' / 'dist' / 'mermaid.min.js'


@pytest.mark.skipif(
    not (_ELECTRON.is_file() and _MERMAID_JS.is_file()),
    reason="dev electron / mermaid.min.js not available — real-process tests skipped",
)
class TestElectronIntegration:
    """真实 Electron 子进程冒烟：覆盖渲染器进程内部行为（mock 层不可见）。"""

    def test_minimal_diagram_renders_to_png(self):
        png = render_mermaid_to_png(str(_ELECTRON), "graph LR\n  A[开始] --> B[结束]")
        assert png is not None
        assert png.startswith(b"\x89PNG")
        # 2× 光栅：解析 IHDR 宽高，确认确实放大过（小图 1× 宽 ≈ 300，2× 应 ≥ 500）
        import struct
        w, h = struct.unpack(">II", png[16:24])
        assert w >= 500, f"PNG 宽 {w}px，疑似未按 2× 光栅"
        assert len(png) > 1500

    def test_invalid_syntax_fails_gracefully(self):
        """非法语法 → 渲染器 exit 非 0 → None（降级契约）。"""
        png = render_mermaid_to_png(str(_ELECTRON), "this is not a mermaid diagram !!!")
        assert png is None

    def test_inline_svg_viewbox_only_renders_to_png(self):
        """真实链路回归（导出丢图根因）：viewBox-only + 中文 text 的内联 SVG
        经真实 Electron 光栅化；尺寸按 viewBox 比例 2× 出图。"""
        import struct
        svg = (
            '<svg viewBox="0 0 300 150" xmlns="http://www.w3.org/2000/svg" '
            'font-family=\'Microsoft YaHei\'>\n'
            '  <rect x="10" y="10" width="280" height="130" fill="#eef" stroke="#334"/>\n'
            '  <text x="150" y="80" text-anchor="middle" font-size="20">架构图</text>\n'
            '</svg>'
        )
        png = render_svg_to_png(str(_ELECTRON), svg)
        assert png is not None
        assert png.startswith(b"\x89PNG")
        w, h = struct.unpack(">II", png[16:24])
        assert (w, h) == (600, 300), f"期望 viewBox 300×150 的 2× 光栅，得 {w}×{h}"

    def test_inline_svg_external_ref_still_rasterizes(self):
        """含外链 image/script 的 SVG：渲染器侧剥离后仍出图（不因清洗失败）。"""
        svg = (
            '<svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">\n'
            '  <script>alert(1)</script>\n'
            '  <image href="https://evil.example/x.png" width="10" height="10"/>\n'
            '  <rect x="5" y="5" width="90" height="90" fill="green"/>\n'
            '</svg>'
        )
        png = render_svg_to_png(str(_ELECTRON), svg)
        assert png is not None
        assert png.startswith(b"\x89PNG")
