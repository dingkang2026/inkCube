"""python/routers/knowledge.py"""
import os
import sys
import base64
import logging
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

from db.database import Database
from knowledge.vector_store import VectorStore
from knowledge.fts_store import FTSStore
from knowledge.knowledge_search import KnowledgeSearchTool
from errors import InkCubeError, VLMNotConfigured


router = APIRouter(prefix="/knowledge", tags=["knowledge"])


def get_db() -> Database:
    """Get database instance from deps"""
    try:
        import deps
        return deps.db
    except ImportError:
        raise HTTPException(status_code=500, detail="Database not initialized")


def get_data_dir() -> str:
    """Get data directory path"""
    if os.environ.get("INKCUBE_DATA_DIR"):
        return os.environ.get("INKCUBE_DATA_DIR")
    if getattr(sys, "frozen", False):
        from platformdirs import user_data_dir
        return os.path.join(user_data_dir("InkCube", "InkCube"), "data")
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


def _validate_vlm_config(db: Database) -> None:
    """Validate VLM API configuration exists in database.
    
    Raises VLMNotConfigured (InkCubeError) if VLM is not configured.
    The global exception handler converts it to a proper HTTP response.
    """
    cursor = db.conn.execute(
        "SELECT key, value FROM settings WHERE key IN ('vlm_api_url', 'vlm_api_key')"
    )
    settings = {}
    for row in cursor.fetchall():
        import json
        val = row["value"]
        try:
            settings[row["key"]] = json.loads(val)
        except (json.JSONDecodeError, TypeError):
            settings[row["key"]] = val
    
    if not settings.get("vlm_api_url") or not settings.get("vlm_api_key"):
        raise VLMNotConfigured()


async def _validate_base_lib_exists(db: Database, lib_id: str):
    """Raise 404 if the base library doesn't exist."""
    lib = db.get_base_lib(lib_id)
    if not lib:
        raise HTTPException(status_code=404, detail="Base library not found")
    return lib


async def _base_lib_upload_core(lib_id: str, upload: FileUpload, db, data_dir, worker):
    """Core upload logic for base library files (shared across base lib routes)."""
    import uuid
    import hashlib
    file_id = f"file_{uuid.uuid4().hex[:8]}"

    files_dir = Path(data_dir) / "files" / lib_id / file_id
    files_dir.mkdir(parents=True, exist_ok=True)
    file_path = files_dir / upload.filename

    file_data = base64.b64decode(
        upload.data.split(",")[1] if "," in upload.data else upload.data
    )
    file_path.write_bytes(file_data)

    file_size = len(file_data)
    file_hash = hashlib.md5(file_data).hexdigest()

    existing = db.find_duplicate_file(lib_id, file_hash)
    if existing:
        import shutil
        shutil.rmtree(files_dir, ignore_errors=True)
        return {
            "success": True, "file_id": existing["id"],
            "chunks_count": existing.get("chunk_count", 0),
            "images_count": existing.get("image_count", 0),
            "duplicate": True, "message": f"文件已存在: {existing['filename']}",
        }

    db.create_file(
        project_id=lib_id, filename=upload.filename,
        file_type=upload.fileType, file_size=file_size,
        file_hash=file_hash, file_id=file_id,
    )

    if worker:
        logger.info("Enqueuing base lib file %s for processing", file_id)
        await worker.enqueue_file(file_id)
    else:
        logger.warning("Worker not available! Base lib file %s will be picked up by queue scanner", file_id)

    return {
        "success": True, "file_id": file_id,
        "chunks_count": 0, "images_count": 0,
        "error": None, "queued": True,
    }


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


