"""BuiltinToolAdapter + AgentSkillAdapter — bridge tools/skills into BaseSkill protocol.

BuiltinToolAdapter: wraps V1 BaseTool subclasses (permanent design).
AgentSkillAdapter: wraps Agent Skills standard skills (SKILL.md format).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from skills.context import SkillContext
from skills.protocol import BaseSkill
from tools.base import BaseTool
from tools.venv_manager import (
    UvExclusionRequired,
    ensure_venv_blocking,
    get_venv_path_from_python,
    is_preinstall_done,
)

# L2 guide budget (progressive disclosure): SKILL.md body chars attached to
# the first execution result / slash hint. 8000 chars covers the full body
# of large workflow skills (e.g. ppt-master ≈ 4.8K chars) without cutting
# tail sections (Windows python3→python notes, sponsor rules).
SKILL_GUIDE_MAX_CHARS = 8000

# Conventional CLI entry points. A skill is auto-executed only when it has
# a discoverable entry: one of these names in scripts/, or exactly one
# runnable script. Multi-script workflow skills (e.g. ppt-master: 70+
# utility scripts orchestrated via SKILL.md + workflows/*.md) have no
# single entry point — alphabetically-first selection would run an
# arbitrary utility (analyze_images.py), so they fall back to
# instructions mode and the LLM drives the scripts itself via shell.
_ENTRY_SCRIPT_NAMES = ("main.py", "cli.py")

# L1 synopsis budget: SKILL.md usage fence attached to the command
# description/input-schema. Lets the LLM build correct args on the FIRST
# call without shipping the whole body into every prompt (full guide
# stays L2, attached to the first execution result / slash hint).
USAGE_SYNOPSIS_MAX_CHARS = 200


class BuiltinToolAdapter(BaseSkill):
    """Wraps a ``BaseTool`` instance so it conforms to the ``BaseSkill`` interface.

    The adapter is intentionally thin (~30 lines of real logic).
    It maps the ``BaseSkill`` protocol (params dict + SkillContext)
    to the ``BaseTool`` protocol (**kwargs).
    """

    def __init__(self, tool: BaseTool):
        self._tool = tool
        # Map BaseTool attributes → BaseSkill attributes
        self.name = tool.name
        self.description = tool.description
        # BaseTool uses `parameters`; BaseSkill uses `input_schema`
        self.input_schema = tool.parameters

    # ── Execution bridging: (params, context) → **kwargs ──

    async def execute(self, params: dict, context: SkillContext) -> str:
        return await self._tool.execute(**params)

    async def pre_execute(self, params: dict, context: SkillContext) -> dict | None:
        return await self._tool.pre_execute(**params)

    async def post_execute(self, result: str, params: dict, context: SkillContext) -> str:
        return await self._tool.post_execute(result, **params)

    # ── Prompt description passthrough ──

    @property
    def prompt_description(self) -> str:
        """Delegate to the wrapped tool's rich prompt description."""
        return self._tool.prompt_description


