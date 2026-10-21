"""python/wiki/ingest.py — Per-file Wiki ingest engine.

Implements the per-file incremental ingestion pipeline per §4.2 of 2.6wiki-design.md:
  Step 1: Prepare ingest context (full text + purpose + schemas + index + overview)
  Step 2: LLM Analysis (first call — plan pages, identify entities/concepts)
  Step 3: LLM Generation (second call — produce PAGE blocks with YAML frontmatter)
  Step 4: Store & trace (write pages, source_pages, change_log, reviews, update overview)

Skip-if-unchanged lives at queue level (get_pending_wiki_files excludes files
with an ingested source_page), not in this module.
"""
import asyncio
import json
import logging
import re
from collections import defaultdict

from errors import LLMNetworkError, LLMCallFailed, LLMInvalidKey
from llm import budget
from llm.usage import UsageContext, push_context  # 2.10 §4.3 用量环境
from wiki.parser import parse_compile_output_v2, parse_resolves_blocks
from wiki.prompts import build_ingest_analysis_prompt, build_ingest_generation_prompt
from wiki.review_id import compute_review_id  # review_id 唯一实现(H-1:必须项目作用域)
from llm.routing import get_task_settings

logger = logging.getLogger(__name__)

# Max chars for raw materials sent to LLM
_MAX_MATERIALS_CHARS = 100_000
# Max chars for overview update
_MAX_OVERVIEW_CHARS = 3000
# Per-page char cap when injecting existing pages into the Stage 2 prompt
_MAX_EXISTING_PAGE_CHARS = 2000
# Total char cap for the existing-pages block (whole-page granularity, see
# _build_existing_content)
_MAX_EXISTING_TOTAL_CHARS = 30000


