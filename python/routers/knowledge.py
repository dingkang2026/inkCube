"""python/routers/knowledge.py"""
import os
import shutil
import asyncio
import base64
import logging
import uuid
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

from db.database import Database
from knowledge.vector_store import VectorStore
from knowledge.fts_store import FTSStore
from knowledge.knowledge_search import KnowledgeSearchTool
from errors import LLMNotConfigured
from readiness import ensure_ready
import activity_events
from paths import get_data_dir


router = APIRouter(prefix="/knowledge", tags=["knowledge"])


def get_db() -> Database:
    """Get database instance from deps"""
    try:
        import deps
        return deps.db
    except ImportError:
        raise HTTPException(status_code=500, detail="Database not initialized")


def _validate_llm_config(db: Database) -> None:
    """Validate LLM API configuration exists in database.

    Raises LLMNotConfigured (InkCubeError) if LLM is not configured.
    The global exception handler converts it to a proper HTTP response.
    """
    from llm.routing import resolve_model

    settings = dict(db.get_settings())
    settings["model"] = resolve_model(settings, "standard")
    if (not settings.get("api_url") or not settings.get("api_key")
            or not settings.get("model")):
        raise LLMNotConfigured()


async def _validate_base_lib_exists(db: Database, lib_id: str):
    """Raise 404 if the base library doesn't exist."""
    lib = db.get_base_lib(lib_id)
    if not lib:
        raise HTTPException(status_code=404, detail="Base library not found")
    return lib


class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    type: Optional[str] = "general"
    stage: Optional[str] = "none"
    company_name: Optional[str] = ""
    credit_code: Optional[str] = ""


class FileUpload(BaseModel):
    projectId: str
    filename: str
    fileType: str
    data: str  # base64 encoded file content


# Upload size cap (L5): checked BEFORE base64 decoding so an oversized
# payload fails fast with 413 instead of being materialized in memory.
# Aligned with the 200MB workspace-export cap in routers/tasks.py.
MAX_UPLOAD_BYTES = 200 * 1024 * 1024


def _check_upload_size(upload: FileUpload) -> None:
    """Reject uploads whose decoded size would exceed MAX_UPLOAD_BYTES."""
    # base64 encodes 3 bytes into 4 chars; strip any data-URI prefix
    payload = upload.data.split(",", 1)[-1]
    estimated = len(payload) * 3 // 4
    if estimated > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"文件过大（约 {estimated // 1024 // 1024}MB），"
                f"超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB 上限"
            ),
        )


def _safe_filename(name: str, fallback: str) -> str:
    """Strip path components from a client-supplied filename.

    Blocks path traversal ("../../evil.txt", "..\\..\\evil.txt") — only the
    final path segment is kept; if nothing remains, `fallback` is returned.
    """
    safe = os.path.basename(str(name).replace("\\", "/")).strip()
    return safe if safe not in ("", ".", "..") else fallback


class SearchRequest(BaseModel):
    projectId: str
    query: str
    topK: int = 5
    contentTypeFilter: Optional[str] = None
    fileId: Optional[str] = None


class ChatRequest(BaseModel):
    """Knowledge base chat request — RAG Q&A, independent from agent chat."""
    message: str
    fileId: Optional[str] = None


async def _upload_core(project_id: str, upload: FileUpload, db, data_dir, worker):
    """上传核心逻辑（项目侧与基础库侧路由共用）：落盘 → 去重 → 建档 → 入队。

    体内含 _check_upload_size（413）；调用方若在 try 块内调用，须先于
    except Exception 声明 except HTTPException: raise，避免 413 被吞成 500。"""
    import uuid
    import hashlib
    # Reject oversized uploads before decoding (L5)
    _check_upload_size(upload)
    file_id = f"file_{uuid.uuid4().hex[:8]}"

    files_dir = Path(data_dir) / "files" / project_id / file_id
    files_dir.mkdir(parents=True, exist_ok=True)
    filename = _safe_filename(upload.filename, f"{file_id}.bin")
    file_path = files_dir / filename

    # Decode, hash and save — off the event loop (H1/L5)
    def _decode_and_save():
        raw = base64.b64decode(
            upload.data.split(",")[1] if "," in upload.data else upload.data
        )
        file_path.write_bytes(raw)
        return raw, hashlib.md5(raw).hexdigest()

    file_data, file_hash = await asyncio.to_thread(_decode_and_save)

    file_size = len(file_data)

    existing = db.find_duplicate_file(project_id, file_hash)
    if existing:
        shutil.rmtree(files_dir, ignore_errors=True)
        return {
            "success": True, "file_id": existing["id"],
            "chunks_count": existing.get("chunk_count", 0),
            "images_count": existing.get("image_count", 0),
            "error": None,
            "duplicate": True, "message": f"文件已存在: {existing['filename']}", "code": "FILE_DUPLICATE",
        }

    db.create_file(
        project_id=project_id, filename=filename,
        file_type=upload.fileType, file_size=file_size,
        file_hash=file_hash, file_id=file_id,
    )

    if worker:
        logger.info("Enqueuing file %s for processing", file_id)
        await worker.enqueue_file(file_id)
    else:
        logger.warning("Worker not available! File %s will be picked up by queue scanner", file_id)

    return {
        "success": True, "file_id": file_id,
        "chunks_count": 0, "images_count": 0,
        "error": None, "queued": True,
    }


# ── Queue Pause（0.4 §队列暂停）──


class QueuePauseRequest(BaseModel):
    kind: str  # parsing | describe（wiki 编译/报告生成无队列暂停，0.4 §6.7 与 2.5 §3.1 修订）
    paused: bool


_QUEUE_PAUSE_KEYS = {
    "parsing": "parsing_paused",
    "describe": "describe_paused",
}


@router.post("/queue/pause")
async def set_queue_pause(req: QueuePauseRequest):
    """停止/恢复后台队列（文件解析/图片描述；UI 呈现为「停止/继续」）。

    语义：停止 = 不再开始新任务——关闸门 + 清空内存队列积压（DB 状态
    不动，任务留在 pending）；在跑的任务自然跑完。恢复 = 开闸门，
    QueueScanner 下一轮（≤ scan_interval）按 DB pending 补投递，断点
    续传。状态持久化 settings（重启保持，main.py lifespan 恢复）。
    基础库与项目共用同一队列，天然同停止。心跳口径（activities.
    _active_files）同步感知：停止后排队中任务不算活跃，只剩在飞收尾。
    wiki 编译不设队列暂停：其任务级「停止」+ 断点续传已覆盖全部价值
    （0.4 §6.7 修订）；报告生成同（报告级「暂停」+「重新生成」双出口，
    2.5 §3.1 修订）。
    """
    key = _QUEUE_PAUSE_KEYS.get(req.kind)
    if not key:
        raise HTTPException(status_code=422, detail=f"Unknown pause kind: {req.kind}")
    db = get_db()
    db.set_flag(key, req.paused)
    import deps
    drained: int | None = None
    if deps.worker:
        if req.kind == "parsing":
            deps.worker.set_file_paused(req.paused)
            if req.paused:
                drained = deps.worker.drain_file_queue()
        elif req.kind == "describe":
            deps.worker.set_image_paused(req.paused)
            if req.paused:
                drained = deps.worker.drain_image_queue()
    # 大积压场景的核心操作：清掉多少积压是事后排查的关键信号，必须可观测
    suffix = f", drained {drained} queued items" if drained is not None else ""
    logger.info("Queue %s %s%s", req.kind, "stopped" if req.paused else "resumed", suffix)
    return {"kind": req.kind, "paused": req.paused}


