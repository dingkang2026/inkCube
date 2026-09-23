import httpx
from tools.base import BaseTool


class TavilySearchTool(BaseTool):
    name = "web_search"
    description = "使用 Tavily 搜索引擎搜索互联网信息，获取实时数据。适用于需要最新信息、事实核查、或知识库中没有的内容。"
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索查询词",
            },
            "max_results": {
                "type": "integer",
                "description": "最大返回结果数，默认 5",
                "default": 5,
            },
        },
        "required": ["query"],
    }

    def __init__(self, api_key: str = ""):
        self.api_key = api_key

    def update_api_key(self, api_key: str):
        self.api_key = api_key

    @property
    def prompt_description(self) -> str:
        return (
            "## web_search\n"
            "使用 Tavily 搜索引擎搜索互联网信息，获取实时数据。\n"
            "适用于需要最新信息、事实核查、或知识库中没有的内容。\n\n"
            "用法:\n"
            "- 搜索实时新闻、技术文档\n"
            "- 事实核查、获取背景信息\n"
            "- 需要 Tavily API Key（未配置时返回错误提示）\n\n"
            "示例:\n"
            '```\n{"query": "Python 3.12 new features"}\n'
            "```\n\n"
            '```\n{"query": "latest AI research papers", "max_results": 3}\n'
            "```"
        )

    async def execute(self, query: str, max_results: int = 5) -> str:
        if not self.api_key:
            return "Tavily API Key 未配置，无法执行网络搜索"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": self.api_key,
                    "query": query,
                    "max_results": max_results,
                    "include_answer": True,
                },
            )
            response.raise_for_status()
            data = response.json()

        lines = []
        if data.get("answer"):
            lines.append(f"摘要: {data['answer']}\n")

        for i, result in enumerate(data.get("results", []), 1):
            lines.append(f"[{i}] {result['title']}")
            lines.append(f"    {result['content']}")
            lines.append(f"    来源: {result['url']}\n")

        return "\n".join(lines) if lines else "未找到相关结果"
