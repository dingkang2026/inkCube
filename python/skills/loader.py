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

        # Agent Skills don't have structured parameter schemas.
        # We expose a single 'command' parameter holding the CLI arguments
        # for the skill's script — the description must demand CLI form:
        # saying "natural language" here misleads the LLM into passing the
        # raw user text, which argparse then rejects.
        self.input_schema = {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": f"CLI arguments for the {self.name} skill's script, "
                                   f"e.g. 'greet --name Alice'. Translate the user's request "
                                   f"into the subcommand and flags documented in the skill's "
                                   f"instructions; never pass natural language directly.",
                },
            },
            "required": ["command"],
        }

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
        execution result — avoids sending the same 4000 chars twice.
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

    def _attach_guide(self, result_obj: dict) -> None:
        """Attach SKILL.md guide + references note on first execution only."""
        if self._guide_sent:
            return
        if self._body:
            result_obj["instructions"] = self._body[:4000]
        note = self.reference_note()
        if note:
            result_obj["references"] = note
        self._guide_sent = True

    async def execute(self, params: dict, context: SkillContext) -> str:
        """Execute the Agent Skill.

        For Agent Skills, execution means:
        1. If the skill has scripts, run them in the task's venv
        2. Otherwise, return the skill's instructions for the agent to follow
        3. On the first execution, attach the SKILL.md guide + references
           note to the result (progressive disclosure, loaded once)

        Args:
            params: Dict with 'command' key containing the user's instruction.
            context: Runtime context with workspace_path, venv_python, etc.

        Returns:
            JSON string result.
        """
        command = params.get("command", "")

        # Check for scripts in the skill directory
        scripts_dir = os.path.join(self._skill_dir, "scripts")
        has_scripts = os.path.isdir(scripts_dir) and any(
            f.endswith((".py", ".sh", ".bash"))
            for f in os.listdir(scripts_dir)
        )

        if has_scripts and context.venv_python:
            # Find the primary script (first .py or .sh file)
            script_path = self._find_primary_script(scripts_dir)
            if script_path:
                return await self._run_script(script_path, command, context)

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

    def _find_primary_script(self, scripts_dir: str) -> str | None:
        """Find the primary script to execute.

        Prefers .py files, then .sh files.  Returns the first one found.
        """
        for ext in (".py", ".sh", ".bash"):
            for fname in sorted(os.listdir(scripts_dir)):
                if fname.endswith(ext) and not fname.startswith("_"):
                    return os.path.join(scripts_dir, fname)
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
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=context.workspace_path,
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
            "  command (string, required): 完整的 CLI 命令参数，例如: greet --name Alice",
        ]
        if self._compatibility:
            lines.append(f"\n环境要求: {self._compatibility}")
        return "\n".join(lines)
