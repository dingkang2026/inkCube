"""python/routers/wiki.py — Wiki system API routes.

Implements all Wiki REST APIs per §4/§5/§7 of 2.6wiki-design.md.
"""
import asyncio
import logging
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from paths import get_data_dir
from readiness import ensure_ready
from llm.usage import UsageContext, push_context  # 2.10 §4.3 用量环境
from wiki.review_id import compute_review_id  # review_id 唯一实现(H-1:必须项目作用域)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/wiki", tags=["wiki"])


def get_db():
    """Get database instance."""
    try:
        import deps
        return deps.db
    except ImportError:
        raise HTTPException(status_code=500, detail="Database not initialized")


# ── Request/Response Models ──


class CompileRequest(BaseModel):
    mode: str = "incremental"  # "full" or "incremental"
    trigger_type: str = "manual"
    force: bool = False  # force re-ingest all files (clear source_page tracking)


class PageEditRequest(BaseModel):
    content: str
    title: Optional[str] = None


class PageWikilinkFixRequest(BaseModel):
    dead_key: str    # 死链目标 key（不带 [[]]）
    target_key: str  # 建议链接到的现存页面 key


class PurposeRequest(BaseModel):
    purpose: str


class OverviewRequest(BaseModel):
    overview: str


class ReviewResolveRequest(BaseModel):
    action: str  # "dismiss" / "create_page" / "auto_resolved"


class ReviewAdjudicateRequest(BaseModel):
    chosen: int  # evidence 索引（§5.5 矛盾裁决：以此侧为准，替换其余各侧）


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
    is_ledger: bool = False           # 3.1 §3 台账页(监控式,只追加,解析源)开关


class SchemaUpdateRequest(BaseModel):
    title: Optional[str] = None
    compile_hint: Optional[str] = None
    base_lib_search_hints: Optional[list[str]] = None
    position: Optional[int] = None
    is_ledger: Optional[bool] = None  # None=不改(区分「切换为 False」与「不传」)


class DedupMergeRequest(BaseModel):
    canonical_key: str
    duplicate_keys: list[str]
    group_id: Optional[str] = None  # 检测组 id：合并成功后标记组 merged（E3-2026-10）


class EnrichWikilinksRequest(BaseModel):
    page_key: str


# ── Intelligence: Ledger entries (3.1 §4.3, phase 4) ──
#
# /ledger/latest 声明在 /projects/{project_id}/... 之前(affairs.py /due 同款防遮蔽惯例)。


@router.get("/ledger/latest")
async def list_latest_ledger_entries():
    """跨项目每项目最新 1 条台账条目(首页卡片墙摘要行,JOIN 项目名防 N+1)。"""
    try:
        return {"latest": get_db().latest_ledger_entries()}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to list latest ledger entries: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects/{project_id}/ledger/entries")
async def list_ledger_entries(
    project_id: str,
    limit: int = 20,
    offset: int = Query(0, ge=0),
    # 普通默认值而非 Query(""):本端点会被测试直呼(绕过 FastAPI DI),Query 默认
    # 对象会原样流入 db 层致 sqlite 绑定失败;同端点 limit=20 同款先例
    page_key: str = "",
):
    """单项目台账条目时间轴(知识首页时间轴卡,3.1 §5.3);limit 1-100,offset 翻页。
    page_key 可选过滤(V2 E3 台账页时间轴);空 = 全项目,向后兼容。"""
    if not 1 <= limit <= 100:
        raise HTTPException(status_code=422, detail="limit must be 1-100")
    try:
        db = get_db()
        if not db.get_project(project_id):
            raise HTTPException(status_code=404, detail="Project not found")
        # limit+1 探测 has_more（list_tasks 同款，免 COUNT）
        entries = db.list_ledger_entries(
            project_id, limit + 1, offset, page_key=page_key or None)
        has_more = len(entries) > limit
        if has_more:
            entries = entries[:limit]
        return {"entries": entries, "has_more": has_more}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to list ledger entries: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Wiki Pages ──


