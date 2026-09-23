from __future__ import annotations

from tools.base import BaseTool


class ToolRegistry:
    def __init__(self):
        self.tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool):
        self.tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self.tools.get(name)

    def configure(self, settings: dict):
        for tool in self.tools.values():
            if hasattr(tool, "update_api_key"):
                key = settings.get("tavily_key", "")
                if key:
                    tool.update_api_key(key)


tool_registry = ToolRegistry()


def _auto_register():
    try:
        from tools.tavily_search import TavilySearchTool
        tool_registry.register(TavilySearchTool())
    except ImportError:
        pass

    try:
        from tools.knowledge_search import KnowledgeSearch
        tool_registry.register(KnowledgeSearch())
    except ImportError:
        pass


_auto_register()