class AgentSkillAdapter(BaseSkill):
    """Wraps an Agent Skills standard skill (SKILL.md format) into the BaseSkill protocol.

    Agent Skills is an open standard (https://agentskills.io/) supported by
    Claude Code, Cursor, Codex, VS Code, etc.  A skill is a directory with:
      - SKILL.md (required): YAML frontmatter + Markdown instructions
      - scripts/ (optional): executable scripts
      - references/ (optional): reference documents
      - assets/ (optional): templates, resources

    Scripts are run in the task's venv.  'compatibility' is a free-text
    environment note (per the standard, NOT a pip dependency list): it is
    surfaced to the LLM, which installs any real dependencies itself via
    shell/pip when the skill's instructions call for them.

    Two execution modes, chosen by the entry-script contract:
      - CLI mode: scripts/ has a conventional entry (main.py / cli.py) or
        exactly one runnable script → it is executed with the LLM-translated
        ``command`` CLI arguments.
      - Instructions mode: otherwise (no scripts, no venv, or multi-script
        workflow skill) → the SKILL.md guide is returned and the LLM drives
        the workflow itself via file_ops/shell (install_path is registered
        as a file_ops read-only root by TaskAgent).

    The function calling definition is generated from the skill's name and
    description.  Since Agent Skills don't have structured parameter schemas,
    the LLM sees a simple tool with a 'command' string parameter containing
    the CLI arguments for the skill's script — the LLM must translate the
    user's request into them (subcommands/flags live in SKILL.md, attached
    to the first execution result and to the slash fallthrough hint).
    """

    def __init__(self, skill_dir: str, parsed: dict, skill_info: dict | None = None):
        """
        Args:
            skill_dir: Absolute path to the skill directory.
            parsed: Output from skills.parser.parse_skill_md().
            skill_info: Optional DB record (name, description, etc.).
        """
        self._skill_dir = skill_dir
        self._parsed = parsed
        self._skill_info = skill_info or {}

        fm = parsed.get("frontmatter", {})
        self.name = fm.get("name", self._skill_info.get("name", os.path.basename(skill_dir)))
        self.description = fm.get("description", self._skill_info.get("description", ""))
        self._compatibility = fm.get("compatibility", "")
        self._body = parsed.get("body", "")
        # Level 2 progressive disclosure: the SKILL.md guide attaches to the
        # FIRST execution result only ("load on activation, once"); later
        # results stay lean.  Reset per run — TaskAgent re-registers adapters
        # each run, and tool results don't persist into rebuilt LLM history.
        self._guide_sent = False

        # Entry-script contract (detect once per adapter; adapters are
        # re-created per task run / per skill_runner call, so a stale
        # snapshot is not a concern).
        scripts_dir = os.path.join(skill_dir, "scripts")
        self._entry_script = (
            self._find_primary_script(scripts_dir)
            if os.path.isdir(scripts_dir)
            else None
        )

        # Schema description matches the execution mode: CLI-mode skills get
        # the strict CLI contract (saying "natural language" there misled the
        # LLM into passing raw user text, which argparse rejects); workflow
        # skills have no CLI to translate — natural language is the input.
        # CLI 契约再附 SKILL.md 的用法围栏（argv 契约）：实测弱模型在无契约时
        # 首调传自然语言（"open pricing"），argparse 报错后直接放弃而非自纠——
        # 首调正确比事后纠错可靠，用法行必须在 L1 可见。
        self._usage_synopsis = (
            self._extract_usage_synopsis(self._body)
            if self._entry_script else ""
        )
        if self._entry_script:
            command_desc = (
                f"CLI arguments for the {self.name} skill's entry script, "
                f"e.g. 'greet --name Alice'. Translate the user's request "
                f"into the subcommand and flags documented in the skill's "
                f"instructions; never pass natural language directly."
            )
            if self._usage_synopsis:
                command_desc += f" Usage: {self._usage_synopsis}"
        else:
            command_desc = (
                f"The user's request for the {self.name} skill (free-form). "
                f"This is a workflow skill driven by its SKILL.md instructions: "
                f"the first execution result returns the full guide to follow."
            )
        self.input_schema = {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": command_desc,
                },
            },
            "required": ["command"],
        }

    @staticmethod
    def _extract_usage_synopsis(body: str) -> str:
        """Extract the usage fence (first fenced code block) from SKILL.md body.

        CLI-mode skills document their argv contract in a fenced block (e.g.
        ``python scripts/main.py <url> [--selector CSS]``). Block-internal
        newlines are collapsed so multi-line fences stay a one-line synopsis;
        missing fence returns "" (description stays as-is).
        """
        start = body.find("```")
        if start < 0:
            return ""
        rest = body[start + 3:]
        nl = rest.find("\n")
        if nl < 0:
            return ""
        end = rest.find("```", nl)
        if end < 0:
            end = len(rest)
        synopsis = " ".join(rest[nl + 1:end].split())
        return synopsis[:USAGE_SYNOPSIS_MAX_CHARS]

    @property
    def usage_instructions(self) -> str:
        """SKILL.md body — the usage guide (subcommands/flags/examples).

        Consumed by the slash fallthrough hint (TaskAgent._build_slash_hint)
        so the LLM can build correct CLI args on its FIRST call instead of
        guessing and self-correcting from the errored tool result.
        """
        return self._body

    def mark_guide_sent(self) -> None:
        """Record that the guide was already delivered out-of-band (slash hint).

        Suppresses the copy that would otherwise attach to the first
        execution result — avoids sending the same guide twice.
        """
        self._guide_sent = True

    def reference_note(self) -> str:
        """L3 pointer to the references/ directory ("" when absent).

        Tells the LLM where the skill's reference files live and how to read
        them on demand (file_ops read/list over the read-only root that
        TaskAgent registers for the skill directory) — the standard
        progressive-disclosure path, so big tables stay out of prompts.
        """
        refs_dir = os.path.join(self._skill_dir, "references")
        if not os.path.isdir(refs_dir):
            return ""
        files = [f for f in sorted(os.listdir(refs_dir)) if not f.startswith(".")]
        if not files:
            return ""
        shown = ", ".join(files[:8]) + (" 等" if len(files) > 8 else "")
        return (
            f"该 Skill 的详细参考文件位于 {refs_dir}（{shown}），"
            f"需要时可用 file_ops 的 read/list 读取，无需提前全部加载。"
        )

    def _dependencies_note(self) -> str:
        """Note pointing the LLM at requirements.txt when the skill ships one.

        The Agent Skills standard has no dependency-manifest mechanism (the
        'compatibility' frontmatter is a free-text env note); large workflow
        skills (e.g. ppt-master) ship requirements.txt as a community
        convention instead. We never parse or auto-install it (that mistake
        is settled — see parser.py NOTE) — only tell the LLM the file exists
        so it can install what it needs via package_manager/shell.
        """
        if not os.path.isfile(os.path.join(self._skill_dir, "requirements.txt")):
            return ""
        return (
            "该 Skill 目录附带 requirements.txt（可选依赖清单）。"
            "脚本执行报 ModuleNotFoundError 时，读取该文件并经 "
            "package_manager/shell 在任务 venv 中安装所需依赖；无需预装全部。"
        )

    def _attach_guide(self, result_obj: dict) -> None:
        """Attach SKILL.md guide + references/deps notes on first execution only."""
        if self._guide_sent:
            return
        if self._body:
            result_obj["instructions"] = self._body[:SKILL_GUIDE_MAX_CHARS]
        note = self.reference_note()
        if note:
            result_obj["references"] = note
        deps_note = self._dependencies_note()
        if deps_note:
            result_obj["dependencies"] = deps_note
        self._guide_sent = True

    async def execute(self, params: dict, context: SkillContext) -> str:
        """Execute the Agent Skill.

        For Agent Skills, execution means:
        1. If the skill has a discoverable entry script (entry contract),
           run it in the task's venv with the LLM-translated CLI args
        2. Otherwise (instructions mode: no entry, no venv, or multi-script
           workflow skill), return the skill's instructions for the agent
           to follow via file_ops/shell
        3. On the first execution, attach the SKILL.md guide + references /
           dependencies notes to the result (progressive disclosure, once)

        Args:
            params: Dict with 'command' key containing the user's instruction.
            context: Runtime context with workspace_path, venv_python, etc.

        Returns:
            JSON string result.
        """
        command = params.get("command", "")

        # Entry-script contract: only CLI-mode skills are auto-executed.
        # 首用同步预装（2.8 §5.5）：marker 缺失（后台预装未完成或曾失败）时
        # 在此「缺就装、装完继续」，不把预装竞态的错误抛给 LLM。
        if self._entry_script and context.venv_python:
            try:
                ready = await self._ensure_venv_ready(context.venv_python)
            except UvExclusionRequired as e:
                # 前置阻断（2.8 §12 F4）：重试无用，须用户先完成系统授权，
                # 不可标 retryable（会诱导 LLM 无谓重试）
                return json.dumps({
                    "error": str(e),
                    "retryable": False,
                    "error_type": "UvExclusionRequired",
                }, ensure_ascii=False)
            if not ready:
                return json.dumps({
                    "error": "任务依赖自动安装失败，请重试（反复失败请检查网络或重启应用）",
                    "retryable": True,
                }, ensure_ascii=False)
            return await self._run_script(self._entry_script, command, context)

        # No scripts or no venv — return instructions for the agent
        result_obj = {
            "output": f"Skill '{self.name}' instructions:",
            "skill_name": self.name,
            "command": command,
            "note": "This skill provides instructions rather than executable code. "
                    "Follow the steps above to complete the task.",
        }
        self._attach_guide(result_obj)
        return json.dumps(result_obj, ensure_ascii=False)

    @staticmethod
    async def _ensure_venv_ready(venv_python: str) -> bool:
        """CLI 模式 skill 首用依赖就绪：marker 在位即快路径，缺失同步补装。

        预装命令本身有 subprocess timeout（venv_manager）兜底，且经
        to_thread 执行不阻塞事件循环；失败返回 False 由 execute 转为
        retryable JSON 交还 LLM（最后防线，正常路径在此自愈）。
        """
        venv_path = get_venv_path_from_python(venv_python)
        if is_preinstall_done(venv_path):
            return True
        return await asyncio.to_thread(ensure_venv_blocking, venv_path)

    def _find_primary_script(self, scripts_dir: str) -> str | None:
        """Find the primary script to execute (explicit CLI contract only).

        Returns a script path only when the skill has a discoverable entry:
        a conventional entry point (main.py / cli.py), or exactly one
        runnable script in scripts/ (``_``-prefixed helpers never count).
        Multi-script workflow skills return None so execute() falls back to
        instructions mode — running an arbitrary alphabetically-first script
        is always wrong for them.
        """
        try:
            entries = sorted(os.listdir(scripts_dir))
        except OSError:
            return None
        runnable = [
            f for f in entries
            if f.endswith((".py", ".sh", ".bash")) and not f.startswith("_")
        ]
        for name in _ENTRY_SCRIPT_NAMES:
            if name in runnable:
                return os.path.join(scripts_dir, name)
        if len(runnable) == 1:
            return os.path.join(scripts_dir, runnable[0])
        return None

    async def _run_script(self, script_path: str, command: str, context: SkillContext) -> str:
        """Run a script in the task's venv.

        The ``command`` string is parsed with ``shlex`` and appended as CLI
        arguments to the script invocation.  This allows the LLM to pass
        natural-language instructions that include CLI flags, e.g.
        ``command="greet --name Alice"``.
        """
        import shlex

        ext = os.path.splitext(script_path)[1]

        if ext == ".py":
            base_cmd = [context.venv_python, script_path]
        elif ext in (".sh", ".bash"):
            if sys.platform == "win32":
                return json.dumps({
                    "error": "Shell scripts are not supported on Windows",
                    "script": script_path,
                }, ensure_ascii=False)
            base_cmd = ["bash", script_path]
        else:
            return json.dumps({"error": f"Unsupported script type: {ext}"}, ensure_ascii=False)

        # Parse command string into CLI arguments
        args = shlex.split(command) if command and command.strip() else []
        cmd = base_cmd + args

        try:
            # 2.8 §3.3：租约锁 owner 需要 task 身份——注入 INKCUBE_TASK_ID
            #（空值不注入；一处改动全 skill 受益）
            spawn_env = os.environ.copy()
            if context.task_id:
                spawn_env["INKCUBE_TASK_ID"] = context.task_id
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=context.workspace_path,
                env=spawn_env,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=context.settings.get("skill_script_timeout", 120),
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return json.dumps({
                "error": f"Skill 脚本执行超时 ({context.settings.get('skill_script_timeout', 120)}s)"
            }, ensure_ascii=False)
        except FileNotFoundError:
            return json.dumps({"error": f"找不到解释器: {cmd[0]}"}, ensure_ascii=False)

        stdout_text = stdout.decode("utf-8", errors="replace").strip()
        stderr_text = stderr.decode("utf-8", errors="replace").strip()

        result_obj: dict[str, Any]
        if stdout_text:
            try:
                result_obj = json.loads(stdout_text)
            except json.JSONDecodeError:
                result_obj = {"output": stdout_text}
        else:
            result_obj = {"output": ""}

        # Level 2/3 (first execution only): SKILL.md guide + references note
        self._attach_guide(result_obj)

        if proc.returncode != 0:
            result_obj.setdefault("error", stderr_text or f"脚本执行失败 (exit code {proc.returncode})")
            result_obj["exit_code"] = proc.returncode

        if stderr_text and proc.returncode == 0:
            result_obj["stderr"] = stderr_text[:1000]

        return json.dumps(result_obj, ensure_ascii=False)

    @property
    def prompt_description(self) -> str:
        """Level 1: only name + description (saves tokens).

        The SKILL.md body attaches to the FIRST execution result (Level 2);
        references/ are readable on demand via file_ops (Level 3).
        """
        lines = [
            f"## {self.name}",
            self.description,
            "",
            "参数:",
        ]
        if self._entry_script:
            lines.append(
                "  command (string, required): 完整的 CLI 命令参数，例如: greet --name Alice"
            )
            if self._usage_synopsis:
                lines.append(f"  用法: {self._usage_synopsis}")
        else:
            lines.append(
                "  command (string, required): 用户需求描述（工作流型 Skill，无 CLI 入口；"
                "首次调用结果将返回 SKILL.md 指南，按指南经 shell/file_ops 执行）"
            )
        if self._compatibility:
            lines.append(f"\n环境要求: {self._compatibility}")
        return "\n".join(lines)
