from __future__ import annotations
from typing import Any


class BaseTool:
    name: str = ""
    description: str = ""
    parameters: dict = {}

    @property
    def prompt_description(self) -> str:
        """Rich description for LLM system prompt. Override in subclasses."""
        return self.description

    async def pre_execute(self, **kwargs: Any) -> dict | None:
        """Hook called before execute(). Return dict to override params, or None."""
        return None

    async def post_execute(self, result: str, **kwargs: Any) -> str:
        """Hook called after execute(). Return modified result or original."""
        return result

    async def execute(self, **kwargs: Any) -> str:
        raise NotImplementedError