@router.get("/projects/{project_id}/pages")
async def list_wiki_pages(
    project_id: str,
    limit: int = Query(50, ge=0, description="Page size; 0 = return all"),
    offset: int = Query(0, ge=0, description="Rows to skip"),
):
    """List all Wiki pages for a project, ordered by schema position."""
    try:
        db = get_db()
        project = db.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        # limit+1 探测 has_more（list_tasks 同款，免 COUNT）
        pages = db.list_wiki_pages(
            project_id, limit=limit + 1 if limit > 0 else 0, offset=offset)
        has_more = limit > 0 and len(pages) > limit
        if has_more:
            pages = pages[:limit]

        # missing_pages 必须基于全量 page_key 集计算（key-only 轻量查询），
        # 不能基于当前页——分页后当前页缺 key 不代表项目缺页
        schemas = db.get_wiki_schemas(project.get("type", "general"))
        existing_keys = set(db.list_wiki_page_keys(project_id))
        missing_schemas = [s for s in schemas if s["page_key"] not in existing_keys]

        return {
            "pages": pages,
            "has_more": has_more,
            "total": db.conn.execute(
                "SELECT COUNT(*) AS cnt FROM wiki_pages WHERE project_id = ?",
                (project_id,),
            ).fetchone()["cnt"],
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


@router.post("/projects/{project_id}/pages/{page_key}/fix-wikilink")
async def fix_page_wikilink(project_id: str, page_key: str, req: PageWikilinkFixRequest):
    """机械死链修复（§5.4 link-fix 写路径，E3-2026-10）。

    服务端正则替换 [[dead]]/[[dead|别名]] → [[target…]]：保留页面原状态
    （不置 user_edited——修链接不冻结汇编更新，enrich 同款）+ 版本快照
    （可回退）+ 时间线。替代旧的「前端读→改正则→editPage 整页写回」
    链路（后者会整页冻结且有丢失更新窗口）。编译进行中拒绝（与
    adjudicate 同一互斥口径）。一键修复与自动治理链共用同一 db 方法。
    """
    try:
        db = get_db()
        if not db.get_project(project_id):
            raise HTTPException(status_code=404, detail="Project not found")
        latest_task = db.get_latest_compile_task(project_id)
        if latest_task and latest_task["status"] in ("queued", "ingesting", "storing"):
            return JSONResponse(
                status_code=409,
                content={"error": f"编译进行中({latest_task['status']})，请等待完成后再修复", "code": "COMPILING_IN_PROGRESS"},
            )
        result = db.fix_wiki_page_wikilink(
            project_id=project_id,
            page_key=page_key,
            dead_key=req.dead_key.strip(),
            target_key=req.target_key.strip(),
        )
        if not result.get("success"):
            # 404 页面 / text_not_found 发现项已过时：不产生写操作，返回明细供前端处置
            return JSONResponse(status_code=200, content=result)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to fix wikilink: %s", e)
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
    """Trigger Wiki compilation for a project (V2 per-file ingest, continuous)."""
    # 准入前置校验（0.4 §7.3）：缺 LLM/嵌入模型时结构化报错
    ensure_ready("wiki_compile")
    try:
        db = get_db()
        project = db.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        # Check if there's already a running compile task
        latest = db.get_latest_compile_task(project_id)
        if latest and latest["status"] in ("queued", "ingesting", "storing"):
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
            # 6. wiki_dedup_groups（E3-2026-10 重复检测组落库）
            cleared["dedup_groups"] = db.conn.execute(
                "DELETE FROM wiki_dedup_groups WHERE project_id = ?", (project_id,)
            ).rowcount
            # 7. Reset auto-generated overview (keep user-set purpose)
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
        if task["status"] not in ("queued", "ingesting", "storing"):
            raise HTTPException(status_code=409, detail=f"Compile task not active (status={task['status']})")
        # Cooperative stop: worker checks the stop flag after the current file
        try:
            import deps
            if hasattr(deps, "worker") and deps.worker:
                deps.worker.signal_compile_stop(task["id"])
            else:
                db.request_compile_stop(task["id"])
        except Exception:
            db.request_compile_stop(task["id"])
        # 2.6 §4.1 路径 A：手动停止 → 该项目 30 分钟自动点火冷却（防「停止后
        # 下一文件解析完立刻重燃」使停止形同虚设；人工触发不受冷却限制）
        from wiki.auto_compile import mark_user_stop
        mark_user_stop(project_id)
        return {"success": True, "task_id": task["id"]}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to stop compile: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects/{project_id}/compile-status")
async def get_compile_status(project_id: str):
    """Get the latest compile task status for a project, including per-file summaries."""
    try:
        db = get_db()
        task = db.get_latest_compile_task(project_id)
        result = {"task": task}
        if task:
            # 2.10 §4.5：该次编译作业端到端消耗（entity_id=compile_task_id 聚合，
            # 仅 ingest 链路；lint/dedup/enrich 属独立操作不计入）
            task["usage"] = db.get_usage_by_entities([task["id"]]).get(task["id"])
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


# ── 知识册导出（2.6 §14，V2.1-T4）─────────────────────────────

# 文件名主干 sanitize（reports._sanitize_filename 同规则本地副本：非法字符
# 含控制字符集统一替换 + 去首尾空白与点；跨 router 不 import 私有函数）
_WIKI_FILENAME_INVALID_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

# 渲染超时放宽（CodeReview M-3：链路对齐——renderer 默认 60s，整册拼接后
# 页数多 pandoc 单进程可达分钟级；Electron 转发层 90s 与此对齐）
_WIKI_RENDER_TIMEOUT = 90


def _wiki_slug(name: str) -> str:
    slug = _WIKI_FILENAME_INVALID_RE.sub("_", str(name or "")).strip(" .")
    return slug[:40].strip(" .") or "知识册"


@router.post("/projects/{project_id}/export-docx")
async def export_wiki_docx(project_id: str):
    """知识册导出 docx（2.6 §14，V2.1-T4）：目录序拼接已编译页 → pandoc 渲染
    → 落 {data_dir}/files/{project_id}/exports/（随项目删除级联清理，生命周期
    闭环同报告产物挂 content_path）。零新表：文件即产物，重复导出新时间戳
    文件不覆盖旧导出。同步短任务（本地确定性渲染）to_thread 下放，防事件
    循环阻塞（reports export 同款；含 pandoc 子进程，册大可达数十秒）。"""
    db = get_db()
    project = db.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    pages = db.list_wiki_pages(project_id)   # 全量，目录序（schema position COALESCE）
    compiled = [p for p in pages if (p.get("content") or "").strip()]
    if not compiled:
        # D1 错误契约：带 code 供前端识别（handlePythonResponse 抛 err.errorCode）
        return JSONResponse(status_code=409, content={
            "error": "知识册暂无已编译页面，请先完成汇编",
            "code": "WIKI_EMPTY"})
    # 拼接：每页一级标题 + 正文（页间水平分隔线；章节序 = list 序 = 目录序）
    parts = [f"# {p['title'] or p['page_key']}\n\n{p['content']}" for p in compiled]
    markdown_text = "\n\n---\n\n".join(parts)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    out_dir = Path(get_data_dir()) / "files" / project_id / "exports"
    # 分钟级时间戳 + 短随机后缀（CodeReview m-1）：同分钟重复导出/并发导出
    # 不同名——确定性递增序号存在 check-then-act 竞态（renderer 的 .tmp 产物
    # 名由最终路径派生，同名并发互踩），随机后缀天然互斥，同保「不覆盖旧
    # 导出」不变量（2.6 §14.2 零新表裁决）
    output = (out_dir /
              f"wiki_{_wiki_slug(project['name'])}_{stamp}"
              f"_{uuid.uuid4().hex[:8]}.docx")
    # 渲染含 pandoc 子进程：图片缺失 pandoc 仅 warning 不失败（护栏「缺失图占位
    # 不阻断」由 pandoc 语义天然保障）；无 mermaid 图不需要 Electron 桥
    from report_generator.renderer import render_markdown_to_docx
    try:
        await asyncio.to_thread(
            render_markdown_to_docx, markdown_text, str(output),
            title=f"{project['name']}·知识册", timeout=_WIKI_RENDER_TIMEOUT)
    except Exception as e:
        # CodeReview M-2：pandoc stderr/超时细节必须透传前端（无包装时 FastAPI
        # 兜底 500 纯文本 body → electron res.json() 得 null → 前端只见
        # "Python backend error (500)"，诊断链全断）
        logger.error("Wiki export failed (project=%s): %s", project_id, e)
        raise HTTPException(status_code=500, detail=str(e))
    logger.info("Wiki exported (project=%s pages=%s): %s",
                project_id, len(compiled), output)
    return {"path": str(output), "pages": len(compiled)}


# ── Reviews ──


@router.get("/projects/{project_id}/reviews")
async def list_reviews(
    project_id: str,
    resolved: Optional[bool] = None,
    limit: int = Query(50, ge=0, description="Page size; 0 = return all"),
    offset: int = Query(0, ge=0, description="Rows to skip"),
):
    """List Wiki reviews (compilation findings) for a project.

    响应附 counts（按 type 分型计数）与 total：reviews 分页后数组不再完整，
    PulseBar/详情页徽章计数改消费 counts，不再客户端全量计数。
    """
    try:
        db = get_db()
        # limit+1 探测 has_more（list_tasks 同款，免 COUNT）
        reviews = db.list_wiki_reviews(
            project_id, resolved=resolved,
            limit=limit + 1 if limit > 0 else 0, offset=offset)
        has_more = limit > 0 and len(reviews) > limit
        if has_more:
            reviews = reviews[:limit]
        return {
            "reviews": reviews,
            "has_more": has_more,
            "total": db.conn.execute(
                "SELECT COUNT(*) AS cnt FROM wiki_reviews WHERE project_id = ?",
                (project_id,),
            ).fetchone()["cnt"],
            "counts": db.count_wiki_reviews_by_type(project_id),
        }
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

        db.resolve_wiki_review(project_id, review_id, req.action)
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to resolve review: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


async def _relocate_quote(db, project_id: str, page_key: str,
                          old_quote: str, new_text: str) -> dict:
    """失配侧 LLM 重定位（§5.5，E3-2026-10）：原文精确替换失败时，在页面

    当前内容中定位表达同一信息的（可能已被改写的）段落原文，供调用方做
    确定性替换。LLM 只干「找到那段旧话」的机械活，替换仍是确定性的。

    返回 {"status": "found" | "not_found" | "unavailable", "text"}：
      - found: text 为页面中精确定位到的原文（已过存在性校验，防幻觉盲改）
      - not_found: LLM 已看到完整内容并确认不存在 → 调用方标 vanished
        （矛盾大概率已随编辑消失）
      - unavailable: LLM 不可用/异常，或内容超长未见全貌 → 调用方标
        manual_needed（未见全貌的 NOT_FOUND 不可信，宁可留待人工）
    """
    try:
        page = db.get_wiki_page(project_id, page_key)
        if not page:
            return {"status": "not_found", "text": None}  # 页面已删，矛盾自然消失
        content = page.get("content") or ""
        if not content.strip():
            return {"status": "not_found", "text": None}
        truncated = len(content) > _RELOCATE_CONTENT_LIMIT
        from llm import LLMClient
        from llm.routing import get_task_settings
        settings = get_task_settings(db.get_settings(), "lint")
        client = LLMClient(settings)
        system = (
            "你是文本定位助手。给定页面当前内容和一段旧原文，找出当前内容中"
            "表达同一信息的那段文字（它可能已被改写、措辞不同）。\n"
            "要求：\n"
            "- 只输出那段文字在当前内容中的精确原文（逐字复制，包括标点），"
            "不要代码块标记、引号或任何解释\n"
            "- 若当前内容中不存在表达该信息的段落，只输出 NOT_FOUND"
        )
        user = (
            f"## 页面当前内容\n{content[:_RELOCATE_CONTENT_LIMIT]}\n\n"
            f"## 旧原文\n{old_quote}\n\n"
            "（旧原文将被替换为其他说法，仅供理解语义，不要输出它）"
        )
        with push_context(UsageContext(
                source="wiki", purpose="adjudicate", project_id=project_id)):
            output = (await client.chat_full([
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]))["content"]
    except Exception as e:
        logger.warning("adjudicate relocate LLM failed for %s/%s: %s",
                       project_id, page_key, e)
        return {"status": "unavailable", "text": None}
    text = output.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    # 剥成对包裹引号（LLM 常见习惯，逐层剥防嵌套）
    for _ in range(2):
        if len(text) >= 2 and text[0] in "\"'“”«" and text[-1] in "\"'“”«":
            text = text[1:-1].strip()
    if not text or text.upper().startswith("NOT_FOUND"):
        # 超长页只给了 LLM 前段：未见全貌的 NOT_FOUND 不可信，降级留人工
        if truncated:
            return {"status": "unavailable", "text": None}
        return {"status": "not_found", "text": None}
    # 存在性校验：定位文本必须在当前内容中逐字存在，否则视为幻觉放弃
    if text not in content:
        return {"status": "unavailable", "text": None}
    return {"status": "found", "text": text}


# 重定位喂给 LLM 的内容上限（字符）：绝大多数知识页远小于此；超长页 LLM 只见
# 前段，定位失败时降级 manual_needed 而非 vanished（避免误判矛盾已消失）
_RELOCATE_CONTENT_LIMIT = 12000


@router.post("/projects/{project_id}/reviews/{review_id}/adjudicate")
async def adjudicate_review(project_id: str, review_id: str, req: ReviewAdjudicateRequest):
    """矛盾裁决（§5.5，E2-2026-10）：以 chosen 侧为准，对其余各侧页面执行

    quote → 选中侧 quote 的确定性替换；替换侧 status=user_edited（编译保护）
    + 版本快照。原文精确失配时先经 LLM 重定位改写后的对应段落再替换
    （_relocate_quote，E3-2026-10）；仍失败按逐侧 status 保留待处理，
    全部成功才归档。LLM 不可用不影响确定性主路径（降级 manual_needed）。
    """
    try:
        db = get_db()
        review = db.get_wiki_review(review_id)
        if not review:
            raise HTTPException(status_code=404, detail="Review not found")
        if review["project_id"] != project_id:
            raise HTTPException(status_code=403, detail="Review belongs to different project")
        if review["type"] != "contradiction":
            return JSONResponse(status_code=400, content={"error": "仅矛盾类发现项支持裁决", "code": "FINDING_NOT_ADJUDICABLE"})
        if review.get("resolved"):
            return JSONResponse(status_code=409, content={"error": "该发现项已处理", "code": "FINDING_ALREADY_RESOLVED"})
        # 编译进行中禁止裁决（§5.5）：与并发摄入对同一批页面的写入互斥——
        # ingest Step 4 已实时重读 user_edited（根因修复），此处提前拒绝给用户
        # 即时反馈，避免裁决与编译产出交错（与 trigger_compile 同一防重入模式）
        latest_task = db.get_latest_compile_task(project_id)
        if latest_task and latest_task["status"] in ("queued", "ingesting", "storing"):
            return JSONResponse(
                status_code=409,
                content={"error": f"编译进行中({latest_task['status']})，请等待完成后再裁决", "code": "COMPILING_IN_PROGRESS"},
            )
        evidence = review.get("evidence")
        if isinstance(evidence, str):
            # 旧调用方可能拿原始行；get_wiki_review 已反序列化，此处仅兼容
            import json as _json
            try:
                evidence = _json.loads(evidence)
            except Exception:
                evidence = None
        if not evidence or not isinstance(evidence, list) or len(evidence) < 2:
            return JSONResponse(
                status_code=400,
                content={"error": "该发现项缺少结构化证据（旧版产出），请点开相关页面手动核对", "code": "FINDING_NO_EVIDENCE"},
            )
        if not 0 <= req.chosen < len(evidence):
            return JSONResponse(status_code=400, content={"error": "chosen 索引越界", "code": "CHOSEN_INDEX_OUT_OF_RANGE"})

        chosen_ev = evidence[req.chosen]
        chosen_quote = (chosen_ev.get("quote") or "").strip()
        chosen_key = (chosen_ev.get("page_key") or "").strip()
        if not chosen_quote:
            return JSONResponse(status_code=400, content={"error": "选中侧证据缺少引用原文", "code": "EVIDENCE_QUOTE_MISSING"})

        details = []
        for idx, ev in enumerate(evidence):
            if idx == req.chosen:
                continue
            page_key = (ev.get("page_key") or "").strip()
            quote = (ev.get("quote") or "").strip()
            if not page_key or not quote:
                details.append({"page_key": page_key, "success": False,
                                "status": "manual_needed", "reason": "invalid_evidence"})
                continue
            result = db.replace_wiki_page_text(
                project_id=project_id,
                page_key=page_key,
                old_text=quote,
                new_text=chosen_quote,
                change_summary=f"矛盾裁决: 以 [[{chosen_key}]] 为准",
            )
            if result.get("success"):
                # 直接替换成功（含 already_applied 重试语义：上轮已替换达成）
                result["status"] = "replaced"
                details.append(result)
                continue
            # 失配纵深（§5.5 E3-2026-10）：原文精确失配（含 review M-1 2026-10-27
            # CAS 后的 content_changed——读-改-写间内容已被他方修改）→ LLM 读
            # 最新内容重定位改写后的对应段落再替换。vanished = 矛盾大概率已随
            # 编辑消失（终态卡）；manual_needed = LLM 不可用/异常，宁可留待
            # 人工不误归档
            if result.get("reason") == "page_not_found":
                result["status"] = "vanished"
                details.append(result)
                continue
            relocated = await _relocate_quote(db, project_id, page_key, quote, chosen_quote)
            if relocated["status"] == "found":
                result2 = db.replace_wiki_page_text(
                    project_id=project_id,
                    page_key=page_key,
                    old_text=relocated["text"],
                    new_text=chosen_quote,
                    change_summary=f"矛盾裁决(LLM重定位): 以 [[{chosen_key}]] 为准",
                )
                result2["status"] = "relocated" if result2.get("success") else "manual_needed"
                details.append(result2)
                continue
            result["status"] = "vanished" if relocated["status"] == "not_found" else "manual_needed"
            details.append(result)

        all_ok = all(d.get("success") for d in details)
        if all_ok:
            db.resolve_wiki_review(project_id, review_id, "adjudicated")
            return {"success": True, "details": details}

        # 部分失配：保留发现项待人工，返回逐侧明细（status: replaced /
        # relocated / vanished / manual_needed）供前端呈现
        # （§4.4 宁可留待人工不误归档）
        return {"success": False, "reason": "partial_or_failed", "details": details}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to adjudicate review: %s", e)
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
    # 事前准入（§7.3 第三层 v3.7）：semantic/full 依赖 LLM，structural 纯本地放行；
    # mode 需在 try 外解析以便条件调用 ensure_ready（InkCubeError 直接冒泡）
    mode = (req.mode if req else "structural") or "structural"
    if mode in ("semantic", "full"):
        ensure_ready("wiki_compile")
    try:
        db = get_db()
        project = db.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        total_findings = 0

        def _store_findings(findings):
            for finding in findings:
                review_id = compute_review_id(project_id, finding["type"], finding["title"])
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
            from llm.routing import get_task_settings
            # 档位制：lint 档解析写入 model 键（2026-10-20 补齐——漏路由时
            # 直构 client 缺 model 键，靠 semantic_lint 内部重建碰巧掩益）
            settings = get_task_settings(db.get_settings(), "lint")
            llm_client = LLMClient(settings)
            # Auto-detect Wiki language for output matching
            lang_code = detect_wiki_language(db, project_id)
            language_hint = get_language_hint(lang_code)
            # 2.10 §4.3: 用量环境（source=wiki / purpose=lint）
            with push_context(UsageContext(
                    source="wiki", purpose="lint", project_id=project_id)):
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


# ── Review Sweep (§12.2.8, E3-2026-10 起改由编译终态自动触发) ──
#
# 手动端点 /sweep-reviews 与前端按钮已删（auto_governance.on_compile_complete_sync
# 是唯一触发路径，经 task_queue run_in_executor 后台化）；sweep_reviews 模块
# 函数保留供自动链复用。


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
            raise_on_error=True,  # 交互式搜索：故障必须透出，不能伪装成「无结果」
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

        # 重复 page_key → 409（与 archive 端点一致），而不是 IntegrityError 落到 500
        if db.get_wiki_schema(project_type, req.page_key):
            raise HTTPException(
                status_code=409,
                detail=f"Schema page_key '{req.page_key}' already exists for type '{project_type}'",
            )

        result = db.create_wiki_schema(
            project_type=project_type,
            page_key=req.page_key,
            title=req.title,
            compile_hint=req.compile_hint,
            base_lib_search_hints=req.base_lib_search_hints,
            position=req.position,
            is_ledger=req.is_ledger,
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
            is_ledger=req.is_ledger,
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
async def get_project_changelog(
    project_id: str,
    limit: int = Query(50, ge=0, description="Page size; 0 = return all"),
    offset: int = Query(0, ge=0, description="Rows to skip"),
):
    """Get project-wide change log (for log.md timeline view)."""
    try:
        db = get_db()
        project = db.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        # limit+1 探测 has_more（list_tasks 同款，免 COUNT）
        logs = db.list_wiki_change_log(
            project_id, limit=limit + 1 if limit > 0 else 0, offset=offset)
        has_more = limit > 0 and len(logs) > limit
        if has_more:
            logs = logs[:limit]
        total = db.conn.execute(
            "SELECT COUNT(*) AS cnt FROM wiki_change_log WHERE project_id = ?",
            (project_id,),
        ).fetchone()["cnt"]
        return {"changelog": logs, "has_more": has_more, "total": total}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get changelog: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Wiki Purpose / Overview (§3.6) ──


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


# ── Wiki Page Versions ──


@router.get("/projects/{project_id}/pages/{page_key}/versions")
async def list_page_versions(
    project_id: str,
    page_key: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List version history for a wiki page."""
    try:
        db = get_db()
        page = db.get_wiki_page(project_id, page_key)
        if not page:
            raise HTTPException(status_code=404, detail="Wiki page not found")
        # limit+1 探测 has_more（list_tasks 同款，免 COUNT）
        versions = db.list_wiki_page_versions(
            page["id"], limit=limit + 1, offset=offset)
        has_more = len(versions) > limit
        if has_more:
            versions = versions[:limit]
        total = db.conn.execute(
            "SELECT COUNT(*) AS cnt FROM wiki_page_versions WHERE wiki_page_id = ?",
            (page["id"],),
        ).fetchone()["cnt"]
        return {
            "versions": versions,
            "has_more": has_more,
            "total": total,
            "current_version": page.get("compile_version", 0),
        }
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


# ── Duplicate Groups (§12.2.10，E3-2026-10 检测改自动、组落库) ──


@router.get("/projects/{project_id}/dedup/groups")
async def list_dedup_groups(project_id: str):
    """待确认重复页面组（检测由编译终态自动执行，结果落库后在此浮现）。"""
    try:
        db = get_db()
        project = db.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        return {"groups": db.list_dedup_groups(project_id)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to list dedup groups: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{project_id}/dedup/groups/{group_id}/dismiss")
async def dismiss_dedup_group(project_id: str, group_id: str):
    """忽略一组重复提案（status=dismissed；重检测同组不再回滚为 pending）。"""
    try:
        db = get_db()
        project = db.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        ok = db.set_dedup_group_status(project_id, group_id, "dismissed")
        if not ok:
            raise HTTPException(status_code=404, detail="Dedup group not found")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to dismiss dedup group: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{project_id}/dedup/merge")
async def merge_duplicate_pages(project_id: str, req: DedupMergeRequest):
    """Merge duplicate pages into a canonical page.

    User must specify which page to keep (canonical) and which to merge.
    合并是最重写操作（删页 + 全库链接重写），编译进行中拒绝——与
    fix-wikilink/adjudicate 同一互斥口径（review s-2 2026-10-27 补齐：
    原最重写操作反而无门）。
    """
    # 事前准入（§7.3 第三层 v3.7）：LLM 必需，try 块外调用
    ensure_ready("wiki_compile")
    try:
        db = get_db()
        project = db.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        latest_task = db.get_latest_compile_task(project_id)
        if latest_task and latest_task["status"] in ("queued", "ingesting", "storing"):
            return JSONResponse(
                status_code=409,
                content={"error": f"编译进行中({latest_task['status']})，请等待完成后再合并", "code": "COMPILING_IN_PROGRESS"},
            )

        from wiki.dedup import merge_pages
        from llm.routing import get_task_settings
        from llm import LLMClient

        settings = db.get_settings()
        # 档位制：先解析后构造一次（同 dedup/detect 清理，2026-10-20）
        effective_settings = get_task_settings(settings, "dedup")
        llm = LLMClient(effective_settings)

        # 2.10 §4.3: 用量环境（source=wiki / purpose=dedup）
        with push_context(UsageContext(
                source="wiki", purpose="dedup", project_id=project_id)):
            result = await merge_pages(
                db, project_id,
                canonical_key=req.canonical_key,
                duplicate_keys=req.duplicate_keys,
                llm_client=llm,
            )
        # 合并成功标记检测组 merged（E3-2026-10：组落库后状态闭环）
        if req.group_id and result.get("merged", 0) > 0:
            db.set_dedup_group_status(project_id, req.group_id, "merged")
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
    # 事前准入（§7.3 第三层 v3.7）：LLM 必需，try 块外调用
    ensure_ready("wiki_compile")
    try:
        db = get_db()
        project = db.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        page = db.get_wiki_page(project_id, req.page_key)
        if not page:
            raise HTTPException(status_code=404, detail="Wiki page not found")

        # 编译互斥（复审 Major 2026-10-27 补口，fix-wikilink/adjudicate/merge
        # 同口径）：补链是整页写路径，不得与编译 ingest 并发读写同页
        latest_task = db.get_latest_compile_task(project_id)
        if latest_task and latest_task["status"] in ("queued", "ingesting", "storing"):
            return JSONResponse(
                status_code=409,
                content={"error": f"编译进行中({latest_task['status']})，请等待完成后再补全链接", "code": "COMPILING_IN_PROGRESS"},
            )

        from wiki.wiki_enrich import enrich_wikilinks
        from llm import LLMClient
        from llm.routing import get_task_settings

        settings = db.get_settings()
        effective_settings = get_task_settings(settings, "enrich")
        llm_client = LLMClient(effective_settings)
        all_pages = db.list_wiki_pages(project_id)
        all_pages_brief = [{"page_key": p["page_key"], "title": p.get("title", "")} for p in all_pages]

        # 2.10 §4.3: 用量环境（source=wiki / purpose=enrich）
        with push_context(UsageContext(
                source="wiki", purpose="enrich", project_id=project_id,
                entity_id=page["id"])):
            enriched_content, link_count = await enrich_wikilinks(
                content=page.get("content") or "",
                page_key=req.page_key,
                all_pages=all_pages_brief,
                llm_client=llm_client,
            )

        # Save enriched content if links were added（复审 Major 2026-10-27：
        # LLM 补链是秒级读-改-写窗口，写回以读取原文作内容级 CAS 条件——
        # 窗口内用户编辑/治理链先行写入则拒绝整页覆盖，LLM 建议整批丢弃）
        if link_count > 0:
            # or ""：content 列为 NULL 时 .get(key, "") 仍返回 None，会把 CAS
            # 静默降级为无条件写（复审建议 2026-10-27）
            write_result = db.update_wiki_page_content(
                page_id=page["id"],
                content=enriched_content,
                status=page.get("status", "compiled"),
                expected_content=page.get("content") or "",
            )
            if not write_result.get("success"):
                return {"success": False, "reason": "content_changed", "links_added": 0}
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
