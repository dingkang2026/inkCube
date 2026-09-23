"""BaseSkill — the unified protocol for all Skills (builtin and Agent Skills)."""

from __future__ import annotations

from skills.context import SkillContext


class BaseSkill:
    """Abstract base class for all Skills.

    Both builtin tools (via BuiltinToolAdapter) and Agent Skills
    (via AgentSkillAdapter) conform to this interface, allowing
    the SkillRegistry to treat them uniformly.

    Subclasses must set class-level attributes and override ``execute()``.
    """

    name: str = ""
    description: str = ""
    input_schema: dict = {}
    permissions: list[str] = []

    # ── Execution protocol ──

    async def execute(self, params: dict, context: SkillContext) -> str:
        """Execute the Skill.

        Args:
            params: Parameters conforming to ``input_schema``.
            context: Runtime context (workspace, venv, settings, etc.).

        Returns:
            JSON string with standard structure:
            - Success: ``{"exit_code": 0, "stdout": "...", ...}``
            - Failure: ``{"error": "...", "error_type": "...", "exit_code": -1}``
        """
        raise NotImplementedError

    async def pre_execute(self, params: dict, context: SkillContext) -> dict | None:
        """Hook called before ``execute()``.

        Return a modified params dict to override the original,
        or ``None`` to keep the original params unchanged.
        """
        return None

    async def post_execute(self, result: str, params: dict, context: SkillContext) -> str:
        """Hook called after ``execute()``.

        Return a (possibly modified) result string.
        """
        return result

    # ── Function Calling integration ──

    def to_function_definition(self) -> dict:
        """Generate an OpenAI-compatible function calling tool definition."""
        # Ensure parameters has a valid schema (GLM requires type: object)
        params = self.input_schema
        if not params or "type" not in params:
            params = {"type": "object", "properties": {}}
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": params,
            },
        }
