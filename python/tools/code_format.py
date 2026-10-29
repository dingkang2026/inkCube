"""code_format tool — Python code formatting using ruff format."""
import json
import os
import asyncio
import subprocess
from pathlib import Path

from tools.base import BaseTool
from tools.proc_priority import hidden_window_kwargs
from tools.tool_resolver import resolve_tool


class CodeFormatTool(BaseTool):
    name = "code_format"
    description = (
        "对工作区中的 Python 文件执行代码格式化。"
        "使用 ruff format，遵循 PEP 8 风格。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "要格式化的文件或目录的相对路径（相对于工作区），默认为 '.'（整个工作区）",
            },
            "check": {
                "type": "boolean",
                "description": "只检查是否需要格式化，不实际修改（默认 false）",
            },
            "line_length": {
                "type": "integer",
                "description": "最大行宽（默认 88）",
            },
        },
        "required": [],
    }

    @property
    def prompt_description(self) -> str:
        return (
            "## code_format\n"
            "对 Python 代码执行格式化（ruff format），统一代码风格。\n\n"
            "用法:\n"
            "- 创建或修改 Python 文件后，用此工具统一格式\n"
            "- 设置 check=true 只检查不修改，返回需要格式化的文件列表\n"
            "- 可指定 line_length 控制行宽\n\n"
            "示例:\n"
            '```\n{"path": "main.py"}\n```\n\n'
            '```\n{"path": ".", "check": true}\n```\n\n'
            "返回: 格式化的文件数量、变更详情。"
        )

    def __init__(self, workspace_dir: str = ""):
        self.workspace_dir = workspace_dir

    def set_workspace(self, workspace_dir: str):
        self.workspace_dir = workspace_dir

    def _find_ruff(self) -> str | None:
        """Try to find ruff binary."""
        return resolve_tool("ruff")

    async def execute(
        self,
        path: str = ".",
        check: bool = False,
        line_length: int = 88,
    ) -> str:
        if not self.workspace_dir:
            return json.dumps({"error": "工作目录未设置"}, ensure_ascii=False)

        ruff = self._find_ruff()
        if not ruff:
            return json.dumps({
                "error": "ruff 未安装",
                "suggestion": "请安装 ruff: pip install ruff，或将 ruff 添加到 PATH",
            }, ensure_ascii=False)

        workspace = Path(self.workspace_dir).resolve()
        target_path = (workspace / path).resolve()
        if target_path != workspace and not str(target_path).startswith(str(workspace) + os.sep):
            return json.dumps({"error": "路径超出工作区范围"}, ensure_ascii=False)
        if not target_path.exists():
            return json.dumps({"error": f"路径不存在: {path}"}, ensure_ascii=False)

        cmd = [ruff, "format"]
        if check:
            cmd.append("--check")
        if line_length != 88:
            cmd.extend(["--line-length", str(line_length)])
        cmd.append(str(target_path))

        try:
            # Worker thread — keep the event loop responsive (H1)
            result = await asyncio.to_thread(
                subprocess.run,
                cmd, capture_output=True, encoding="utf-8", errors="replace", timeout=30, cwd=str(workspace),
                **hidden_window_kwargs(),  # 无控制台父进程下 ruff 闪窗（2026-10-28）
            )

            # Parse ruff format output
            stdout = result.stdout.strip()
            changed_files: list[str] = []
            already_formatted = 0
            reformatted = 0

            if check:
                # In check mode, ruff lists files that would be reformatted
                import re as _re
                for line in stdout.split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    if "already formatted" in line:
                        m = _re.search(r"(\d+) files? already formatted", line)
                        if m:
                            already_formatted = int(m.group(1))
                    elif not line.startswith("Would"):
                        try:
                            rel = str(Path(line).relative_to(workspace))
                        except ValueError:
                            rel = line
                        changed_files.append(rel)
            else:
                # In format mode, parse the summary
                import re as _re
                m = _re.search(r"(\d+) files? reformatted", stdout)
                reformatted = int(m.group(1)) if m else 0
                m2 = _re.search(r"(\d+) files? already formatted", stdout)
                already_formatted = int(m2.group(1)) if m2 else 0

            return json.dumps({
                "exit_code": result.returncode,
                "reformatted": reformatted if not check else len(changed_files),
                "already_formatted": already_formatted,
                "files": changed_files if check else [],
                "check_mode": check,
            }, ensure_ascii=False)

        except subprocess.TimeoutExpired:
            return json.dumps({"error": "格式化超时（30 秒）"}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
