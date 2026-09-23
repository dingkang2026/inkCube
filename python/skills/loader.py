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

    Agent Skills is an open standard (https://agenticskills.io/) supported by
    Claude Code, Cursor, Codex, VS Code, etc.  A skill is a directory with:
      - SKILL.md (required): YAML frontmatter + Markdown instructions
      - scripts/ (optional): executable scripts
      - references/ (optional): reference documents
      - assets/ (optional): templates, resources

    Scripts are run in the task's venv.  Dependencies are declared in the
    'compatibility' frontmatter field and installed on demand.

    The function calling definition is generated from the skill's name and
    description.  Since Agent Skills don't have structured parameter schemas,
    the LLM sees a simple tool with a 'command' string parameter that contains
    the natural-language instruction for the skill.
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

        # Agent Skills don't have structured parameter schemas.
        # We expose a simple 'command' parameter for the LLM to pass
        # natural-language instructions.
        self.input_schema = {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": f"The command or instruction for the {self.name} skill. "
                                   f"Describe what you want to do in natural language.",
                },
            },
            "required": ["command"],
        }

    async def execute(self, params: dict, context: SkillContext) -> str:
        """Execute the Agent Skill.

        For Agent Skills, execution means:
        1. Check and install dependencies (from 'compatibility' field)
        2. If the skill has scripts, run them in the task's venv
        3. Otherwise, return the skill's instructions for the agent to follow

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
            # Ensure dependencies are installed
            dep_result = await self._ensure_dependencies(context)
            if dep_result:
                return dep_result

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
        # Level 2: inject full SKILL.md body into execution result
        if self._body:
            result_obj["instructions"] = self._body[:4000]
        return json.dumps(result_obj, ensure_ascii=False)

    async def _ensure_dependencies(self, context: SkillContext) -> str | None:
        """Check and install dependencies from the 'compatibility' field.

        Uses uv (faster, better cache) if available, falls back to pip.

        Returns:
            None if successful, or a JSON error string if installation fails.
        """
        from skills.parser import parse_compatibility

        required = parse_compatibility(self._compatibility)
        if not required:
            return None

        if not context.venv_python:
            return None  # Can't install without a Python interpreter

        # Check which packages are already installed
        try:
            proc = await asyncio.create_subprocess_exec(
                context.venv_python, "-m", "pip", "list", "--format=json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            installed = {
                pkg["name"].lower()
                for pkg in json.loads(stdout.decode("utf-8", errors="replace"))
            }
        except Exception:
            installed = set()

        missing = [pkg for pkg in required if pkg.lower() not in installed]
        if not missing:
            return None

        # Install missing packages: prefer uv (faster, better cache), fallback to pip
        from tools.tool_resolver import resolve_tool
        uv = resolve_tool("uv")

        try:
            if uv:
                cmd = [uv, "pip", "install", "--python", context.venv_python] + missing
            else:
                cmd = [context.venv_python, "-m", "pip", "install", "--quiet"] + missing

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            if proc.returncode != 0:
                return json.dumps({
                    "error": f"安装依赖失败: {stderr.decode('utf-8', errors='replace')[:500]}",
                    "missing_packages": missing,
                }, ensure_ascii=False)
        except asyncio.TimeoutError:
            return json.dumps({"error": "安装依赖超时 (120s)"}, ensure_ascii=False)
        except FileNotFoundError:
            return json.dumps({"error": f"找不到解释器: {cmd[0]}"}, ensure_ascii=False)

        return None

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

        # Level 2: inject full SKILL.md body into execution result
        if self._body:
            result_obj["instructions"] = self._body[:4000]

        if proc.returncode != 0:
            result_obj.setdefault("error", stderr_text or f"脚本执行失败 (exit code {proc.returncode})")
            result_obj["exit_code"] = proc.returncode

        if stderr_text and proc.returncode == 0:
            result_obj["stderr"] = stderr_text[:1000]

        return json.dumps(result_obj, ensure_ascii=False)

    @property
    def prompt_description(self) -> str:
        """Level 1: only name + description (saves tokens).

        Full SKILL.md body is injected into tool result at execution time (Level 2).
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
