"""python/tools/knowledge_search.py

ReAct agent tool for querying the knowledge base.
Wraps knowledge.knowledge_search.KnowledgeSearchTool as a BaseTool.
"""
from tools.base import BaseTool


class KnowledgeSearch(BaseTool):
    name = "knowledge_search"
    description = (
        "从知识库中检索与查询相关的信息。"
        "当需要基于项目文档、上传的资料回答问题时使用此工具。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索查询词（自然语言或关键词）",
            },
            "project_id": {
                "type": "string",
                "description": "知识库项目 ID",
            },
            "top_k": {
                "type": "integer",
                "description": "返回结果数量，默认 5",
                "default": 5,
            },
        },
        "required": ["query", "project_id"],
    }

    def __init__(self):
        # Dependencies injected later via configure()
        self.db = None
        self.chroma_persist_dir = ""
        self.models_dir = ""
        self._search_tool = None

    def configure(self, db, chroma_persist_dir: str, models_dir: str):
        """Inject runtime dependencies (called from main.py lifespan)."""
        self.db = db
        self.chroma_persist_dir = chroma_persist_dir
        self.models_dir = models_dir
        # Reset cached tool so it picks up new db on next call
        self._search_tool = None

    def _get_search_tool(self):
        """Lazy-initialise the underlying KnowledgeSearchTool."""
        if self._search_tool is None and self.db is not None:
            from knowledge.knowledge_search import KnowledgeSearchTool as _KST
            self._search_tool = _KST(
                db=self.db,
                chroma_persist_dir=self.chroma_persist_dir,
                models_dir=self.models_dir,
            )
        return self._search_tool

    async def execute(self, query: str, project_id: str, top_k: int = 5) -> str:
        tool = self._get_search_tool()
        if tool is None:
            return "知识库未初始化，请检查模型是否已下载。"

        try:
            results = await tool.search(
                query=query,
                project_id=project_id,
                top_k=top_k,
            )

            if not results:
                return "未在知识库中找到相关信息。"

            lines = [f"找到 {len(results)} 条相关知识片段：\n"]
            for i, r in enumerate(results, 1):
                filename = r.get("metadata", {}).get("filename", "未知文件")
                content = r.get("content", "")
                score = r.get("_score", 0)
                lines.append(f"[{i}] 来源: {filename} (相关度: {score:.4f})")
                lines.append(f"    {content[:500]}")
                lines.append("")

            return "\n".join(lines)

        except Exception as e:
            return f"知识库检索失败: {e}"
