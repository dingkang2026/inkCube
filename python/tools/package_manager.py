"""package_manager tool — manage Python dependencies using uv pip.

装包唯一推荐通道（2026-10-16 装包路径统一，1.1 §2.3）：uv 优先（不依赖
venv 内 pip——uv 创建的 venv 无 pip.exe）+ 腾讯云镜像 + 300s 超时，
与 venv_manager 预装链同一标准；pip 仅作 uv 不可用时的兑底。
"""
import json
import logging
import os
import asyncio
import subprocess
import sys
from pathlib import Path

from tools.proc_priority import low_priority_kwargs

from tools.base import BaseTool
from tools.venv_manager import PYPI_MIRROR

logger = logging.getLogger(__name__)


class PackageManagerTool(BaseTool):
    name = "package_manager"
    description = (
        "管理任务虚拟环境中的 Python 依赖包。"
        "支持安装、卸载、列出已安装的包。"
        "uv 优先（国内镜像 + 300s 超时，不依赖 venv 内 pip），pip 兑底。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["install", "uninstall", "list"],
                "description": "操作类型: install（安装包）、uninstall（卸载包）、list（列出已安装包）",
            },
            "packages": {
                "type": "array",
                "items": {"type": "string"},
                "description": "包名列表（install/uninstall 时必填），如 ['requests', 'flask>=2.0']",
            },
            "requirements": {
                "type": "string",
                "description": "requirements.txt 文件的相对路径（install 时可选，用于批量安装）",
            },
        },
        "required": ["action"],
    }

    @property
    def prompt_description(self) -> str:
        return (
            "## package_manager\n"
            "管理任务虚拟环境中的 Python 依赖包（uv 优先，自动使用国内镜像）。\n"
            "安装 Python 依赖请一律使用本工具，勿在 shell 里直接 pip install"
            "（任务 venv 由 uv 创建、不含 pip，PATH 中的 pip 属于其他环境）。\n\n"
            "操作:\n"
            "- install: 安装包，支持版本约束如 'flask>=2.0'\n"
            "- uninstall: 卸载包\n"
            "- list: 列出已安装的所有包\n\n"
            "示例:\n"
            '```\n{"action": "install", "packages": ["requests", "pandas"]}\n```\n\n'
            '```\n{"action": "install", "requirements": "requirements.txt"}\n```\n\n'
            '```\n{"action": "list"}\n```\n\n'
            "注意: 每个任务有独立的虚拟环境，安装的包只在此任务中可用；"
            "成功输出中的 venv 字段即目标环境路径。"
        )

    def __init__(self, workspace_dir: str = "", venv_path: str = ""):
        self.workspace_dir = workspace_dir
        self.venv_path = venv_path

    def set_workspace(self, workspace_dir: str):
        self.workspace_dir = workspace_dir

    def set_venv_path(self, venv_path: str):
        self.venv_path = venv_path

    def _get_pip(self) -> str | None:
        """Get the pip executable path for the task venv."""
        if not self.venv_path:
            return None
        if sys.platform == "win32":
            pip = os.path.join(self.venv_path, "Scripts", "pip.exe")
        else:
            pip = os.path.join(self.venv_path, "bin", "pip")
        return pip if os.path.isfile(pip) else None

    def _venv_python(self) -> str:
        """Path to the python interpreter inside the task venv."""
        if sys.platform == "win32":
            return os.path.join(self.venv_path, "Scripts", "python.exe")
        return os.path.join(self.venv_path, "bin", "python")

    def _find_uv(self) -> str | None:
        """Try to find uv binary."""
        from tools.tool_resolver import resolve_tool
        return resolve_tool("uv")

    async def execute(
        self,
        action: str,
        packages: list[str] | None = None,
        requirements: str = "",
    ) -> str:
        logger.info("package_manager: action=%s, packages=%s", action, packages)
        if not self.venv_path:
            return json.dumps({
                "error": "虚拟环境未设置",
                "suggestion": "请先创建任务以初始化虚拟环境",
            }, ensure_ascii=False)

        # All pip/uv subprocess calls run in worker threads — pip install can
        # take 120s and would otherwise freeze the whole event loop (H1)
        if action == "list":
            return await asyncio.to_thread(self._list_packages)
        elif action == "install":
            return await asyncio.to_thread(self._install_packages, packages, requirements)
        elif action == "uninstall":
            return await asyncio.to_thread(self._uninstall_packages, packages)
        else:
            return json.dumps({"error": f"未知操作: {action}"}, ensure_ascii=False)

    def _list_packages(self) -> str:
        """List installed packages. uv first (uv-created venvs have no pip), pip fallback."""
        uv = self._find_uv()
        if uv:
            try:
                result = subprocess.run(
                    [uv, "pip", "list", "--python", self._venv_python(),
                     "--format", "json"],
                    capture_output=True, encoding="utf-8", errors="replace", timeout=30,
                    **low_priority_kwargs(),   # 2.11 §5.6 R3
                )
                if result.returncode == 0:
                    pkgs = json.loads(result.stdout) if result.stdout.strip() else []
                    return json.dumps({
                        "exit_code": 0,
                        "backend": "uv",
                        "venv": self.venv_path,
                        "packages": pkgs,
                        "total": len(pkgs),
                    }, ensure_ascii=False)
                logger.warning("uv pip list failed (%s), falling back to pip",
                               (result.stderr or "")[-200:])
            except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
                logger.warning("uv pip list failed (%s), falling back to pip", e)

        pip = self._get_pip()
        if not pip:
            return json.dumps({
                "error": "uv 与 venv 内 pip 均不可用，虚拟环境可能未正确创建",
            }, ensure_ascii=False)

        try:
            result = subprocess.run(
                [pip, "list", "--format", "json"],
                capture_output=True, encoding="utf-8", errors="replace", timeout=30,
                **low_priority_kwargs(),   # 2.11 §5.6 R3
            )
            pkgs = json.loads(result.stdout) if result.stdout.strip() else []
            return json.dumps({
                "exit_code": 0,
                "backend": "pip",
                "venv": self.venv_path,
                "packages": pkgs,
                "total": len(pkgs),
            }, ensure_ascii=False)
        except subprocess.TimeoutExpired:
            return json.dumps({"error": "列出包超时"}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    def _install_packages(self, packages: list[str] | None, requirements: str) -> str:
        if not packages and not requirements:
            return json.dumps({
                "error": "请指定要安装的包或 requirements 文件路径",
            }, ensure_ascii=False)

        uv = self._find_uv()
        pip = self._get_pip()

        # Prefer uv if available, fallback to pip
        if uv:
            return self._uv_install(uv, packages, requirements)
        elif pip:
            return self._pip_install(pip, packages, requirements)
        else:
            return json.dumps({"error": "uv 和 pip 均不可用"}, ensure_ascii=False)

    def _validate_requirements_path(self, requirements: str) -> str | None:
        """Validate requirements file path is within workspace. Returns resolved path or error message."""
        if not self.workspace_dir:
            return None
        workspace = Path(self.workspace_dir).resolve()
        req_path = (workspace / requirements).resolve()
        if req_path != workspace and not str(req_path).startswith(str(workspace) + os.sep):
            return None  # path traversal blocked
        if req_path.exists():
            return str(req_path)
        return None

    def _uv_install(self, uv: str, packages: list[str] | None, requirements: str) -> str:
        # 镜像：官方源慢网下 resolve 28s~1m37s（2026-10-12 实测），
        # 与预装链同一常量同一标准（venv_manager.PYPI_MIRROR）
        cmd = [uv, "pip", "install", "--default-index", PYPI_MIRROR]
        if self.venv_path:
            cmd.extend(["--python", self._venv_python()])
        if requirements and self.workspace_dir:
            workspace = Path(self.workspace_dir).resolve()
            req_path = (workspace / requirements).resolve()
            if req_path != workspace and not str(req_path).startswith(str(workspace) + os.sep):
                return json.dumps({"error": "requirements 路径超出工作区范围"}, ensure_ascii=False)
            if req_path.exists():
                cmd.extend(["-r", str(req_path)])
            else:
                return json.dumps({"error": f"requirements 文件不存在: {requirements}"}, ensure_ascii=False)
        if packages:
            cmd.extend(packages)

        try:
            result = subprocess.run(
                cmd, capture_output=True, encoding="utf-8", errors="replace",
                timeout=300,  # playwright ~40MB wheel 冷缓存下载需较长时间（对齐预装链）
                cwd=self.workspace_dir,
                **low_priority_kwargs(),   # 2.11 §5.6 R3
            )
            if result.returncode == 0:
                installed = packages or [f"from {requirements}"]
                return json.dumps({
                    "exit_code": 0,
                    "backend": "uv",
                    "venv": self.venv_path,
                    "installed": installed,
                    "output": result.stdout[-500:] if result.stdout else "",
                }, ensure_ascii=False)
            else:
                return json.dumps({
                    "exit_code": result.returncode,
                    "error": result.stderr[-500:] if result.stderr else "安装失败",
                }, ensure_ascii=False)
        except subprocess.TimeoutExpired:
            return json.dumps({"error": "安装超时（300 秒）"}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    def _pip_install(self, pip: str, packages: list[str] | None, requirements: str) -> str:
        cmd = [pip, "install", "-i", PYPI_MIRROR]
        if requirements and self.workspace_dir:
            workspace = Path(self.workspace_dir).resolve()
            req_path = (workspace / requirements).resolve()
            if req_path != workspace and not str(req_path).startswith(str(workspace) + os.sep):
                return json.dumps({"error": "requirements 路径超出工作区范围"}, ensure_ascii=False)
            if req_path.exists():
                cmd.extend(["-r", str(req_path)])
            else:
                return json.dumps({"error": f"requirements 文件不存在: {requirements}"}, ensure_ascii=False)
        if packages:
            cmd.extend(packages)

        try:
            result = subprocess.run(
                cmd, capture_output=True, encoding="utf-8", errors="replace",
                timeout=300,  # 对齐 uv 分支与预装链
                cwd=self.workspace_dir,
                **low_priority_kwargs(),   # 2.11 §5.6 R3
            )
            if result.returncode == 0:
                installed = packages or [f"from {requirements}"]
                return json.dumps({
                    "exit_code": 0,
                    "backend": "pip",
                    "venv": self.venv_path,
                    "installed": installed,
                    "output": result.stdout[-500:] if result.stdout else "",
                }, ensure_ascii=False)
            else:
                return json.dumps({
                    "exit_code": result.returncode,
                    "error": result.stderr[-500:] if result.stderr else "安装失败",
                }, ensure_ascii=False)
        except subprocess.TimeoutExpired:
            return json.dumps({"error": "安装超时（300 秒）"}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    def _uninstall_packages(self, packages: list[str] | None) -> str:
        if not packages:
            return json.dumps({"error": "请指定要卸载的包"}, ensure_ascii=False)

        # uv 优先：uv 创建的 venv 无 pip.exe（与 install / list 一致）
        uv = self._find_uv()
        if uv:
            try:
                result = subprocess.run(
                    [uv, "pip", "uninstall", "--python", self._venv_python(), "-y", *packages],
                    capture_output=True, encoding="utf-8", errors="replace", timeout=30,
                    **low_priority_kwargs(),   # 2.11 §5.6 R3
                )
                if result.returncode == 0:
                    return json.dumps({
                        "exit_code": 0,
                        "backend": "uv",
                        "venv": self.venv_path,
                        "uninstalled": packages,
                        "output": result.stdout[-500:] if result.stdout else "",
                    }, ensure_ascii=False)
                logger.warning("uv pip uninstall failed (%s), falling back to pip",
                               (result.stderr or "")[-200:])
            except (subprocess.TimeoutExpired, OSError) as e:
                logger.warning("uv pip uninstall failed (%s), falling back to pip", e)

        pip = self._get_pip()
        if not pip:
            return json.dumps({"error": "uv 与 venv 内 pip 均不可用"}, ensure_ascii=False)

        cmd = [pip, "uninstall", "-y"] + packages
        try:
            result = subprocess.run(
                cmd, capture_output=True, encoding="utf-8", errors="replace", timeout=30,
                **low_priority_kwargs(),   # 2.11 §5.6 R3
            )
            return json.dumps({
                "exit_code": result.returncode,
                "backend": "pip",
                "venv": self.venv_path,
                "uninstalled": packages,
                "output": result.stdout[-500:] if result.stdout else "",
            }, ensure_ascii=False)
        except subprocess.TimeoutExpired:
            return json.dumps({"error": "卸载超时"}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
