"""python/routers/system.py — Data management & cleanup API.

Provides endpoints for querying data usage and cleaning specific categories:
- GET  /system/data/usage        → disk usage breakdown by category
- DELETE /system/data/{category}  → clean a specific category
  （可清：logs / knowledge / settings / models / tasks / uv_cache；
   reports / extras 只展示占用，清理走各自功能页）
"""
import asyncio
import os
import shutil
import logging
import subprocess
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

    2026-10-20 存储审计补全：此前只统计 4 类，tasks/reports/skills/DB 文件
    等大头全部漏计，total_size 严重低估。现口径：
    - settings = inkcube.db + -wal + -shm 实测（原 count×200 估算废除）；
    - tasks = data/tasks 全量（workspace + .venv + 任务日志）；
    - reports = data/reports；extras = skills/schedules/form_templates/
      tiktoken-cache 合并；uv_cache = data/uv-cache；fetched = data/fetched
      （两者目录不存在时不返回该键，前端不渲染零值行）。
    每类带 cleanable：false 的类只展示占用，清理走各自功能页（报告逐个删、
    skill 卸载、订阅删除、表单模板删除）。
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

    # Settings = DB 文件实测（inkcube.db + WAL/SHM）
    settings_size = 0
    for suffix in ("", "-wal", "-shm"):
        fp = os.path.join(data_dir, f"inkcube.db{suffix}")
        try:
            settings_size += os.path.getsize(fp)
        except OSError:
            pass
    settings_count = _count_rows("settings")

    # Models
    models_dir = os.path.join(data_dir, "models")
    models_size = _dir_size(models_dir)

    # Tasks: workspace + .venv + 任务日志（data/tasks 全量）
    tasks_dir = os.path.join(data_dir, "tasks")
    tasks_size = _dir_size(tasks_dir)
    archived_count = _count_rows_where(
        "tasks", "status = 'archived'")

    # Reports 产物（目录由报告删除链管理，此处仅展示）
    reports_dir = os.path.join(data_dir, "reports")
    reports_size = _dir_size(reports_dir)
    reports_count = _count_rows("reports")

    # Extras: 用户资产与小型缓存合并展示（清理走各自功能页）
    extras_size = sum(
        _dir_size(os.path.join(data_dir, name))
        for name in ("skills", "schedules", "form_templates", "tiktoken-cache")
    )

    categories = {
        "logs": {"size": logs_size, "count": 0, "cleanable": True},
        "knowledge": {
            "size": knowledge_size,
            "count": project_count,
            "file_count": file_count,
            "image_count": image_count,
            "cleanable": True,
        },
        "settings": {"size": settings_size, "count": settings_count,
                     "cleanable": True},
        "models": {"size": models_size, "count": 0, "cleanable": True},
        "tasks": {"size": tasks_size, "count": archived_count,
                  "cleanable": True},
        "reports": {"size": reports_size, "count": reports_count,
                    "cleanable": False},
        "extras": {"size": extras_size, "count": 0, "cleanable": False},
    }

    # uv-cache：dev 外置（InkCube-dev-cache）时不存在于 data_dir，不返回该键
    uv_cache_dir = os.path.join(data_dir, "uv-cache")
    if os.path.isdir(uv_cache_dir):
        categories["uv_cache"] = {
            "size": _dir_size(uv_cache_dir), "count": 0, "cleanable": True}

    # fetched：browser-fetch extract 产物（B7 锚定 data_dir 后可整体清理；
    # 首次抓取前目录不存在，不返回该键，同 uv_cache 惯例）
    fetched_dir = os.path.join(data_dir, "fetched")
    if os.path.isdir(fetched_dir):
        categories["fetched"] = {
            "size": _dir_size(fetched_dir), "count": 0, "cleanable": True}

    total = sum(cat["size"] for cat in categories.values())

    return {
        "data_dir": data_dir,
        "categories": categories,
        "total_size": total,
    }


def _count_rows_where(table: str, where: str) -> int:
    try:
        cursor = deps.db.conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {where}")
        return cursor.fetchone()[0]
    except Exception:
        return 0


