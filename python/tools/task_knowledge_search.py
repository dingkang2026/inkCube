"""Task-specific knowledge search tool.

Extends the existing KnowledgeSearch tool with project listing capability,
so the task agent can discover and search across knowledge base projects.
"""
import json

from tools.base import BaseTool


class TaskKnowledgeSearchTool(BaseTool):
    name = "knowledge_search"
    description = (
        "从知识库中检索与查询相关的信息。"
        "可以搜索知识库项目中的文档，辅助编程任务（如查找项目规范、参考资料）。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search", "list_projects"],
                "description": "操作类型: search（搜索知识库内容）、list_projects（列出可用项目）",
            },
            "query": {
                "type": "string",
                "description": "搜索查询词（自然语言或关键词），action=search 时必填",
            },
            "project_id": {
                "type": "string",
                "description": "知识库项目 ID，action=search 时必填。用 list_projects 获取可用 ID",
            },
            "top_k": {
                "type": "integer",
                "description": "返回结果数量，默认 5",
            },
        },
        "required": ["action"],
    }

    @property
    def prompt_description(self) -> str:
        return (
            "## knowledge_search\n"
            "从知识库中检索相关文档内容。当任务需要参考项目文档、规范或上传的资料时使用。\n\n"
            "操作:\n"
            "- list_projects: 列出所有知识库项目（获取 project_id）\n"
            "- search: 在指定项目中搜索相关内容\n\n"
            "示例:\n"
            '```\n{"action": "list_projects"}\n```\n\n'
            '```\n{"action": "search", "query": "API 接口规范", "project_id": "abc-123"}\n```\n\n'
            "注意: 先用 list_projects 获取项目列表，再用 search 在目标项目中搜索。"
        )

    def __init__(self):
        # Shared dependencies injected via configure()
        self.db = None
        self.chroma_persist_dir = ""
        self.models_dir = ""
        self._search_tool = None

    def configure(self, db, chroma_persist_dir: str, models_dir: str):
        """Inject runtime dependencies."""
        self.db = db
        self.chroma_persist_dir = chroma_persist_dir
        self.models_dir = models_dir
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

    async def execute(
        self,
        action: str = "search",
        query: str = "",
        project_id: str = "",
        top_k: int = 5,
    ) -> str:
        if action == "list_projects":
            return self._list_projects()
        elif action == "search":
            return await self._search(query, project_id, top_k)
        else:
            return json.dumps({"error": f"未知操作: {action}"}, ensure_ascii=False)

    def _list_projects(self) -> str:
        """List all knowledge base projects."""
        if not self.db:
            return json.dumps({"error": "数据库未初始化"}, ensure_ascii=False)

        try:
            tool = self._get_search_tool()
            if tool:
                projects = tool.list_projects()
            else:
                # Fallback: query DB directly
                cursor = self.db.conn.execute("""
                    SELECT p.id, p.name, p.description,
                           COUNT(DISTINCT f.id) as file_count,
                           COALESCE(SUM(f.chunk_count), 0) as chunk_count
                    FROM projects p
                    LEFT JOIN files f ON f.project_id = p.id
                    GROUP BY p.id
                    ORDER BY p.created_at DESC
                """)
                projects = [dict(row) for row in cursor.fetchall()]

            if not projects:
                return json.dumps({
                    "projects": [],
                    "message": "暂无知识库项目，请先在知识库页面创建项目并上传文档。",
                }, ensure_ascii=False)

            return json.dumps({
                "projects": [
                    {
                        "id": p["id"],
                        "name": p["name"],
                        "description": p.get("description", ""),
                        "file_count": p.get("file_count", 0),
                        "chunk_count": p.get("chunk_count", 0),
                    }
                    for p in projects
                ],
            }, ensure_ascii=False)

        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    async def _search(self, query: str, project_id: str, top_k: int) -> str:
        """Search within a specific knowledge base project."""
        if not query:
            return json.dumps({"error": "query 不能为空"}, ensure_ascii=False)
        if not project_id:
            return json.dumps({
                "error": "project_id 不能为空",
                "suggestion": "请先用 list_projects 获取可用的项目 ID",
            }, ensure_ascii=False)

        tool = self._get_search_tool()
        if tool is None:
            return json.dumps({
                "error": "知识库未初始化",
                "suggestion": "请检查 embedding 模型（bge-m3）是否已下载",
            }, ensure_ascii=False)

        try:
            results = await tool.search(
                query=query,
                project_id=project_id,
                top_k=top_k,
            )

            if not results:
                return json.dumps({
                    "results": [],
                    "message": "未在知识库中找到相关信息。",
                }, ensure_ascii=False)

            formatted = []
            for i, r in enumerate(results, 1):
                formatted.append({
                    "index": i,
                    "filename": r.get("metadata", {}).get("filename", "未知文件"),
                    "score": round(r.get("_score", 0), 4),
                    "content": r.get("content", "")[:500],
                })

            return json.dumps({
                "results": formatted,
                "total": len(formatted),
                "project_id": project_id,
            }, ensure_ascii=False)

        except Exception as e:
            return json.dumps({"error": f"知识库检索失败: {e}"}, ensure_ascii=False)
