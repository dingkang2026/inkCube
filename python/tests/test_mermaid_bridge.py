"""python/tests/test_mermaid_bridge.py — Mermaid → PNG 离线渲染桥单测（§3.7）。

覆盖：
  - MERMAID_RE：```mermaid 围栏匹配、非 mermaid 围栏不识别、多块全匹配
  - render_mermaid_to_png：非零退出 / 非法 base64 / 非 PNG 内容 / 超时 → None；
    合法 PNG 原样返回；输入经临时文件传递（Windows 下管道 stdin 读 0 字节，回归用例）
  - preprocess_mermaid 四条路径：无块 / Electron 不可用 / 成功（替换 + PNG 落盘）/
    全部失败（保留原文件），调用方契约 (actual_input, temp_dir_or_None)
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
    MERMAID_RE,
    _resolve_renderer_script,
    cleanup_temp_dir,
    preprocess_mermaid,
    render_mermaid_to_png,
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