@router.get("/projects")
async def list_projects():
    """List all projects with file and chunk counts"""
    try:
        db = get_db()
        cursor = db.conn.execute("""
            SELECT 
                p.id,
                p.name,
                p.description,
                p.type,
                p.stage,
                p.company_name,
                p.credit_code,
                COUNT(DISTINCT f.id) as file_count,
                COALESCE(SUM(f.chunk_count), 0) as chunk_count,
                p.created_at
            FROM projects p
            LEFT JOIN files f ON f.project_id = p.id
            WHERE p.kind = 'project'
            GROUP BY p.id
            ORDER BY p.created_at DESC
        """)
        
        projects = []
        for row in cursor.fetchall():
            projects.append({
                "id": row["id"],
                "name": row["name"],
                "description": row["description"],
                "type": row.get("type", "general"),
                "stage": row.get("stage", "none"),
                "company_name": row.get("company_name", ""),
                "credit_code": row.get("credit_code", ""),
                "file_count": row["file_count"],
                "chunk_count": row["chunk_count"],
                "created_at": row["created_at"]
            })
        
        return {"projects": projects}
    
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
            "created_at": result["created_at"]
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects/{project_id}/files")
async def get_project_files(
    project_id: str,
    limit: int = 20,
    before_created_at: Optional[str] = None,
    before_id: Optional[str] = None,
    file_type: Optional[str] = None,
    filename_search: Optional[str] = None,
):
    """Get files in a project with cursor-based pagination, type filter, and filename search."""
    try:
        db = get_db()

        # Build query with optional filters
        where_clauses = ["f.project_id = ?"]
        params: list = [project_id]

        if file_type and file_type != "all":
            # Map UI categories to extensions
            type_map = {
                "pdf": [".pdf"],
                "docx": [".docx", ".doc"],
                "pptx": [".pptx", ".ppt"],
                "xlsx": [".xlsx", ".xls"],
                "image": [".png", ".jpg", ".jpeg"],
            }
            extensions = type_map.get(file_type, [])
            if extensions:
                placeholders = ",".join("?" for _ in extensions)
                where_clauses.append(f"f.file_type IN ({placeholders})")
                params.extend(extensions)

        if filename_search:
            where_clauses.append("f.filename LIKE ?")
            params.append(f"%{filename_search}%")

        where_sql = " AND ".join(where_clauses)

        # Cursor-based pagination
        if before_created_at and before_id:
            where_sql += " AND (f.created_at < ? OR (f.created_at = ? AND f.id < ?))"
            params.extend([before_created_at, before_created_at, before_id])

        params.append(limit + 1)  # fetch one extra to detect has_more

        cursor = db.conn.execute(
            f"""SELECT f.id, f.filename, f.file_type, f.status, f.progress,
                       f.chunk_count, f.image_count, f.image_status,
                       f.image_described_count, f.error_message, f.created_at
                FROM files f
                WHERE {where_sql}
                ORDER BY f.created_at DESC, f.id DESC
                LIMIT ?""",
            params,
        )
        rows = [dict(r) for r in cursor.fetchall()]
        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

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
        return {"files": result, "has_more": has_more}
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
    try:
        import deps
        db = get_db()
        data_dir = get_data_dir()

        import uuid
        file_id = f"file_{uuid.uuid4().hex[:8]}"

        # Save file to disk: data/files/{project_id}/{file_id}/
        files_dir = Path(data_dir) / "files" / project_id / file_id
        files_dir.mkdir(parents=True, exist_ok=True)
        file_path = files_dir / upload.filename

        # Decode base64 and save
        file_data = base64.b64decode(
            upload.data.split(",")[1] if "," in upload.data else upload.data
        )
        file_path.write_bytes(file_data)

        # Calculate file size and hash
        import hashlib
        file_size = len(file_data)
        file_hash = hashlib.md5(file_data).hexdigest()

        # Check for duplicate
        existing = db.find_duplicate_file(project_id, file_hash)
        if existing:
            import shutil
            shutil.rmtree(files_dir, ignore_errors=True)
            return {
                "success": True,
                "file_id": existing["id"],
                "chunks_count": existing.get("chunk_count", 0),
                "images_count": existing.get("image_count", 0),
                "error": None,
                "duplicate": True,
                "message": f"文件已存在: {existing['filename']}",
            }

        # Insert file record (status=pending)
        db.create_file(
            project_id=project_id,
            filename=upload.filename,
            file_type=upload.fileType,
            file_size=file_size,
            file_hash=file_hash,
            file_id=file_id,
        )

        # Enqueue for async processing
        if deps.worker:
            logger.info("Enqueuing file %s for processing", file_id)
            await deps.worker.enqueue_file(file_id)
        else:
            logger.warning("Worker not available! File %s will be picked up by queue scanner", file_id)

        return {
            "success": True,
            "file_id": file_id,
            "chunks_count": 0,
            "images_count": 0,
            "error": None,
            "queued": True,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects/{project_id}/recent-chunks")
async def get_recent_chunks(project_id: str, limit: int = 20, file_id: Optional[str] = None):
    """Get the most recent chunks for a project (by insertion order). Optionally filter by file_id."""
    try:
        db = get_db()
        where_clauses = ["project_id = ?"]
        params = [project_id]
        if file_id:
            where_clauses.append("(chunk_id LIKE ? OR chunk_id LIKE ?)")
            params.extend([f"chunk_{file_id}_%", f"img_{file_id}_%"])
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
        return {"results": chunks}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/search")
async def search(request: SearchRequest):
    """Search knowledge base with hybrid retrieval"""
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


@router.patch("/projects/{project_id}")
async def update_project(project_id: str, body: ProjectUpdate):
    """Update project name and/or description."""
    try:
        db = get_db()
        if body.name is None and body.description is None:
            raise HTTPException(status_code=400, detail="No fields to update")
        db.update_project(project_id, name=body.name, description=body.description)
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str):
    """Delete a project and all its data."""
    try:
        db = get_db()
        data_dir = get_data_dir()

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
            import shutil
            shutil.rmtree(files_dir, ignore_errors=True)

        # Delete from database (cascade handles files, images; kind check prevents base_lib deletion)
        db.delete_project(project_id)

        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{project_id}/files/{file_id}/retry")
