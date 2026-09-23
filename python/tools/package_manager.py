"""package_manager tool — manage Python dependencies using uv pip."""
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from tools.base import BaseTool

logger = logging.getLogger(__name__)


class PackageManagerTool(BaseTool):
    name = "package_manager"
    description = (
        "管理任务虚拟环境中的 Python 依赖包。"
        "支持安装、卸载、列出已安装的包。使用 uv pip 作为后端。"
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
            "管理任务虚拟环境中的 Python 依赖包（uv pip）。\n\n"
            "操作:\n"
            "- install: 安装包，支持版本约束如 'flask>=2.0'\n"
            "- uninstall: 卸载包\n"
            "- list: 列出已安装的所有包\n\n"
            "示例:\n"
            '```\n{"action": "install", "packages": ["requests", "pandas"]}\n```\n\n'
            '```\n{"action": "install", "requirements": "requirements.txt"}\n```\n\n'
            '```\n{"action": "list"}\n```\n\n'
            "注意: 每个任务有独立的虚拟环境，安装的包只在此任务中可用。"
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

        if action == "list":
            return self._list_packages()
        elif action == "install":
            return self._install_packages(packages, requirements)
        elif action == "uninstall":
            return self._uninstall_packages(packages)
        else:
            return json.dumps({"error": f"未知操作: {action}"}, ensure_ascii=False)

    def _list_packages(self) -> str:
        pip = self._get_pip()
        if not pip:
            return json.dumps({"error": "pip 不可用，虚拟环境可能未正确创建"}, ensure_ascii=False)

        try:
            result = subprocess.run(
                [pip, "list", "--format", "json"],
                capture_output=True, encoding="utf-8", errors="replace", timeout=15,
            )
            pkgs = json.loads(result.stdout) if result.stdout.strip() else []
            return json.dumps({
                "exit_code": 0,
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
        cmd = [uv, "pip", "install"]
        if self.venv_path:
            cmd.extend(["--python", os.path.join(self.venv_path, "Scripts", "python.exe")
                        if sys.platform == "win32"
                        else os.path.join(self.venv_path, "bin", "python")])
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
                cmd, capture_output=True, encoding="utf-8", errors="replace", timeout=120,
                cwd=self.workspace_dir,
            )
            if result.returncode == 0:
                installed = packages or [f"from {requirements}"]
                return json.dumps({
                    "exit_code": 0,
                    "installed": installed,
                    "output": result.stdout[-500:] if result.stdout else "",
                }, ensure_ascii=False)
            else:
                return json.dumps({
                    "exit_code": result.returncode,
                    "error": result.stderr[-500:] if result.stderr else "安装失败",
                }, ensure_ascii=False)
        except subprocess.TimeoutExpired:
            return json.dumps({"error": "安装超时（120 秒）"}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    def _pip_install(self, pip: str, packages: list[str] | None, requirements: str) -> str:
        cmd = [pip, "install"]
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
                cmd, capture_output=True, encoding="utf-8", errors="replace", timeout=120,
                cwd=self.workspace_dir,
            )
            if result.returncode == 0:
                installed = packages or [f"from {requirements}"]
                return json.dumps({
                    "exit_code": 0,
                    "installed": installed,
                    "output": result.stdout[-500:] if result.stdout else "",
                }, ensure_ascii=False)
            else:
                return json.dumps({
                    "exit_code": result.returncode,
                    "error": result.stderr[-500:] if result.stderr else "安装失败",
                }, ensure_ascii=False)
        except subprocess.TimeoutExpired:
            return json.dumps({"error": "安装超时（120 秒）"}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    def _uninstall_packages(self, packages: list[str] | None) -> str:
        if not packages:
            return json.dumps({"error": "请指定要卸载的包"}, ensure_ascii=False)

        pip = self._get_pip()
        if not pip:
            return json.dumps({"error": "pip 不可用"}, ensure_ascii=False)

        cmd = [pip, "uninstall", "-y"] + packages
        try:
            result = subprocess.run(
                cmd, capture_output=True, encoding="utf-8", errors="replace", timeout=30,
            )
            return json.dumps({
                "exit_code": result.returncode,
                "uninstalled": packages,
                "output": result.stdout[-500:] if result.stdout else "",
            }, ensure_ascii=False)
        except subprocess.TimeoutExpired:
            return json.dumps({"error": "卸载超时"}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
