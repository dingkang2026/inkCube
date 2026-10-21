# -*- coding: utf-8 -*-
"""python/tests/test_package_manager.py — 装包路径统一回归（2026-10-16，1.1 §2.3）。

回归背景（实测 5 轮依赖穿越）：uv 创建的任务 venv 不含 pip.exe，PATH 前插
后裸 pip 漂移到后端运行时环境把包装错地方。修复后 package_manager 是装包
唯一正道：uv 优先（--default-index 镜像 + 300s 超时，与预装链同一标准）+
list/uninstall 同样 uv 优先、pip 兜底 + 成功输出携带目标 venv 路径。
"""
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.package_manager import PackageManagerTool  # noqa: E402
from tools.venv_manager import PYPI_MIRROR  # noqa: E402


def _ok_proc(stdout: str = "") -> MagicMock:
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = stdout
    proc.stderr = ""
    return proc


def _fail_proc(stderr: str = "boom") -> MagicMock:
    proc = MagicMock()
    proc.returncode = 1
    proc.stdout = ""
    proc.stderr = stderr
    return proc


@pytest.fixture()
def tool(tmp_path) -> PackageManagerTool:
    return PackageManagerTool(workspace_dir=str(tmp_path),
                              venv_path=str(tmp_path / ".venv"))


@pytest.fixture()
def make_fake_pip(tmp_path):
    """在假 venv 内创建 pip 可执行文件（按平台放 Scripts/bin），返回路径。"""
    def _make() -> str:
        scripts = "Scripts" if sys.platform == "win32" else "bin"
        exe = "pip.exe" if sys.platform == "win32" else "pip"
        pip_dir = tmp_path / ".venv" / scripts
        pip_dir.mkdir(parents=True, exist_ok=True)
        pip_path = pip_dir / exe
        pip_path.write_text("", encoding="utf-8")
        return str(pip_path)
    return _make


class TestInstall:
    def test_uv_install_uses_mirror_and_300s(self, tool):
        """uv 优先：--default-index 镜像 + --python 指向 venv + timeout 300（对齐预装链）。"""
        with patch("tools.tool_resolver.resolve_tool", return_value="C:/tools/uv.exe"), \
             patch("tools.package_manager.subprocess.run",
                   return_value=_ok_proc("Installed 2 packages")) as run:
            out = json.loads(tool._install_packages(["requests"], ""))

        cmd = run.call_args.args[0]
        assert cmd[:3] == ["C:/tools/uv.exe", "pip", "install"]
        assert "--default-index" in cmd and PYPI_MIRROR in cmd
        py_idx = cmd.index("--python")
        assert cmd[py_idx + 1].lower().endswith(
            ("python.exe", "python")), "必须 --python 指向任务 venv（不依赖 venv 内 pip）"
        assert cmd[-1] == "requests"
        assert run.call_args.kwargs["timeout"] == 300

        assert out["exit_code"] == 0
        assert out["backend"] == "uv"
        assert out["venv"] == tool.venv_path, "成功输出必须携带目标环境路径（装哪了可见）"
        assert out["installed"] == ["requests"]

    def test_pip_fallback_also_uses_mirror_and_300s(self, tool, make_fake_pip):
        """uv 不可用时 pip 兜底：-i 镜像 + timeout 300（不再用裸官方源）。"""
        make_fake_pip()
        with patch("tools.tool_resolver.resolve_tool", return_value=None), \
             patch("tools.package_manager.subprocess.run",
                   return_value=_ok_proc("Successfully installed requests")) as run:
            out = json.loads(tool._install_packages(["requests"], ""))

        cmd = run.call_args.args[0]
        assert "-i" in cmd and PYPI_MIRROR in cmd
        assert run.call_args.kwargs["timeout"] == 300
        assert out["backend"] == "pip"
        assert out["venv"] == tool.venv_path

    def test_uv_unavailable_and_no_pip_reports_error(self, tool):
        with patch("tools.tool_resolver.resolve_tool", return_value=None):
            out = json.loads(tool._install_packages(["requests"], ""))
        assert "error" in out


class TestList:
    def test_uv_list_works_without_pip_exe(self, tool):
        """uv 创建的 venv 无 pip.exe：list 必须走 uv 路径而非报「pip 不可用」。"""
        with patch("tools.tool_resolver.resolve_tool", return_value="uv"), \
             patch("tools.package_manager.subprocess.run",
                   return_value=_ok_proc('[{"name": "requests"}]')) as run:
            out = json.loads(tool._list_packages())

        cmd = run.call_args.args[0]
        assert cmd[:3] == ["uv", "pip", "list"]
        assert "--python" in cmd
        assert out["backend"] == "uv"
        assert out["packages"] == [{"name": "requests"}]
        assert out["total"] == 1

    def test_list_falls_back_to_pip_when_uv_fails(self, tool, make_fake_pip):
        make_fake_pip()
        with patch("tools.tool_resolver.resolve_tool", return_value="uv"), \
             patch("tools.package_manager.subprocess.run",
                   side_effect=[_fail_proc("uv broken"), _ok_proc('[{"name": "pip"}]')]):
            out = json.loads(tool._list_packages())
        assert out["backend"] == "pip"
        assert out["packages"] == [{"name": "pip"}]

    def test_list_pip_fallback_when_no_uv(self, tool, make_fake_pip):
        make_fake_pip()
        with patch("tools.tool_resolver.resolve_tool", return_value=None), \
             patch("tools.package_manager.subprocess.run",
                   return_value=_ok_proc('[{"name": "pillow"}]')):
            out = json.loads(tool._list_packages())
        assert out["backend"] == "pip"
        assert out["packages"] == [{"name": "pillow"}]


class TestUninstall:
    def test_uv_uninstall_reports_venv(self, tool):
        with patch("tools.tool_resolver.resolve_tool", return_value="uv"), \
             patch("tools.package_manager.subprocess.run",
                   return_value=_ok_proc("Uninstalled requests")) as run:
            out = json.loads(tool._uninstall_packages(["requests"]))

        cmd = run.call_args.args[0]
        assert cmd[:3] == ["uv", "pip", "uninstall"]
        assert "--python" in cmd
        assert out["backend"] == "uv"
        assert out["venv"] == tool.venv_path
        assert out["uninstalled"] == ["requests"]

    def test_uninstall_falls_back_to_pip(self, tool, make_fake_pip):
        make_fake_pip()
        with patch("tools.tool_resolver.resolve_tool", return_value="uv"), \
             patch("tools.package_manager.subprocess.run",
                   side_effect=[_fail_proc("uv broken"), _ok_proc("Removed")]):
            out = json.loads(tool._uninstall_packages(["requests"]))
        assert out["backend"] == "pip"

    def test_uninstall_no_backend_reports_error(self, tool):
        with patch("tools.tool_resolver.resolve_tool", return_value=None):
            out = json.loads(tool._uninstall_packages(["requests"]))
        assert "error" in out
