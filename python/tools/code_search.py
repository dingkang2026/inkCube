import asyncio
import json
import logging
import os
import re
import subprocess
from pathlib import Path

from tools.base import BaseTool
from tools.tool_resolver import resolve_tool

logger = logging.getLogger(__name__)


class CodeSearchTool(BaseTool):
    name = "code_search"
    description = (
        "在任务工作区中搜索代码或文本内容。"
        "支持正则表达式搜索，返回匹配的文件路径、行号和行内容。"
        "优先使用 ripgrep（如果可用），否则回退到 Python 内置搜索。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "搜索模式（支持正则表达式）",
            },
            "path": {
                "type": "string",
                "description": "搜索的相对路径（相对于工作区），默认为 '.'（整个工作区）",
            },
            "file_pattern": {
                "type": "string",
                "description": "文件名过滤 glob 模式，如 '*.py'、'*.ts'（可选）",
            },
            "max_results": {
                "type": "integer",
                "description": "最大返回结果数（默认 50）",
            },
        },
        "required": ["pattern"],
    }

    @property
    def prompt_description(self) -> str:
        return (
            "## code_search\n"
            "在工作区中搜索代码/文本，支持正则表达式。返回匹配的文件路径、行号、行内容。\n\n"
            "用法:\n"
            "- 快速定位代码中的函数定义、类名、变量引用\n"
            "- 用 file_pattern 过滤文件类型，如 '*.py'\n"
            "- 优先使用 ripgrep，回退到 Python 搜索\n\n"
            "示例:\n"
            '```\n{"pattern": "def main", "file_pattern": "*.py"}\n'
            '```\n\n{"pattern": "import pandas"}\n'
            "```\n\n"
            "注意: pattern 是正则表达式，特殊字符需要转义。"
        )

    def __init__(self, workspace_dir: str = ""):
        self.workspace_dir = workspace_dir

    def set_workspace(self, workspace_dir: str):
        self.workspace_dir = workspace_dir

    def _find_rg(self) -> str | None:
        """Try to find ripgrep binary."""
        return resolve_tool("rg")

    async def execute(
        self,
        pattern: str,
        path: str = ".",
        file_pattern: str = "",
        max_results: int = 50,
    ) -> str:
        if not self.workspace_dir:
            return json.dumps({"error": "工作目录未设置"}, ensure_ascii=False)

        workspace = Path(self.workspace_dir).resolve()
        search_path = (workspace / path).resolve()
        if search_path != workspace and not str(search_path).startswith(str(workspace) + os.sep):
            return json.dumps({"error": "路径超出工作区范围"}, ensure_ascii=False)
        if not search_path.exists():
            return json.dumps({"error": f"路径不存在: {path}"}, ensure_ascii=False)

        rg = self._find_rg()
        engine = "ripgrep" if rg else "python"
        logger.info("code_search: pattern=%r, path=%s, engine=%s, file_pattern=%s", pattern[:50], path, engine, file_pattern)
        # Both engines do blocking subprocess/IO work — run in a worker
        # thread so the event loop stays responsive (H1)
        if rg:
            return await asyncio.to_thread(
                self._search_rg, rg, pattern, search_path, file_pattern, max_results,
            )
        return await asyncio.to_thread(
            self._search_python, pattern, search_path, workspace, file_pattern, max_results,
        )

    def _search_rg(
        self, rg: str, pattern: str, search_path: Path,
        file_pattern: str, max_results: int,
    ) -> str:
        cmd = [rg, "--json", "--max-count", str(max_results), pattern, str(search_path)]
        if file_pattern:
            cmd.extend(["--glob", file_pattern])
        try:
            result = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace", timeout=15)
            matches = []
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if obj.get("type") == "match":
                        data = obj["data"]
                        matches.append({
                            "path": str(Path(data["path"]["text"]).relative_to(self.workspace_dir)),
                            "line_number": data["line_number"],
                            "line": data["lines"]["text"].rstrip(),
                        })
                        if len(matches) >= max_results:
                            break
                except (json.JSONDecodeError, KeyError):
                    continue
            return json.dumps({
                "matches": matches,
                "total": len(matches),
                "engine": "ripgrep",
            }, ensure_ascii=False)
        except subprocess.TimeoutExpired:
            return json.dumps({"error": "搜索超时"}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    def _search_python(
        self, pattern: str, search_path: Path, workspace: Path,
        file_pattern: str, max_results: int,
    ) -> str:
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return json.dumps({"error": f"正则表达式错误: {e}"}, ensure_ascii=False)

        # Determine file filter
        file_glob = file_pattern if file_pattern else None

        matches = []
        files_searched = 0
        try:
            if search_path.is_file():
                files = [search_path]
            else:
                files = search_path.rglob("*")

            for fpath in files:
                if not fpath.is_file():
                    continue
                if file_glob and not fpath.match(file_glob):
                    continue
                # Skip binary / hidden / large files
                if fpath.stat().st_size > 1_000_000:
                    continue
                if any(p.startswith(".") for p in fpath.relative_to(workspace).parts):
                    continue
                files_searched += 1
                try:
                    text = fpath.read_text(encoding="utf-8", errors="replace")
                    for i, line in enumerate(text.splitlines(), 1):
                        if regex.search(line):
                            matches.append({
                                "path": str(fpath.relative_to(workspace)),
                                "line_number": i,
                                "line": line.rstrip()[:200],
                            })
                            if len(matches) >= max_results:
                                break
                except (OSError, UnicodeDecodeError):
                    continue
                if len(matches) >= max_results:
                    break
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        return json.dumps({
            "matches": matches,
            "total": len(matches),
            "files_searched": files_searched,
            "engine": "python",
        }, ensure_ascii=False)

