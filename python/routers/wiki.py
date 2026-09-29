"""python/routers/wiki.py — Wiki system API routes.

Implements all Wiki REST APIs per §4/§5/§7 of 2.6wiki-design.md.
"""
import hashlib
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/wiki", tags=["wiki"])


def get_db():
    """Get database instance."""
    try:
        import deps
        return deps.db
    except ImportError:
        raise HTTPException(status_code=500, detail="Database not initialized")


def get_data_dir() -> str:
    import os
    import sys
    if os.environ.get("INKCUBE_DATA_DIR"):
        return os.environ.get("INKCUBE_DATA_DIR")
    if getattr(sys, "frozen", False):
        from platformdirs import user_data_dir
        return os.path.join(user_data_dir("InkCube", "InkCube"), "data")
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


# ── Request/Response Models ──


class CompileRequest(BaseModel):
    mode: str = "incremental"  # "full" or "incremental"
    trigger_type: str = "manual"
    file_ids: list[str] = []  # empty = all pending files (legacy batch compile)
    supervised: bool = False  # Step 1.4: pause after each file for user review
    force: bool = False  # force re-ingest all files (clear source_page tracking)


class PageEditRequest(BaseModel):
    content: str
    title: Optional[str] = None


class PurposeRequest(BaseModel):
    purpose: str


class OverviewRequest(BaseModel):
    overview: str


class ReviewResolveRequest(BaseModel):
    action: str  # "dismiss" / "create_page" / "auto_resolved"


class BatchReviewRequest(BaseModel):
    review_ids: list[str]
    action: str  # "dismiss" / "auto_resolved"


class LintRequest(BaseModel):
    mode: str = "structural"  # "structural" / "semantic" / "full"


class ArchiveRequest(BaseModel):
    title: str
    content: str
    page_key: Optional[str] = None
    source_question: str = ""


class SchemaCreateRequest(BaseModel):
    page_key: str
    title: str
    compile_hint: str = ""
    base_lib_search_hints: Optional[list[str]] = None
    position: Optional[int] = None


class SchemaUpdateRequest(BaseModel):
    title: Optional[str] = None
    compile_hint: Optional[str] = None
    base_lib_search_hints: Optional[list[str]] = None
    position: Optional[int] = None


class DedupMergeRequest(BaseModel):
    canonical_key: str
    duplicate_keys: list[str]


class EnrichWikilinksRequest(BaseModel):
    page_key: str


# ── Helper ──


def _compute_review_id(review_type: str, title: str) -> str:
    """Compute a stable review ID based on type+title (FNV-1a hash, per §3.5)."""
    normalized = f"{review_type}::{title.strip().lower()}"
    # FNV-1a 32-bit
    h = 0x811C9DC5
    for byte in normalized.encode("utf-8"):
        h ^= byte
        h = (h * 0x01000193) & 0xFFFFFFFF
    return f"review_{h:08x}"


# ── Wiki Pages ──


