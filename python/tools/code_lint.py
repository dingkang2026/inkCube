"""code_lint tool — static analysis using ruff check."""
import json
import logging
import os
import asyncio
import subprocess
from pathlib import Path

from tools.base import BaseTool
from tools.tool_resolver import resolve_tool

logger = logging.getLogger(__name__)


class CodeLintTool(BaseTool):
    name = "code_lint"
    description = (
        "对工作区中的 Python 文件执行静态分析（lint），"
        "检查代码错误、风格问题和潜在 bug。使用 ruff 作为后端。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "要检查的文件或目录的相对路径（相对于工作区），默认为 '.'（整个工作区）",
            },
            "fix": {
                "type": "boolean",
                "description": "是否自动修复可修复的问题（默认 false）",
            },
            "select": {
                "type": "string",
                "description": "只检查指定的规则类别，逗号分隔，如 'E,F,W'（可选）",
            },
        },
        "required": [],
    }

    @property
    def prompt_description(self) -> str:
        return (
            "## code_lint\n"
            "对 Python 代码执行静态分析（ruff check），检查语法错误、风格问题、未使用的导入等。\n\n"
            "用法:\n"
            "- 创建或修改 Python 文件后，用此工具检查代码质量\n"
            "- 设置 fix=true 可自动修复部分问题（如移除未使用的导入）\n"
            "- 用 select 只检查特定规则，如 'E,W' 检查 pycodestyle\n\n"
            "示例:\n"
            '```\n{"path": "main.py"}\n```\n\n'
            '```\n{"path": ".", "fix": true}\n```\n\n'
            "返回: 问题数量、每个问题的文件/行号/规则/描述。"
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
        fix: bool = False,
        select: str = "",
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

        cmd = [ruff, "check", "--output-format", "json"]
        if fix:
            cmd.append("--fix")
        if select:
            cmd.extend(["--select", select])
        cmd.append(str(target_path))

        try:
            # Worker thread — keep the event loop responsive (H1)
            result = await asyncio.to_thread(
                subprocess.run,
                cmd, capture_output=True, encoding="utf-8", errors="replace", timeout=30, cwd=str(workspace),
            )
            # ruff exits 1 when issues are found (not an error)
            issues = []
            try:
                raw = json.loads(result.stdout) if result.stdout.strip() else []
            except json.JSONDecodeError:
                raw = []

            logger.info("code_lint: path=%s, issues=%d, fix=%s", path, len(raw), fix)

            for item in raw:
                issues.append({
                    "file": str(Path(item.get("filename", "")).relative_to(workspace)) if item.get("filename") else "",
                    "line": item.get("location", {}).get("row", 0),
                    "col": item.get("location", {}).get("column", 0),
                    "code": item.get("code", ""),
                    "message": item.get("message", ""),
                    "url": item.get("url", ""),
                    "fixable": item.get("fix", None) is not None,
                })

            # Count by severity
            errors = sum(1 for i in issues if i["code"].startswith("E"))
            warnings = sum(1 for i in issues if i["code"].startswith(("W", "F")))
            other = len(issues) - errors - warnings

            return json.dumps({
                "exit_code": 0 if not issues else 1,
                "total_issues": len(issues),
                "errors": errors,
                "warnings": warnings,
                "other": other,
                "issues": issues[:100],  # limit output
                "fixable_count": sum(1 for i in issues if i["fixable"]),
                "fixed": fix,
            }, ensure_ascii=False)

        except subprocess.TimeoutExpired:
            return json.dumps({"error": "lint 检查超时（30 秒）"}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
