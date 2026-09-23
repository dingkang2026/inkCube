"""SkillRegistry — unified registry for all Skills (builtin and external).

The agent interacts with the Skill framework exclusively through this registry:
- Registration (builtin tools via adapter, external skills via loader)
- Discovery (list enabled skills, generate function calling definitions)
- Execution (unified pre_execute → execute → post_execute pipeline)
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from skills.context import SkillContext
from skills.protocol import BaseSkill

if TYPE_CHECKING:
    from tools.base import BaseTool

logger = logging.getLogger(__name__)


class SkillRegistry:
    """Central registry for all Skills.

    Holds references to both builtin tools (wrapped via BuiltinToolAdapter)
    and Agent Skills (wrapped via AgentSkillAdapter).  The agent sees
    a uniform interface regardless of the underlying skill type.
    """

    def __init__(self):
        self._skills: dict[str, BaseSkill] = {}
        self._metadata: dict[str, dict] = {}

    # ── Registration ──

    def register(self, skill: BaseSkill, metadata: dict | None = None):
        """Register a single Skill.

        Args:
            skill: A BaseSkill instance (either native or adapter-wrapped).
            metadata: Optional metadata dict (e.g. ``{"type": "builtin", "enabled": True}``).
        """
        self._skills[skill.name] = skill
        self._metadata[skill.name] = metadata or {"type": "unknown", "enabled": True}
        logger.debug("SkillRegistry: registered '%s' (type=%s)",
                      skill.name, self._metadata[skill.name].get("type", "?"))

    def register_builtin_tools(self, tools: dict[str, "BaseTool"]):
        """Batch-register builtin tools via BuiltinToolAdapter.

        This is the primary entry point for wiring V1 tools into the
        new framework.  Each tool is automatically wrapped in an adapter.

        Args:
            tools: Dict mapping tool name → BaseTool instance.
        """
        from skills.loader import BuiltinToolAdapter  # local import to avoid circular

        for name, tool in tools.items():
            adapter = BuiltinToolAdapter(tool)
            self.register(adapter, {"type": "builtin", "enabled": True})
        logger.info("SkillRegistry: registered %d builtin tools", len(tools))

    # ── Discovery ──

    def get(self, name: str) -> BaseSkill | None:
        """Look up a Skill by name."""
        return self._skills.get(name)

    def list_enabled(self) -> list[str]:
        """Return names of all enabled Skills."""
        return [
            name for name, meta in self._metadata.items()
            if meta.get("enabled", True)
        ]

    # ── Function Calling integration ──

    def to_function_definitions(self) -> list[dict]:
        """Generate OpenAI function calling ``tools`` list.

        Only includes enabled Skills.  This is the core method that
        bridges the Skill framework with the LLM's native tool calling.
        """
        defs = []
        for name in self.list_enabled():
            if name in self._skills:
                skill = self._skills[name]
                defn = skill.to_function_definition()
                # Validate: ensure parameters has type field (GLM requirement)
                params = defn.get("function", {}).get("parameters", {})
                if not params.get("type"):
                    logger.warning("Skill '%s' has invalid parameters schema (missing 'type'), fixing...", name)
                    defn["function"]["parameters"] = {"type": "object", "properties": params.get("properties", {})}
                defs.append(defn)
        logger.info("Generated %d tool definitions for LLM", len(defs))
        return defs

    # ── Unified execution ──

    async def execute(self, name: str, params: dict, context: SkillContext) -> str:
        """Execute a Skill through the unified pipeline.

        Runs: pre_execute → execute → post_execute

        Args:
            name: Skill name.
            params: Parameters dict conforming to the skill's input_schema.
            context: Runtime context.

        Returns:
            JSON string result.
        """
        skill = self._skills.get(name)
        if not skill:
            return json.dumps({"error": f"未知 Skill: {name}"}, ensure_ascii=False)

        # pre_execute hook (may override params)
        override = await skill.pre_execute(params, context)
        if override is not None:
            params = override

        # main execution
        result = await skill.execute(params, context)

        # post_execute hook (may modify result)
        result = await skill.post_execute(result, params, context)

        return result