@router.get("/projects/{project_id}/pages")
async def list_wiki_pages(project_id: str):
    """List all Wiki pages for a project, ordered by schema position."""
    try:
        db = get_db()
        project = db.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        pages = db.list_wiki_pages(project_id)
        schemas = db.get_wiki_schemas(project.get("type", "general"))

        # Build schema map for missing pages
        existing_keys = {p["page_key"] for p in pages}
        missing_schemas = [s for s in schemas if s["page_key"] not in existing_keys]

        return {
            "pages": pages,
            "missing_pages": [
                {"page_key": s["page_key"], "title": s["title"], "position": s["position"]}
                for s in missing_schemas
            ],
            "pending_compile_count": db.get_pending_compile_count(project_id),
            "wiki_purpose": db.get_wiki_purpose(project_id),
            "wiki_overview": db.get_wiki_overview(project_id),
            "source_pages": db.list_source_pages(project_id),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to list wiki pages: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects/{project_id}/pages/{page_key}")
async def get_wiki_page(project_id: str, page_key: str):
    """Get a single Wiki page with sources and recent change log."""
    try:
        db = get_db()
        page = db.get_wiki_page(project_id, page_key)
        if not page:
            raise HTTPException(status_code=404, detail="Wiki page not found")

        sources = db.list_wiki_page_sources(page["id"])
        change_log = db.list_wiki_change_log(project_id, wiki_page_id=page["id"], limit=20)

        return {
            "page": page,
            "sources": sources,
            "change_log": change_log,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get wiki page: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/projects/{project_id}/pages/{page_key}")
async def edit_wiki_page(project_id: str, page_key: str, req: PageEditRequest):
    """User manually edits a Wiki page. Sets status to user_edited."""
    try:
        db = get_db()
        page = db.get_wiki_page(project_id, page_key)
        if not page:
            raise HTTPException(status_code=404, detail="Wiki page not found")

        db.update_wiki_page_content(
            page_id=page["id"],
            content=req.content,
            title=req.title,
            status="user_edited",
        )
        db.add_wiki_change_log(
            project_id=project_id,
            action="user_edited",
            wiki_page_id=page["id"],
            summary="用户手动编辑",
        )
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to edit wiki page: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/projects/{project_id}/pages/{page_key}")
async def delete_wiki_page(project_id: str, page_key: str):
    """Delete a Wiki page with full cascade cleanup."""
    try:
        db = get_db()
        page = db.get_wiki_page(project_id, page_key)
        if not page:
            raise HTTPException(status_code=404, detail="Wiki page not found")

        # Full cascade delete (sources, change_log, reviews, refs)
        db.delete_wiki_page(project_id, page_key)

        # Log deletion AFTER cascade (page-level logs were cleaned by cascade)
        db.add_wiki_change_log(
            project_id=project_id,
            action="deleted",
            summary=f"删除页面: {page_key}",
        )

        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to delete wiki page: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Compile ──


@router.post("/projects/{project_id}/compile")
async def trigger_compile(project_id: str, req: CompileRequest):
    """Trigger Wiki compilation for a project.

    If file_ids is provided, uses the new per-file ingest engine (V2).
    Otherwise, falls back to the legacy batch compile (V1).
    """
    try:
        db = get_db()
        project = db.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        # Check if there's already a running compile task
        latest = db.get_latest_compile_task(project_id)
        if latest and latest["status"] in ("queued", "ingesting", "storing", "waiting_review"):
            raise HTTPException(
                status_code=409,
                detail=f"已有编译任务进行中: {latest['status']}",
            )

        # Force re-ingest: clear ALL wiki data so the project starts from scratch
        if req.force:
            cleared = {}
            # 1. wiki_pages (CASCADE deletes wiki_page_sources)
            cleared["pages"] = db.conn.execute(
                "DELETE FROM wiki_pages WHERE project_id = ?", (project_id,)
            ).rowcount
            # 2. wiki_page_versions
            cleared["versions"] = db.conn.execute(
                "DELETE FROM wiki_page_versions WHERE project_id = ?", (project_id,)
            ).rowcount
            # 3. wiki_change_log
            cleared["change_log"] = db.conn.execute(
                "DELETE FROM wiki_change_log WHERE project_id = ?", (project_id,)
            ).rowcount
            # 4. wiki_reviews
            cleared["reviews"] = db.conn.execute(
                "DELETE FROM wiki_reviews WHERE project_id = ?", (project_id,)
            ).rowcount
            # 5. wiki_source_pages
            cleared["source_pages"] = db.conn.execute(
                "DELETE FROM wiki_source_pages WHERE project_id = ?", (project_id,)
            ).rowcount
            # 6. Reset auto-generated overview (keep user-set purpose)
            db.conn.execute(
                "UPDATE projects SET wiki_overview = '' WHERE id = ?", (project_id,)
            )
            db.conn.commit()
            logger.info(
                "Force compile: cleared wiki data for project %s — %s",
                project_id, cleared,
            )

        # Create compile task
        task = db.create_wiki_compile_task(
            project_id=project_id,
            trigger_type=req.trigger_type,
        )

        # Reset pending count
        db.reset_pending_compile_count(project_id)

        # Set supervised flag if supervised mode requested
        if req.supervised:
            db.set_compile_supervised(task["id"], True)

        # Enqueue to compile worker
        try:
            import deps
            if hasattr(deps, "worker") and deps.worker:
                await deps.worker.enqueue_compile(task["id"])
            else:
                logger.warning("Compile worker not available, task will be picked up by scanner")
        except Exception as e:
            logger.warning("Failed to enqueue compile task: %s", e)

        return {"task": task}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to trigger compile: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{project_id}/compile/stop")
async def stop_compile(project_id: str):
    """Request the running compile task to stop after the current file."""
    try:
        db = get_db()
        task = db.get_latest_compile_task(project_id)
        if not task:
            raise HTTPException(status_code=404, detail="No compile task found")
        if task["status"] not in ("queued", "ingesting", "storing", "waiting_review"):
            raise HTTPException(status_code=409, detail=f"Compile task not active (status={task['status']})")
        # Use the worker's signal to also wake the event if waiting
        try:
            import deps
            if hasattr(deps, "worker") and deps.worker:
                deps.worker.signal_compile_stop(task["id"])
            else:
                db.request_compile_stop(task["id"])
        except Exception:
            db.request_compile_stop(task["id"])
        return {"success": True, "task_id": task["id"]}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to stop compile: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{project_id}/compile/continue")
async def continue_compile(project_id: str):
    """Continue a compile task that is paused in supervised mode (waiting_review)."""
    try:
        db = get_db()
        task = db.get_latest_compile_task(project_id)
        if not task:
            raise HTTPException(status_code=404, detail="No compile task found")
        if task["status"] != "waiting_review":
            raise HTTPException(status_code=409, detail=f"Compile task not waiting for review (status={task['status']})")
        # Use the worker's signal to wake the event and continue
        try:
            import deps
            if hasattr(deps, "worker") and deps.worker:
                deps.worker.signal_compile_continue(task["id"])
            else:
                db.update_compile_task_status(task["id"], "queued")
        except Exception:
            db.update_compile_task_status(task["id"], "queued")
        return {"task": db.get_wiki_compile_task(task["id"])}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to continue compile: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects/{project_id}/compile-status")
async def get_compile_status(project_id: str):
    """Get the latest compile task status for a project, including per-file summaries."""
    try:
        db = get_db()
        task = db.get_latest_compile_task(project_id)
        result = {"task": task}
        if task:
            summaries = db.get_ingest_summaries(task["id"])
            result["ingest_summaries"] = summaries
            # Detect crash recovery: task is 'queued' but has prior ingest progress
            result["is_resumed"] = (
                task["status"] == "queued"
                and len(summaries) > 0
            )
        return result
    except Exception as e:
        logger.error("Failed to get compile status: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/review-status")
async def get_global_review_status():
    """Global endpoint: returns all projects with compile tasks waiting for review,
    plus projects with active compile tasks (ingesting/queued/storing).
    Used by the frontend to show notifications and activity indicators.
    """
    try:
        db = get_db()
        tasks = db.get_waiting_review_compile_tasks()
        result = []
        for task in tasks:
            project = db.get_project(task["project_id"])
            result.append({
                "project_id": task["project_id"],
                "project_name": project["name"] if project else "Unknown",
                "task_id": task["id"],
                "status": task["status"],
            })
        # Also check for active compile tasks (ingesting/queued/storing)
        active_cursor = db.conn.execute(
            "SELECT id, project_id, status FROM wiki_compile_tasks "
            "WHERE status IN ('ingesting', 'queued', 'storing') "
            "ORDER BY created_at DESC"
        )
        active_compiles = [dict(r) for r in active_cursor.fetchall()]
        return {"waiting_reviews": result, "active_compiles": active_compiles}
    except Exception as e:
        logger.error("Failed to get global review status: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{project_id}/pages/{page_key}/retry")
async def retry_page(project_id: str, page_key: str):
    """Retry compiling a single failed page."""
    try:
        db = get_db()
        page = db.get_wiki_page(project_id, page_key)
        if not page:
            raise HTTPException(status_code=404, detail="Wiki page not found")

        # Create a new compile task for single page
        task = db.create_wiki_compile_task(
            project_id=project_id,
            trigger_type="manual",
        )

        # Enqueue
        try:
            import deps
            if hasattr(deps, "worker") and deps.worker:
                await deps.worker.enqueue_compile(task["id"])
        except Exception as e:
            logger.warning("Failed to enqueue retry compile: %s", e)

        return {"task": task}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to retry page: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Reviews ──


@router.get("/projects/{project_id}/reviews")
async def list_reviews(project_id: str, resolved: Optional[bool] = None):
    """List Wiki reviews (compilation findings) for a project."""
    try:
        db = get_db()
        reviews = db.list_wiki_reviews(project_id, resolved=resolved)
        return {"reviews": reviews}
    except Exception as e:
        logger.error("Failed to list reviews: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/projects/{project_id}/reviews/{review_id}")
async def resolve_review(project_id: str, review_id: str, req: ReviewResolveRequest):
    """Resolve a review item."""
    try:
        db = get_db()
        review = db.get_wiki_review(review_id)
        if not review:
            raise HTTPException(status_code=404, detail="Review not found")
        if review["project_id"] != project_id:
            raise HTTPException(status_code=403, detail="Review belongs to different project")

        db.resolve_wiki_review(review_id, req.action)
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to resolve review: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{project_id}/reviews/batch")
async def batch_resolve_reviews(project_id: str, req: BatchReviewRequest):
    """Batch resolve multiple reviews."""
    try:
        db = get_db()
        count = db.batch_resolve_reviews(project_id, req.review_ids, req.action)
        return {"success": True, "resolved_count": count}
    except Exception as e:
        logger.error("Failed to batch resolve reviews: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Lint ──


@router.post("/projects/{project_id}/lint")
async def trigger_lint(project_id: str, req: LintRequest = None):
    """Run lint checks on Wiki pages.

    mode="structural": deterministic checks only (fast, free).
    mode="semantic": LLM-driven checks (contradiction/stale-claim/data-gap).
    mode="full": both structural and semantic.
    """
    try:
        db = get_db()
        project = db.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        mode = (req.mode if req else "structural") or "structural"
        total_findings = 0

        def _store_findings(findings):
            for finding in findings:
                review_id = _compute_review_id(finding["type"], finding["title"])
                # Build description with optional metadata
                desc = finding.get("description", "")
                # Append severity for semantic lint findings
                severity = finding.get("severity")
                if severity:
                    desc = f"[{severity}] {desc}"
                # Append pages list for semantic lint
                lint_pages = finding.get("pages")
                if lint_pages:
                    desc += f"\nPAGES: {', '.join(lint_pages)}"
                # Append suggestion info from structural lint
                if finding.get("suggested_source"):
                    desc += f"\n建议关联页面: [[{finding['suggested_source']}]]"
                if finding.get("suggested_target"):
                    desc += f"\n建议链接到: [[{finding['suggested_target']}]]"
                db.create_or_update_wiki_review(
                    review_id=review_id,
                    project_id=project_id,
                    review_type=finding["type"],
                    title=finding["title"],
                    description=desc,
                    affected_page_ids=finding.get("affected_page_ids"),
                )

        # Structural lint (fast, no LLM)
        if mode in ("structural", "full"):
            from wiki.lint import run_structural_lint
            structural_findings = run_structural_lint(db, project_id)
            _store_findings(structural_findings)
            total_findings += len(structural_findings)

        # Semantic lint (LLM-driven)
        if mode in ("semantic", "full"):
            from wiki.semantic_lint import run_semantic_lint
            from wiki.detect_language import detect_wiki_language, get_language_hint
            from llm import LLMClient
            settings = db.get_settings()
            llm_client = LLMClient(settings)
            # Auto-detect Wiki language for output matching
            lang_code = detect_wiki_language(db, project_id)
            language_hint = get_language_hint(lang_code)
            semantic_findings = await run_semantic_lint(
                db, project_id, llm_client, settings, language_hint=language_hint,
            )
            _store_findings(semantic_findings)
            total_findings += len(semantic_findings)

        return {"success": True, "findings_count": total_findings, "mode": mode}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to run lint: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Review Sweep (§12.2.8) ──


@router.post("/projects/{project_id}/sweep-reviews")
async def sweep_project_reviews(project_id: str):
    """Run two-stage review sweep: rule matching + optional LLM judgment."""
    try:
        db = get_db()
        project = db.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        from wiki.sweep_reviews import sweep_reviews

        # Try to use LLM for Stage 2 if available
        llm_client = None
        llm_settings = None
        try:
            from llm import LLMClient
            settings = db.get_settings()
            llm_client = LLMClient(settings)
            llm_settings = settings
        except Exception as e:
            logger.info("Sweep reviews: LLM not available for Stage 2: %s", e)

        result = await sweep_reviews(db, project_id, llm_client, llm_settings)
        return {"success": True, **result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to sweep reviews: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Wiki Graph (§12.2.2) ──


@router.get("/projects/{project_id}/graph")
async def get_wiki_graph(project_id: str, insights: bool = False):
    """Get Wiki knowledge graph data (nodes, edges, communities)."""
    try:
        db = get_db()
        project = db.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        from wiki.wiki_graph import get_graph_data
        result = get_graph_data(db, project_id, include_insights=insights)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get wiki graph: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Wiki Search (§12.2.7) ──


@router.get("/projects/{project_id}/search")
async def search_wiki_pages(
    project_id: str,
    q: str = "",
    top_k: int = 20,
    enable_graph: bool = True,
):
    """Search Wiki pages using hybrid retrieval (keyword + graph expansion)."""
    try:
        db = get_db()
        project = db.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        if not q.strip():
            return {"results": []}

        from wiki.wiki_search import search_wiki
        results = search_wiki(
            query=q.strip(),
            project_id=project_id,
            db=db,
            top_k=top_k,
            enable_graph=enable_graph,
        )
        return {"results": results, "count": len(results)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to search wiki: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Wiki Schemas (CRUD) ──


@router.get("/projects/{project_id}/schemas")
async def get_project_schemas(project_id: str):
    """Get Wiki schemas for a project type."""
    try:
        db = get_db()
        project = db.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        project_type = project.get("type", "general")
        schemas = db.get_wiki_schemas(project_type)
        return {"schemas": schemas, "project_type": project_type}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get schemas: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{project_id}/schemas")
async def create_schema(project_id: str, req: SchemaCreateRequest):
    """Create a new schema entry for the project's type."""
    try:
        db = get_db()
        project = db.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        project_type = project.get("type", "general")
        result = db.create_wiki_schema(
            project_type=project_type,
            page_key=req.page_key,
            title=req.title,
            compile_hint=req.compile_hint,
            base_lib_search_hints=req.base_lib_search_hints,
            position=req.position,
        )
        return {"success": True, "schema": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to create schema: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/projects/{project_id}/schemas/{schema_id}")
async def update_schema(project_id: str, schema_id: str, req: SchemaUpdateRequest):
    """Update an existing schema entry."""
    try:
        db = get_db()
        result = db.update_wiki_schema(
            schema_id=schema_id,
            title=req.title,
            compile_hint=req.compile_hint,
            base_lib_search_hints=req.base_lib_search_hints,
            position=req.position,
        )
        if not result:
            raise HTTPException(status_code=404, detail="Schema not found")
        return {"success": True, "schema": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to update schema: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/projects/{project_id}/schemas/{schema_id}")
async def delete_schema(project_id: str, schema_id: str):
    """Delete a schema entry."""
    try:
        db = get_db()
        result = db.delete_wiki_schema(schema_id)
        return {"success": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to delete schema: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Archive (Chat → Wiki) ──


@router.post("/projects/{project_id}/archive")
async def archive_chat_answer(project_id: str, req: ArchiveRequest):
    """Archive a Chat answer as a new Wiki page.

    Per §8.4 of 2.6wiki-design.md — query result archiving.
    """
    try:
        db = get_db()
        project = db.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        if not req.content.strip():
            raise HTTPException(status_code=400, detail="Content cannot be empty")

        # Generate page_key if not provided (random suffix avoids same-second collision)
        import time
        import secrets
        page_key = req.page_key or f"chat_archive_{int(time.time())}_{secrets.token_hex(3)}"

        # Check if page_key already exists
        existing = db.get_wiki_page(project_id, page_key)
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"Page key '{page_key}' already exists",
            )

        # Create wiki page
        result = db.create_or_update_wiki_page(
            project_id=project_id,
            page_key=page_key,
            title=req.title,
            content=req.content,
            status="user_edited",
        )

        # Record in change log
        summary = f"从 Chat 归档"
        if req.source_question:
            summary += f": {req.source_question[:100]}"
        db.add_wiki_change_log(
            project_id=project_id,
            action="archived",
            wiki_page_id=result["id"],
            summary=summary,
        )

        return {"success": True, "page": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to archive chat answer: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects/{project_id}/pages/{page_key}/backlinks")
async def get_page_backlinks(project_id: str, page_key: str):
    """Get pages that link to the given page (reverse refs)."""
    try:
        db = get_db()
        backlinks = db.get_wiki_backlinks(project_id, page_key)
        return {"backlinks": backlinks}
    except Exception as e:
        logger.error("Failed to get backlinks: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects/{project_id}/changelog")
async def get_project_changelog(project_id: str, limit: int = 50):
    """Get project-wide change log (for log.md timeline view)."""
    try:
        db = get_db()
        project = db.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        logs = db.list_wiki_change_log(project_id, limit=limit)
        return {"changelog": logs}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get changelog: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Wiki Purpose / Overview (§3.6) ──


@router.get("/projects/{project_id}/purpose")
async def get_wiki_purpose(project_id: str):
    """Get the Wiki purpose (purpose.md equivalent) for a project."""
    try:
        db = get_db()
        project = db.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        return {"purpose": db.get_wiki_purpose(project_id)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get wiki purpose: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/projects/{project_id}/purpose")
async def update_wiki_purpose(project_id: str, req: PurposeRequest):
    """Update the Wiki purpose (purpose.md equivalent) for a project."""
    try:
        db = get_db()
        project = db.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        db.update_wiki_purpose(project_id, req.purpose)
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to update wiki purpose: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects/{project_id}/overview")
async def get_wiki_overview(project_id: str):
    """Get the Wiki overview (overview.md equivalent) for a project."""
    try:
        db = get_db()
        project = db.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        return {"overview": db.get_wiki_overview(project_id)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get wiki overview: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/projects/{project_id}/overview")
async def update_wiki_overview(project_id: str, req: OverviewRequest):
    """Update the Wiki overview (overview.md equivalent) for a project."""
    try:
        db = get_db()
        project = db.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        db.update_wiki_overview(project_id, req.overview)
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to update wiki overview: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Source Pages (ingestion tracking, §3.7) ──


@router.get("/projects/{project_id}/source-pages")
async def list_source_pages(project_id: str):
    """List all source pages (ingestion tracking) for a project."""
    try:
        db = get_db()
        project = db.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        return {"source_pages": db.list_source_pages(project_id)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to list source pages: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects/{project_id}/source-pages/{file_id}")
async def get_source_page(project_id: str, file_id: str):
    """Get a single source page by file_id."""
    try:
        db = get_db()
        sp = db.get_source_page(project_id, file_id)
        if not sp:
            raise HTTPException(status_code=404, detail="Source page not found")
        return {"source_page": sp}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get source page: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Wiki Page Versions ──


@router.get("/projects/{project_id}/pages/{page_key}/versions")
async def list_page_versions(project_id: str, page_key: str, limit: int = 20):
    """List version history for a wiki page."""
    try:
        db = get_db()
        page = db.get_wiki_page(project_id, page_key)
        if not page:
            raise HTTPException(status_code=404, detail="Wiki page not found")
        versions = db.list_wiki_page_versions(page["id"], limit=limit)
        return {"versions": versions, "current_version": page.get("compile_version", 0)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to list page versions: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects/{project_id}/versions/{version_id}")
async def get_page_version(project_id: str, version_id: str):
    """Get a specific version with full content."""
    try:
        db = get_db()
        version = db.get_wiki_page_version(version_id)
        if not version:
            raise HTTPException(status_code=404, detail="Version not found")
        return {"version": version}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get page version: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Duplicate Detection (§12.2.10) ──


@router.get("/projects/{project_id}/dedup/detect")
async def detect_duplicate_pages(project_id: str):
    """Detect duplicate pages using LLM.

    Returns groups of pages that appear to cover the same topic.
    """
    try:
        db = get_db()
        project = db.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        from wiki.dedup import extract_entity_summaries, detect_duplicates
        from wiki.llm_routing import get_task_settings
        from llm import LLMClient

        settings = db.get_settings()
        summaries = extract_entity_summaries(db, project_id)
        if len(summaries) < 2:
            return {"groups": [], "summaries_count": len(summaries)}

        llm = LLMClient(settings)
        effective_settings = get_task_settings(settings, "dedup")
        if effective_settings.get("model") != settings.get("model"):
            llm = LLMClient(effective_settings)

        groups = await detect_duplicates(summaries, llm, effective_settings)
        return {"groups": groups, "summaries_count": len(summaries)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to detect duplicates: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{project_id}/dedup/merge")
async def merge_duplicate_pages(project_id: str, req: DedupMergeRequest):
    """Merge duplicate pages into a canonical page.

    User must specify which page to keep (canonical) and which to merge.
    """
    try:
        db = get_db()
        project = db.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        from wiki.dedup import merge_pages
        from wiki.llm_routing import get_task_settings
        from llm import LLMClient

        settings = db.get_settings()
        llm = LLMClient(settings)
        effective_settings = get_task_settings(settings, "dedup")
        if effective_settings.get("model") != settings.get("model"):
            llm = LLMClient(effective_settings)

        result = await merge_pages(
            db, project_id,
            canonical_key=req.canonical_key,
            duplicate_keys=req.duplicate_keys,
            llm_client=llm,
            llm_settings=effective_settings,
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to merge duplicates: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Wiki-link Enrichment (§12.2.4) ──


@router.post("/projects/{project_id}/enrich-wikilinks")
async def enrich_page_wikilinks(project_id: str, req: EnrichWikilinksRequest):
    """Auto-complete wiki-links for a specific page using LLM.

    LLM suggests links; code performs the actual text replacement.
    """
    try:
        db = get_db()
        project = db.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        page = db.get_wiki_page(project_id, req.page_key)
        if not page:
            raise HTTPException(status_code=404, detail="Wiki page not found")

        from wiki.wiki_enrich import enrich_wikilinks
        from llm import LLMClient

        settings = db.get_settings()
        llm_client = LLMClient(settings)
        all_pages = db.list_wiki_pages(project_id)
        all_pages_brief = [{"page_key": p["page_key"], "title": p.get("title", "")} for p in all_pages]

        enriched_content, link_count = await enrich_wikilinks(
            content=page.get("content", ""),
            page_key=req.page_key,
            all_pages=all_pages_brief,
            llm_client=llm_client,
            llm_settings=settings,
        )

        # Save enriched content if links were added
        if link_count > 0:
            db.update_wiki_page_content(
                page_id=page["id"],
                content=enriched_content,
                status=page.get("status", "compiled"),
            )
            db.add_wiki_change_log(
                project_id=project_id,
                action="enriched",
                wiki_page_id=page["id"],
                summary=f"自动补全 {link_count} 个 Wiki 链接",
            )

        return {"success": True, "links_added": link_count}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to enrich wikilinks: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
