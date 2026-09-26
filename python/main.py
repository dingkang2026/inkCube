import os
import sys
from pathlib import Path

import logging
from logging.handlers import RotatingFileHandler
from contextlib import asynccontextmanager


def get_data_dir() -> str:
    if os.environ.get("INKCUBE_DATA_DIR"):
        return os.environ["INKCUBE_DATA_DIR"]
    if getattr(sys, "frozen", False):
        from platformdirs import user_data_dir
        return os.path.join(user_data_dir("InkCube", "InkCube"), "data")
    return os.path.join(os.path.dirname(__file__), "data")


# ── Logging: console + rotating file ──
# Shared log file: data/logs/app.log
# Both Python and Electron write to this file with aligned format:
#   [YYYY-MM-DD HH:MM:SS.mmm] [source] [LEVEL] message
# Rotation: 5 MB per file, keep 3 backups (app.log.1 / .2 / .3)
_data_dir = get_data_dir()
_log_dir = Path(_data_dir) / "logs"
_log_dir.mkdir(parents=True, exist_ok=True)

_LOG_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
_LOG_BACKUP_COUNT = 3
_LOG_DATE_FMT = "%Y-%m-%d %H:%M:%S"
_LOG_FILE_FMT = "[%(asctime)s] [python] [%(levelname)s] %(message)s"


class _MillisecondFormatter(logging.Formatter):
    """Formatter that appends milliseconds with a dot separator.
    
    Overrides default_msec_format to use '.' instead of ',' so the output is:
      [2026-09-21 10:30:15.123] instead of [2026-09-21 10:30:15,123]
    """
    default_time_format = _LOG_DATE_FMT
    default_msec_format = "%s.%03d"  # Use dot instead of comma


_file_handler = RotatingFileHandler(
    _log_dir / "app.log",
    maxBytes=_LOG_MAX_BYTES,
    backupCount=_LOG_BACKUP_COUNT,
    encoding="utf-8",
)
_file_handler.setFormatter(_MillisecondFormatter(_LOG_FILE_FMT))

_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_MillisecondFormatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
))

logging.root.handlers = [_file_handler, _console_handler]
logging.root.setLevel(logging.INFO)

# CRITICAL: Force offline mode for HuggingFace/transformers BEFORE any other imports
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_OFFLINE"] = "1"

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import deps
from errors import InkCubeError
from db.database import Database
from agent.task_agent import TaskAgent
from knowledge.model_manager import ModelManager
from watchdog import start_watchdog
from routers import settings, models, knowledge, health, system, tasks, skills


@asynccontextmanager
async def lifespan(app: FastAPI):
    data_dir = get_data_dir()
    os.makedirs(data_dir, exist_ok=True)
    db_path = os.path.join(data_dir, "inkcube.db")

    deps.db = Database(db_path)
    deps.db.initialize()
    deps.task_agent = TaskAgent(deps.db)
    deps.model_manager = ModelManager()

    # Configure task agent's knowledge search tool
    deps.task_agent.knowledge_search.configure(
        db=deps.db,
        chroma_persist_dir=os.path.join(data_dir, "chroma"),
        models_dir=os.path.join(data_dir, "models"),
    )

    # Preload bge-m3 embedding model in background so the first query
    # (search / chat / file processing) doesn't block on model loading.
    import threading as _thr
    from knowledge.vector_store import preload_bge_m3
    _bge_m3_path = os.path.join(data_dir, "models", "bge-m3")
    if os.path.isdir(_bge_m3_path):
        def _preload():
            try:
                preload_bge_m3(_bge_m3_path)
                logging.getLogger(__name__).info("bge-m3 model preloaded")
            except Exception as e:
                logging.getLogger(__name__).warning("bge-m3 preload failed: %s", e)
        _thr.Thread(target=_preload, daemon=True, name="bge-m3-preload").start()

    # Initialize knowledge worker for async file/image processing
    settings = deps.db.get_settings()
    from knowledge.task_queue import KnowledgeWorker
    deps.worker = KnowledgeWorker(
        db=deps.db,
        data_dir=data_dir,
        max_file_concurrency=int(settings.get("max_concurrent_file_parsing", 1)),
        max_image_concurrency=int(settings.get("max_concurrent_image_description", 2)),
    )
    await deps.worker.start()
    logging.getLogger(__name__).info("Knowledge worker initialized")

    # Auto-archive stale tasks on startup
    archive_days = int(settings.get("task_auto_archive_days", 30))
    if archive_days > 0:
        archived_count = deps.db.auto_archive_stale_tasks(archive_days)
        if archived_count > 0:
            logging.getLogger(__name__).info(
                "Auto-archived %d stale tasks (>%d days inactive)", archived_count, archive_days
            )

    start_watchdog()

    # ── Detect interrupted tasks (crash recovery) ──
    _interrupted = deps.db.list_tasks_with_saved_state()
    if _interrupted:
        logging.getLogger(__name__).info(
            "Found %d interrupted task states: %s", len(_interrupted), _interrupted,
        )
        for _tid in _interrupted:
            _state = deps.db.load_agent_state(_tid)
            if _state and _state.get("auto_saved"):
                # Avoid duplicate interrupt messages on repeated restarts
                if not deps.db.has_interrupt_marker(_tid):
                    deps.db.save_task_message(_tid, "assistant",
                        "\u26a0\ufe0f \u6b64\u4efb\u52a1\u5728\u4e0a\u6b21\u7a0b\u5e8f\u5173\u95ed\u65f6\u88ab\u4e2d\u65ad\u3002"
                        "\u70b9\u51fb\u300c\u6062\u590d\u300d\u53ef\u4ece\u4e0a\u6b21\u4e2d\u65ad\u5904\u7ee7\u7eed\uff0c"
                        "\u6216\u53d1\u9001\u65b0\u6d88\u606f\u91cd\u65b0\u5f00\u59cb\u3002")

    yield

    # Shutdown workers
    if deps.worker:
        await deps.worker.stop()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(settings.router)
app.include_router(models.router)
app.include_router(knowledge.router)
app.include_router(health.router)
app.include_router(system.router)
app.include_router(tasks.router)
app.include_router(skills.router)


# ── Global Exception Handlers ──

_STATUS_MAP = {
    "CONFIG": 400,
    "CREDENTIAL": 401,
    "NETWORK": 502,
    "RESOURCE": 422,
    "INTERNAL": 500,
    "PROCESS": 503,
}


@app.exception_handler(InkCubeError)
async def inkcube_error_handler(request, exc: InkCubeError):
    logging.getLogger(__name__).error(
        "InkCubeError [%s] %s: %s", exc.code, exc.category, exc.message,
    )
    return JSONResponse(
        status_code=_STATUS_MAP.get(exc.category, 500),
        content=exc.to_dict(),
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request, exc: Exception):
    logging.getLogger(__name__).error(
        "Unhandled exception: %s: %s", type(exc).__name__, exc, exc_info=True,
    )
    return JSONResponse(status_code=500, content={
        "code": "SYSTEM_UNEXPECTED_ERROR",
        "category": "INTERNAL",
        "message": f"程序内部错误: {type(exc).__name__}",
        "suggestion": "请查看日志或提交 Issue",
        "detail": str(exc),
    })


@app.post("/shutdown")
async def shutdown():
    import asyncio

    async def delayed():
        await asyncio.sleep(0.1)
        os._exit(0)

    asyncio.create_task(delayed())
    return {"ok": True}


if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
