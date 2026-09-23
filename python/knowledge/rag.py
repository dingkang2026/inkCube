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

        if not results:
            logger.info("RAG no results found")
            return {
                "answer": "未找到相关内容。请确保已上传并解析相关文件。",
                "sources": [],
            }

        # Step 2: Build context from search results
        context_parts: list[str] = []
        sources: list[dict] = []
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

        # Step 3: Build RAG prompt and call LLM
        from llm import LLMClient

        _settings = settings or {}
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
        answer = await llm.chat(messages, _settings)
        logger.info("RAG answer generated: len=%d", len(answer))

        return {"answer": answer, "sources": sources}