@router.get("/projects")
async def list_projects(
    archived: bool = False,
    limit: int = Query(50, ge=0, description="Page size; 0 = return all"),
    offset: int = Query(0, ge=0, description="Rows to skip"),
):
    """List projects with file and chunk counts.

    archived=False 返回有效项目（现状语义），True 返回已归档项目；
    响应附 archived_count 供侧边栏归档 tab 徽记（一次聚合查询）；
    total 与列表同口径（archived 过滤后）供分页控件。
    """
    try:
        db = get_db()
        # Offset-based pagination（占位符顺序 LIMIT ? OFFSET ? → 先 append limit+1 再 append offset）
        sql = """
            SELECT 
                p.id,
                p.name,
                p.description,
                p.type,
                p.stage,
                p.company_name,
                p.credit_code,
                p.archived_at,
                COUNT(DISTINCT f.id) as file_count,
                COALESCE(SUM(f.chunk_count), 0) as chunk_count,
                p.created_at
            FROM projects p
            LEFT JOIN files f ON f.project_id = p.id
            WHERE p.kind = 'project' AND (p.archived_at IS NOT NULL) = ?
            GROUP BY p.id
            ORDER BY p.created_at DESC
        """
        params: list = [1 if archived else 0]
        if limit > 0:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit + 1, offset])

        cursor = db.conn.execute(sql, params)
        rows = cursor.fetchall()
        has_more = limit > 0 and len(rows) > limit
        if has_more:
            rows = rows[:limit]

        projects = []
        for row in rows:
            projects.append({
                "id": row["id"],
                "name": row["name"],
                "description": row["description"],
                "type": row["type"],
                "stage": row["stage"],
                "company_name": row["company_name"],
                "credit_code": row["credit_code"],
                "archived_at": row["archived_at"],
                "file_count": row["file_count"],
                "chunk_count": row["chunk_count"],
                "created_at": row["created_at"]
            })
        
        # 2.10 §4.5：项目列表行附加累计消耗（无记录为 null，前端不渲染小字）——只算当前页行
        usage_map = db.get_usage_by_projects([p["id"] for p in projects])
        for p in projects:
            p["usage"] = usage_map.get(p["id"])

        total = db.conn.execute(
            "SELECT COUNT(*) AS cnt FROM projects "
            "WHERE kind = 'project' AND (archived_at IS NOT NULL) = ?",
            (1 if archived else 0,),
        ).fetchone()["cnt"]

        return {
            "projects": projects,
            "has_more": has_more,
            "total": total,
            "archived_count": db.count_archived_projects(),
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects")
async def create_project(project: ProjectCreate):
    """Create a new project"""
    try:
        db = get_db()
        
        result = db.create_project(
            name=project.name,
            description=project.description or "",
            project_type=project.type or "general",
            stage=project.stage or "none",
            company_name=project.company_name or "",
            credit_code=project.credit_code or "",
        )
        
        return {
            "id": result["id"],
            "name": result["name"],
            "description": result["description"],
            "type": result["type"],
            "stage": result["stage"],
            "company_name": result.get("company_name", ""),
            "credit_code": result.get("credit_code", ""),
            "file_count": 0,
            "chunk_count": 0,
            "created_at": result["created_at"],
            "seeded_tasks_count": result.get("seeded_tasks_count", 0),  # 裁决 8:创建确认态铺入计数(3.0 二期 Task 13)
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _escape_like(text: str) -> str:
    """转义 LIKE 通配符（配合 SQL 中 ESCAPE '\\' 使用）。

    用户可控输入（filename_search / search / file_id）进入 LIKE 模式前必须转义，
    避免 % / _ 改变匹配语义——与 fts_store.py 用户搜索场景同一约定。"""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _file_status_clause(status: Optional[str]) -> tuple[str, list]:
    """文件状态筛选子句（状态筛选服务端下沉，项目/基础库两端点共用）。
    processing = 排队+解析中（pending/parsing），其余为字面状态；空值/all 不过滤。"""
    if not status or status == "all":
        return "", []
    if status == "processing":
        return "f.status IN ('pending', 'parsing')", []
    return "f.status = ?", [status]


# UI 文件类型类别 → 扩展名（项目/基础库两端点共用）
_FILE_TYPE_MAP = {
    "pdf": [".pdf"],
    "docx": [".docx", ".doc"],
    "pptx": [".pptx", ".ppt"],
    "xlsx": [".xlsx", ".xls"],
    "image": [".png", ".jpg", ".jpeg"],
}


def _file_type_clause(file_type: Optional[str]) -> tuple[str, list]:
    """文件类型筛选子句（UI 类别映射扩展名；空值/all/未知类别不过滤）。"""
    if not file_type or file_type == "all":
        return "", []
    extensions = _FILE_TYPE_MAP.get(file_type, [])
    if not extensions:
        return "", []
    placeholders = ",".join("?" for _ in extensions)
    return f"f.file_type IN ({placeholders})", list(extensions)


@router.get("/projects/{project_id}/files")
async def get_project_files(
    project_id: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = 0,
    status: Optional[str] = None,
    file_type: Optional[str] = None,
    filename_search: Optional[str] = None,
):
    """Get files in a project with offset pagination, status/type filters, and filename search."""
    try:
        db = get_db()

        # Build query with optional filters（status_counts 走不含状态筛选的同口径子集）
        where_clauses = ["f.project_id = ?"]
        params: list = [project_id]
        counts_clauses = ["f.project_id = ?"]
        counts_params: list = [project_id]

        status_clause, status_params = _file_status_clause(status)
        if status_clause:
            where_clauses.append(status_clause)
            params.extend(status_params)

        type_clause, type_params = _file_type_clause(file_type)
        if type_clause:
            where_clauses.append(type_clause)
            params.extend(type_params)
            counts_clauses.append(type_clause)
            counts_params.extend(type_params)

        if filename_search:
            where_clauses.append("f.filename LIKE ? ESCAPE '\\'")
            like = f"%{_escape_like(filename_search)}%"
            params.append(like)
            counts_clauses.append("f.filename LIKE ? ESCAPE '\\'")
            counts_params.append(like)

        where_sql = " AND ".join(where_clauses)
        counts_where_sql = " AND ".join(counts_clauses)

        # Offset-based pagination（占位符顺序 LIMIT ? OFFSET ? → 先 append limit+1 再 append offset）
        sql = (
            f"""SELECT f.id, f.filename, f.file_type, f.status, f.progress,
                       f.chunk_count, f.image_count, f.image_status,
                       f.image_described_count, f.error_message, f.created_at
                FROM files f
                WHERE {where_sql}
                ORDER BY CASE WHEN f.status IN ('pending','parsing') THEN 0 ELSE 1 END,
                         f.created_at DESC, f.id DESC
                LIMIT ?"""
        )
        params.append(limit + 1)  # fetch one extra to detect has_more
        if offset > 0:
            sql += " OFFSET ?"
            params.append(offset)

        cursor = db.conn.execute(sql, params)
        rows = [dict(r) for r in cursor.fetchall()]
        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        # Get total count（与主查询同口径，含状态筛选）
        total_params: list = [project_id] + status_params + type_params
        if filename_search:
            total_params.append(f"%{_escape_like(filename_search)}%")
        total = db.conn.execute(
            f"SELECT COUNT(*) as cnt FROM files f WHERE {where_sql}",
            total_params,
        ).fetchone()["cnt"]

        # 状态分布（搜索/类型口径，不含状态筛选）——供前端筛选 Tab 计数
        status_counts = {
            r["status"]: r["cnt"]
            for r in db.conn.execute(
                f"SELECT f.status, COUNT(*) AS cnt FROM files f WHERE {counts_where_sql} GROUP BY f.status",
                counts_params,
            ).fetchall()
        }

        result = []
        for row in rows:
            result.append({
                "id": row["id"],
                "filename": row["filename"],
                "file_type": row["file_type"],
                "status": row["status"],
                "progress": row.get("progress", 0),
                "chunks_count": row["chunk_count"],
                "images_count": row["image_count"],
                "image_status": row.get("image_status", "none"),
                "image_described_count": row.get("image_described_count", 0),
                "error_message": row.get("error_message"),
                "created_at": row["created_at"],
            })
        return {"files": result, "has_more": has_more, "total": total, "status_counts": status_counts}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects/{project_id}/files/active")
async def get_active_files(project_id: str):
    """Get files with non-terminal status (for polling)."""
    try:
        db = get_db()
        cursor = db.conn.execute(
            "SELECT id, filename, status, progress, image_status, "
            "image_described_count, image_count, error_message "
            "FROM files WHERE project_id = ? AND "
            "(status IN ('pending','parsing') OR "
            "image_status IN ('pending','describing','error')) "
            "ORDER BY created_at ASC",
            (project_id,)
        )
        return {"files": [dict(r) for r in cursor.fetchall()]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{project_id}/upload")
async def upload_file(project_id: str, upload: FileUpload):
    """Upload a file and enqueue for async processing."""
    # 准入前置校验（0.4 §7.3）：缺解析/嵌入模型时结构化报错，避免逐文件 error
    ensure_ready("file_parsing")
    try:
        import deps
        db = get_db()
        return await _upload_core(project_id, upload, db, get_data_dir(), deps.worker)
    except HTTPException:
        raise  # 413（超限）/ core 内结构化错误不被吞成 500
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _backfill_markdown_content(chunks: list, project_id: str) -> None:
    """Fill in metadata.markdown_content for recent-chunk lists (2.2 §4.3).

    Recent-chunk rows come from FTS which stores plain text only; the display
    markdown (table structure) lives in ChromaDB metadata. Failure degrades to
    plain-text display — the list itself must still render.
    """
    try:
        if not chunks:
            return
        vs = VectorStore(str(Path(get_data_dir()) / "chroma"))
        meta_map = vs.get_metadata_by_ids(project_id, [c["id"] for c in chunks])
        for c in chunks:
            md = (meta_map.get(c["id"]) or {}).get("markdown_content")
            if md:
                c["metadata"]["markdown_content"] = md
    except Exception as e:
        logger.warning("recent-chunks markdown backfill failed: %s", e)


@router.get("/projects/{project_id}/recent-chunks")
async def get_recent_chunks(project_id: str, limit: int = 20, file_id: Optional[str] = None):
    """Get the most recent chunks for a project (by insertion order). Optionally filter by file_id."""
    try:
        db = get_db()
        where_clauses = ["project_id = ?"]
        params = [project_id]
        if file_id:
            safe_id = _escape_like(file_id)
            where_clauses.append("(chunk_id LIKE ? ESCAPE '\\' OR chunk_id LIKE ? ESCAPE '\\')")
            params.extend([f"chunk_{safe_id}%", f"img_{safe_id}%"])
        where_sql = " AND ".join(where_clauses)
        cursor = db.conn.execute(
            f"""SELECT chunk_id, content, filename, content_type
               FROM chunks_fts
               WHERE {where_sql}
               ORDER BY rowid DESC
               LIMIT ?""",
            params + [limit],
        )
        chunks = []
        for row in cursor.fetchall():
            chunks.append({
                "id": row["chunk_id"],
                "content": row["content"],
                "_score": 0,
                "metadata": {
                    "filename": row["filename"],
                    "content_type": row["content_type"],
                },
            })
        _backfill_markdown_content(chunks, project_id)
        return {"results": chunks}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/search")
async def search(request: SearchRequest):
    """Search knowledge base with hybrid retrieval"""
    # 准入前置校验（0.4 §7.3）：缺嵌入模型时结构化报错
    ensure_ready("knowledge_search")
    try:
        db = get_db()
        data_dir = get_data_dir()
        chroma_dir = str(Path(data_dir) / "chroma")
        models_dir = str(Path(data_dir) / "models")
        
        search_tool = KnowledgeSearchTool(
            db=db,
            chroma_persist_dir=chroma_dir,
            models_dir=models_dir
        )
        
        results = await search_tool.search(
            query=request.query,
            project_id=request.projectId,
            top_k=request.topK,
            content_type_filter=request.contentTypeFilter,
            file_id=request.fileId
        )
        
        return {"results": results}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{project_id}/chat")
async def chat_project(project_id: str, request: ChatRequest):
    """Knowledge base RAG chat — independent from agent (产业洞察) chat."""
    # 事前准入（§7.3 第三层 v3.7）：组合入口，embedding + LLM 双依赖；try 块外调用（同下）
    ensure_ready("knowledge_search")
    ensure_ready("task")
    try:
        db = get_db()
        data_dir = get_data_dir()

        from knowledge.rag import KnowledgeRAG
        rag = KnowledgeRAG(
            db=db,
            chroma_persist_dir=str(Path(data_dir) / "chroma"),
            models_dir=str(Path(data_dir) / "models"),
        )

        import deps
        settings = deps.db.get_settings() if hasattr(deps.db, 'get_settings') else {}

        return await rag.ask(
            project_id=project_id,
            message=request.message,
            file_id=request.fileId,
            settings=settings,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    type: Optional[str] = None
    stage: Optional[str] = None


@router.patch("/projects/{project_id}")
async def update_project(project_id: str, body: ProjectUpdate):
    """Update project name, description, type, and/or stage."""
    try:
        db = get_db()
        if body.name is None and body.description is None and body.type is None and body.stage is None:
            raise HTTPException(status_code=400, detail="No fields to update")
        # 3.0 §5.3: 阶段切换 → 激活该时期 pending_activation 实例。先激活后写 stage
        # （review M-2）：激活失败异常上抛→500，stage 保持旧值——避免「阶段已换、
        # 任务未铺出」的中间态（原实现在 update_project 之后吞异常，失败用户零感知）
        if body.stage:
            _old = db.get_project(project_id)
            if _old and _old.get("stage") != body.stage:
                import affairs_service
                affairs_service.activate_phase(db, project_id, body.stage)
        db.update_project(
            project_id,
            name=body.name,
            description=body.description,
            type=body.type,
            stage=body.stage,
        )
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _project_active_work(db, project_id: str) -> list[str]:
    """归档/删除守门：项目内进行中活动清单（解析/报告/填表/Wiki 编译/图片描述），空表 = 可归档可删。"""
    checks = [
        ("文件解析", "SELECT COUNT(*) AS c FROM files "
                     "WHERE project_id = ? AND status IN ('pending', 'parsing')"),
        ("报告生成", "SELECT COUNT(*) AS c FROM reports "
                     "WHERE project_id = ? AND status = 'generating'"),
        ("填表执行", "SELECT COUNT(*) AS c FROM form_fills "
                     "WHERE project_id = ? AND status IN ('pending', 'filling')"),
        ("Wiki 编译", "SELECT COUNT(*) AS c FROM wiki_compile_tasks "
                      "WHERE project_id = ? AND status IN ('queued', 'ingesting', 'storing')"),
        # 图片 LLM 描述（Minor-3 口径补全）：文件落 done 后描述仍异步在途，
        # 在途 = pending（待描述）/ processing（worker 已取走）
        ("图片描述", "SELECT COUNT(*) AS c FROM images "
                      "JOIN files ON images.file_id = files.id "
                      "WHERE files.project_id = ? AND images.status IN ('pending', 'processing')"),
        # PPT 交付（2.3 §7.3 口径对齐）：running 执行中 / awaiting_input 门禁
        # 等待均算进行中（复用统一活动 status 字段，非新增轮询/活跃谓词）
        ("PPT 任务", "SELECT COUNT(*) AS c FROM ppts "
                      "WHERE project_id = ? AND status IN ('running', 'awaiting_input')"),
    ]
    return [label for label, sql in checks
            if db.conn.execute(sql, (project_id,)).fetchone()["c"] > 0]


@router.post("/projects/{project_id}/archive")
async def archive_project(project_id: str):
    """归档项目（生命周期软关闭：写 archived_at，数据零清理）。

    删除准入 = 已归档；进行中活动（解析/报告/填表/Wiki 编译）拒绝归档。
    """
    try:
        db = get_db()
        proj = db.get_project(project_id)
        if not proj or proj.get("kind") != "project":
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
        active = _project_active_work(db, project_id)
        if active:
            return JSONResponse(
                status_code=409,
                content={
                    "error": f"项目尚有进行中的活动（{'、'.join(active)}），完成后再归档",
                    "code": "PROJECT_HAS_ACTIVE_WORK",
                },
            )
        db.archive_project(project_id)
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{project_id}/unarchive")
async def unarchive_project(project_id: str):
    """恢复归档项目（清 archived_at，移回有效列表）。"""
    try:
        db = get_db()
        proj = db.get_project(project_id)
        if not proj or proj.get("kind") != "project":
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
        if not db.unarchive_project(project_id):
            return JSONResponse(
                status_code=409,
                content={"error": "项目已处于有效状态，无需恢复", "code": "PROJECT_ALREADY_ACTIVE"},
            )
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str):
    """Delete a project and all its data.

    删除准入 = 已归档：未归档项目 409（先归档再删除，归档 tab 才有删除入口）。
    已归档但仍有在途活动同样 409：归档后详情页仍可发起活动（归档仅是状态层，
    2.1 §11.6），删除前必须复查，防 runner 写已删行产出孤儿产物（Major-1）。
    """
    try:
        db = get_db()
        data_dir = get_data_dir()

        proj = db.get_project(project_id)
        if not proj or proj.get("kind") != "project":
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
        if not proj.get("archived_at"):
            return JSONResponse(
                status_code=409,
                content={"error": "项目未归档，请先归档再删除", "code": "PROJECT_NOT_ARCHIVED"},
            )
        active = _project_active_work(db, project_id)
        if active:
            return JSONResponse(
                status_code=409,
                content={
                    "error": f"项目尚有进行中的活动（{'、'.join(active)}），完成后再删除",
                    "code": "PROJECT_HAS_ACTIVE_WORK",
                },
            )

        # PPT 级联清理（2.3 §7.3）：ppts 无外键、tasks 无 project_id，均不随
        # db.delete_project 级联——逐行复用 service.delete 既有链路（bounded
        # 等待 agent 退出 + 关停确认页 + 删任务工作区 + 删 ppts 行）。守门已过
        # = 无 running/awaiting 行，此处通常瞬间完成；失败即中止（此时索引与
        # 文件均未动，整单可重试），防半删状态产生不可见残留
        from ppt_service import service as ppt_service
        for row in db.list_ppts(project_id):
            result = await ppt_service.delete(db, row["id"])
            if not result.get("ok"):
                import logging
                logging.getLogger(__name__).error(
                    "Project delete: ppt cleanup failed project=%s ppt=%s result=%s",
                    project_id, row["id"], result)
                raise HTTPException(status_code=500, detail=str(result.get("error")))
        # 清理后复查（与前置守门同模式）：快照与删除的间隙内归档项目仍可能
        # 被发起新活动，放行会产生孤儿行——409 后数据未动，重试即可
        active_after = _project_active_work(db, project_id)
        if active_after:
            return JSONResponse(
                status_code=409,
                content={
                    "error": f"项目尚有进行中的活动（{'、'.join(active_after)}），完成后再删除",
                    "code": "PROJECT_HAS_ACTIVE_WORK",
                },
            )

        # Delete from ChromaDB
        chroma_dir = str(Path(data_dir) / "chroma")
        vector_store = VectorStore(chroma_dir)
        vector_store.delete_project(project_id)
        # Delete from FTS
        fts_store = FTSStore(db)
        fts_store.delete_project_chunks(project_id)

        # Delete files from disk: data/files/{project_id}/
        files_dir = Path(data_dir) / "files" / project_id
        if files_dir.exists():
            shutil.rmtree(files_dir, ignore_errors=True)

        # Explicit Wiki cleanup before CASCADE (belt-and-suspenders)
        try:
            wiki_counts = db.cleanup_wiki_data_for_project(project_id)
            import logging
            logging.getLogger(__name__).info(
                "Wiki pre-cascade cleanup for project %s: %s", project_id, wiki_counts,
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "Wiki pre-cleanup failed for project %s: %s", project_id, e,
            )

        # 验收-P2（产品验收发现）：目标容器删除联动（对称置空）——指向本项目的订阅
        # 去向置空并合并一条通知；不联动则每期 archive_failed 幽灵警告（产出
        # 正常但无处沉淀，根因无处可见）。与「删订阅时 tasks.schedule_id 置
        # NULL」同构（target_id 无外键，手动等价 ON DELETE SET NULL）。
        affected = db.clear_schedules_target("project", project_id)

        # 报告产物目录补清（2026-10-20 存储审计）：reports 行随项目级联删，
        # 但磁盘目录仅在单报告删除端点清理——级联删行后目录变孤儿。
        # 级联删行前按 project_id 收集 content_path 逐个删除。
        report_rows = db.conn.execute(
            "SELECT id, content_path FROM reports WHERE project_id = ?",
            (project_id,)).fetchall()
        for row in report_rows:
            content_path = row["content_path"] or ""
            if content_path and os.path.isdir(content_path):
                shutil.rmtree(content_path, ignore_errors=True)
                logger.info("Project delete: removed report dir %s (report=%s)",
                            content_path, row["id"])

        # Delete from database (cascade handles files, images, wiki_*; kind check prevents base_lib deletion)
        db.delete_project(project_id)

        if affected:
            names = "、".join(r["name"] for r in affected)
            activity_events.notify(
                db, entity_type="schedule",
                entity_id=f"target_removed_{project_id}",
                project_id=None, container="project", kind="info",
                entity_title="定时订阅",
                detail=(f"项目「{proj['name']}」已删除，{len(affected)} 个订阅"
                        f"（{names}）的去向已重置为不沉淀"))

        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Project transfer（2.9project-transfer-design.md §5/§6）──

# asyncio.create_task 仅持弱引用（官方要求显式保存，Review m4）：GC 中途回收
# 会让导入后台链静默消失、run 永久卡 running（该项目导入永远 409）。
_TRANSFER_BG_TASKS: set = set()

class ExportProjectBody(BaseModel):
    target_path: str


class ImportProjectBody(BaseModel):
    source_path: str


@router.post("/projects/{project_id}/export")
def export_project_endpoint(project_id: str, body: ExportProjectBody):
    """导出项目交换包（2.9 §5；同步执行，大项目分钟级，调用方放宽 HTTP 超时）。"""
    from transfer import exporter
    from transfer import service as transfer_service
    db = get_db()
    project = db.get_project(project_id)
    if not project or project.get("kind") != "project":
        raise HTTPException(status_code=404, detail="Project not found")   # 2.9 §5.1
    title = project["name"]
    # 原子防重入（CodeReview M1）：check+start 同临界区，线程池并发不双过 409
    run_id = transfer_service.try_start_run("project_export", project_id, title)
    if run_id is None:
        raise HTTPException(status_code=409, detail="Export already running for this project")
    try:
        result = exporter.export_project(
            db, get_data_dir(), project_id, body.target_path,
            on_progress=lambda p, m: transfer_service.update_run(
                run_id, progress=p, message=m))
    except exporter.TransferError as e:
        transfer_service.finish_run(run_id)
        activity_events.fail_transfer(db, "project_export", run_id,
                                      project_id=project_id, entity_title=title,
                                      error_message=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        transfer_service.finish_run(run_id)
        activity_events.fail_transfer(db, "project_export", run_id,
                                      project_id=project_id, entity_title=title,
                                      error_message=str(e))
        raise HTTPException(status_code=500, detail=str(e))
    transfer_service.finish_run(run_id)
    activity_events.complete_transfer(db, "project_export", run_id,
                                      project_id=project_id, entity_title=title)
    return result


@router.post("/projects/import")
async def import_project_endpoint(body: ImportProjectBody):
    """导入项目交换包（2.9 §6）：快速校验后立即返回，后台导入走统一活动。"""
    from transfer import importer
    from transfer import service as transfer_service
    db = get_db()
    try:
        manifest = importer.validate_package(body.source_path)
    except importer.TransferError as e:
        raise HTTPException(status_code=400, detail=str(e))
    new_pid = str(uuid.uuid4())
    title = manifest["project"]["name"]
    # 原子全局单飞（2.9 §6.1 + CodeReview M1）：与导出端点同款原子入口，
    # 消除同文件两套防护强度差异
    run_id = transfer_service.try_start_run(
        "project_import", new_pid, title, dedupe="kind")
    if run_id is None:
        raise HTTPException(status_code=409, detail="An import is already running")

    async def _bg():
        def _on_progress(p, m):
            transfer_service.update_run(run_id, progress=p, message=m)
        try:
            await asyncio.to_thread(
                importer.run_import, db, get_data_dir(), body.source_path, new_pid,
                _on_progress)
        except Exception as e:
            transfer_service.finish_run(run_id)
            activity_events.fail_transfer(db, "project_import", run_id,
                                          project_id=new_pid, entity_title=title,
                                          error_message=str(e))
            return
        transfer_service.finish_run(run_id)
        activity_events.complete_transfer(db, "project_import", run_id,
                                          project_id=new_pid, entity_title=title)

    _bg_task = asyncio.create_task(_bg())
    _TRANSFER_BG_TASKS.add(_bg_task)   # 强引用防 GC 中途回收（Review m4）
    _bg_task.add_done_callback(_TRANSFER_BG_TASKS.discard)
    return {"project_id": new_pid, "activity_id": run_id}


# ── File Management ──


@router.delete("/projects/{project_id}/files/{file_id}")
async def delete_file(project_id: str, file_id: str):
    """Delete a single file and all its data."""
    try:
        db = get_db()
        data_dir = get_data_dir()
        vs = VectorStore(str(Path(data_dir) / "chroma"))
        fts = FTSStore(db)
        from knowledge.file_cleanup import cleanup_file_data
        await cleanup_file_data(
            db, vs, fts, file_id, project_id, data_dir,
            remove_file_record=True,
        )
        # Wiki notification: file deleted → increment pending compile count
        try:
            db.increment_pending_compile_count(project_id)
        except Exception:
            pass
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{project_id}/files/{file_id}/retry")
async def retry_file(project_id: str, file_id: str):
    """Retry processing a failed file."""
    # 事前准入（§7.3 第三层 v3.7）：与 upload 同等拦截，try 块外调用
    ensure_ready("file_parsing")
    try:
        import deps
        db = get_db()
        data_dir = get_data_dir()
        vs = VectorStore(str(Path(data_dir) / "chroma"))
        fts = FTSStore(db)
        from knowledge.file_cleanup import cleanup_file_data
        await cleanup_file_data(
            db, vs, fts, file_id, project_id, data_dir,
            remove_file_record=False,
        )
        db.reset_file_for_retry(file_id)
        if deps.worker:
            await deps.worker.enqueue_file(file_id)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{project_id}/files/retry-failed")
async def retry_failed_files(project_id: str):
    """Retry all failed files in a project."""
    # 事前准入（§7.3 第三层 v3.7）：try 块外调用
    ensure_ready("file_parsing")
    try:
        import deps
        db = get_db()
        data_dir = get_data_dir()
        vs = VectorStore(str(Path(data_dir) / "chroma"))
        fts = FTSStore(db)
        from knowledge.file_cleanup import cleanup_file_data

        cursor = db.conn.execute(
            "SELECT id FROM files WHERE project_id = ? AND status = 'error'",
            (project_id,),
        )
        ids = [r["id"] for r in cursor.fetchall()]
        for fid in ids:
            await cleanup_file_data(
                db, vs, fts, fid, project_id, data_dir,
                remove_file_record=False,
            )
            db.reset_file_for_retry(fid)
            if deps.worker:
                await deps.worker.enqueue_file(fid)
        return {"success": True, "retried_count": len(ids)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class BatchFileRequest(BaseModel):
    file_ids: list[str]


@router.post("/projects/{project_id}/files/batch-delete")
async def batch_delete_files(project_id: str, req: BatchFileRequest):
    """Batch delete multiple files."""
    try:
        db = get_db()
        data_dir = get_data_dir()
        vs = VectorStore(str(Path(data_dir) / "chroma"))
        fts = FTSStore(db)
        from knowledge.file_cleanup import cleanup_file_data

        deleted = 0
        for fid in req.file_ids:
            try:
                await cleanup_file_data(
                    db, vs, fts, fid, project_id, data_dir,
                    remove_file_record=True,
                )
                deleted += 1
            except Exception:
                pass
        # Wiki notification: files deleted → increment pending compile count
        if deleted > 0:
            try:
                db.increment_pending_compile_count(project_id, deleted)
            except Exception:
                pass
        return {"success": True, "deleted_count": deleted}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{project_id}/files/batch-reprocess")
async def batch_reprocess_files(project_id: str, req: BatchFileRequest):
    """Batch reprocess multiple files."""
    # 事前准入（§7.3 第三层 v3.7）：try 块外调用
    ensure_ready("file_parsing")
    try:
        import deps
        db = get_db()
        data_dir = get_data_dir()
        vs = VectorStore(str(Path(data_dir) / "chroma"))
        fts = FTSStore(db)
        from knowledge.file_cleanup import cleanup_file_data

        reprocessed = 0
        for fid in req.file_ids:
            try:
                await cleanup_file_data(
                    db, vs, fts, fid, project_id, data_dir,
                    remove_file_record=False,
                )
                db.reset_file_for_retry(fid)
                if deps.worker:
                    await deps.worker.enqueue_file(fid)
                reprocessed += 1
            except Exception:
                pass
        return {"success": True, "reprocessed_count": reprocessed}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Image Management ──


@router.get("/projects/{project_id}/images")
async def get_project_images(
    project_id: str,
    limit: int = 40,
    offset: int = 0,
    file_id: Optional[str] = None,
    status: Optional[str] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
    size: Optional[str] = None,
):
    """Get all images for a project with pagination and server-side filtering."""
    try:
        db = get_db()

        # Base WHERE = project + file + search
        base_clauses = ["f.project_id = ?"]
        base_params = [project_id]
        if file_id:
            base_clauses.append("i.file_id = ?")
            base_params.append(file_id)
        if search and search.strip():
            q = f"%{_escape_like(search.strip())}%"
            base_clauses.append("(i.vlm_description LIKE ? ESCAPE '\\' OR i.caption LIKE ? ESCAPE '\\' OR f.filename LIKE ? ESCAPE '\\')")
            base_params.extend([q, q, q])

        # Category filter clauses (for status_counts: base + category + size)
        cat_filter_clauses = list(base_clauses)
        cat_filter_params = list(base_params)
        if category and category != 'all':
            if category == 'uncategorized':
                cat_filter_clauses.append("(i.category IS NULL OR i.category = 'uncategorized')")
            else:
                cat_filter_clauses.append("i.category = ?")
                cat_filter_params.append(category)
        if size and size != 'all':
            if size == 'small':
                cat_filter_clauses.append("MAX(COALESCE(i.width_px, 0), COALESCE(i.height_px, 0)) < 200")
            elif size == 'medium':
                cat_filter_clauses.append("MAX(COALESCE(i.width_px, 0), COALESCE(i.height_px, 0)) >= 200")
                cat_filter_clauses.append("MAX(COALESCE(i.width_px, 0), COALESCE(i.height_px, 0)) < 800")
            elif size == 'large':
                cat_filter_clauses.append("MAX(COALESCE(i.width_px, 0), COALESCE(i.height_px, 0)) >= 800")
        status_count_where = " AND ".join(cat_filter_clauses)

        # Status filter clauses (for category_counts: base + status + size)
        status_filter_clauses = list(base_clauses)
        status_filter_params = list(base_params)
        if status:
            if status == 'active':
                status_filter_clauses.append("i.status IN ('pending','processing','done')")
            elif status != 'all':
                status_filter_clauses.append("i.status = ?")
                status_filter_params.append(status)
        if size and size != 'all':
            if size == 'small':
                status_filter_clauses.append("MAX(COALESCE(i.width_px, 0), COALESCE(i.height_px, 0)) < 200")
            elif size == 'medium':
                status_filter_clauses.append("MAX(COALESCE(i.width_px, 0), COALESCE(i.height_px, 0)) >= 200")
                status_filter_clauses.append("MAX(COALESCE(i.width_px, 0), COALESCE(i.height_px, 0)) < 800")
            elif size == 'large':
                status_filter_clauses.append("MAX(COALESCE(i.width_px, 0), COALESCE(i.height_px, 0)) >= 800")
        cat_count_where = " AND ".join(status_filter_clauses)

        # Full WHERE = base + status + category + size (for main query and total)
        full_clauses = list(cat_filter_clauses)
        full_params = list(cat_filter_params)
        if status:
            if status == 'active':
                full_clauses.append("i.status IN ('pending','processing','done')")
            elif status != 'all':
                full_clauses.append("i.status = ?")
                full_params.append(status)
        full_where_sql = " AND ".join(full_clauses)

        cursor = db.conn.execute(
            f"""SELECT i.id, i.file_id, i.page_no, i.caption, i.local_path,
                      i.vlm_description, i.status, i.context_text,
                      i.error_message, i.category, i.width_px, i.height_px,
                      i.created_at, f.filename, f.file_size
               FROM images i
               JOIN files f ON f.id = i.file_id
               WHERE {full_where_sql}
               ORDER BY CASE WHEN i.status IN ('pending','processing') THEN 0 ELSE 1 END,
                        i.created_at DESC
               LIMIT ? OFFSET ?""",
            full_params + [limit + 1, offset],
        )
        rows = [dict(r) for r in cursor.fetchall()]
        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        # Total count (with status + category filters)
        count_cursor = db.conn.execute(
            f"""SELECT COUNT(*) as cnt FROM images i
               JOIN files f ON f.id = i.file_id
               WHERE {full_where_sql}""",
            full_params,
        )
        total = count_cursor.fetchone()["cnt"]

        # Status counts (base + category filter, NOT status filter)
        status_cursor = db.conn.execute(
            f"""SELECT i.status, COUNT(*) as cnt
               FROM images i
               JOIN files f ON f.id = i.file_id
               WHERE {status_count_where}
               GROUP BY i.status""",
            cat_filter_params,
        )
        status_counts = {r["status"]: r["cnt"] for r in status_cursor.fetchall()}

        # Category counts (base + status filter, NOT category filter)
        cat_cursor = db.conn.execute(
            f"""SELECT COALESCE(i.category, 'uncategorized') as category, COUNT(*) as cnt
               FROM images i
               JOIN files f ON f.id = i.file_id
               WHERE {cat_count_where}
               GROUP BY COALESCE(i.category, 'uncategorized')""",
            status_filter_params,
        )
        category_counts = {r["category"]: r["cnt"] for r in cat_cursor.fetchall()}

        return {
            "images": [
                {
                    "id": r["id"],
                    "file_id": r["file_id"],
                    "filename": r["filename"],
                    "page_no": r["page_no"],
                    "caption": r["caption"],
                    "local_path": r["local_path"],
                    "vlm_description": r["vlm_description"],
                    "status": r.get("status", "pending"),
                    "error_message": r.get("error_message"),
                    "context_text": r["context_text"],
                    "category": r.get("category", "uncategorized"),
                    "width_px": r.get("width_px"),
                    "height_px": r.get("height_px"),
                    "file_size": r.get("file_size"),
                    "created_at": r["created_at"],
                }
                for r in rows
            ],
            "total": total,
            "has_more": has_more,
            "status_counts": status_counts,
            "category_counts": category_counts,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects/{project_id}/files/{file_id}/images")
async def get_file_images(project_id: str, file_id: str):
    """Get all images for a file with their status."""
    try:
        db = get_db()
        images = db.list_images_by_file(file_id)
        return {
            "images": [
                {
                    "id": i["id"],
                    "page_no": i["page_no"],
                    "caption": i["caption"],
                    "local_path": i["local_path"],
                    "vlm_description": i["vlm_description"],
                    "context_text": i["context_text"],
                    "status": i.get("status", "pending"),
                    "error_message": i.get("error_message"),
                    "category": i.get("category", "uncategorized"),
                    "created_at": i["created_at"],
                }
                for i in images
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/projects/{project_id}/images/{image_id}")
async def delete_image(project_id: str, image_id: str):
    """Delete a single image and its data."""
    try:
        db = get_db()
        data_dir = get_data_dir()
        img = db.get_image(image_id)
        if not img:
            raise HTTPException(status_code=404, detail="Image not found")
        file_id = img["file_id"]

        # Delete ChromaDB chunk（按 metadata.image_id 过滤，旧格式存量向量一并删净）
        try:
            vs = VectorStore(str(Path(data_dir) / "chroma"))
            vs.delete_by_image_id(project_id, image_id)
        except Exception:
            pass

        # Delete image file from disk
        img_path = Path(img["local_path"])
        if img_path.exists():
            img_path.unlink()

        # Delete DB record and update file image status
        db.delete_image(image_id)
        db.update_file_image_status(file_id)
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ImageCategoryUpdate(BaseModel):
    category: str


@router.post("/projects/{project_id}/images/{image_id}/retry")
async def retry_image(project_id: str, image_id: str, force: bool = False):
    """Retry LLM description for a failed/archived image.
    
    Args:
        force: If True, skip small-image check and force LLM description.
    """
    try:
        import deps
        db = get_db()
        data_dir = get_data_dir()
        
        # Validate LLM config before enqueueing
        _validate_llm_config(db)
        
        img = db.get_image(image_id)
        if not img:
            raise HTTPException(status_code=404, detail="Image not found")
        file_id = img["file_id"]

        # Clean up existing ChromaDB chunk（按 metadata.image_id 过滤，旧格式存量向量一并删净）
        try:
            vs = VectorStore(str(Path(data_dir) / "chroma"))
            vs.delete_by_image_id(project_id, image_id)
        except Exception:
            pass

        db.update_image_status(image_id, "pending", error_message=None, clear_vlm_description=True)
        if deps.worker:
            await deps.worker.enqueue_image(image_id, force=force)
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/projects/{project_id}/images/{image_id}/category")
async def update_image_category(project_id: str, image_id: str, body: ImageCategoryUpdate):
    """Update the manual category of an image."""
    try:
        db = get_db()
        img = db.get_image(image_id)
        if not img:
            raise HTTPException(status_code=404, detail="Image not found")
        db.update_image_category(image_id, body.category)
        return {"success": True, "category": body.category}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{project_id}/images/{image_id}/archive")
async def archive_image(project_id: str, image_id: str):
    """Manually archive an image. 
    
    Archived images are excluded from LLM description and vector search.
    If the image was already processed (done), its vector is deleted from ChromaDB.
    """
    try:
        db = get_db()
        data_dir = get_data_dir()
        img = db.get_image(image_id)
        if not img:
            raise HTTPException(status_code=404, detail="Image not found")
        file_id = img["file_id"]

        # If image was processed (done), delete its vector from ChromaDB
        # （按 metadata.image_id 过滤，旧格式存量向量一并删净）
        if img.get("status") == "done":
            try:
                vs = VectorStore(str(Path(data_dir) / "chroma"))
                vs.delete_by_image_id(project_id, image_id)
            except Exception:
                pass

        db.update_image_status(
            image_id, "archived",
            # D1 错误契约：落库协议串，前端 translateStoredError 还原为当前语言
            error_message="INKCUBE_CODE:MANUALLY_ARCHIVED",
            clear_vlm_description=True,
        )
        db.update_file_image_status(file_id)
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{project_id}/files/{file_id}/images/retry-failed")
async def retry_failed_images(project_id: str, file_id: str):
    """Retry all failed images for a file."""
    try:
        import deps
        db = get_db()
        data_dir = get_data_dir()
        
        # Validate LLM config before enqueueing
        _validate_llm_config(db)
        
        vs = VectorStore(str(Path(data_dir) / "chroma"))

        cursor = db.conn.execute(
            "SELECT id, page_no, status FROM images WHERE file_id = ? AND status IN ('error', 'archived')",
            (file_id,),
        )
        images = [dict(r) for r in cursor.fetchall()]
        for img in images:
            try:
                vs.delete_by_image_id(project_id, img["id"])
            except Exception:
                pass
            db.update_image_status(img["id"], "pending", error_message=None, clear_vlm_description=True)
            if deps.worker:
                # Archived images need force=True to bypass small-image auto-archive
                force = img["status"] == "archived"
                await deps.worker.enqueue_image(img["id"], force=force)
        return {"success": True, "retried_count": len(images)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Base Library ──


@router.get("/base-libs")
async def list_base_libs(
    limit: int = Query(50, ge=0, description="Page size; 0 = return all"),
    offset: int = Query(0, ge=0, description="Rows to skip"),
):
    """List all base libraries."""
    try:
        db = get_db()
        # limit+1 探测 has_more（list_tasks 同款，免 COUNT）
        libs = db.list_base_libs(
            limit=limit + 1 if limit > 0 else 0, offset=offset)
        has_more = limit > 0 and len(libs) > limit
        if has_more:
            libs = libs[:limit]
        total = db.conn.execute(
            "SELECT COUNT(*) AS cnt FROM projects WHERE kind = 'base_lib'"
        ).fetchone()["cnt"]
        return {"base_libs": libs, "has_more": has_more, "total": total}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Base Library File Management ──


@router.get("/base-libs/{lib_id}/files")
async def get_base_lib_files(
    lib_id: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = 0,
    status: Optional[str] = None,
    file_type: Optional[str] = None,
    filename_search: Optional[str] = None,
):
    """Get files in a base library with offset pagination, status/type filters, and filename search."""
    try:
        db = get_db()
        await _validate_base_lib_exists(db, lib_id)

        where_clauses = ["f.project_id = ?"]
        params: list = [lib_id]
        counts_clauses = ["f.project_id = ?"]
        counts_params: list = [lib_id]
        status_clause, status_params = _file_status_clause(status)
        if status_clause:
            where_clauses.append(status_clause)
            params.extend(status_params)
        type_clause, type_params = _file_type_clause(file_type)
        if type_clause:
            where_clauses.append(type_clause)
            params.extend(type_params)
            counts_clauses.append(type_clause)
            counts_params.extend(type_params)
        if filename_search:
            where_clauses.append("f.filename LIKE ? ESCAPE '\\'")
            like = f"%{_escape_like(filename_search)}%"
            params.append(like)
            counts_clauses.append("f.filename LIKE ? ESCAPE '\\'")
            counts_params.append(like)
        where_sql = " AND ".join(where_clauses)
        counts_where_sql = " AND ".join(counts_clauses)

        # Offset-based pagination（占位符顺序 LIMIT ? OFFSET ? → 先 append limit+1 再 append offset）
        sql = f"""SELECT f.id, f.filename, f.file_type, f.status, f.progress,
                       f.chunk_count, f.image_count, f.image_status,
                       f.image_described_count, f.error_message, f.created_at
                FROM files f WHERE {where_sql}
                ORDER BY CASE WHEN f.status IN ('pending','parsing') THEN 0 ELSE 1 END,
                         f.created_at DESC, f.id DESC LIMIT ?"""
        params.append(limit + 1)  # fetch one extra to detect has_more
        if offset > 0:
            sql += " OFFSET ?"
            params.append(offset)

        cursor = db.conn.execute(sql, params)
        rows = [dict(r) for r in cursor.fetchall()]
        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        # Total count（筛选同口径，供分页器计算总页数）
        total_params: list = [lib_id] + status_params + type_params
        if filename_search:
            total_params.append(f"%{_escape_like(filename_search)}%")
        total = db.conn.execute(
            f"SELECT COUNT(*) AS cnt FROM files f WHERE {where_sql}", total_params
        ).fetchone()["cnt"]

        # 状态分布（搜索/类型口径，不含状态筛选）——供前端筛选 Tab 计数
        status_counts = {
            r["status"]: r["cnt"]
            for r in db.conn.execute(
                f"SELECT f.status, COUNT(*) AS cnt FROM files f WHERE {counts_where_sql} GROUP BY f.status",
                counts_params,
            ).fetchall()
        }

        return {
            "files": [{
                "id": r["id"], "filename": r["filename"], "file_type": r["file_type"],
                "status": r["status"], "progress": r.get("progress", 0),
                "chunks_count": r["chunk_count"], "images_count": r["image_count"],
                "image_status": r.get("image_status", "none"),
                "image_described_count": r.get("image_described_count", 0),
                "error_message": r.get("error_message"), "created_at": r["created_at"],
            } for r in rows],
            "has_more": has_more,
            "total": total,
            "status_counts": status_counts,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/base-libs/{lib_id}/upload")
async def upload_base_lib_file(lib_id: str, upload: FileUpload):
    """Upload a file to a base library."""
    # 准入前置校验（0.4 §7.3）：与项目侧 upload 同等拦截，缺解析模型时结构化报错
    ensure_ready("file_parsing")
    try:
        import deps
        db = get_db()
        await _validate_base_lib_exists(db, lib_id)
        return await _upload_core(lib_id, upload, db, get_data_dir(), deps.worker)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/base-libs/{lib_id}/files/active")
async def get_base_lib_active_files(lib_id: str):
    """Get active (non-terminal) files in a base library."""
    try:
        db = get_db()
        await _validate_base_lib_exists(db, lib_id)
        cursor = db.conn.execute(
            "SELECT id, filename, status, progress, image_status, "
            "image_described_count, image_count, error_message "
            "FROM files WHERE project_id = ? AND "
            "(status IN ('pending','parsing') OR "
            "image_status IN ('pending','describing','error')) "
            "ORDER BY created_at ASC",
            (lib_id,),
        )
        return {"files": [dict(r) for r in cursor.fetchall()]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/base-libs/{lib_id}/files/{file_id}")
async def delete_base_lib_file(lib_id: str, file_id: str):
    """Delete a file from a base library."""
    try:
        db = get_db()
        await _validate_base_lib_exists(db, lib_id)
        data_dir = get_data_dir()
        from knowledge.file_cleanup import cleanup_file_data
        await cleanup_file_data(
            db, VectorStore(str(Path(data_dir) / "chroma")),
            FTSStore(db), file_id, lib_id, data_dir,
            remove_file_record=True,
        )
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/base-libs/{lib_id}/files/{file_id}/retry")
async def retry_base_lib_file(lib_id: str, file_id: str):
    """Retry a failed file in a base library."""
    # 事前准入（§7.3 第三层 v3.7）：与 upload 同等拦截，try 块外调用
    ensure_ready("file_parsing")
    try:
        import deps
        db = get_db()
        await _validate_base_lib_exists(db, lib_id)
        data_dir = get_data_dir()
        from knowledge.file_cleanup import cleanup_file_data
        await cleanup_file_data(
            db, VectorStore(str(Path(data_dir) / "chroma")),
            FTSStore(db), file_id, lib_id, data_dir,
            remove_file_record=False,
        )
        db.reset_file_for_retry(file_id)
        if deps.worker:
            await deps.worker.enqueue_file(file_id)
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/base-libs/{lib_id}/recent-chunks")
async def get_base_lib_recent_chunks(lib_id: str, limit: int = 20, file_id: Optional[str] = None):
    """Get the most recent chunks for a base library."""
    try:
        db = get_db()
        await _validate_base_lib_exists(db, lib_id)
        where_clauses = ["project_id = ?"]
        params = [lib_id]
        if file_id:
            safe_id = _escape_like(file_id)
            where_clauses.append("(chunk_id LIKE ? ESCAPE '\\' OR chunk_id LIKE ? ESCAPE '\\')")
            params.extend([f"chunk_{safe_id}%", f"img_{safe_id}%"])
        where_sql = " AND ".join(where_clauses)
        cursor = db.conn.execute(
            f"""SELECT chunk_id, content, filename, content_type
               FROM chunks_fts WHERE {where_sql}
               ORDER BY rowid DESC LIMIT ?""",
            params + [limit],
        )
        chunks = [{"id": r["chunk_id"], "content": r["content"], "_score": 0,
                    "metadata": {"filename": r["filename"], "content_type": r["content_type"]}}
                   for r in cursor.fetchall()]
        _backfill_markdown_content(chunks, lib_id)
        return {"results": chunks}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/base-libs/{lib_id}/files/retry-failed")
async def retry_base_lib_failed_files(lib_id: str):
    """Retry all failed files in a base library."""
    # 事前准入（§7.3 第三层 v3.7）：try 块外调用
    ensure_ready("file_parsing")
    try:
        import deps
        db = get_db()
        await _validate_base_lib_exists(db, lib_id)
        data_dir = get_data_dir()
        vs = VectorStore(str(Path(data_dir) / "chroma"))
        fts = FTSStore(db)
        from knowledge.file_cleanup import cleanup_file_data
        cursor = db.conn.execute(
            "SELECT id FROM files WHERE project_id = ? AND status = 'error'", (lib_id,),
        )
        ids = [r["id"] for r in cursor.fetchall()]
        for fid in ids:
            await cleanup_file_data(db, vs, fts, fid, lib_id, data_dir, remove_file_record=False)
            db.reset_file_for_retry(fid)
            if deps.worker:
                await deps.worker.enqueue_file(fid)
        return {"success": True, "retried_count": len(ids)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/base-libs/{lib_id}/files/batch-delete")
async def batch_delete_base_lib_files(lib_id: str, req: BatchFileRequest):
    """Batch delete multiple files in a base library."""
    try:
        db = get_db()
        await _validate_base_lib_exists(db, lib_id)
        data_dir = get_data_dir()
        vs = VectorStore(str(Path(data_dir) / "chroma"))
        fts = FTSStore(db)
        from knowledge.file_cleanup import cleanup_file_data
        deleted = 0
        for fid in req.file_ids:
            try:
                await cleanup_file_data(db, vs, fts, fid, lib_id, data_dir, remove_file_record=True)
                deleted += 1
            except Exception:
                pass
        return {"success": True, "deleted_count": deleted}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/base-libs/{lib_id}/files/batch-reprocess")
async def batch_reprocess_base_lib_files(lib_id: str, req: BatchFileRequest):
    """Batch reprocess multiple files in a base library."""
    # 事前准入（§7.3 第三层 v3.7）：try 块外调用
    ensure_ready("file_parsing")
    try:
        import deps
        db = get_db()
        await _validate_base_lib_exists(db, lib_id)
        data_dir = get_data_dir()
        vs = VectorStore(str(Path(data_dir) / "chroma"))
        fts = FTSStore(db)
        from knowledge.file_cleanup import cleanup_file_data
        reprocessed = 0
        for fid in req.file_ids:
            try:
                await cleanup_file_data(db, vs, fts, fid, lib_id, data_dir, remove_file_record=False)
                db.reset_file_for_retry(fid)
                if deps.worker:
                    await deps.worker.enqueue_file(fid)
                reprocessed += 1
            except Exception:
                pass
        return {"success": True, "reprocessed_count": reprocessed}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Base Library Search & Chat ──


@router.post("/base-libs/{lib_id}/search")
async def search_base_lib(lib_id: str, request: SearchRequest):
    """Search within a base library."""
    # 准入前置校验（0.4 §7.3）：与项目侧 /search 同等拦截，缺嵌入模型时结构化报错
    ensure_ready("knowledge_search")
    try:
        db = get_db()
        await _validate_base_lib_exists(db, lib_id)
        data_dir = get_data_dir()
        search_tool = KnowledgeSearchTool(
            db=db,
            chroma_persist_dir=str(Path(data_dir) / "chroma"),
            models_dir=str(Path(data_dir) / "models"),
        )
        results = await search_tool.search(
            query=request.query, project_id=lib_id,
            top_k=request.topK, content_type_filter=request.contentTypeFilter,
            file_id=request.fileId,
        )
        return {"results": results}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/base-libs/{lib_id}/chat")
async def chat_base_lib(lib_id: str, request: ChatRequest):
    """RAG chat within a base library."""
    # 事前准入（§7.3 第三层 v3.7）：组合入口，embedding + LLM 双依赖；try 块外调用
    ensure_ready("knowledge_search")
    ensure_ready("task")
    try:
        db = get_db()
        await _validate_base_lib_exists(db, lib_id)
        data_dir = get_data_dir()
        from knowledge.rag import KnowledgeRAG
        rag = KnowledgeRAG(
            db=db,
            chroma_persist_dir=str(Path(data_dir) / "chroma"),
            models_dir=str(Path(data_dir) / "models"),
        )
        import deps
        settings = deps.db.get_settings() if hasattr(deps.db, 'get_settings') else {}
        return await rag.ask(
            project_id=lib_id, message=request.message,
            file_id=request.fileId, settings=settings,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/base-libs/{lib_id}/images")
async def get_base_lib_images(
    lib_id: str, limit: int = 40, offset: int = 0,
    file_id: Optional[str] = None,
    status: Optional[str] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
    size: Optional[str] = None,
):
    """Get images in a base library with server-side filtering."""
    try:
        db = get_db()
        await _validate_base_lib_exists(db, lib_id)
        # Base WHERE = project + file + search
        base_clauses = ["f.project_id = ?"]
        base_params = [lib_id]
        if file_id:
            base_clauses.append("i.file_id = ?")
            base_params.append(file_id)
        if search and search.strip():
            q = f"%{_escape_like(search.strip())}%"
            base_clauses.append("(i.vlm_description LIKE ? ESCAPE '\\' OR i.caption LIKE ? ESCAPE '\\' OR f.filename LIKE ? ESCAPE '\\')")
            base_params.extend([q, q, q])

        # Category filter clauses (for status_counts: base + category + size)
        cat_filter_clauses = list(base_clauses)
        cat_filter_params = list(base_params)
        if category and category != 'all':
            if category == 'uncategorized':
                cat_filter_clauses.append("(i.category IS NULL OR i.category = 'uncategorized')")
            else:
                cat_filter_clauses.append("i.category = ?")
                cat_filter_params.append(category)
        if size and size != 'all':
            if size == 'small':
                cat_filter_clauses.append("MAX(COALESCE(i.width_px, 0), COALESCE(i.height_px, 0)) < 200")
            elif size == 'medium':
                cat_filter_clauses.append("MAX(COALESCE(i.width_px, 0), COALESCE(i.height_px, 0)) >= 200")
                cat_filter_clauses.append("MAX(COALESCE(i.width_px, 0), COALESCE(i.height_px, 0)) < 800")
            elif size == 'large':
                cat_filter_clauses.append("MAX(COALESCE(i.width_px, 0), COALESCE(i.height_px, 0)) >= 800")
        status_count_where = " AND ".join(cat_filter_clauses)

        # Status filter clauses (for category_counts: base + status + size)
        status_filter_clauses = list(base_clauses)
        status_filter_params = list(base_params)
        if status:
            if status == 'active':
                status_filter_clauses.append("i.status IN ('pending','processing','done')")
            elif status != 'all':
                status_filter_clauses.append("i.status = ?")
                status_filter_params.append(status)
        if size and size != 'all':
            if size == 'small':
                status_filter_clauses.append("MAX(COALESCE(i.width_px, 0), COALESCE(i.height_px, 0)) < 200")
            elif size == 'medium':
                status_filter_clauses.append("MAX(COALESCE(i.width_px, 0), COALESCE(i.height_px, 0)) >= 200")
                status_filter_clauses.append("MAX(COALESCE(i.width_px, 0), COALESCE(i.height_px, 0)) < 800")
            elif size == 'large':
                status_filter_clauses.append("MAX(COALESCE(i.width_px, 0), COALESCE(i.height_px, 0)) >= 800")
        cat_count_where = " AND ".join(status_filter_clauses)

        # Full WHERE = base + status + category
        full_clauses = list(cat_filter_clauses)
        full_params = list(cat_filter_params)
        if status:
            if status == 'active':
                full_clauses.append("i.status IN ('pending','processing','done')")
            elif status != 'all':
                full_clauses.append("i.status = ?")
                full_params.append(status)
        full_where_sql = " AND ".join(full_clauses)

        cursor = db.conn.execute(
            f"""SELECT i.id, i.file_id, i.page_no, i.caption, i.local_path,
                      i.vlm_description, i.status, i.context_text,
                      i.error_message, i.category, i.width_px, i.height_px,
                      i.created_at, f.filename, f.file_size
               FROM images i JOIN files f ON f.id = i.file_id
               WHERE {full_where_sql}
               ORDER BY CASE WHEN i.status IN ('pending','processing') THEN 0 ELSE 1 END,
                        i.created_at DESC LIMIT ? OFFSET ?""",
            full_params + [limit + 1, offset],
        )
        rows = [dict(r) for r in cursor.fetchall()]
        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]
        count_cursor = db.conn.execute(
            f"SELECT COUNT(*) as cnt FROM images i JOIN files f ON f.id = i.file_id WHERE {full_where_sql}",
            full_params,
        )
        total = count_cursor.fetchone()["cnt"]
        # Status counts (base + category filter, NOT status filter)
        status_cursor = db.conn.execute(
            f"SELECT i.status, COUNT(*) as cnt FROM images i JOIN files f ON f.id = i.file_id WHERE {status_count_where} GROUP BY i.status",
            cat_filter_params,
        )
        status_counts = {r["status"]: r["cnt"] for r in status_cursor.fetchall()}
        # Category counts (base + status filter, NOT category filter)
        cat_cursor = db.conn.execute(
            f"""SELECT COALESCE(i.category, 'uncategorized') as category, COUNT(*) as cnt
               FROM images i JOIN files f ON f.id = i.file_id
               WHERE {cat_count_where} GROUP BY COALESCE(i.category, 'uncategorized')""",
            status_filter_params,
        )
        category_counts = {r["category"]: r["cnt"] for r in cat_cursor.fetchall()}
        return {
            "images": [{
                "id": r["id"], "file_id": r["file_id"], "filename": r["filename"],
                "page_no": r["page_no"], "caption": r["caption"],
                "local_path": r["local_path"], "vlm_description": r["vlm_description"],
                "status": r.get("status", "pending"), "error_message": r.get("error_message"),
                "context_text": r["context_text"], "category": r.get("category", "uncategorized"),
                "width_px": r.get("width_px"), "height_px": r.get("height_px"),
                "file_size": r.get("file_size"), "created_at": r["created_at"],
            } for r in rows],
            "total": total, "has_more": has_more,
            "status_counts": status_counts, "category_counts": category_counts,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Project-BaseLib Association ──


@router.get("/projects/{project_id}/base-libs")
async def get_project_base_libs(project_id: str):
    """Get base libraries linked to a project."""
    try:
        db = get_db()
        libs = db.get_project_base_libs(project_id)
        return {"base_libs": libs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class LinkBaseLibRequest(BaseModel):
    base_lib_id: str


@router.post("/projects/{project_id}/base-libs")
async def link_project_base_lib(project_id: str, body: LinkBaseLibRequest):
    """Link a base library to a project."""
    try:
        db = get_db()
        db.link_project_to_base_lib(project_id, body.base_lib_id)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/projects/{project_id}/base-libs/{base_lib_id}")
async def unlink_project_base_lib(project_id: str, base_lib_id: str):
    """Unlink a base library from a project."""
    try:
        db = get_db()
        db.unlink_project_from_base_lib(project_id, base_lib_id)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
