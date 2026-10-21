"""python/tools/web_search.py — 网络搜索工具（双供应商：Tavily / 智谱，2026-10-19）。

供应商由用户在 设置 > 基础配置 > 网络搜索 二选一（web_search_provider，
无默认——未选择视为未配置）；两把 Key 并存，切换供应商不丢配置。
- Tavily：api.tavily.com；answer 段 CJK 查询关闭（c2f24e5c 翻译失真回归）
- 智谱：open.bigmodel.cn /paas/v4/web_search；search_query 上限 70 字符
  （超长截断），响应无 score（以返回序号为权重）、无 answer 段（无翻译失真问题）
"""
import httpx
from tools.base import BaseTool

# 智谱引擎档位白名单（settings:save 全量 PUT 可透传任意值，入口归一防脏值直传 API）
ZHIPU_ENGINES = ("search_std", "search_pro")
DEFAULT_ZHIPU_ENGINE = "search_pro"


def has_cjk(text: str) -> bool:
    """含 CJK 字符判定（c2f24e5c：Tavily answer 段对中文查询翻译失真，需关闭）。

    模块级导出：报告检索层（report_generator/retrieval.py）与工具层共用同一判定。
    """
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


class WebSearchTool(BaseTool):
    name = "web_search"
    description = "使用网络搜索引擎搜索互联网信息，获取实时数据。适用于需要最新信息、事实核查、或知识库中没有的内容。"
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

    def __init__(
        self,
        provider: str = "",
        tavily_key: str = "",
        zhipu_key: str = "",
        zhipu_engine: str = "search_pro",
    ):
        self.provider = (provider or "").strip()
        self.tavily_key = tavily_key
        self.zhipu_key = zhipu_key
        self.zhipu_engine = (
            zhipu_engine if zhipu_engine in ZHIPU_ENGINES else DEFAULT_ZHIPU_ENGINE
        )

    @property
    def _configured(self) -> bool:
        """未选供应商或所选供应商 Key 为空 = 未配置（readiness 同口径）。"""
        if self.provider == "tavily":
            return bool(self.tavily_key)
        if self.provider == "zhipu":
            return bool(self.zhipu_key)
        return False

    @property
    def prompt_description(self) -> str:
        return (
            "## web_search\n"
            "使用网络搜索引擎搜索互联网信息，获取实时数据。\n"
            "适用于需要最新信息、事实核查、或知识库中没有的内容。\n\n"
            "用法:\n"
            "- 搜索实时新闻、技术文档\n"
            "- 事实核查、获取背景信息\n"
            "- 需要在设置中配置网络搜索（未配置时返回错误提示）\n\n"
            "示例:\n"
            '```\n{"query": "Python 3.12 new features"}\n'
            "```\n\n"
            '```\n{"query": "latest AI research papers", "max_results": 3}\n'
            "```"
        )

    async def execute(self, query: str, max_results: int = 5) -> str:
        if not self._configured:
            return "网络搜索未配置：请前往 设置 > 基础配置 > 网络搜索 选择供应商并填写 API Key"
        if self.provider == "zhipu":
            return await self._execute_zhipu(query, max_results)
        return await self._execute_tavily(query, max_results)

    async def _execute_tavily(self, query: str, max_results: int) -> str:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                # 认证走 Bearer header（官方推荐，Key 不进请求体）；
                # language=zh-cn 中文命中加权（纯加权不丢结果）。
                # CJK 查询关闭 include_answer：Tavily 的 answer 段不受
                # language 控制，中文查询会返回英文摘要且伴随数量级翻译
                # 失真（亿→million，c2f24e5c 交互⑦实证差 1000 倍），
                # 数据可靠性低于结果条目原文。
                json={
                    "query": query,
                    "max_results": max_results,
                    "include_answer": not has_cjk(query),
                    "language": "zh-cn",
                },
                headers={"Authorization": f"Bearer {self.tavily_key}"},
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

    async def _execute_zhipu(self, query: str, max_results: int) -> str:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://open.bigmodel.cn/api/paas/v4/web_search",
                # 认证与智谱 LLM 同平台 Bearer（同一个 API Key 体系）；
                # search_query 上限 70 字符（API 硬限，超长截断）；
                # search_intent=False：工具场景显式搜索，跳过意图识别；
                # content_size=high：摘要取详细档（信息密度优先，报告/任务场景）。
                json={
                    "search_query": query[:70],
                    "search_engine": self.zhipu_engine,
                    "search_intent": False,
                    "count": max_results,
                    "content_size": "high",
                },
                headers={"Authorization": f"Bearer {self.zhipu_key}"},
            )
            response.raise_for_status()
            data = response.json()

        lines = []
        for i, result in enumerate(data.get("search_result") or [], 1):
            lines.append(f"[{i}] {result.get('title', '')}")
            lines.append(f"    {result.get('content', '')}")
            media = result.get("media", "")
            link = result.get("link", "")
            origin = f"{media} | {link}" if media and link else (link or media)
            lines.append(f"    来源: {origin}\n")

        return "\n".join(lines) if lines else "未找到相关结果"
