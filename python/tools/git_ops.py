"""python/tools/git_ops.py — Git version control operations.

Provides read-only git commands (status, diff, log, show, blame)
plus a write operation (commit) that requires user confirmation
via the shell/skill confirmation flow.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import subprocess
from pathlib import Path

from tools.base import BaseTool

logger = logging.getLogger(__name__)


class GitOpsTool(BaseTool):
    name = "git_ops"
    description = (
        "Git 版本控制操作。支持以下操作：\n"
        "- status: 查看工作区状态 (git status --short)\n"
        "- diff: 查看未暂存变更 (git diff)\n"
        "- diff_staged: 查看已暂存变更 (git diff --cached)\n"
        "- log: 查看提交历史 (git log --oneline)\n"
        "- show: 查看某次提交详情 (git show <commit>)\n"
        "- blame: 查看文件逐行归属 (git blame <file>)\n"
        "- commit: 提交变更 (git commit -m <message>，需确认)\n"
        "所有操作在工作区根目录下执行。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "status", "diff", "diff_staged",
                    "log", "show", "blame", "commit",
                ],
                "description": "Git 操作类型",
            },
            "args": {
                "type": "string",
                "description": (
                    "传递给 git 命令的额外参数。"
                    "show: 传入 commit hash（如 'abc1234'）；"
                    "blame: 传入文件路径；"
                    "log: 传入数量（如 '-10'）；"
                    "commit: 传入提交信息（如 '-m \"fix bug\"'）"
                ),
            },
        },
        "required": ["action"],
    }

    @property
    def prompt_description(self) -> str:
        return (
            "## git_ops\n"
            "Git 版本控制：status / diff / diff_staged / log / show / blame / commit。\n"
            "在工作区根目录下执行 git 命令。\n\n"
            "用法:\n"
            "- 先用 status 查看工作区状态\n"
            "- 用 diff / diff_staged 查看具体变更\n"
            "- 用 log 查看提交历史\n"
            "- 用 show <commit> 查看某次提交详情\n"
            "- 用 blame <file> 查看文件逐行归属\n"
            "- 用 commit 提交变更（需用户确认）\n\n"
            "示例:\n"
            '```\n{"action": "status"}\n'
            '```\n\n{"action": "log", "args": "-10"}\n'
            '```\n\n{"action": "show", "args": "abc1234"}\n'
            '```\n\n{"action": "blame", "args": "src/main.py"}\n'
            '```\n\n{"action": "commit", "args": "-m \\"fix: resolve auth bug\\""}\n'
            "```"
        )

    def __init__(self, workspace_dir: str = ""):
        self.workspace_dir = workspace_dir

    def set_workspace(self, workspace_dir: str):
        self.workspace_dir = workspace_dir

    async def _is_git_repo(self) -> bool:
        """Check if workspace is a git repository (supports worktrees)."""
        if not self.workspace_dir:
            return False
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["git", "rev-parse", "--is-inside-work-tree"],
                capture_output=True, encoding="utf-8", errors="replace",
                cwd=self.workspace_dir, timeout=5,
            )
            return result.returncode == 0 and result.stdout.strip() == "true"
        except Exception:
            return False

    async def _run_git(self, args: list[str], timeout: int = 15) -> dict:
        """Run a git command and return structured result (blocking part in a worker thread — H1).

        Note: the caller (execute) checks _is_git_repo() once up front —
        checking again here would double the git process spawns per call (L3).
        """
        if not self.workspace_dir:
            return {"error": "工作目录未设置", "error_type": "ConfigError"}

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["git"] + args,
                capture_output=True,
                encoding="utf-8", errors="replace",
                cwd=self.workspace_dir,
                timeout=timeout,
            )
            output = result.stdout.strip()
            stderr = result.stderr.strip()

            if result.returncode != 0:
                return {
                    "exit_code": result.returncode,
                    "stdout": output,
                    "stderr": stderr or f"git {' '.join(args)} failed",
                    "error_type": "GitError",
                }
            return {
                "exit_code": 0,
                "stdout": output,
                "stderr": stderr,
            }
        except subprocess.TimeoutExpired:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Git 命令超时 ({timeout}s)",
                "error_type": "Timeout",
            }
        except FileNotFoundError:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": "Git 未安装或不在 PATH 中",
                "error_type": "GitNotFound",
            }
        except Exception as e:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": str(e),
                "error_type": type(e).__name__,
            }

    async def execute(self, action: str, args: str = "") -> str:
        if not self.workspace_dir:
            return json.dumps({"error": "工作目录未设置"}, ensure_ascii=False)

        if not await self._is_git_repo():
            return json.dumps(
                {"error": "当前工作区不是 Git 仓库，无法执行 git 操作"},
                ensure_ascii=False,
            )

        logger.info("git_ops: action=%s, args=%s", action, args[:100])

        # Parse args into list (respecting quoted strings)
        try:
            arg_list = shlex.split(args) if args.strip() else []
        except ValueError:
            # Fallback to simple split if shlex fails (e.g. unmatched quotes)
            arg_list = args.split() if args.strip() else []

        if action == "status":
            result = await self._run_git(["status", "--short"])

        elif action == "diff":
            cmd = ["diff"] + arg_list
            result = await self._run_git(cmd)

        elif action == "diff_staged":
            cmd = ["diff", "--cached"] + arg_list
            result = await self._run_git(cmd)

        elif action == "log":
            cmd = ["log", "--oneline"] + arg_list
            # Default to 20 entries if no count specified
            if not any(a.startswith("-") for a in arg_list):
                cmd = ["log", "--oneline", "-20"] + arg_list
            result = await self._run_git(cmd)

        elif action == "show":
            if not arg_list:
                return json.dumps(
                    {"error": "show 操作需要提供 commit hash"},
                    ensure_ascii=False,
                )
            cmd = ["show", "--stat"] + arg_list
            result = await self._run_git(cmd)

        elif action == "blame":
            if not arg_list:
                return json.dumps(
                    {"error": "blame 操作需要提供文件路径"},
                    ensure_ascii=False,
                )
            cmd = ["blame"] + arg_list
            result = await self._run_git(cmd, timeout=30)

        elif action == "commit":
            # Commit requires user confirmation (handled by agent loop)
            if not arg_list:
                return json.dumps(
                    {"error": "commit 操作需要提供提交信息，如 -m \"message\""},
                    ensure_ascii=False,
                )
            # Note: agent should use shell to stage files (git add) before committing.
            # We do NOT auto-stage here to avoid unintended files being committed.

            # Pre-commit: show what will be committed (diff --cached --stat)
            stat_result = await self._run_git(["diff", "--cached", "--stat"])
            pre_stat = stat_result.get("stdout", "").strip()
            if not pre_stat:
                # Also check unstaged changes for context
                unstaged = await self._run_git(["diff", "--stat"]).get("stdout", "").strip()
                untracked = await self._run_git(["ls-files", "--others", "--exclude-standard"]).get("stdout", "").strip()
                if not unstaged and not untracked:
                    return json.dumps({
                        "exit_code": -1, "stdout": "",
                        "stderr": "没有可提交的变更（暂存区为空，工作区也无修改）",
                        "error_type": "NothingToCommit",
                    }, ensure_ascii=False)

            cmd = ["commit"] + arg_list
            result = await self._run_git(cmd)
            # Inject pre-commit stat into result for user visibility
            if pre_stat and result.get("exit_code", -1) == 0:
                result["commit_stat"] = pre_stat

        else:
            return json.dumps(
                {"error": f"未知操作: {action}"},
                ensure_ascii=False,
            )

        return json.dumps(result, ensure_ascii=False)
