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
# uv 全局缓存指到数据目录同盘（进程级环境变量，所有 uv 子进程继承）：uv pip
# 安装默认以硬链接复用缓存文件，硬链接不能跨盘符——缓存留在默认的
# %LOCALAPPDATA%（C 盘）而任务 venv 在 D 盘时会退化为整包复制，每个任务
# venv 重复占一份包体积。同盘后同版本包只存物理一份，新增任务零增量。
# 用户显式设置的 UV_CACHE_DIR 优先（setdefault）。
os.environ.setdefault("UV_CACHE_DIR", os.path.join(_data_dir, "uv-cache"))
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
import activity_events
from errors import InkCubeError
from db.database import Database
from agent.task_agent import TaskAgent
from knowledge.model_manager import ModelManager
from watchdog import start_watchdog
from routers import settings, models, knowledge, activities, health, system, tasks, skills, reports, wiki, web_logins, affairs


@asynccontextmanager
async def lifespan(app: FastAPI):
    data_dir = get_data_dir()
    os.makedirs(data_dir, exist_ok=True)
    db_path = os.path.join(data_dir, "inkcube.db")

    deps.db = Database(db_path)
    deps.db.initialize()
    deps.db.ensure_preset_base_libs()

    # Seed Wiki schemas on startup (idempotent)
    from db.wiki_seed import get_all_wiki_schemas
    deps.db.seed_wiki_schemas(get_all_wiki_schemas())

    # Seed Playbook templates on startup (idempotent, 3.0 §4.1):
    # 顺序裁决(§5.4): 建表迁移 → seed_wiki_schemas → seed_playbook_templates
    # → migrate_backfill_playbooks(Task 2 接入,依赖模板已就位) → 其余启动流程
    from db.playbook_seed import get_all_playbook_templates
    deps.db.seed_playbook_templates(get_all_playbook_templates())

    # 存量项目回填(3.0 §5.3):seed 之后、其余启动流程之前;幂等由 playbook_seeded 防重入
    _backfilled = deps.db.migrate_backfill_playbooks()
    if _backfilled:
        logging.getLogger(__name__).info(
            "Backfilled playbooks for %d existing projects", _backfilled)

    # Seed builtin skills on startup (idempotent, 2.8 §4.2)：
    # 拷 resources/skills → data/skills + skills 表 upsert (source_type='builtin')
    from skills.seed import seed_builtin_skills
    seed_builtin_skills(deps.db, data_dir)

    # Load report templates once at cold start (single entry point):
    # preset files are the source of truth (unconditional reload overwrites DB cache);
    # custom templates are migrated/reconciled in place (auto-upgrade + flag problems).
    from report_generator.loader import TemplateLoader
    deps.template_loader = TemplateLoader(db=deps.db, data_dir=data_dir)
    _preset = deps.template_loader.load_preset_templates()
    _custom = deps.template_loader.load_custom_templates()
    logging.getLogger(__name__).info(
        "Templates loaded at startup: %d preset, %d custom (%d flagged for upgrade)",
        len(_preset), len(_custom), len(deps.template_loader.list_custom_problems()),
    )

    deps.task_agent = TaskAgent(deps.db)
    deps.model_manager = ModelManager()
    # 下载状态持久化 + 终态通知（0.4 §3.3）
    deps.model_manager.set_db(deps.db)

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
        max_file_concurrency=int(settings.get("max_concurrent_file_parsing", 2)),
        max_image_concurrency=int(settings.get("max_concurrent_image_description", 2)),
        max_compile_concurrency=int(settings.get("max_compile_concurrency", 2)),
    )
    # 恢复暂停状态（重启保持暂停）：暂停持久化于 settings，经 /knowledge/queue/pause 写入
    if settings.get("parsing_paused"):
        deps.worker.set_file_paused(True)
    if settings.get("describe_paused"):
        deps.worker.set_image_paused(True)
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

    # ── Activity Hub: 启动清理旧通知（0.4 §3.2，保留最近 100 条） ──
    try:
        _cleaned = deps.db.cleanup_old_notifications(keep=100)
        if _cleaned:
            logging.getLogger(__name__).info("Cleaned %d old notifications", _cleaned)
    except Exception as e:
        logging.getLogger(__name__).warning("Notification cleanup failed: %s", e)

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

    # ── Detect interrupted reports (crash recovery, §3.8.3) ──
    # 扫描遗留的 generating 报告 → 标记 error（保留 checkpoint）：runner 跑在本进程
    # 内，重启后必然全部不存在，任何 generating 均为孤儿（不设时间阈值，§3.8.3）。
    # 用户在详情页点「恢复生成」从 checkpoint 续跑；启动时不自动拉起后台续跑。
    _stale_reports = deps.db.recover_stale_generating_reports()
    if _stale_reports:
        logging.getLogger(__name__).info(
            "Marked %d interrupted report(s) as error (stale generating): %s",
            len(_stale_reports), _stale_reports,
        )
        for _rid in _stale_reports:
            deps.db.create_report_log(
                _rid, "检测到中断报告，已标记失败，可重新生成续跑", level="warn",
            )
            # 通知联动（0.4 §2.4）：崩溃期间发生的中断也能被用户感知。
            # 经 activity_events 统一入口（E2，顺序 4）：内部 JOIN projects 补 container
            # （旧直调缺 container，基础库报告中断通知会导航错容器），异常自行兜底
            activity_events.fail_report(deps.db, _rid, "程序异常中断")

    # ── Detect interrupted model downloads (crash recovery, 0.4 §2.4 step 6) ──
    # 遗留 downloading 记录 → 标 failed + 发通知；不自动重新下载（断点续传）。
    try:
        _stale_downloads = deps.db.recover_stale_model_downloads()
        for _dl in _stale_downloads:
            logging.getLogger(__name__).info(
                "Marked interrupted model download as failed: %s", _dl["name"])
            # 经 activity_events 统一入口（E2 收尾，v3.9；内部异常自行兜底）
            activity_events.fail_download(deps.db, _dl["name"], "程序异常中断")
    except Exception:
        logging.getLogger(__name__).warning(
            "Model download recovery failed", exc_info=True)

    yield

    # Shutdown workers
    if deps.worker:
        await deps.worker.stop()


app = FastAPI(lifespan=lifespan)


# ── Access log middleware with timestamps ──
# Access log 仅写文件（v3.7 日志收敛）：每请求一条属低价值高频噪音，
# 不进 console/stderr——否则 Electron 捕获 stderr 后回写同一 app.log 造成双写
_access_log = logging.getLogger("inkcube.access")
_access_log.addHandler(_file_handler)
_access_log.propagate = False


@app.middleware("http")
async def _log_request(request, call_next):
    import time
    start = time.time()
    response = await call_next(request)
    elapsed = (time.time() - start) * 1000
    _access_log.info(
        '%s %s %d %.0fms',
        request.method,
        request.url.path,
        response.status_code,
        elapsed,
    )
    return response


# All renderer→Python traffic goes through Electron IPC (ipc-handlers.js
# fetches from the main process, which sends no Origin and ignores CORS),
# so restricting origins has zero impact on the app itself. It only blocks
# external web pages from reading localhost API responses (e.g. plaintext
# API keys via GET /settings). Allowed: Vite dev server only.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(settings.router)
app.include_router(models.router)
app.include_router(knowledge.router)
app.include_router(activities.router)
app.include_router(health.router)
app.include_router(system.router)
app.include_router(tasks.router)
app.include_router(skills.router)
app.include_router(web_logins.router)
app.include_router(reports.router)
app.include_router(wiki.router)
app.include_router(affairs.router)


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
