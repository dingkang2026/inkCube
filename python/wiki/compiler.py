"""python/wiki/compiler.py — Two-stage Wiki compilation engine.

Implements the compile pipeline per §4.2 of 2.6wiki-design.md:
  1. Determine compile scope (schema + existing pages)
  2. Collect raw materials from RAG chunks
  3. Stage 1: Analysis (LLM call — plan pages, identify concepts)
  4. Stage 2: Generation (LLM call — produce PAGE/REVIEW blocks)
  5. Parse blocks → write to DB
"""
import asyncio
import hashlib
import json
import logging
from collections import defaultdict
from typing import Optional

from errors import (
    LLMNetworkError, LLMCallFailed, LLMInvalidKey,
    LLMRateLimited, LLMQuotaExhausted, LLMModelNotFound,
)
from wiki.parser import parse_compile_output
from wiki.prompts import build_stage1_analysis_prompt, build_stage2_generation_prompt
from wiki.llm_routing import get_task_settings
import activity_events

logger = logging.getLogger(__name__)


class WikiCompiler:
    """Two-stage Wiki compiler.

    Consumes RAG chunks (not raw files) and produces structured Markdown pages.
    """

    def __init__(self, db, data_dir: str, llm_client, llm_settings: dict):
        self.db = db
        self.data_dir = data_dir
        self.llm = llm_client
        self.settings = llm_settings

    async def compile(
        self,
        project_id: str,
        task_id: str,
        mode: str = "incremental",
    ) -> dict:
        """Run the full two-stage compilation.

        Args:
            project_id: Target project.
            task_id: Compile task ID for status tracking.
            mode: "full" or "incremental".

        Returns:
            Summary dict with pages_created, pages_updated, reviews_count, etc.
        """
        project = self.db.get_project(project_id)
        if not project:
            raise ValueError(f"Project not found: {project_id}")

        project_type = project.get("type", "general")

        # ── Step 1: Determine scope ──
        self.db.update_compile_task_status(task_id, "analyzing", stage="stage1_analysis")

        schemas = self.db.get_wiki_schemas(project_type)
        if not schemas:
            logger.warning("No wiki schemas for project_type=%s", project_type)
            schemas = self.db.get_wiki_schemas("general")

        existing_pages = self.db.list_wiki_pages(project_id)
        existing_map = {p["page_key"]: p for p in existing_pages}

        # Determine which pages need compile/update
        target_keys = [s["page_key"] for s in schemas]
        if mode == "incremental" and existing_pages:
            # For incremental, only compile pages that don't exist yet
            # or whose content might be affected by new files
            new_keys = [k for k in target_keys if k not in existing_map]
            if new_keys:
                target_keys = new_keys
            # If all pages exist, still compile all (LLM decides what to update)

        # Build schema hints
        schema_hints = "\n".join(
            f"- {s['page_key']}: {s.get('compile_hint', '')}"
            for s in schemas
        )
        compile_hints = {s["page_key"]: s.get("compile_hint", "") for s in schemas}

        # ── Step 2: Collect raw materials ──
        raw_materials = await self._collect_materials(project_id, schemas)
        base_lib_context = await self._collect_base_lib_context(project_id, schemas)
        logger.info("Wiki compile [%s]: raw_materials_len=%d, base_lib_context_len=%d",
                     task_id, len(raw_materials), len(base_lib_context))
        logger.info("Wiki compile [%s]: raw_materials:\n%s", task_id, raw_materials)
        logger.info("Wiki compile [%s]: base_lib_context:\n%s", task_id, base_lib_context)

        # Build existing pages summary for Stage 1
        existing_summary = ""
        if existing_pages:
            parts = []
            for p in existing_pages:
                content = (p.get("content") or "")[:200]
                parts.append(f"- {p['page_key']} ({p.get('title', '')}): {content}...")
            existing_summary = "\n".join(parts)

        # ── Step 3: Stage 1 — Analysis ──
        logger.info("Wiki compile [%s]: Stage 1 analysis (project=%s, targets=%d)",
                     task_id, project_id, len(target_keys))

        stage1_messages = build_stage1_analysis_prompt(
            raw_materials=raw_materials,
            existing_pages_summary=existing_summary,
            base_lib_context=base_lib_context,
            schema_hints=schema_hints,
        )

        analysis_report = await self._chat_with_retry(stage1_messages, self.settings, task_id, "stage1")
        logger.info("Wiki compile [%s]: Stage 1 done, analysis_len=%d", task_id, len(analysis_report))
        logger.info("Wiki compile [%s]: Stage 1 analysis output:\n%s", task_id, analysis_report)

        # ── Step 4: Stage 2 — Generation ──
        self.db.update_compile_task_status(task_id, "generating", stage="stage2_generation")

        # Build existing pages content for incremental merge
        existing_content = ""
        if existing_pages:
            parts = []
            for p in existing_pages:
                if p["page_key"] in target_keys:
                    parts.append(f"### {p['page_key']}\n{p.get('content', '')}")
            existing_content = "\n\n".join(parts)

        stage2_messages = build_stage2_generation_prompt(
            raw_materials=raw_materials,
            analysis_report=analysis_report,
            existing_pages_content=existing_content,
            base_lib_context=base_lib_context,
            compile_hints=compile_hints,
        )

        # Stage 2 needs larger token budget: deep-thinking models (e.g. GLM-5)
        # can consume most of max_tokens on reasoning, leaving nothing for content.
        # Override max_tokens to ensure enough room for page generation.
        stage2_settings = dict(self.settings)
        stage2_settings["max_tokens"] = max(
            int(self.settings.get("max_tokens", 4096)),
            32000,
        )
        logger.info(
            "Wiki compile [%s]: Stage 2 max_tokens=%d (original=%d)",
            task_id, stage2_settings["max_tokens"], int(self.settings.get("max_tokens", 4096)),
        )

        generation_output = await self._chat_with_retry(stage2_messages, stage2_settings, task_id, "stage2")
        logger.info("Wiki compile [%s]: Stage 2 done, output_len=%d", task_id, len(generation_output))
        logger.info("Wiki compile [%s]: Stage 2 generation output:\n%s", task_id, generation_output)

        # ── Step 5: Parse & Store ──
        self.db.update_compile_task_status(task_id, "storing", stage="store")

        pages, reviews = parse_compile_output(generation_output)

        pages_created = 0
        pages_updated = 0
        pages_failed = 0
        stored_page_keys = []  # track successfully stored pages for change log
        user_edited_keys = set()  # user_edited pages skipped — should not appear as "updated" in change log

        for page_data in pages:
            page_key = page_data["page_key"]
            content = page_data["content"]

            # Find schema title
            title = page_key
            for s in schemas:
                if s["page_key"] == page_key:
                    title = s["title"]
                    break

            existing = existing_map.get(page_key)

            # P6: Skip user_edited pages — user edits take priority over compile
            if existing and existing.get("status") == "user_edited":
                logger.info("Compile [%s]: skipping user_edited page '%s'", task_id, page_key)
                pages_updated += 1  # count as "handled" without overwriting
                stored_page_keys.append(page_key)
                user_edited_keys.add(page_key)
                continue

            try:
                if existing:
                    # Update existing page
                    new_version = (existing.get("compile_version") or 0) + 1
                    self.db.update_wiki_page_content(
                        page_id=existing["id"],
                        content=content,
                        title=title,
                        status="compiled",
                        compile_version=new_version,
                    )
                    pages_updated += 1
                else:
                    # Create new page
                    self.db.create_or_update_wiki_page(
                        project_id=project_id,
                        page_key=page_key,
                        title=title,
                        content=content,
                        status="compiled",
                    )
                    pages_created += 1
                stored_page_keys.append(page_key)
            except Exception as e:
                pages_failed += 1
                logger.error("Compile [%s]: failed to store page '%s': %s", task_id, page_key, e)

        # Store reviews
        reviews_count = 0
        for review_data in reviews:
            try:
                review_id = self._compute_review_id(review_data["type"], review_data["title"])
                self.db.create_or_update_wiki_review(
                    review_id=review_id,
                    project_id=project_id,
                    review_type=review_data["type"],
                    title=review_data["title"],
                    description=review_data.get("description", ""),
                )
                reviews_count += 1
            except Exception as e:
                logger.error("Compile [%s]: failed to store review '%s': %s", task_id, review_data.get("title", "?"), e)

        # Add change log entries (only for successfully stored pages, excluding user_edited)
        for page_key in stored_page_keys:
            if page_key in user_edited_keys:
                continue  # user_edited pages were not actually modified
            page = self.db.get_wiki_page(project_id, page_key)
            if page:
                action = "updated" if page_key in existing_map else "created"
                self.db.add_wiki_change_log(
                    project_id=project_id,
                    action=action,
                    wiki_page_id=page["id"],
                    summary=f"编译{'更新' if action == 'updated' else '创建'}: {page_key}",
                )

        # Update compile task — determine final status
        total_pages = len(pages)
        if pages_failed > 0 and pages_created + pages_updated == 0:
            # All pages failed
            compile_status = "failed"
        elif pages_failed > 0:
            # Partial success
            compile_status = "partial"
        else:
            compile_status = "completed"

        self.db.update_compile_task_status(
            task_id, compile_status,
            pages_total=total_pages,
            pages_completed=pages_created + pages_updated,
            pages_failed=pages_failed,
        )
        # 终态事件显式声明（0.4 §6.4 E2，顺序 4）：partial 按 completed 发（detail 注明部分完成）；
        # failed 分支无 error_message（与旧钩子行为等价，pages_failed 计数在快照里）
        if compile_status == "completed":
            activity_events.complete_compile(self.db, task_id)
        elif compile_status == "partial":
            activity_events.complete_compile(self.db, task_id, partial=True)
        else:
            activity_events.fail_compile(self.db, task_id)

        summary = {
            "pages_created": pages_created,
            "pages_updated": pages_updated,
            "pages_failed": pages_failed,
            "reviews_count": reviews_count,
            "total_pages": total_pages,
        }
        logger.info("Wiki compile [%s] %s: %s", task_id, compile_status, summary)
        return summary

    # Max chars for raw_materials (~50K tokens in Chinese).
    # Both Stage 1 and Stage 2 include raw_materials, and Stage 2 also needs
    # room for analysis_report + existing_pages + output tokens (32K).
    _MAX_MATERIALS_CHARS = 100_000

    async def _collect_materials(self, project_id: str, schemas: list[dict]) -> str:
        """Collect raw material chunks for the project.

        Fetches all chunks from chunks_fts. If total content exceeds
        _MAX_MATERIALS_CHARS, samples proportionally per source file to
        ensure every file is represented while staying within token budget.
        """
        try:
            cursor = self.db.conn.execute(
                """SELECT chunk_id, content, filename, content_type
                   FROM chunks_fts
                   WHERE project_id = ?
                   ORDER BY rowid ASC""",
                (project_id,),
            )
            rows = cursor.fetchall()

            if not rows:
                logger.warning("_collect_materials: 0 chunks found for project=%s", project_id)
                return ""

            # Group chunks by source file
            by_file: dict[str, list] = defaultdict(list)
            for row in rows:
                content = row["content"] or ""
                if content.strip():
                    by_file[row["filename"] or "unknown"].append(dict(row))

            all_chunks = [c for chunks in by_file.values() for c in chunks]
            total_chars = sum(len(c["content"]) for c in all_chunks)

            logger.info(
                "_collect_materials: %d chunks from %d files, total %d chars for project=%s",
                len(all_chunks), len(by_file), total_chars, project_id,
            )

            # If within budget, use all chunks
            if total_chars <= self._MAX_MATERIALS_CHARS:
                selected = all_chunks
            else:
                # Over budget — sample proportionally per file
                ratio = self._MAX_MATERIALS_CHARS / total_chars
                selected = []
                for fname, chunks in by_file.items():
                    n = max(1, int(len(chunks) * ratio))
                    step = len(chunks) / n
                    selected.extend(chunks[int(i * step)] for i in range(n))
                logger.warning(
                    "_collect_materials: truncated from %d to %d chars (%d files, ratio=%.2f)",
                    total_chars, self._MAX_MATERIALS_CHARS, len(by_file), ratio,
                )

            parts = []
            for i, c in enumerate(selected, 1):
                parts.append(f"--- 片段 {i} (来源: {c['filename']}) ---\n{c['content']}")

            result = "\n\n".join(parts)
            logger.info(
                "_collect_materials: final %d chunks, %d chars for project=%s",
                len(parts), len(result), project_id,
            )
            return result
        except Exception as e:
            logger.error("_collect_materials failed for project %s: %s", project_id, e)
            return ""

    async def _collect_base_lib_context(self, project_id: str, schemas: list[dict]) -> str:
        """Collect base library RAG context based on schema search hints."""
        try:
            from knowledge.knowledge_search import KnowledgeSearchTool
            from pathlib import Path

            # Get linked base libraries
            linked_libs = self.db.get_project_base_libs(project_id)
            if not linked_libs:
                logger.info("_collect_base_lib_context: no linked base libs for project=%s", project_id)
                return ""

            logger.info("_collect_base_lib_context: found %d linked libs for project=%s", len(linked_libs), project_id)

            search_tool = KnowledgeSearchTool(
                db=self.db,
                chroma_persist_dir=str(Path(self.data_dir) / "chroma"),
                models_dir=str(Path(self.data_dir) / "models"),
            )

            all_parts = []
            for schema in schemas:
                hints = schema.get("base_lib_search_hints", [])
                if isinstance(hints, str):
                    try:
                        hints = json.loads(hints)
                    except (json.JSONDecodeError, TypeError):
                        hints = []

                for hint in hints:
                    for lib in linked_libs:
                        lib_id = lib.get("id", "")
                        results = await search_tool.search(
                            query=hint,
                            project_id=lib_id,
                            top_k=3,
                        )
                        for r in results:
                            content = r.get("content", "")
                            if content:
                                all_parts.append(f"[{lib.get('name', '')}] {content}")

            return "\n\n".join(all_parts[:30])  # Limit to avoid token overflow
        except Exception as e:
            logger.error("_collect_base_lib_context failed for project %s: %s", project_id, e)
            return ""

    # ── LLM retry helper ──

    _LLM_MAX_RETRIES = 2  # Pipeline-level retries (on top of client's internal retry)

    async def _chat_with_retry(self, messages, settings, task_id: str, stage: str) -> str:
        """Call LLM with pipeline-level retry for transient errors.

        Retries on LLMNetworkError (timeout/connection) and LLMCallFailed (5xx).
        Does NOT retry on config/credential errors (401 Key 失效 / 429 限流额度 /
        402·403 欠费 / 404 模型名错）— those need user action (0.4 §7.3 v3.6).
        Uses task-specific model if configured via llm_routing.
        """
        # Apply task routing: use ingest-specific model if configured
        effective_settings = get_task_settings(settings, "ingest")
        llm = self.llm
        if effective_settings.get("model") != settings.get("model"):
            from llm import LLMClient
            llm = LLMClient(effective_settings)

        last_err = None
        for attempt in range(self._LLM_MAX_RETRIES + 1):
            try:
                return await llm.chat(messages, effective_settings)
            except (LLMInvalidKey, LLMRateLimited, LLMQuotaExhausted, LLMModelNotFound):
                raise  # Config/credential errors need user action — not retryable
            except (LLMNetworkError, LLMCallFailed) as e:
                last_err = e
                if attempt < self._LLM_MAX_RETRIES:
                    backoff = 2 ** (attempt + 1)  # 2s, 4s
                    logger.warning(
                        "Compile [%s] %s LLM retry %d/%d (backoff %ds): %s",
                        task_id, stage, attempt + 1, self._LLM_MAX_RETRIES, backoff, e,
                    )
                    await asyncio.sleep(backoff)
                else:
                    logger.error(
                        "Compile [%s] %s LLM failed after %d retries: %s",
                        task_id, stage, self._LLM_MAX_RETRIES, e,
                    )
        raise last_err

    @staticmethod
    def _compute_review_id(review_type: str, title: str) -> str:
        """Compute stable review ID (FNV-1a hash)."""
        normalized = f"{review_type}::{title.strip().lower()}"
        h = 0x811C9DC5
        for byte in normalized.encode("utf-8"):
            h ^= byte
            h = (h * 0x01000193) & 0xFFFFFFFF
        return f"review_{h:08x}"