async def retry_file(project_id: str, file_id: str):
    """Retry processing a failed file."""
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
        return {"success": True, "deleted_count": deleted}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{project_id}/files/batch-reprocess")
async def batch_reprocess_files(project_id: str, req: BatchFileRequest):
    """Batch reprocess multiple files."""
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
):
    """Get all images for a project with pagination. Optionally filter by file_id."""
    try:
        db = get_db()

        where_clauses = ["f.project_id = ?"]
        params = [project_id]
        if file_id:
            where_clauses.append("i.file_id = ?")
            params.append(file_id)
        where_sql = " AND ".join(where_clauses)

        cursor = db.conn.execute(
            f"""SELECT i.id, i.file_id, i.page_no, i.caption, i.local_path,
                      i.vlm_description, i.status, i.context_text,
                      i.error_message, i.category, i.created_at, f.filename
               FROM images i
               JOIN files f ON f.id = i.file_id
               WHERE {where_sql}
               ORDER BY i.created_at DESC
               LIMIT ? OFFSET ?""",
            params + [limit + 1, offset],
        )
        rows = [dict(r) for r in cursor.fetchall()]
        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        # Get total count
        count_cursor = db.conn.execute(
            f"""SELECT COUNT(*) as cnt FROM images i
               JOIN files f ON f.id = i.file_id
               WHERE {where_sql}""",
            params,
        )
        total = count_cursor.fetchone()["cnt"]

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
                    "created_at": r["created_at"],
                }
                for r in rows
            ],
            "total": total,
            "has_more": has_more,
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

        # Delete ChromaDB chunk
        try:
            vs = VectorStore(str(Path(data_dir) / "chroma"))
            vs.delete_by_ids(project_id, [f"img_{file_id}_{img.get('page_no', 0)}"])
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
    """Retry VLM description for a failed/archived image.
    
    Args:
        force: If True, skip small-image check and force VLM parsing.
    """
    try:
        import deps
        db = get_db()
        data_dir = get_data_dir()
        
        # Validate VLM config before enqueueing
        _validate_vlm_config(db)
        
        img = db.get_image(image_id)
        if not img:
            raise HTTPException(status_code=404, detail="Image not found")
        file_id = img["file_id"]

        # Clean up existing ChromaDB chunk
        try:
            vs = VectorStore(str(Path(data_dir) / "chroma"))
            vs.delete_by_ids(project_id, [f"img_{file_id}_{img.get('page_no', 0)}"])
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
    
    Archived images are excluded from VLM processing and vector search.
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
        if img.get("status") == "done":
            try:
                vs = VectorStore(str(Path(data_dir) / "chroma"))
                vs.delete_by_ids(project_id, [f"img_{file_id}_{img.get('page_no', 0)}"])
            except Exception:
                pass

        db.update_image_status(
            image_id, "archived",
            error_message="手动归档",
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
        
        # Validate VLM config before enqueueing
        _validate_vlm_config(db)
        
        vs = VectorStore(str(Path(data_dir) / "chroma"))

        cursor = db.conn.execute(
            "SELECT id, page_no, status FROM images WHERE file_id = ? AND status IN ('error', 'archived')",
            (file_id,),
        )
        images = [dict(r) for r in cursor.fetchall()]
        for img in images:
            try:
                vs.delete_by_ids(project_id, [f"img_{file_id}_{img.get('page_no', 0)}"])
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


# ── Base Library CRUD ──


class BaseLibCreate(BaseModel):
    name: str
    description: Optional[str] = None


class BaseLibUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


@router.get("/base-libs")
async def list_base_libs():
    """List all base libraries."""
    try:
        db = get_db()
        libs = db.list_base_libs()
        return {"base_libs": libs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/base-libs")
async def create_base_lib(body: BaseLibCreate):
    """Create a custom base library."""
    try:
        db = get_db()
        result = db.create_base_lib(
            name=body.name,
            description=body.description or "",
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/base-libs/{lib_id}")
async def update_base_lib(lib_id: str, body: BaseLibUpdate):
    """Update a custom base library (preset libs cannot be modified)."""
    try:
        db = get_db()
        if not db.update_base_lib(lib_id, name=body.name, description=body.description):
            raise HTTPException(status_code=404, detail="Base library not found or is preset")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/base-libs/{lib_id}")
async def delete_base_lib(lib_id: str):
    """Delete a custom base library (preset libs cannot be deleted)."""
    try:
        db = get_db()
        data_dir = get_data_dir()

        lib = db.get_base_lib(lib_id)
        if not lib:
            raise HTTPException(status_code=404, detail="Base library not found")
        if lib.get("lib_type", "").startswith("preset_"):
            raise HTTPException(status_code=403, detail="Preset base libraries cannot be deleted")

        # Delete from ChromaDB
        chroma_dir = str(Path(data_dir) / "chroma")
        vector_store = VectorStore(chroma_dir)
        vector_store.delete_project(lib_id)

        # Delete from FTS
        fts_store = FTSStore(db)
        fts_store.delete_project_chunks(lib_id)

        # Delete files from disk
        files_dir = Path(data_dir) / "files" / lib_id
        if files_dir.exists():
            import shutil
            shutil.rmtree(files_dir, ignore_errors=True)

        # Delete from database
        db.delete_base_lib(lib_id)
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Base Library File Management ──


@router.get("/base-libs/{lib_id}/files")
async def get_base_lib_files(
    lib_id: str,
    limit: int = 20,
    before_created_at: Optional[str] = None,
    before_id: Optional[str] = None,
    file_type: Optional[str] = None,
    filename_search: Optional[str] = None,
):
    """Get files in a base library."""
    try:
        db = get_db()
        await _validate_base_lib_exists(db, lib_id)

        where_clauses = ["f.project_id = ?"]
        params: list = [lib_id]
        if file_type and file_type != "all":
            type_map = {
                "pdf": [".pdf"], "docx": [".docx", ".doc"],
                "pptx": [".pptx", ".ppt"], "xlsx": [".xlsx", ".xls"],
                "image": [".png", ".jpg", ".jpeg"],
            }
            extensions = type_map.get(file_type, [])
            if extensions:
                placeholders = ",".join("?" for _ in extensions)
                where_clauses.append(f"f.file_type IN ({placeholders})")
                params.extend(extensions)
        if filename_search:
            where_clauses.append("f.filename LIKE ?")
            params.append(f"%{filename_search}%")
        where_sql = " AND ".join(where_clauses)
        if before_created_at and before_id:
            where_sql += " AND (f.created_at < ? OR (f.created_at = ? AND f.id < ?))"
            params.extend([before_created_at, before_created_at, before_id])
        params.append(limit + 1)

        cursor = db.conn.execute(
            f"""SELECT f.id, f.filename, f.file_type, f.status, f.progress,
                       f.chunk_count, f.image_count, f.image_status,
                       f.image_described_count, f.error_message, f.created_at
                FROM files f WHERE {where_sql}
                ORDER BY f.created_at DESC, f.id DESC LIMIT ?""",
            params,
        )
        rows = [dict(r) for r in cursor.fetchall()]
        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]
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
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/base-libs/{lib_id}/upload")
async def upload_base_lib_file(lib_id: str, upload: FileUpload):
    """Upload a file to a base library."""
    try:
        import deps
        db = get_db()
        await _validate_base_lib_exists(db, lib_id)
        return await _base_lib_upload_core(lib_id, upload, db, get_data_dir(), deps.worker)
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
            where_clauses.append("(chunk_id LIKE ? OR chunk_id LIKE ?)")
            params.extend([f"chunk_{file_id}_%", f"img_{file_id}_%"])
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
        return {"results": chunks}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/base-libs/{lib_id}/files/retry-failed")
async def retry_base_lib_failed_files(lib_id: str):
    """Retry all failed files in a base library."""
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
):
    """Get images in a base library."""
    try:
        db = get_db()
        await _validate_base_lib_exists(db, lib_id)
        where_clauses = ["f.project_id = ?"]
        params = [lib_id]
        if file_id:
            where_clauses.append("i.file_id = ?")
            params.append(file_id)
        where_sql = " AND ".join(where_clauses)
        cursor = db.conn.execute(
            f"""SELECT i.id, i.file_id, i.page_no, i.caption, i.local_path,
                      i.vlm_description, i.status, i.context_text,
                      i.error_message, i.category, i.created_at, f.filename
               FROM images i JOIN files f ON f.id = i.file_id
               WHERE {where_sql}
               ORDER BY i.created_at DESC LIMIT ? OFFSET ?""",
            params + [limit + 1, offset],
        )
        rows = [dict(r) for r in cursor.fetchall()]
        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]
        count_cursor = db.conn.execute(
            f"SELECT COUNT(*) as cnt FROM images i JOIN files f ON f.id = i.file_id WHERE {where_sql}",
            params,
        )
        total = count_cursor.fetchone()["cnt"]
        return {
            "images": [{
                "id": r["id"], "file_id": r["file_id"], "filename": r["filename"],
                "page_no": r["page_no"], "caption": r["caption"],
                "local_path": r["local_path"], "vlm_description": r["vlm_description"],
                "status": r.get("status", "pending"), "error_message": r.get("error_message"),
                "context_text": r["context_text"], "category": r.get("category", "uncategorized"),
                "created_at": r["created_at"],
            } for r in rows],
            "total": total, "has_more": has_more,
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