@router.delete("/data/{category}")
async def clean_data_category(category: str):
    """Clean a specific data category.

    Categories: logs, knowledge, settings, models, tasks, uv_cache, fetched
    （reports/extras 不可整体清理，各自行删除链在功能页）。
    """
    if category == "logs":
        return _clean_logs()
    elif category == "knowledge":
        return await _clean_knowledge()
    elif category == "settings":
        return _clean_settings()
    elif category == "models":
        return _clean_models()
    elif category == "tasks":
        return _clean_task_workspaces()
    elif category == "uv_cache":
        return _clean_uv_cache()
    elif category == "fetched":
        return _clean_fetched()
    else:
        return {"success": False,
                "message": f"Category not cleanable or unknown: {category}"}


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
    partial: list[str] = []
    try:
        db = deps.db
        data_dir = get_data_dir()

        # 1. Close and evict the process-wide chroma client — and do NOT
        # reopen one here: stop() releases the on-disk sqlite/segment
        # handles so step 3's rmtree can actually remove the directory on
        # Windows, while the old evict-then-reopen pattern re-held the
        # directory and silently broke deletion (incident 2026-10-20:
        # "Deleted ChromaDB directory" logged while chroma/ survived).
        # Registry note (incident 2026-10-07): eviction via
        # drop_chroma_client keeps the (path, settings) registry clean;
        # the next legit get_chroma_client() re-registers normally.
        chroma_dir = os.path.join(data_dir, "chroma")
        try:
            from knowledge.vector_store import drop_chroma_client
            drop_chroma_client(chroma_dir)
        except Exception as e:
            logger.warning("ChromaDB client eviction failed: %s", e)
        # worker 单例的懒加载 VectorStore 缓存着旧 client（_system 已被上面
        # stop）——不失效则后续解析/编译确定性失败直至重启（与 B1 同族）
        _invalidate_worker_vector_store()

        # 2. Delete FTS data
        try:
            fts_store = deps.FTSStore(db) if hasattr(deps, 'FTSStore') else None
            if fts_store:
                fts_store.delete_all()
        except Exception:
            # FTS table will be cleaned up by project cascade
            logger.debug("FTS cleanup skipped", exc_info=True)

        # 3. Delete knowledge artifacts from disk (reports included: every
        # report row belongs to a project — content_path dirs would be
        # orphaned once the cascade removes the rows in step 4). Verify
        # each rmtree actually removed its directory: ignore_errors
        # swallows locked-file failures, and a false "deleted" log
        # misleads troubleshooting (same incident as above).
        files_dir = os.path.join(data_dir, "files")
        reports_dir = os.path.join(data_dir, "reports")
        for d in (files_dir, chroma_dir, reports_dir):
            if not os.path.exists(d):
                continue
            shutil.rmtree(d, ignore_errors=True)
            if os.path.exists(d):
                partial.append(d)
                logger.warning("Directory not fully removed: %s", d)
            else:
                logger.info("Deleted directory: %s", d)

        # 4. Delete from database (images/files/reports rows + cascade).
        # Explicit deletes beyond the projects cascade:
        # - reports rows: content_path dirs removed in step 3; sections/logs
        #   follow via FK CASCADE
        # - ppts rows: project_id has NO FK — DELETE FROM projects alone
        #   would leave dangling registry rows (global template runs with
        #   project_id='' stay: they outlive projects by design)
        # - schedule targets: weak references (no FK) — same unbind the
        #   delete_project endpoint performs via clear_schedules_target
        for row in db.conn.execute(
                "SELECT DISTINCT target_id FROM task_schedules"
                " WHERE target_type = 'project' AND target_id IS NOT NULL").fetchall():
            db.clear_schedules_target("project", row["target_id"])
        db.conn.execute("DELETE FROM ppts WHERE project_id != ''")
        db.conn.execute("DELETE FROM images")
        db.conn.execute("DELETE FROM files")
        db.conn.execute("DELETE FROM reports")
        db.conn.execute("DELETE FROM projects")
        # Clean FTS table
        try:
            db.conn.execute("DELETE FROM chunks_fts")
        except Exception:
            pass  # FTS table might not exist
        # 预置库补种：projects 含 kind='base_lib' 系统内置行（招商/投资），
        # 清理目标是用户知识数据，目标态 = 初始状态（预置库空容器在位）。
        # 只在启动播种不补种的话，清理后不重启则侧栏基础库消失
        # （2026-10-20 复盘）；幂等，内部 commit 一并落前面的 DELETE
        db.ensure_preset_base_libs()
        db.conn.commit()

        logger.info("Cleaned all knowledge base data")
        result: dict = {"success": True}
        if partial:
            result["partial"] = True
            result["message"] = (
                "Some directories could not be fully removed"
                f": {', '.join(partial)}")
        return result
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

    下载中拒绝（对齐报告删除 409 门）：在飞写盘与 rmtree 竞态会产生半删
    状态。成功后联动清 model_downloads 行——该表仅服务崩溃恢复扫描
    （recover_stale_model_downloads），文件已删后行已失义；下载判定以
    文件系统 _verify 为准（check_status），不受影响。
    On Windows, model files loaded in memory may fail to delete.
    We use ignore_errors=True and report partial success.
    """
    try:
        active = deps.model_manager.get_active_downloads()
    except Exception:
        logger.warning("get_active_downloads failed, skip in-flight gate",
                       exc_info=True)
        active = []
    if active:
        names = "、".join(dl.get("name", "?") for dl in active)
        return {"success": False, "code": "MODEL_DOWNLOAD_IN_PROGRESS",
                "message": f"有模型正在下载（{names}），请等待完成后再清理"}
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
        # 状态联动：下载登记行随文件删除失效，全清无副作用
        deps.db.conn.execute("DELETE FROM model_downloads")
        deps.db.conn.commit()
    except Exception as e:
        logger.error("Failed to clean models: %s", e)
        return {"success": False, "message": str(e)}

    result = {"success": True, "cleaned_bytes": cleaned}
    if failed_items:
        result["partial"] = True
        result["failed_items"] = failed_items
        result["message"] = f"Some files could not be deleted: {', '.join(failed_items)}"
    return result


def _clean_task_workspaces() -> dict:
    """清理全部归档任务的磁盘工作区（含 .venv 与任务日志），保留 DB 行。

    活跃任务永不触碰；与启动保留期清盘共享 maintenance 核心
    （days=None = 全部归档）。
    """
    try:
        import maintenance
        removed = maintenance.cleanup_archived_task_workspaces(deps.db, None)
        logger.info("Cleaned %d archived task workspaces", removed)
        return {"success": True, "removed": removed}
    except Exception as e:
        logger.error("Failed to clean task workspaces: %s", e)
        return {"success": False, "message": str(e)}


def _clean_uv_cache() -> dict:
    """清空 uv 下载缓存（data/uv-cache）。uv 对缓存缺失有韧性（下次安装
    重新下载），整体 rmtree 安全；与 venv 安装并发的竞态由 uv 自身的
    archive 原子落盘兜底，最坏情况该次安装回退镜像源重装。"""
    data_dir = get_data_dir()
    uv_cache_dir = os.path.join(data_dir, "uv-cache")
    try:
        if not os.path.isdir(uv_cache_dir):
            return {"success": True, "cleaned_bytes": 0}
        cleaned = _dir_size(uv_cache_dir)
        shutil.rmtree(uv_cache_dir, ignore_errors=True)
        logger.info("Cleaned uv cache (%d bytes)", cleaned)
        return {"success": True, "cleaned_bytes": cleaned}
    except Exception as e:
        logger.error("Failed to clean uv cache: %s", e)
        return {"success": False, "message": str(e)}


def _clean_fetched() -> dict:
    """清空网页抓取产物（data/fetched，browser-fetch extract 落盘目录）。

    产物为可再生副本（重新 extract 即得），整体 rmtree 安全；下次抓取
    由技能侧 makedirs 自建，无需重建空目录。抓取进行中并发 rmtree 的
    最坏情况 = 当次 extract 写文件失败报错，无数据损坏。"""
    data_dir = get_data_dir()
    fetched_dir = os.path.join(data_dir, "fetched")
    try:
        if not os.path.isdir(fetched_dir):
            return {"success": True, "cleaned_bytes": 0}
        cleaned = _dir_size(fetched_dir)
        shutil.rmtree(fetched_dir, ignore_errors=True)
        logger.info("Cleaned fetched artifacts (%d bytes)", cleaned)
        return {"success": True, "cleaned_bytes": cleaned}
    except Exception as e:
        logger.error("Failed to clean fetched artifacts: %s", e)
        return {"success": False, "message": str(e)}


def _invalidate_worker_vector_store() -> None:
    """chroma 目录被 wipe 后失效 KnowledgeWorker 的懒加载 VectorStore。

    worker 是启动期单例，其 _vector_store 缓存的 client 在
    drop_chroma_client stop 后已死；不失效则下次解析/图片描述/Wiki 编译
    确定性失败直至进程重启。测试环境无 worker，静默跳过。"""
    worker = getattr(deps, "worker", None)
    if worker is not None:
        worker.reset_vector_store()


# ── 真·全清（2026-10-20 审计 B3：resetAll 文案与行为对齐）──

# 磁盘全清清单（data_dir 内；目录缺失跳过）。inkcube.db 保留：schema 即
# 应用结构，全清语义 = 行全删 + 磁盘产物全删，无需删库重建。
_RESET_DISK_DIRS = (
    "chroma", "files", "reports", "schedules", "tasks", "models",
    "skills", "form_templates", "tiktoken-cache", "uv-cache", "logs",
    "fetched",
)

# DB 全清清单，按 FK 依赖序（先子后父，库以 foreign_keys=ON 运行）。
# 逐表显式 DELETE——不依赖 CASCADE 健全性（如 ppts.project_id 无 FK，
# 只靠级联会留悬挂行），与「真·全清」语义一致。
_RESET_TABLES = (
    # 孙子层（被引用者）
    "wiki_page_sources", "wiki_page_versions", "task_messages",
    "phase_tasks", "task_suggestions", "form_fills",
    "report_sections", "report_logs", "task_schedule_runs",
    # 子层（ledger_entries 虽有 ON DELETE CASCADE，仍显式列入——清单
    # 原则是不依赖 CASCADE 健全性，与测试的 schema 枚举比对同一口径）
    "wiki_pages", "wiki_change_log", "wiki_reviews", "wiki_compile_tasks",
    "wiki_source_pages", "task_schedules", "templates", "form_templates",
    "ppts", "reports", "files", "images", "skills", "ledger_entries",
    # 父层（task_templates 无 FK——project_id 仅业务约束，不随级联删，
    # 漏列则重置后用户自定义模板残留）
    "project_base_libs", "projects", "tasks", "notifications",
    "llm_usage", "model_downloads", "web_logins", "task_agent_state",
    "task_templates", "settings",
)


def _reset_gates() -> dict | None:
    """真·全清前置门：命中在飞作业返回失败体（None = 放行）。

    窗口期残余风险与 delete_project 同性质：门到删完之间新发起的作业
    可能被清行——重置是用户显式破坏性操作，不做删后复查。"""
    db = deps.db
    gates = []
    if deps.task_agent.running_count() > 0:
        gates.append("任务执行")
    for label, sql in (
        ("文件解析", "SELECT COUNT(*) AS c FROM files"
                     " WHERE status IN ('pending', 'parsing')"),
        ("报告生成", "SELECT COUNT(*) AS c FROM reports"
                     " WHERE status = 'generating'"),
        ("填表执行", "SELECT COUNT(*) AS c FROM form_fills"
                     " WHERE status IN ('pending', 'filling')"),
        ("Wiki 编译", "SELECT COUNT(*) AS c FROM wiki_compile_tasks"
                      " WHERE status IN ('queued', 'ingesting', 'storing')"),
        ("图片描述", "SELECT COUNT(*) AS c FROM images"
                      " WHERE status IN ('pending', 'processing')"),
        ("PPT 任务", "SELECT COUNT(*) AS c FROM ppts"
                      " WHERE status IN ('running', 'awaiting_input')"),
    ):
        if db.conn.execute(sql).fetchone()["c"] > 0:
            gates.append(label)
    if gates:
        return {"success": False,
                "code": "RESET_BLOCKED_ACTIVE_WORK",
                "message": f"尚有进行中的活动（{'、'.join(gates)}），完成或停止后再重置"}
    try:
        active = deps.model_manager.get_active_downloads()
    except Exception:
        logger.warning("get_active_downloads failed, skip in-flight gate",
                       exc_info=True)
        active = []
    if active:
        names = "、".join(dl.get("name", "?") for dl in active)
        return {"success": False,
                "code": "MODEL_DOWNLOAD_IN_PROGRESS",
                "message": f"有模型正在下载（{names}），请等待完成后再重置"}
    return None


@router.post("/data/reset-all")
async def reset_all_data():
    """真·全清：删除全部用户数据，恢复初始状态。

    - DB：全部用户表按 FK 依赖序显式 DELETE，单事务 commit（settings
      清空 = 下次 get_settings 返回默认值，前端 reloadSettings 回灌）；
    - 磁盘：data_dir 内产物目录逐个 rmtree 并验证删净（partial 上报，
      同 knowledge 清理语义）；
    - 网页登录态：cookies/storage 存 Electron partition（python 够不到
      userData），由 IPC 层在成功后联动 clearAllWebData。
    """
    gate = _reset_gates()
    if gate:
        logger.warning("Reset-all blocked: %s", gate["message"])
        return gate
    data_dir = get_data_dir()
    db = deps.db
    partial: list[str] = []
    disk_cleared = False
    try:
        # chroma 句柄先关（同 _clean_knowledge：不关句柄 rmtree 删不掉），
        # worker 缓存随之失效（同 _clean_knowledge）
        from knowledge.vector_store import drop_chroma_client
        drop_chroma_client(os.path.join(data_dir, "chroma"))
        _invalidate_worker_vector_store()
        for name in _RESET_DISK_DIRS:
            if name == "logs":
                # RotatingFileHandler 全程持有 logs/app.log（Windows 下句柄
                # 无 FILE_SHARE_DELETE，rmtree 必失败 → 每次重置都误报
                # partial）——改走 _clean_logs 的 truncate 语义
                continue
            d = os.path.join(data_dir, name)
            if not os.path.isdir(d):
                continue
            shutil.rmtree(d, ignore_errors=True)
            if os.path.exists(d):
                partial.append(d)
                logger.warning("Reset-all: directory not fully removed: %s", d)
            else:
                logger.info("Reset-all: removed %s", d)
        disk_cleared = True
        # logs：truncate 替代删除（app.log 被本进程持有）；失败计入 partial
        if not _clean_logs().get("success"):
            partial.append(os.path.join(data_dir, "logs"))
        for table in _RESET_TABLES:
            db.conn.execute(f"DELETE FROM {table}")
        # FTS 虚表与 _clean_knowledge 同口径：chunks_fts 非普通表不进
        # _RESET_TABLES，残留会让重置后的 DB 留有旧知识库全文
        try:
            db.conn.execute("DELETE FROM chunks_fts")
        except Exception:
            pass  # FTS 表可能不存在
        # 预置库补种：初始状态含两个预置基础库（系统内置空容器），「恢复
        # 初始状态」必须还原——与 _clean_knowledge 同类缺口，只在启动播种
        # 则重置后不重启侧栏基础库消失；幂等，内部 commit 一并落 DELETE
        db.ensure_preset_base_libs()
        db.conn.commit()
        logger.info("Reset-all done: all user tables and data dirs cleared")
        result: dict = {"success": True}
        if partial:
            result["partial"] = True
            result["message"] = (
                "Some directories could not be fully removed"
                f": {', '.join(partial)}")
        return result
    except Exception as e:
        # 半事务防落库：DELETE 循环中途异常时未提交的行必须回滚，
        # 否则后续任意 commit 会把半删状态写穿
        try:
            db.conn.rollback()
        except Exception:
            pass
        logger.error("Reset-all failed: %s", e)
        msg = str(e)
        if disk_cleared:
            msg += "（磁盘产物已删除，数据库未变更）"
        return {"success": False, "message": msg}


@router.get("/deps/check")
async def check_external_deps():
    """Check availability of external dependencies used by skills or tools.

    Returns status for: git, ffmpeg.
    pandoc 不在列表：实际消费走 tool_resolver 捆绑版（随应用分发必有，
    系统 PATH 检测与真实解析结果脱节）；libreoffice 走模型管理器
    /models/libreoffice/status 精确检测（ms-office 优先降级链）。
    """
    deps_status = {}

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