class WikiIngestor:
    """Per-file Wiki ingest engine.

    Each file is ingested independently. The ingest reads the full original
    text (reconstructed from FTS chunks), runs two-stage LLM pipeline,
    and stores the results with full traceability.
    """

    def __init__(self, db, data_dir: str, llm_client, llm_settings: dict):
        self.db = db
        self.data_dir = data_dir
        self.llm = llm_client
        self.settings = llm_settings

    async def ingest_file(self, project_id: str, file_id: str, task_id: str) -> dict:
        """Ingest a single file into the Wiki.

        Returns summary dict with pages_created, pages_updated, reviews_count, etc.
        Task status is managed by the caller (compile worker, task_queue).
        """
        project = self.db.get_project(project_id)
        if not project:
            raise ValueError(f"Project not found: {project_id}")

        file_rec = self.db.get_file(file_id)
        if not file_rec:
            raise ValueError(f"File not found: {file_id}")

        project_type = project.get("type", "general")
        filename = file_rec.get("filename", "unknown")

        # File SHA-256, recorded in wiki_source_pages for ingestion tracking (§3.7).
        # Skip-if-unchanged lives at queue level: get_pending_wiki_files excludes
        # files with an ingested source_page, so every file reaching here is new work.
        content_hash = file_rec.get("file_hash", "")

        self.db.update_compile_task_status(task_id, "ingesting", stage="read_source")

        # Source page key for this file (needed both for skip-tracking below and Step 4)
        file_slug = re.sub(r"[^\w]+", "_", filename.rsplit(".", 1)[0]).strip("_").lower()
        source_page_key = f"src_{file_slug}" if file_slug else f"src_{file_id[:8]}"

        # ── Step 1: Prepare context ──
        full_text = self._read_full_text(file_id, project_id)
        if not full_text.strip():
            # 无文本内容（纯扫描件/空文件）——skipped 留痕后跳过，不再进入待编译集合
            # （否则每轮增量重复撞同一文件且计入 completed，伪装成功，§3.7/§4.2）
            logger.warning("Ingest [%s]: no text content for file '%s' — skipped", task_id, filename)
            self.db.create_or_update_source_page(
                project_id=project_id,
                file_id=file_id,
                source_page_key=source_page_key,
                content_hash=content_hash,
                status="skipped",
                pages_touched=[],
            )
            return {"skipped": True, "error": "no_text", "filename": filename, "file_id": file_id}

        purpose = self.db.get_wiki_purpose(project_id)
        overview = self.db.get_wiki_overview(project_id)
        schemas = self.db.get_wiki_schemas(project_type)
        if not schemas:
            schemas = self.db.get_wiki_schemas("general")

        existing_pages = self.db.list_wiki_pages(project_id)
        index_summary = self._build_index_summary(existing_pages)
        schema_hints = self._build_schema_hints(schemas)
        base_lib_context = await self._collect_base_lib_context(project_id, schemas)

        logger.info(
            "Ingest [%s]: file='%s' text_len=%d, pages=%d, schemas=%d",
            task_id, filename, len(full_text), len(existing_pages), len(schemas),
        )

        # ── Step 2: LLM Analysis ──
        self.db.update_compile_task_status(task_id, "ingesting", stage="stage1_analysis")

        analysis_messages = build_ingest_analysis_prompt(
            source_text=full_text,
            filename=filename,
            purpose=purpose,
            overview=overview,
            index_summary=index_summary,
            schema_hints=schema_hints,
            base_lib_context=base_lib_context,
            project_type=project_type,
        )

        # max_tokens 不在此覆写：LLMClient 构造期经 budget.output_tokens floor 抬升
        # （≥32K，原 max(设置值, 32000) 在 floor 落地后为死代码，语义被 D1 吸收）；
        # reasoning_effort comes from user settings (no hard override — see settings page 思考深度)
        prompt_chars = sum(len(m.get("content", "")) for m in analysis_messages)
        logger.info(
            "Ingest [%s]: Stage 1 start — model=%s, max_tokens=%d, reasoning_effort=%s, prompt_chars=%d, timeout=%ds",
            task_id, self.settings.get("model", "?"), budget.output_tokens(self.settings),
            self.settings.get("reasoning_effort", "default"),
            prompt_chars, int(self.settings.get("llm_read_timeout", 300)) + 60,
        )
        analysis_report = await self._chat_with_retry(
            analysis_messages, self.settings, task_id, "stage1",
            project_id=project_id)
        logger.info("Ingest [%s]: Stage 1 analysis done, len=%d", task_id, len(analysis_report))

        # Validate Stage 1 result — empty analysis means LLM failed silently
        _MIN_ANALYSIS_LEN = 50
        if not analysis_report or len(analysis_report.strip()) < _MIN_ANALYSIS_LEN:
            err_msg = f"Stage 1 分析结果为空或过短（len={len(analysis_report) if analysis_report else 0}），LLM 可能未正常响应"
            logger.error("Ingest [%s]: %s", task_id, err_msg)
            raise LLMNetworkError(detail=err_msg)

        # ── Step 3: LLM Generation ──
        self.db.update_compile_task_status(task_id, "ingesting", stage="stage2_generation")

        # Build existing pages content for incremental merge
        existing_content = self._build_existing_content(existing_pages)

        # 缺口闭环（§4.3/§4.4，E2-2026-10）：查未解决 data-gap/missing-page
        # 注入编号清单，LLM 认定本文件补上则输出 RESOLVES，落库后按编号归档
        open_gap_rows = [
            r for r in (self.db.list_wiki_reviews(project_id, resolved=False) or [])
            if r.get("type") in ("data-gap", "missing-page")
        ]
        open_gaps = [
            {
                "index": i,
                "review_id": r["id"],
                "title": r.get("title", ""),
                "description": (r.get("description") or "")[:200],
            }
            for i, r in enumerate(open_gap_rows, start=1)
        ]

        generation_messages = build_ingest_generation_prompt(
            source_text=full_text,
            filename=filename,
            analysis_report=analysis_report,
            purpose=purpose,
            overview=overview,
            existing_content=existing_content,
            schema_hints=schema_hints,
            base_lib_context=base_lib_context,
            project_type=project_type,
            open_gaps=open_gaps,
        )

        gen_prompt_chars = sum(len(m.get("content", "")) for m in generation_messages)
        logger.info(
            "Ingest [%s]: Stage 2 start — model=%s, max_tokens=%d, reasoning_effort=%s, prompt_chars=%d, timeout=%ds",
            task_id, self.settings.get("model", "?"), budget.output_tokens(self.settings),
            self.settings.get("reasoning_effort", "default"),
            gen_prompt_chars, int(self.settings.get("llm_read_timeout", 300)) + 60,
        )
        generation_output = await self._chat_with_retry(
            generation_messages, self.settings, task_id, "stage2",
            project_id=project_id)
        logger.info("Ingest [%s]: Stage 2 generation done, len=%d", task_id, len(generation_output))

        # ── Step 4: Store & Trace ──
        self.db.update_compile_task_status(task_id, "storing", stage="store")

        pages, reviews, overview_update = parse_compile_output_v2(generation_output)

        pages_created = 0
        pages_updated = 0
        pages_failed = 0
        stored_page_keys = []
        all_touched_keys = []
        user_edited_keys = set()  # user_edited pages — should not count as "updated" in change log

        existing_map = {p["page_key"]: p for p in existing_pages}

        for page_data in pages:
            page_key = page_data["page_key"]
            content = page_data["content"]
            page_type = page_data.get("page_type", "entity")
            title = page_data.get("title", page_key)
            refs = page_data.get("refs", [])
            tags = page_data.get("tags", [])

            # Skip user_edited pages — 以落库前实时状态为准：快照取自 Step 1，
            # 其后的数分钟 LLM 窗口内用户可能已通过矛盾裁决/手动编辑写入
            # user_edited（§5.5），按陈旧快照判定会静默回滚用户内容
            existing = existing_map.get(page_key)
            if existing:
                current = self.db.get_wiki_page(project_id, page_key)
                if current and current.get("status") == "user_edited":
                    logger.info("Ingest [%s]: skipping user_edited page '%s'", task_id, page_key)
                    pages_updated += 1
                    stored_page_keys.append(page_key)
                    all_touched_keys.append(page_key)
                    user_edited_keys.add(page_key)
                    continue

            try:
                if existing:
                    new_version = (existing.get("compile_version") or 0) + 1
                    self.db.update_wiki_page_content(
                        page_id=existing["id"],
                        content=content,
                        title=title,
                        status="compiled",
                        compile_version=new_version,
                        refs=refs,
                        tags=tags,
                        page_type=page_type,
                    )
                    pages_updated += 1
                else:
                    result = self.db.create_or_update_wiki_page(
                        project_id=project_id,
                        page_key=page_key,
                        title=title,
                        content=content,
                        status="compiled",
                        page_type=page_type,
                        refs=refs,
                        sources=[file_id],
                        tags=tags,
                    )
                    pages_created += 1

                # Track source relationship
                page_rec = self.db.get_wiki_page(project_id, page_key)
                if page_rec:
                    self.db.add_wiki_page_source(page_rec["id"], file_id, contribution=f"摄入自 {filename}")

                stored_page_keys.append(page_key)
                all_touched_keys.append(page_key)
            except Exception as e:
                pages_failed += 1
                logger.error("Ingest [%s]: failed to store page '%s': %s", task_id, page_key, e)

        # Store reviews
        reviews_count = 0
        for review_data in reviews:
            try:
                review_id = compute_review_id(project_id, review_data["type"], review_data["title"])
                affected_ids = []
                for pk in review_data.get("affected_page_keys", []):
                    p = self.db.get_wiki_page(project_id, pk)
                    if p:
                        affected_ids.append(p["id"])
                # confirm（「材料与百科吻合」类正面结论）不是待办——落库即归档（§4.4），
                # 不占 Lint 待处理列表；其余类型 resolved=0 进入待处理
                is_confirm = review_data["type"] == "confirm"
                self.db.create_or_update_wiki_review(
                    review_id=review_id,
                    project_id=project_id,
                    review_type=review_data["type"],
                    title=review_data["title"],
                    description=review_data.get("description", ""),
                    affected_page_ids=affected_ids if affected_ids else None,
                    source_file_id=file_id,
                    resolved=1 if is_confirm else 0,
                    resolved_action="auto_confirmed" if is_confirm else None,
                    evidence=review_data.get("evidence") or None,
                )
                reviews_count += 1
            except Exception as e:
                logger.error("Ingest [%s]: failed to store review: %s", task_id, e)

        # RESOLVES 归档（缺口闭环，§4.3/§4.4）：按注入编号确定性映射 review_id；
        # 与新 reviews 落库无序——RESOLVES 引用的是摄入前快照里的旧缺口
        gaps_resolved = 0
        gap_number_map = {str(g["index"]): g["review_id"] for g in open_gaps}
        for num in parse_resolves_blocks(generation_output):
            rid = gap_number_map.get(num)
            if not rid:
                continue
            try:
                if self.db.resolve_wiki_review(project_id, rid, "auto_resolved"):
                    gaps_resolved += 1
            except Exception as e:
                logger.error("Ingest [%s]: failed to resolve gap #%s: %s", task_id, num, e)
        if gaps_resolved:
            logger.info("Ingest [%s]: %d open gap(s) auto-resolved via RESOLVES", task_id, gaps_resolved)

        # Record in wiki_source_pages (ingestion tracking)
        self.db.create_or_update_source_page(
            project_id=project_id,
            file_id=file_id,
            source_page_key=source_page_key,
            content_hash=content_hash,
            status="ingested",
            pages_touched=all_touched_keys,
        )

        # Add change log entry (log.md equivalent) — exclude user_edited from "affected" keys
        actual_touched_keys = [k for k in all_touched_keys if k not in user_edited_keys]
        summary_text = f"摄入 {filename}: 创建 {pages_created} 页, 更新 {pages_updated} 页"
        if reviews_count:
            summary_text += f", {reviews_count} 个发现项"
        self.db.add_wiki_change_log(
            project_id=project_id,
            action="ingest",
            trigger_file_id=file_id,
            source_title=filename,
            affected_page_keys=actual_touched_keys,
            summary=summary_text,
        )

        # Update overview if LLM provided one
        if overview_update:
            self.db.update_wiki_overview(project_id, overview_update[:_MAX_OVERVIEW_CHARS])
        else:
            # Auto-generate a brief overview from current pages
            self._auto_update_overview(project_id)

        return {
            "skipped": False,
            "pages_created": pages_created,
            "pages_updated": pages_updated,
            "pages_failed": pages_failed,
            "reviews_count": reviews_count,
            "gaps_resolved": gaps_resolved,
            "pages_touched": all_touched_keys,
            "source_page_key": source_page_key,
            "filename": filename,
            "file_id": file_id,
        }

    # ── Private helpers ──

    def _read_full_text(self, file_id: str, project_id: str) -> str:
        """Reconstruct full text from FTS chunks for a given file."""
        try:
            cursor = self.db.conn.execute(
                """SELECT content FROM chunks_fts
                   WHERE project_id = ? AND chunk_id LIKE ?
                   ORDER BY rowid ASC""",
                (project_id, f"chunk_{file_id}_%"),
            )
            chunks = [row["content"] for row in cursor.fetchall() if row["content"]]
            if chunks:
                text = "\n\n".join(chunks)
                if len(text) > _MAX_MATERIALS_CHARS:
                    text = text[:_MAX_MATERIALS_CHARS]
                    logger.warning("_read_full_text: truncated to %d chars for file=%s", len(text), file_id)
                return text
        except Exception as e:
            logger.warning("_read_full_text failed for file=%s: %s", file_id, e)
        return ""

    def _build_index_summary(self, pages: list[dict]) -> str:
        """Build a one-line-per-page index summary for LLM context."""
        if not pages:
            return "（Wiki 尚无页面）"
        lines = []
        for p in pages:
            title = p.get("title", "")
            page_type = p.get("page_type", "structure")
            content = (p.get("content") or "")[:120].replace("\n", " ")
            lines.append(f"- [{page_type}] {p['page_key']}: {title} — {content}...")
        return "\n".join(lines)

    def _build_schema_hints(self, schemas: list[dict]) -> str:
        """Build schema compile hints for LLM."""
        if not schemas:
            return ""
        lines = []
        for s in schemas:
            hint = s.get("compile_hint", "")
            lines.append(f"- {s['page_key']}: {s.get('title', '')} — {hint}")
        return "\n".join(lines)

    def _build_existing_content(self, pages: list[dict]) -> str:
        """Build existing page content for incremental merge.

        整页粒度预算：单页截断到 _MAX_EXISTING_PAGE_CHARS；总量按「放得下整页才
        放」累计——避免在总量上限处把最后一页切成半页注入 Stage 2。
        """
        if not pages:
            return ""
        parts: list[str] = []
        total = 0
        for p in pages:
            content = (p.get("content") or "")[:_MAX_EXISTING_PAGE_CHARS]
            part = f"### {p['page_key']} ({p.get('title', '')})\n{content}"
            if total + len(part) > _MAX_EXISTING_TOTAL_CHARS:
                logger.info(
                    "_build_existing_content: total cap %d reached before page '%s', "
                    "skipping remaining %d pages",
                    _MAX_EXISTING_TOTAL_CHARS, p["page_key"], len(pages) - len(parts),
                )
                break
            parts.append(part)
            total += len(part)
        return "\n\n---\n\n".join(parts)

    async def _collect_base_lib_context(self, project_id: str, schemas: list[dict]) -> str:
        """Collect base library RAG context based on schema search hints."""
        try:
            from knowledge.knowledge_search import KnowledgeSearchTool
            from pathlib import Path

            linked_libs = self.db.get_project_base_libs(project_id)
            if not linked_libs:
                return ""

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
                        results = await search_tool.search(query=hint, project_id=lib_id, top_k=3)
                        for r in results:
                            content = r.get("content", "")
                            if content:
                                all_parts.append(f"[{lib.get('name', '')}] {content}")

            return "\n\n".join(all_parts[:30])
        except Exception as e:
            logger.error("_collect_base_lib_context failed for project %s: %s", project_id, e)
            return ""

    def _auto_update_overview(self, project_id: str):
        """Auto-generate a brief overview from current page titles."""
        pages = self.db.list_wiki_pages(project_id)
        if not pages:
            return
        by_type = defaultdict(list)
        for p in pages:
            by_type[p.get("page_type", "structure")].append(p.get("title", p["page_key"]))

        parts = []
        type_labels = {
            "source": "来源摘要", "entity": "实体", "concept": "概念",
            "topic": "主题", "structure": "项目结构",
        }
        for ptype, titles in by_type.items():
            label = type_labels.get(ptype, ptype)
            parts.append(f"**{label}**（{len(titles)} 页）: {', '.join(titles[:10])}")

        overview = f"本 Wiki 共 {len(pages)} 个页面。\n\n" + "\n\n".join(parts)
        self.db.update_wiki_overview(project_id, overview[:_MAX_OVERVIEW_CHARS])

    # ── LLM retry helper ──

    _LLM_MAX_RETRIES = 2  # Pipeline-level retries (on top of client's internal retry)

    async def _chat_with_retry(self, messages, settings, task_id: str, stage: str,
                               project_id: str | None = None) -> str:
        """Call LLM with pipeline-level retry for transient errors.

        Retries on LLMNetworkError (timeout/connection) and LLMCallFailed (5xx).
        Does NOT retry on LLMInvalidKey (auth) — those need user action.
        Uses task-specific model if configured via llm_routing.

        Safety: wraps each attempt in asyncio.wait_for to prevent infinite hangs
        (e.g. when the LLM API accepts the connection but never returns data).
        """
        # Apply task routing: use ingest-specific model if configured
        effective_settings = get_task_settings(settings, "ingest")
        llm = self.llm
        if effective_settings.get("model") != settings.get("model"):
            from llm import LLMClient
            llm = LLMClient(effective_settings)

        # Per-attempt hard timeout: httpx read_timeout + 60s buffer
        per_attempt_timeout = int(effective_settings.get("llm_read_timeout", 300)) + 60

        last_err = None
        model_name = effective_settings.get("model", "?")
        for attempt in range(self._LLM_MAX_RETRIES + 1):
            try:
                logger.info(
                    "Ingest [%s] %s LLM attempt %d/%d (model=%s, timeout=%ds)",
                    task_id, stage, attempt + 1, self._LLM_MAX_RETRIES + 1,
                    model_name, per_attempt_timeout,
                )
                # 2.10 §4.3: 用量环境（source=wiki / purpose=ingest，归属项目与
                # 编译任务）；重试尝试同在环境内，按次落库
                with push_context(UsageContext(
                        source="wiki", purpose="ingest",
                        project_id=project_id, entity_id=task_id)):
                    return (await asyncio.wait_for(
                        llm.chat_full(messages),
                        timeout=per_attempt_timeout,
                    ))["content"]
            except asyncio.TimeoutError:
                last_err = LLMNetworkError(
                    detail=f"LLM 调用超时（{per_attempt_timeout}s），模型可能挂起"
                )
                logger.error(
                    "Ingest [%s] %s LLM hard-timeout after %ds (attempt %d/%d, model=%s)",
                    task_id, stage, per_attempt_timeout, attempt + 1,
                    self._LLM_MAX_RETRIES + 1, model_name,
                )
            except LLMInvalidKey:
                raise  # Auth errors are not retryable
            except (LLMNetworkError, LLMCallFailed) as e:
                last_err = e
                logger.warning(
                    "Ingest [%s] %s LLM error (attempt %d/%d, model=%s): %s",
                    task_id, stage, attempt + 1, self._LLM_MAX_RETRIES + 1,
                    model_name, e,
                )

            # Retry with backoff if attempts remain
            if attempt < self._LLM_MAX_RETRIES:
                backoff = 15 * (2 ** attempt)  # 15s, 30s — enough for LLM service recovery
                logger.warning(
                    "Ingest [%s] %s LLM retry %d/%d (backoff %ds)",
                    task_id, stage, attempt + 1, self._LLM_MAX_RETRIES, backoff,
                )
                await asyncio.sleep(backoff)

        logger.error(
            "Ingest [%s] %s LLM failed after %d retries: %s",
            task_id, stage, self._LLM_MAX_RETRIES, last_err,
        )
        raise last_err

