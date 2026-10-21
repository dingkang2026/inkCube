"""SkillRunnerTool — discover and execute third-party skills at runtime.

Each installed skill lives in its own directory with a .venv.
The runner invokes the skill's entry point (__init__.py or main.py)
inside the skill's venv via subprocess, passing JSON input via stdin
and reading JSON output from stdout.

Skill entry-point contract:
    The skill module must define a `run(params: dict) -> dict | str` function.
    The runner generates a small wrapper script that imports the skill,
    calls `run()`, and prints the result as JSON to stdout.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

from tools.base import BaseTool


class SkillRunnerTool(BaseTool):
    name = "skill_runner"
    description = "运行已安装的第三方 Skill 扩展。"

    parameters = {
        "type": "object",
        "properties": {
            "skill": {
                "type": "string",
                "description": "要运行的 Skill 名称",
            },
            "input": {
                "type": "object",
                "description": "传递给 Skill 的输入参数 (JSON 对象)",
                "default": {},
            },
        },
        "required": ["skill"],
    }

    def __init__(self, db=None):
        self.db = db
        self._skill_cache: list[dict] = []
        self.task_venv_python: str = ""  # task venv python for Agent Skills
        self.task_workspace_path: str = ""  # task workspace for subprocess cwd
        self.task_id: str = ""  # task identity for SkillContext (INKCUBE_TASK_ID 注入链)
        self.fc_skill_names: set[str] = set()  # 已注册为独立 FC 工具的 Skill 名（双通道去冗）

    def configure(self, db):
        """Set the database reference for skill discovery."""
        self.db = db
        self._skill_cache = []
        self.fc_skill_names = set()

    def mark_fc_registered(self, names: list[str]) -> None:
        """记录已注册为独立 function-calling 工具的 Agent Skill 名。

        CLI 型 Agent Skill 每次任务都会经 TaskAgent._register_external_skills
        注册成独立 FC 工具（L1 自带用法围栏）。若 skill_runner 的 L1 清单再
        列一遍同名 Skill，模型会走 skill_runner「先激活」——参数约定不同
        （skill+input vs command），首调必错一轮（2026-10-16 天眼查任务
        实证：空 input 调 skill_runner → CLI usage 报错 → 换道独立工具）。
        prompt_description 据此分组标注。
        """
        self.fc_skill_names = set(names or [])

    def _refresh_cache(self):
        """Reload enabled skills from DB."""
        if not self.db:
            self._skill_cache = []
            return
        try:
            all_skills = self.db.list_skills()
            self._skill_cache = [
                s for s in all_skills
                if s.get("enabled", 1) and s.get("install_path")
            ]
        except Exception:
            self._skill_cache = []

    def _find_skill(self, name: str) -> dict | None:
        """Find a skill by name from the cache."""
        for s in self._skill_cache:
            if s["name"] == name:
                return s
        return None

    @property
    def prompt_description(self) -> str:
        """Level 1: only name + description (saves tokens).

        Full SKILL.md usage instructions are loaded at execution time (Level 2).
        """
        self._refresh_cache()
        lines = [
            "## skill_runner",
            "运行已安装的第三方 Skill 扩展。每个 Skill 在独立虚拟环境中执行。",
            "",
            "参数: `skill` (名称) + `input` (JSON 对象)",
            "",
        ]
        if self._skill_cache:
            standalone = [s for s in self._skill_cache
                          if s["name"] in self.fc_skill_names]
            rest = [s for s in self._skill_cache
                    if s["name"] not in self.fc_skill_names]
            if standalone:
                names = ", ".join(f"**{s['name']}**" for s in standalone)
                lines.append(
                    f"已作为独立工具注册的 Skill（直接按工具名调用，无需经本工具）: {names}")
            if rest:
                lines.append("可用 Skill:")
                for s in rest:
                    desc = s.get("description", "") or "无描述"
                    lines.append(f"- **{s['name']}**: {desc}")
        else:
            lines.append("当前无已安装的第三方 Skill。")
        return "\n".join(lines)

    @staticmethod
    def _read_skill_usage(install_path: str) -> str:
        """Read usage instructions from SKILL.md body."""
        skill_md = os.path.join(install_path, "SKILL.md")
        if not os.path.isfile(skill_md):
            return ""
        try:
            from skills.parser import parse_skill_md
            parsed = parse_skill_md(skill_md)
            body = parsed.get("body", "")
            # Return first few lines of the body as usage hint
            lines = body.strip().splitlines()[:5]
            return "\n".join(lines)
        except Exception:
            return ""

    async def _execute_agent_skill(
        self, install_path: str, skill_md: str, skill_info: dict, input_params: dict,
    ) -> str:
        """Execute an Agent Skill (SKILL.md format) using the task's venv.

        Agent Skills don't have their own .venv. They use the task's venv python.
        """
        try:
            from skills.parser import parse_skill_md, validate_skill_md
            from skills.loader import AgentSkillAdapter
            from skills.context import SkillContext

            parsed = parse_skill_md(skill_md)
            errors = validate_skill_md(parsed)
            if errors:
                return json.dumps(
                    {"error": f"SKILL.md 验证失败: {'; '.join(errors)}"},
                    ensure_ascii=False,
                )

            adapter = AgentSkillAdapter(install_path, parsed, skill_info)

            # Use task venv python (Agent Skills share the task's environment)
            # task_id 必须透传：loader 依它注入 INKCUBE_TASK_ID（租约锁身份，2.8 §3.3）
            venv_python = self.task_venv_python
            ctx = SkillContext(
                workspace_path=self.task_workspace_path,
                venv_python=venv_python,
                task_id=self.task_id,
                settings={},
            )
            return await adapter.execute(input_params, ctx)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    async def execute(self, skill: str = "", input: dict | None = None) -> str:
        if not skill:
            return json.dumps({"error": "缺少 skill 参数"}, ensure_ascii=False)

        self._refresh_cache()
        skill_info = self._find_skill(skill)
        if not skill_info:
            available = [s["name"] for s in self._skill_cache]
            return json.dumps({
                "error": f"未找到 Skill: {skill}",
                "available": available,
            }, ensure_ascii=False)

        install_path = skill_info["install_path"]
        if not os.path.isdir(install_path):
            return json.dumps({
                "error": f"Skill 目录不存在: {install_path}",
            }, ensure_ascii=False)

        # ── Agent Skill execution (SKILL.md format) ──
        # Agent Skills don't have their own .venv; they use the task's venv.
        skill_md = os.path.join(install_path, "SKILL.md")
        if os.path.isfile(skill_md):
            return await self._execute_agent_skill(install_path, skill_md, skill_info, input or {})

        # Not a recognized Agent Skill
        return json.dumps({
            "error": f"Skill `{skill}` 不是有效的 Agent Skill (缺少 SKILL.md)",
        }, ensure_ascii=False)
