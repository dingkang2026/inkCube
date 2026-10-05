"""python/tools/lsp_client.py — Lightweight LSP-like code intelligence.

Provides type checking, go-to-definition, and find-references
using external tools (pyright, tsc) when available, with
regex-based fallback for cross-platform compatibility.
"""

import asyncio
import json
import logging
import os
import re
import subprocess
from pathlib import Path

from tools.base import BaseTool

logger = logging.getLogger(__name__)


class LSPClientTool(BaseTool):
    name = "lsp_client"
    description = (
        "代码智能分析工具（LSP 集成）。支持以下操作：\n"
        "- type_check: 类型检查（Python 用 pyright，TypeScript 用 tsc）\n"
        "- go_to_definition: 跳转到符号定义\n"
        "- find_references: 查找符号的所有引用\n"
        "- hover: 获取符号的类型信息\n"
        "所有操作在工作区内执行。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["type_check", "go_to_definition", "find_references", "hover"],
                "description": "操作类型",
            },
            "file_path": {
                "type": "string",
                "description": "目标文件的相对路径",
            },
            "symbol": {
                "type": "string",
                "description": "符号名称（函数/类/变量名，用于 go_to_definition/find_references/hover）",
            },
            "line": {
                "type": "integer",
                "description": "行号（1-based，用于 go_to_definition/hover）",
            },
            "column": {
                "type": "integer",
                "description": "列号（1-based，用于 go_to_definition/hover）",
            },
        },
        "required": ["action"],
    }

    @property
    def prompt_description(self) -> str:
        return (
            "## lsp_client\n"
            "代码智能分析：type_check / go_to_definition / find_references / hover。\n"
            "利用类型检查器（pyright/tsc）提供专业级代码理解。\n\n"
            "用法:\n"
            "- type_check: 检查文件的类型错误（编辑后验证）\n"
            "- go_to_definition: 找到函数/类的定义位置\n"
            "- find_references: 找到所有调用/引用某符号的地方\n"
            "- hover: 获取符号的类型签名\n\n"
            "示例:\n"
            '```\n{"action": "type_check", "file_path": "main.py"}\n```\n\n'
            '```\n{"action": "find_references", "symbol": "calculate_total"}\n```\n\n'
            '```\n{"action": "go_to_definition", "file_path": "main.py", "symbol": "Config", "line": 10, "column": 5}\n```\n\n'
            "注意: type_check 需要 pyright (Python) 或 tsc (TypeScript) 已安装。"
        )

    def __init__(self, workspace_dir: str = ""):
        self.workspace_dir = workspace_dir

    def set_workspace(self, workspace_dir: str):
        self.workspace_dir = workspace_dir

    async def execute(
        self,
        action: str,
        file_path: str = "",
        symbol: str = "",
        line: int = 0,
        column: int = 0,
    ) -> str:
        if not self.workspace_dir:
            return json.dumps({"error": "工作目录未设置"}, ensure_ascii=False)

        workspace = Path(self.workspace_dir).resolve()

        # All four helpers block: type_check/find_references spawn subprocesses
        # (pyright/mypy/tsc/rg), go_to_definition/hover scan workspace files —
        # run them in a worker thread so the event loop stays responsive (H1)
        if action == "type_check":
            return await asyncio.to_thread(self._type_check, workspace, file_path)
        elif action == "go_to_definition":
            return await asyncio.to_thread(
                self._go_to_definition, workspace, file_path, symbol, line, column,
            )
        elif action == "find_references":
            return await asyncio.to_thread(self._find_references, workspace, symbol)
        elif action == "hover":
            return await asyncio.to_thread(
                self._hover, workspace, file_path, symbol, line, column,
            )
        else:
            return json.dumps({"error": f"未知操作: {action}"}, ensure_ascii=False)

    def _type_check(self, workspace: Path, file_path: str) -> str:
        """Run type checker on a file."""
        if not file_path:
            return json.dumps({"error": "type_check 需要 file_path 参数"}, ensure_ascii=False)

        target = (workspace / file_path).resolve()
        if target != workspace and not str(target).startswith(str(workspace) + os.sep):
            return json.dumps({"error": "路径超出工作区范围"}, ensure_ascii=False)
        if not target.is_file():
            return json.dumps({"error": f"文件不存在: {file_path}"}, ensure_ascii=False)

        ext = target.suffix
        logger.info("lsp type_check: file=%s, ext=%s", file_path, ext)

        if ext == ".py":
            return self._type_check_python(workspace, target)
        elif ext in (".ts", ".tsx"):
            return self._type_check_typescript(workspace, target)
        else:
            return json.dumps({
                "error": f"不支持的文件类型: {ext}",
                "supported": [".py", ".ts", ".tsx"],
            }, ensure_ascii=False)

    def _type_check_python(self, workspace: Path, target: Path) -> str:
        """Run pyright on a Python file."""
        pyright = self._find_tool("pyright")
        if not pyright:
            # Fallback: use mypy
            mypy = self._find_tool("mypy")
            if mypy:
                return self._run_mypy(mypy, workspace, target)
            return json.dumps({
                "error": "pyright 和 mypy 均未安装",
                "suggestion": "请安装: pip install pyright 或 pip install mypy",
            }, ensure_ascii=False)

        try:
            result = subprocess.run(
                [pyright, "--outputjson", str(target)],
                capture_output=True, encoding="utf-8", errors="replace", timeout=30,
                cwd=str(workspace),
            )
            try:
                data = json.loads(result.stdout) if result.stdout.strip() else {}
            except json.JSONDecodeError:
                data = {}

            diagnostics = data.get("generalDiagnostics", [])
            errors = []
            for diag in diagnostics:
                errors.append({
                    "file": file_path if (file_path := diag.get("file", "")) else str(target),
                    "line": diag.get("range", {}).get("start", {}).get("line", 0) + 1,
                    "column": diag.get("range", {}).get("start", {}).get("character", 0) + 1,
                    "severity": diag.get("severity", "error"),
                    "message": diag.get("message", ""),
                    "rule": diag.get("rule", ""),
                })

            logger.info("pyright: file=%s, errors=%d", target.name, len(errors))
            return json.dumps({
                "exit_code": 0 if not errors else 1,
                "tool": "pyright",
                "total_errors": len(errors),
                "errors": errors[:50],
            }, ensure_ascii=False)

        except subprocess.TimeoutExpired:
            return json.dumps({"error": "pyright 检查超时（30 秒）"}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    def _run_mypy(self, mypy: str, workspace: Path, target: Path) -> str:
        """Run mypy as fallback type checker."""
        try:
            result = subprocess.run(
                [mypy, "--no-error-summary", str(target)],
                capture_output=True, encoding="utf-8", errors="replace", timeout=30,
                cwd=str(workspace),
            )
            errors = []
            for line in result.stdout.strip().split("\n"):
                if not line or line.startswith("Found"):
                    continue
                # Format: file.py:line: error: message
                m = re.match(r"(.+?):(\d+):\s*(\w+):\s*(.+)", line)
                if m:
                    errors.append({
                        "file": m.group(1),
                        "line": int(m.group(2)),
                        "severity": m.group(3),
                        "message": m.group(4),
                    })

            logger.info("mypy: file=%s, errors=%d", target.name, len(errors))
            return json.dumps({
                "exit_code": 0 if not errors else 1,
                "tool": "mypy",
                "total_errors": len(errors),
                "errors": errors[:50],
            }, ensure_ascii=False)

        except subprocess.TimeoutExpired:
            return json.dumps({"error": "mypy 检查超时（30 秒）"}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    def _type_check_typescript(self, workspace: Path, target: Path) -> str:
        """Run tsc on a TypeScript file."""
        tsc = self._find_tool("tsc")
        if not tsc:
            # Fallback: try npx tsc
            npx = self._find_tool("npx")
            if npx:
                tsc = npx
                cmd_prefix = [npx, "tsc"]
            else:
                return json.dumps({
                    "error": "tsc 未安装",
                    "suggestion": "请安装 TypeScript: npm install -g typescript",
                }, ensure_ascii=False)
        else:
            cmd_prefix = [tsc]

        try:
            cmd = cmd_prefix + ["--noEmit", "--pretty", "false", str(target)]
            result = subprocess.run(
                cmd, capture_output=True, encoding="utf-8", errors="replace", timeout=30,
                cwd=str(workspace),
            )
            errors = []
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                # Format: file.ts(line,col): error TSxxxx: message
                m = re.match(r"(.+?)\((\d+),(\d+)\):\s*error\s+(\w+):\s*(.+)", line)
                if m:
                    errors.append({
                        "file": m.group(1),
                        "line": int(m.group(2)),
                        "column": int(m.group(3)),
                        "code": m.group(4),
                        "message": m.group(5),
                    })

            logger.info("tsc: file=%s, errors=%d", target.name, len(errors))
            return json.dumps({
                "exit_code": 0 if not errors else 1,
                "tool": "tsc",
                "total_errors": len(errors),
                "errors": errors[:50],
            }, ensure_ascii=False)

        except subprocess.TimeoutExpired:
            return json.dumps({"error": "tsc 检查超时（30 秒）"}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    def _go_to_definition(
        self, workspace: Path, file_path: str, symbol: str,
        line: int, column: int,
    ) -> str:
        """Find the definition of a symbol."""
        if not symbol:
            return json.dumps({"error": "go_to_definition 需要 symbol 参数"}, ensure_ascii=False)

        logger.info("lsp go_to_definition: symbol=%s, file=%s, line=%d", symbol, file_path, line)

        # Strategy: search for the definition pattern in workspace files
        # Python: def symbol( or class symbol
        # JS/TS: function symbol, const symbol, class symbol, export ... symbol
        patterns = []
        escaped_symbol = re.escape(symbol)
        if file_path.endswith(".py"):
            patterns = [
                rf"^\s*def\s+{escaped_symbol}\s*\(",
                rf"^\s*class\s+{escaped_symbol}\s*[\(:]",
                rf"^\s*{escaped_symbol}\s*=\s*",
                rf"^\s*async\s+def\s+{escaped_symbol}\s*\(",
            ]
        elif file_path.endswith((".js", ".ts", ".tsx", ".jsx")):
            patterns = [
                rf"(?:export\s+)?(?:async\s+)?function\s+{escaped_symbol}\s*\(",
                rf"(?:export\s+)?(?:const|let|var)\s+{escaped_symbol}\s*=",
                rf"(?:export\s+)?class\s+{escaped_symbol}\s*\{{",
                rf"(?:export\s+)?interface\s+{escaped_symbol}\s*\{{",
                rf"(?:export\s+)?type\s+{escaped_symbol}\s*=",
            ]
        else:
            # Generic: look for the symbol as a definition
            patterns = [
                rf"^\s*(?:def|function|class|const|let|var|func|fn)\s+{escaped_symbol}\b",
            ]

        results = []
        for pattern in patterns:
            regex = re.compile(pattern, re.MULTILINE)
            # Search in the file first, then workspace
            search_files = []
            if file_path:
                target = workspace / file_path
                if target.is_file():
                    search_files.append(target)
            # Also search in all code files
            for ext in ("*.py", "*.js", "*.ts", "*.tsx"):
                search_files.extend(workspace.rglob(ext))

            seen = set()
            for fpath in search_files[:200]:
                if not fpath.is_file() or str(fpath) in seen:
                    continue
                seen.add(str(fpath))
                try:
                    text = fpath.read_text(encoding="utf-8", errors="replace")
                    for m in regex.finditer(text):
                        line_no = text[:m.start()].count("\n") + 1
                        rel_path = str(fpath.relative_to(workspace))
                        results.append({
                            "file": rel_path,
                            "line": line_no,
                            "text": m.group(0).strip()[:120],
                        })
                except (OSError, UnicodeDecodeError):
                    continue

            if results:
                break  # Found matches, stop trying patterns

        logger.info("go_to_definition: symbol=%s, found=%d locations", symbol, len(results))
        return json.dumps({
            "symbol": symbol,
            "definitions": results[:20],
            "total": len(results),
        }, ensure_ascii=False)

    def _find_references(self, workspace: Path, symbol: str) -> str:
        """Find all references to a symbol in the workspace."""
        if not symbol:
            return json.dumps({"error": "find_references 需要 symbol 参数"}, ensure_ascii=False)

        logger.info("lsp find_references: symbol=%s", symbol)

        # Use ripgrep if available for fast search
        rg = self._find_tool("rg")
        if rg:
            return self._find_refs_rg(rg, workspace, symbol)

        # Fallback: Python-based search
        return self._find_refs_python(workspace, symbol)

    def _find_refs_rg(self, rg: str, workspace: Path, symbol: str) -> str:
        """Find references using ripgrep."""
        try:
            # Search for word-boundary matches
            cmd = [rg, "--json", "--word-regexp", symbol, str(workspace)]
            result = subprocess.run(
                cmd, capture_output=True, encoding="utf-8", errors="replace", timeout=15,
                cwd=str(workspace),
            )
            refs = []
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if obj.get("type") == "match":
                        data = obj["data"]
                        refs.append({
                            "file": str(Path(data["path"]["text"]).relative_to(workspace)),
                            "line": data["line_number"],
                            "text": data["lines"]["text"].rstrip()[:200],
                        })
                except (json.JSONDecodeError, KeyError):
                    continue
                if len(refs) >= 100:
                    break

            logger.info("find_references (rg): symbol=%s, found=%d", symbol, len(refs))
            return json.dumps({
                "symbol": symbol,
                "references": refs[:50],
                "total": len(refs),
            }, ensure_ascii=False)

        except subprocess.TimeoutExpired:
            return json.dumps({"error": "搜索超时"}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    def _find_refs_python(self, workspace: Path, symbol: str) -> str:
        """Find references using Python (fallback)."""
        escaped = re.escape(symbol)
        pattern = re.compile(rf"\b{escaped}\b")
        refs = []
        for ext in ("*.py", "*.js", "*.ts", "*.tsx"):
            for fpath in workspace.rglob(ext):
                if not fpath.is_file():
                    continue
                # Skip hidden/large files
                if any(p.startswith(".") for p in fpath.relative_to(workspace).parts):
                    continue
                try:
                    if fpath.stat().st_size > 500_000:
                        continue
                    text = fpath.read_text(encoding="utf-8", errors="replace")
                    for i, line in enumerate(text.splitlines(), 1):
                        if pattern.search(line):
                            refs.append({
                                "file": str(fpath.relative_to(workspace)),
                                "line": i,
                                "text": line.rstrip()[:200],
                            })
                except (OSError, UnicodeDecodeError):
                    continue
                if len(refs) >= 100:
                    break
            if len(refs) >= 100:
                break

        logger.info("find_references (python): symbol=%s, found=%d", symbol, len(refs))
        return json.dumps({
            "symbol": symbol,
            "references": refs[:50],
            "total": len(refs),
        }, ensure_ascii=False)

    def _hover(
        self, workspace: Path, file_path: str, symbol: str,
        line: int, column: int,
    ) -> str:
        """Get type/signature info for a symbol."""
        if not symbol:
            return json.dumps({"error": "hover 需要 symbol 参数"}, ensure_ascii=False)

        logger.info("lsp hover: symbol=%s, file=%s", symbol, file_path)

        # Strategy: find the definition and extract its signature
        # First, try to find the definition
        def_result = self._go_to_definition(workspace, file_path, symbol, line, column)
        try:
            def_data = json.loads(def_result)
        except (json.JSONDecodeError, TypeError):
            return json.dumps({"symbol": symbol, "info": "无法获取类型信息"}, ensure_ascii=False)

        definitions = def_data.get("definitions", [])
        if not definitions:
            return json.dumps({
                "symbol": symbol,
                "info": f"未找到符号 '{symbol}' 的定义",
            }, ensure_ascii=False)

        # Extract the signature from the definition location
        def_loc = definitions[0]
        def_file = workspace / def_loc["file"]
        if not def_file.is_file():
            return json.dumps({"symbol": symbol, "info": "定义文件不存在"}, ensure_ascii=False)

        try:
            text = def_file.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            def_line = def_loc["line"] - 1

            # Extract multi-line signature (up to the opening brace/colon)
            sig_lines = []
            for i in range(def_line, min(def_line + 20, len(lines))):
                sig_lines.append(lines[i])
                if "{" in lines[i] or ":" in lines[i]:
                    break

            signature = "\n".join(sig_lines).strip()
            if len(signature) > 500:
                signature = signature[:500] + "..."

            return json.dumps({
                "symbol": symbol,
                "definition_file": def_loc["file"],
                "definition_line": def_loc["line"],
                "signature": signature,
            }, ensure_ascii=False)

        except Exception as e:
            return json.dumps({"symbol": symbol, "error": str(e)}, ensure_ascii=False)

    @staticmethod
    def _find_tool(name: str) -> str | None:
        """Find a tool executable using unified resolver."""
        from tools.tool_resolver import resolve_tool
        try:
            return resolve_tool(name)
        except ValueError:
            # Unknown tool, fall back to PATH lookup
            import shutil
            return shutil.which(name)
