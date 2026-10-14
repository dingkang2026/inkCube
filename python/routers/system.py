"""python/routers/system.py — Data management & cleanup API.

Provides endpoints for querying data usage and cleaning specific categories:
- GET  /system/data/usage        → disk usage breakdown by category
- DELETE /system/data/{category}  → clean a specific category
"""
import asyncio
import os
import shutil
import logging
from pathlib import Path

from fastapi import APIRouter

import deps
from paths import get_data_dir

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/system", tags=["system"])


def _dir_size(path: str) -> int:
    """Return total size of a directory in bytes."""
    total = 0
    try:
        for dirpath, _dirnames, filenames in os.walk(path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _count_rows(table: str) -> int:
    """Count rows in a database table."""
    try:
        cursor = deps.db.conn.execute(f"SELECT COUNT(*) FROM {table}")
        return cursor.fetchone()[0]
    except Exception:
        return 0


@router.get("/data/usage")
async def get_data_usage():
    """Return disk usage breakdown by category.

    Response:
    {
        "data_dir": "/path/to/data",
        "categories": {
            "logs": {"size": bytes, "count": 0},
            "knowledge": {"size": bytes, "count": N},
            "settings": {"size": bytes, "count": N},
            "models": {"size": bytes, "count": 0},
        },
        "total_size": bytes,
    }
    """
    data_dir = get_data_dir()

    # Logs
    logs_dir = os.path.join(data_dir, "logs")
    logs_size = _dir_size(logs_dir)

    # Knowledge: files on disk + chroma vectors
    files_dir = os.path.join(data_dir, "files")
    chroma_dir = os.path.join(data_dir, "chroma")
    knowledge_size = _dir_size(files_dir) + _dir_size(chroma_dir)
    project_count = _count_rows("projects")
    file_count = _count_rows("files")
    image_count = _count_rows("images")

    # Settings
    settings_count = _count_rows("settings")
    settings_size = settings_count * 200  # estimate

    # Models
    models_dir = os.path.join(data_dir, "models")
    models_size = _dir_size(models_dir)

    total = logs_size + knowledge_size + settings_size + models_size

    return {
        "data_dir": data_dir,
        "categories": {
            "logs": {"size": logs_size, "count": 0},
            "knowledge": {
                "size": knowledge_size,
                "count": project_count,
                "file_count": file_count,
                "image_count": image_count,
            },
            "settings": {"size": settings_size, "count": settings_count},
            "models": {"size": models_size, "count": 0},
        },
        "total_size": total,
    }


@router.delete("/data/{category}")
async def clean_data_category(category: str):
    """Clean a specific data category.

    Categories: logs, knowledge, settings, models
    """
    if category == "logs":
        return _clean_logs()
    elif category == "knowledge":
        return await _clean_knowledge()
    elif category == "settings":
        return _clean_settings()
    elif category == "models":
        return _clean_models()
    else:
        return {"success": False, "message": f"Unknown category: {category}"}


def _clean_logs() -> dict:
    """Truncate log files.

    On Windows, the active app.log may be held open by RotatingFileHandler
    or Electron's appendFileSync, so we truncate instead of delete.
    Backup files (app.log.1, .2, .3) are deleted.
    """
    data_dir = get_data_dir()
    logs_dir = os.path.join(data_dir, "logs")
    cleaned = 0
    try:
        for f in Path(logs_dir).glob("*.log*"):
            size = f.stat().st_size
            try:
                f.unlink()
            except PermissionError:
                # File is held open by RotatingFileHandler — truncate instead
                with open(f, 'w', encoding='utf-8'):
                    pass
            except OSError:
                # Other OS errors — try truncate as fallback
                with open(f, 'w', encoding='utf-8'):
                    pass
            cleaned += size
            logger.info("Cleaned log file: %s (%d bytes)", f.name, size)
    except Exception as e:
        logger.error("Failed to clean logs: %s", e)
        return {"success": False, "message": str(e)}
    return {"success": True, "cleaned_bytes": cleaned}


async def _clean_knowledge() -> dict:
    """Delete all knowledge base data: projects, files, images, chroma, disk files."""
    try:
        db = deps.db
        data_dir = get_data_dir()

        # 1. Delete ChromaDB collections
        chroma_dir = os.path.join(data_dir, "chroma")
        try:
            from knowledge.vector_store import get_chroma_client, drop_chroma_client
            # Evict from the process-wide cache FIRST: the directory is about
            # to be removed, and opening it with a bare PersistentClient()
            # (default settings) would poison chromadb's (path, settings)
            # registry and break every later VectorStore(...) — incident
            # 2026-10-07, 181 files failed after a restart
            drop_chroma_client(chroma_dir)
            client = get_chroma_client(chroma_dir)
            for coll in client.list_collections():
                client.delete_collection(coll.name)
                logger.info("Deleted ChromaDB collection: %s", coll.name)
        except Exception as e:
            logger.warning("ChromaDB cleanup failed: %s", e)

        # 2. Delete FTS data
        try:
            fts_store = deps.FTSStore(db) if hasattr(deps, 'FTSStore') else None
            if fts_store:
                fts_store.delete_all()
        except Exception:
            # FTS table will be cleaned up by project cascade
            pass

        # 3. Delete uploaded files from disk
        files_dir = os.path.join(data_dir, "files")
        if os.path.exists(files_dir):
            shutil.rmtree(files_dir, ignore_errors=True)
            logger.info("Deleted files directory: %s", files_dir)

        # 4. Delete ChromaDB directory
        if os.path.exists(chroma_dir):
            shutil.rmtree(chroma_dir, ignore_errors=True)
            logger.info("Deleted ChromaDB directory: %s", chroma_dir)

        # 5. Delete from database (cascade handles files, images)
        db.conn.execute("DELETE FROM images")
        db.conn.execute("DELETE FROM files")
        db.conn.execute("DELETE FROM projects")
        # Clean FTS table
        try:
            db.conn.execute("DELETE FROM chunks_fts")
        except Exception:
            pass  # FTS table might not exist
        db.conn.commit()

        logger.info("Cleaned all knowledge base data")
        return {"success": True}
    except Exception as e:
        logger.error("Failed to clean knowledge: %s", e)
        return {"success": False, "message": str(e)}


def _clean_settings() -> dict:
    """Reset all settings to defaults (delete all rows)."""
    try:
        db = deps.db
        db.conn.execute("DELETE FROM settings")
        db.conn.commit()
        logger.info("Cleaned all settings")
        return {"success": True}
    except Exception as e:
        logger.error("Failed to clean settings: %s", e)
        return {"success": False, "message": str(e)}


def _clean_models() -> dict:
    """Delete downloaded model files.

    On Windows, model files loaded in memory may fail to delete.
    We use ignore_errors=True and report partial success.
    """
    data_dir = get_data_dir()
    models_dir = os.path.join(data_dir, "models")
    cleaned = 0
    failed_items = []
    try:
        if os.path.exists(models_dir):
            cleaned = _dir_size(models_dir)
            # Delete all subdirectories (model files)
            for item in Path(models_dir).iterdir():
                try:
                    if item.is_dir():
                        shutil.rmtree(item, ignore_errors=False)
                        logger.info("Deleted model directory: %s", item.name)
                    elif item.is_file():
                        item.unlink()
                except (PermissionError, OSError) as e:
                    # File might be locked on Windows
                    logger.warning("Failed to delete %s: %s", item.name, e)
                    failed_items.append(item.name)
            logger.info("Cleaned models (%d bytes)", cleaned)
    except Exception as e:
        logger.error("Failed to clean models: %s", e)
        return {"success": False, "message": str(e)}

    result = {"success": True, "cleaned_bytes": cleaned}
    if failed_items:
        result["partial"] = True
        result["failed_items"] = failed_items
        result["message"] = f"Some files could not be deleted: {', '.join(failed_items)}"
    return result


@router.get("/deps/check")
async def check_external_deps():
    """Check availability of external dependencies used by skills or tools.

    Returns status for: pandoc, libreoffice, git, ffmpeg, etc.
    """
    deps_status = {}

    # pandoc
    pandoc_path = shutil.which("pandoc")
    if pandoc_path:
        try:
            # Worker thread — subprocess must not block the event loop (H1)
            proc = await asyncio.to_thread(
                subprocess.run, [pandoc_path, "--version"],
                capture_output=True, encoding="utf-8", errors="replace", timeout=5,
            )
            version_line = proc.stdout.split("\n")[0] if proc.stdout else ""
            deps_status["pandoc"] = {"available": True, "path": pandoc_path, "version": version_line}
        except Exception:
            deps_status["pandoc"] = {"available": True, "path": pandoc_path, "version": ""}
    else:
        deps_status["pandoc"] = {"available": False, "path": None, "version": ""}

    # libreoffice / soffice
    lo_path = shutil.which("soffice") or shutil.which("libreoffice")
    if lo_path:
        deps_status["libreoffice"] = {"available": True, "path": lo_path}
    else:
        deps_status["libreoffice"] = {"available": False, "path": None}

    # git
    git_path = shutil.which("git")
    if git_path:
        try:
            # Worker thread — subprocess must not block the event loop (H1)
            proc = await asyncio.to_thread(
                subprocess.run, [git_path, "--version"],
                capture_output=True, encoding="utf-8", errors="replace", timeout=5,
            )
            version_line = proc.stdout.strip() if proc.stdout else ""
            deps_status["git"] = {"available": True, "path": git_path, "version": version_line}
        except Exception:
            deps_status["git"] = {"available": True, "path": git_path, "version": ""}
    else:
        deps_status["git"] = {"available": False, "path": None, "version": ""}

    # ffmpeg
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        deps_status["ffmpeg"] = {"available": True, "path": ffmpeg_path}
    else:
        deps_status["ffmpeg"] = {"available": False, "path": None}

    return {"deps": deps_status}


@router.get("/tools/status")
async def get_tools_status():
    """Return status of all bundled tools resolved by the unified tool resolver.

    Returns resolved paths and version info for: uv, rg, ruff, pandoc, python.
    Also includes bundled version info from tool-versions.json if available.
    """
    import json
    from tools.tool_resolver import get_all_tool_paths, get_tool_versions_path

    # Resolve all tool paths
    paths = get_all_tool_paths()

    # Read bundled version info
    versions_path = get_tool_versions_path()
    bundled_versions = None
    if versions_path.is_file():
        try:
            bundled_versions = json.loads(versions_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Build status for each tool
    tools_status = {}
    for name, tool_path in paths.items():
        tools_status[name] = {
            "available": tool_path is not None,
            "path": tool_path,
        }

    return {
        "tools": tools_status,
        "bundled_versions": bundled_versions,
    }
