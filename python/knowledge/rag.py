"""python/knowledge/rag.py — Knowledge Base RAG (Retrieval-Augmented Generation).

This module is the knowledge base's OWN chat logic, completely independent
from the agent (产业洞察) subsystem.

Flow:
  1. Receive user question + optional file_id scope
  2. Search knowledge base via KnowledgeSearchTool
  3. Build context from retrieved chunks
  4. Call LLM with RAG prompt → return answer + sources
"""
import logging
from pathlib import Path
from typing import Optional

from knowledge.knowledge_search import KnowledgeSearchTool
from knowledge.vector_store import VectorStore
from knowledge.fts_store import FTSStore
from llm.budget import CHARS_PER_TOKEN, context_window as budget_context_window
from llm.usage import UsageContext, push_context  # 2.10 §4.3 用量环境
from db.database import Database

logger = logging.getLogger(__name__)


class KnowledgeRAG:
    """Knowledge base RQA engine. Isolated from the agent subsystem."""

    def __init__(self, db: Database, chroma_persist_dir: str, models_dir: str):
        self.db = db
        self.chroma_persist_dir = chroma_persist_dir
        self.models_dir = models_dir

    async def ask(
        self,
        project_id: str,
        message: str,
        file_id: Optional[str] = None,
        settings: Optional[dict] = None,
    ) -> dict:
        """
        Answer a question using RAG over the knowledge base.

        Args:
            project_id: The project to search within.
            message: User's question.
            file_id: Optional — restrict to a single file.
            settings: LLM settings dict (api_url, api_key, model, etc.).

        Returns:
            {"answer": str, "sources": list[dict]}
        """
        logger.info("RAG ask: project_id=%s, message_len=%d, file_id=%s",
                    project_id, len(message), file_id)
        # Step 1: Search for relevant chunks
        search_tool = KnowledgeSearchTool(
            db=self.db,
            chroma_persist_dir=self.chroma_persist_dir,
            models_dir=self.models_dir,
        )
        results = await search_tool.search(
            query=message,
            project_id=project_id,
            top_k=5,
        )
        logger.debug("RAG search results: %d chunks", len(results))

        # Filter by file_id if specified (single-file scope)
        if file_id:
            results = [
                r for r in results
                if r.get("metadata", {}).get("file_id") == file_id
            ]
            logger.debug("RAG filtered by file_id: %d chunks", len(results))

        # Step 1b: Wiki page retrieval (high-weight skeleton context)
        # Use the new wiki search engine with context budget
        wiki_context = self._search_wiki_context(project_id, message, settings=settings)

        if not results and not wiki_context:
            logger.info("RAG no results found")
            return {
                "answer": "未找到相关内容。请确保已上传并解析相关文件。",
                "sources": [],
            }

        # Step 2: Build context from search results
        context_parts: list[str] = []
        sources: list[dict] = []

        # Wiki pages first (high-weight skeleton)
        if wiki_context:
            context_parts.append("=== Wiki 知识页面（结构化参考） ===")
            for wp in wiki_context:
                context_parts.append(f"## {wp['title']} ({wp['page_key']})")
                # Content already truncated by context_budget
                context_parts.append(wp.get("content", ""))
                context_parts.append("")
                sources.append({
                    "filename": f"Wiki: {wp['title']}",
                    "page": None,
                    "section_path": wp.get("page_key", ""),
                    "content_type": "wiki_page",
                    "score": wp.get("_score", 1.0),
                })
            context_parts.append("=== 原始知识库片段 ===")

        for i, r in enumerate(results, 1):
            meta = r.get("metadata", {})
            md = meta.get("markdown_content") or r.get("content", "")
            filename = meta.get("filename", "")
            page = meta.get("page")
            section = meta.get("section_path", "")

            context_parts.append(f"--- 片段 {i} (来源: {filename}) ---")
            if section:
                context_parts.append(f"章节: {section}")
            context_parts.append(md)
            context_parts.append("")

            sources.append({
                "filename": filename,
                "page": page,
                "section_path": section,
                "content_type": meta.get("content_type", "paragraph"),
                "score": r.get("_score", 0),
            })

        context_text = "\n".join(context_parts)
        logger.debug("RAG context built: len=%d chars, %d sources", len(context_text), len(sources))

        # Step 3: Build RAG prompt and call LLM（rag_model 路由，空值回退全局模型）
        from llm import LLMClient
        from llm.routing import get_task_settings

        _settings = get_task_settings(settings or {}, "rag")
        llm = LLMClient(_settings)

        system_msg = (
            "你是一个知识库问答助手。"
            "根据以下检索到的知识库片段回答用户问题。"
            "如果片段中包含表格，请保留 Markdown 表格格式。"
            "回答时注明信息来源。"
            "如果知识库内容不足以回答，请如实说明。"
        )

        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": f"知识库内容：\n{context_text}\n\n---\n\n问题：{message}"},
        ]

        logger.info("RAG calling LLM: model=%s", _settings.get("model", "default"))
        # 2.10 §4.3: 用量环境（source=chat / purpose=rag，归属项目与文件范围）
        with push_context(UsageContext(
                source="chat", purpose="rag", project_id=project_id,
                entity_id=file_id)):
            result = await llm.chat_full(messages)
        answer = result["content"]
        logger.info("RAG answer generated: len=%d", len(answer))

        # 2.10 §4.5: 响应体带 usage（RAG 气泡操作行实时呈现；无精确值时为空）
        return {"answer": answer, "sources": sources,
                "usage": result.get("usage") or {}}

    def _search_wiki_context(
        self, project_id: str, message: str, top_k: int = 5,
        settings: Optional[dict] = None,
    ) -> list[dict]:
        """Search compiled Wiki pages as high-weight skeleton context.

        Uses the wiki search engine (keyword + graph expansion) with
        context budget management.
        """
        try:
            from wiki.wiki_search import search_wiki
            from wiki.context_budget import compute_budgets, truncate_wiki_context

            # Search with higher top_k to allow budget filtering
            pages = search_wiki(
                query=message,
                project_id=project_id,
                db=self.db,
                top_k=top_k * 2,
                enable_graph=True,
            )
            if not pages:
                return []

            # Apply context budget truncation（窗口经 budget 唯一入口 + CHARS_PER_TOKEN
            # 换算；旧 compute_budgets() 用 400_000 私设兜底，低估中文 token 占用）
            wiki_window_chars = int(budget_context_window(settings or {}) * CHARS_PER_TOKEN)
            budgets = compute_budgets(context_window=wiki_window_chars)
            truncated = truncate_wiki_context(pages, budgets["wiki"])

            logger.info("Wiki context: %d pages searched → %d after budget truncation",
                        len(pages), len(truncated))
            return truncated
        except Exception as e:
            logger.warning("Wiki context search failed: %s", e)
            return []
