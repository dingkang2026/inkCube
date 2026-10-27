import os
import sys
import asyncio
from pathlib import Path

import logging
from logging.handlers import RotatingFileHandler
from contextlib import asynccontextmanager

from paths import get_data_dir


# ── Logging: console + rotating file ──
# Shared log file: data/logs/app.log
# Both Python and Electron write to this file with aligned format:
#   [YYYY-MM-DD HH:MM:SS.mmm] [source] [LEVEL] message
# Rotation: 5 MB per file, keep 3 backups (app.log.1 / .2 / .3)
_data_dir = get_data_dir()
# uv 全局缓存：生产跟随数据目录（与 venv 同盘，硬链接复用——uv 默认 hardlink
# 不能跨盘符，缓存若留在 %LOCALAPPDATA% 而 venv 在其他盘会退化为整包复制）；
# dev(Windows) 外置项目树——项目内 cache 的 uv 解压 rename 被 IDE 文件监视器
# 锁死（os error 5，2026-10-12 根因实测，杀软排除/停用实时防护均无效），放
# 项目盘根外置目录（盘符取后端代码所在盘，不硬编码），与任务 venv 仍同盘，
# 硬链接语义不变；Electron ipc-handlers uvCacheDir 以同规则推导保持两侧一致。
# 用户显式设置的 UV_CACHE_DIR 优先（setdefault）。
# dev 判据不能用 sys.frozen：本项目生产无 PyInstaller（python-manager 注释
# 「PyInstaller was dropped」），生产是 python-build-standalone 裸解释器跑
# 源码，frozen 恒 False——用 frozen 会令生产误走 dev 分支、cache 落安装盘
# 根、闸门读不到 Electron 写的 marker。生产真信号 = Electron 仅在生产
# spawn 时注入 INKCUBE_DATA_DIR（python-manager 非 dev 分支）；dev 四条
# 启动路径（npm/PyCharm/Electron spawn/手动）均无此变量，恰好覆盖「外部
# 手动启动也要命中 dev 分支」的诉求。
if sys.platform == "win32" and not os.environ.get("INKCUBE_DATA_DIR"):
    os.environ.setdefault(
        "UV_CACHE_DIR",
        os.path.join(
            os.path.splitdrive(os.path.dirname(os.path.abspath(__file__)))[0] + os.sep,
            "InkCube-dev-cache", "uv-cache"))
else:
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
from scheduler import ScheduleScheduler
from watchdog import start_watchdog
from maintenance import run_storage_maintenance
from routers import settings, models, knowledge, activities, health, system, tasks, skills, reports, wiki, web_logins, affairs, forms, usage, ppts, ppt_templates, task_templates, schedules, leads


@asynccontextmanager
async def lifespan(app: FastAPI):
    data_dir = get_data_dir()
    os.makedirs(data_dir, exist_ok=True)
    db_path = os.path.join(data_dir, "inkcube.db")

    deps.db = Database(db_path)
    deps.db.initialize()
    deps.db.ensure_preset_base_libs()

    # Token usage 记录层 sink 注入（2.10 §4.1）：llm/ 零 DB 依赖，组装期绑定
    from llm import usage as llm_usage
    llm_usage.configure(deps.db.insert_llm_usage)

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

    # 节奏滚动启动补滚(3.0 §5.4;整体 review S3):停机跨天后不等首个心跳;
    # maybe_roll_daily 同日幂等,与心跳入口互不重复滚
    from affairs_service import maybe_roll_daily
    maybe_roll_daily(deps.db)

    # Seed builtin skills on startup (idempotent, 2.8 §4.2)：
    # 同步部分仅毫秒级（定位 + DB upsert，TaskAgent 执行期实时 list_skills、
    # 管理页立即可见）；文件同步（ppt-master 13,245 文件，实测 ~22s）后台
    # to_thread 执行，不再阻塞 lifespan 的 /health（2026-10-24 启动提速）。
    # 拷贝走 staging 原子替换，install_path 任意时刻完整；进程被杀仅残留
    # .staging，下次启动开头清理。显式持引用防 GC（下方存储维护先例）。
    from skills import seed as _skill_seed
    _skill_seed_pending = _skill_seed.seed_builtin_skills_deferred(deps.db, data_dir)
    if _skill_seed_pending:
        logging.getLogger(__name__).info(
            "Skill file sync deferred to background: %s",
            [name for name, _src in _skill_seed_pending])
        _skill_seed_tasks: set = set()
        _sst = asyncio.create_task(asyncio.to_thread(
            _skill_seed.sync_skill_files_batch, _skill_seed_pending, data_dir))
        _skill_seed_tasks.add(_sst)
        _sst.add_done_callback(_skill_seed_tasks.discard)

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

    # bge-m3 预热已移出启动链（POST /models/preload，routers/models.py）：
    # 启动期 CUDA 加载与渲染层模块装载争抢 CPU/磁盘，拖慢 dev 冷启动首屏
    # （2026-10-13 实测 DCL 84s）；现由 Electron 窗口 did-finish-load 触发。
    # 首查询不依赖预热：vector_store 单例懒加载（双检锁）。

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

    # 存储维护（2026-10-20 数据管理审计）：孤儿任务目录清扫 + 保留策略清盘
    # （归档任务/llm_usage/订阅记录）。后台执行不阻塞启动；显式持引用防 GC
    # 中途回收（knowledge.py _TRANSFER_BG_TASKS 先例）
    _maintenance_tasks: set = set()
    _mt = asyncio.create_task(asyncio.to_thread(
        run_storage_maintenance, deps.db, data_dir,
        archived_days=int(settings.get("archived_task_cleanup_days", 30)),
        usage_days=int(settings.get("llm_usage_retention_days", 90)),
        runs_days=int(settings.get("schedule_runs_retention_days", 90))))
    _maintenance_tasks.add(_mt)
    _mt.add_done_callback(_maintenance_tasks.discard)

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

    # ── Forms: 预置模板 seed + 崩溃恢复 + 后台拾取（2.4 §3.1/§2.4）──
    # seed 幂等（已存在不覆盖分析产物）；analyzing/filling 残留重置 pending
    # 后由 resume_pending_work 在事件循环内拾取重跑。
    _seeded_forms = deps.db.seed_form_templates(forms.preset_form_templates())
    _stale_tpl = deps.db.recover_stale_form_templates()
    _stale_fills = deps.db.recover_stale_form_fills()
    if _seeded_forms or _stale_tpl or _stale_fills:
        logging.getLogger(__name__).info(
            "Forms startup: %d seeded, %d stale template(s) reset, "
            "%d stale fill(s) reset",
            len(_seeded_forms), len(_stale_tpl), len(_stale_fills))
    forms.resume_pending_work(deps.db, data_dir)

    # ── PPT 交付物：启动恢复（2.3 §6）── 泵随进程消失，active 行按 workspace
    # 产物落位（exports→completed / pending→awaiting / running→paused），不发新泵。
    # 线程池执行：模板分支含收录拷贝、deck 分支含 confirm_ui 关停子进程，
    # 同步直调会在启动期冻结事件循环
    try:
        from ppt_service import service as _ppt_service
        await asyncio.to_thread(_ppt_service.recover_orphans, deps.db)
    except Exception as e:
        logging.getLogger(__name__).warning("PPT orphan recovery failed: %s", e)

    # ── 定时订阅调度器（2.11 §5.1，E3）── 主进程内 asyncio 循环；启动即错过
    # 补偿（§5.4，进程残留的 running run 行由 fail_stale_running_runs 启动
    # 清扫标记失败）。shutdown cancel：在途执行随进程退出，残留行由下次
    # 启动清扫。worker 注入 §6 档案入库链
    deps.scheduler = ScheduleScheduler(deps.db, data_dir, agent=deps.task_agent,
                                       worker=deps.worker)
    await deps.scheduler.start()

    yield

    # Shutdown workers
    if deps.worker:
        await deps.worker.stop()

    # 定时订阅调度器（2.11 §5.1，E3）
    if deps.scheduler:
        await deps.scheduler.stop()


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
app.include_router(forms.router)
app.include_router(usage.router)
app.include_router(ppts.router)
app.include_router(ppt_templates.router)
app.include_router(task_templates.router)
app.include_router(schedules.router)
app.include_router(leads.router)


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
