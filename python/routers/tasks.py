import asyncio
import io
import json
import logging
import os
import zipfile
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, Query
from fastapi.responses import StreamingResponse, JSONResponse, Response

import deps
from readiness import ensure_ready
from schemas import TaskCreateRequest, TaskChatRequest
from paths import get_data_dir

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/tasks")
async def list_tasks(
    status: str | None = Query(None, description="Filter: active, archived, or None for all"),
    limit: int = Query(0, ge=0, description="Page size; 0 = return all"),
    offset: int = Query(0, ge=0, description="Rows to skip"),
    origin: str | None = Query(None, description="Filter by origin（2.3 §3.1 形态分离；任务 Tab 固定传 'chat'，None = 全量）"),
):
    tasks, has_more = deps.db.list_tasks(status=status, limit=limit, offset=offset, origin=origin)
    # 2.10 §4.5：任务列表行附加累计消耗（无记录为 null，前端不渲染小字）
    usage_map = deps.db.get_usage_by_tasks([t["id"] for t in tasks])
    for t in tasks:
        t["usage"] = usage_map.get(t["id"])
    return {"tasks": tasks, "has_more": has_more}


@router.post("/tasks")
async def create_task(req: TaskCreateRequest):
    logger.info("Creating task: title=%s, mode=%s, workspace=%s", req.title, req.mode, req.workspace_path)
    data_dir = get_data_dir()
    # Check task count quota
    settings = deps.db.get_settings()
    max_count = settings.get("task_max_count", 50)
    if max_count > 0:
        active_count = deps.db.count_tasks()
        if active_count >= max_count:
            logger.warning("Task creation blocked: quota reached (%d/%d)", active_count, max_count)
            # 结构化错误（409 + code）：前端据此 i18n 渲染，不静默
            return JSONResponse(
                status_code=409,
                content={"error": f"任务数量已达上限 ({max_count})，请删除或归档旧任务后再创建。", "code": "TASK_QUOTA_REACHED"},
            )
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, deps.db.create_task, req.title, data_dir, req.workspace_path, req.mode,
    )
    logger.info("Task created: %s", result.get("id", "unknown") if isinstance(result, dict) else "ok")
    return result


@router.get("/tasks/status")
async def tasks_status():
    """Return running task status for frontend UI. Must be before /tasks/{task_id}."""
    return {
        "running_count": deps.task_agent.running_count,
        "can_start": deps.task_agent.can_start_task(),
        "max_concurrent": deps.task_agent.MAX_CONCURRENT,
        "running_task_ids": list(deps.task_agent._running_task_ids),
    }


@router.get("/tasks/{task_id}/has-state")
async def task_has_state(task_id: str):
    """Check if a task has saved agent state (for resume UI)."""
    state = deps.db.load_agent_state(task_id)
    return {"has_state": state is not None}


@router.get("/tasks/{task_id}")
async def get_task(task_id: str):
    task = deps.db.get_task(task_id)
    if not task:
        return {"error": "任务不存在", "code": "TASK_NOT_FOUND"}
    messages = deps.db.get_task_messages(task_id)
    return {"task": task, "messages": messages}


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str):
    logger.info("Deleting task: %s", task_id)
    task = deps.db.get_task(task_id)
    if not task:
        logger.warning("Delete failed: task %s not found", task_id)
        return {"error": "任务不存在", "code": "TASK_NOT_FOUND"}
    # Only cancel if this task is currently running
    if deps.task_agent.is_task_running(task_id):
        logger.info("Cancelling running task before delete: %s", task_id)
        deps.task_agent.request_cancel(task_id)
        # Wait (bounded) for the run loop to actually exit, so workspace
        # deletion doesn't race with in-flight file writes from the agent.
        for _ in range(20):  # up to ~10s
            if not deps.task_agent.is_task_running(task_id):
                break
            await asyncio.sleep(0.5)
        else:
            logger.warning(
                "Task %s still running after cancel timeout, deleting anyway", task_id
            )
    # Run in thread pool (directory deletion can be slow)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, deps.db.delete_task, task_id)
    logger.info("Task deleted: %s", task_id)
    return {"ok": True}


@router.patch("/tasks/{task_id}")
async def rename_task(task_id: str, body: dict):
    title = (body.get("title") or "").strip()
    if not title:
        return JSONResponse(
            status_code=400,
            content={"error": "标题不能为空", "code": "INVALID_TITLE"},
        )
    ok = deps.db.rename_task(task_id, title)
    if not ok:
        return JSONResponse(
            status_code=404,
            content={"error": "任务不存在", "code": "TASK_NOT_FOUND"},
        )
    return {"ok": True}


@router.put("/tasks/{task_id}/mode")
async def set_task_mode(task_id: str, body: dict):
    """Set task mode to 'coding' or 'general'."""
    mode = body.get("mode", "coding")
    if mode not in ("coding", "general"):
        return {"error": "mode must be 'coding' or 'general'", "code": "INVALID_MODE"}
    ok = deps.db.set_task_mode(task_id, mode)
    if not ok:
        return {"error": "任务不存在", "code": "TASK_NOT_FOUND"}
    return {"ok": True, "mode": mode}


@router.post("/tasks/{task_id}/archive")
async def archive_task(task_id: str):
    """Archive a task (hide from active list, keep data)."""
    # 纵深防御：前端已隐藏运行中任务的归档入口，此处拦截保证 API 层规则一致
    if deps.task_agent.is_task_running(task_id):
        logger.warning("Archive blocked: task %s is running", task_id)
        return JSONResponse(
            status_code=409,
            content={"error": "任务正在执行中，请先停止再归档", "code": "TASK_RUNNING"},
        )
    ok = deps.db.archive_task(task_id)
    if not ok:
        return {"error": "任务不存在或已删除", "code": "TASK_NOT_FOUND"}
    return {"ok": True}


@router.post("/tasks/{task_id}/unarchive")
async def unarchive_task(task_id: str):
    """Restore an archived task to active."""
    ok = deps.db.unarchive_task(task_id)
    if not ok:
        return {"error": "任务不存在", "code": "TASK_NOT_FOUND"}
    return {"ok": True}


@router.get("/tasks/{task_id}/export")
async def export_task(task_id: str):
    """Export task workspace as a zip file."""
    task = deps.db.get_task(task_id)
    if not task:
        return {"error": "任务不存在", "code": "TASK_NOT_FOUND"}
    workspace = Path(task["workspace_path"])
    if not workspace.is_dir():
        return {"error": "工作区目录不存在", "code": "WORKSPACE_NOT_FOUND"}

    loop = asyncio.get_event_loop()
    zip_buffer = await loop.run_in_executor(None, _create_workspace_zip, workspace)
    if isinstance(zip_buffer, dict):
        return zip_buffer  # error response

    # 文件名由 Electron 保存对话框决定（tasks:export 用 task.title 拼 defaultPath），
    # 响应不携带 Content-Disposition：HTTP header 仅允许 latin-1，中文标题会直接 500
    return Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
    )


@router.get("/tasks/{task_id}/messages")
async def get_task_messages(task_id: str):
    messages = deps.db.get_task_messages(task_id)
    return {"messages": messages}


@router.post("/tasks/{task_id}/chat")
async def task_chat(task_id: str, req: TaskChatRequest):
    logger.info("Task chat: task_id=%s, message_len=%d, resume=%s", task_id, len(req.message), req.resume)
    # 准入前置校验（0.4 §7.3）：缺 LLM 配置时结构化报错，而非运行中途失败
    ensure_ready("task")

    # Resume 前置校验（review 2026-10-20 T-1）：任何早退必须发生在 try_acquire_task
    # 之前——acquire 之后的早退不经过 run() 的 finally，_running_task_ids 永久残留
    # 该任务（释放唯一位置在 run()），后果链：后续 chat 恒 409、并发槽被侵占、
    # Activity 假报运行中、scheduler R2 永久让路，唯一恢复手段是重启进程。
    # load_agent_state 对损坏 state_json 抛异常 → 500，同样属 acquire 前必须兑住的
    # 早退路径。校验通过后到 acquire 之间无 state 消失路径（delete 仅发生在
    # run() 内部与下方非 resume 分支，均被同任务互斥拦截），无 TOCTOU。
    resume_state = None
    if req.resume:
        try:
            resume_state = deps.db.load_agent_state(task_id)
        except Exception:
            logger.warning("Resume state unreadable: task %s", task_id, exc_info=True)
        if not resume_state:
            # 409 与同文件 TASK_RUNNING 先例一致：请求无法执行的结构化错误，
            # Electron tasks:chat 桥按 !ok 抛错 → 前端 catch 复位生成态
            return JSONResponse(
                status_code=409,
                content={"error": "没有可恢复的任务状态", "code": "NO_RESUMABLE_STATE"},
            )

    # Atomic reserve (H4): rejects both the global limit AND a second chat
    # on a task that is already running (would corrupt its message history).
    if not deps.task_agent.try_acquire_task(task_id):
        if deps.task_agent.is_task_running(task_id):
            logger.warning("Task chat blocked: task %s already running", task_id)
            return JSONResponse(
                status_code=409,
                content={"error": "该任务正在执行中，请等待完成或先停止", "code": "TASK_RUNNING"},
            )
        logger.warning("Task chat blocked: max concurrent reached")
        return JSONResponse(
            status_code=429,
            content={"error": "已达到最大并发任务数限制", "code": "TASK_LIMIT_REACHED"},
        )

    if req.resume:
        # Resume mode: skip saving user message (state already verified above)
        logger.info("Resuming task %s from iteration %d", task_id, resume_state.get("iteration", 0))
    else:
        # Normal mode: save user message and update title
        # Clear any saved agent state so _run_inner builds fresh context
        deps.db.delete_agent_state(task_id)
        deps.db.save_task_message(task_id, "user", req.message)
        deps.db.update_task_title_from_message(task_id, req.message)

    async def event_stream():
        logger.debug("Starting task event stream: %s", task_id)
        async for event in deps.task_agent.run(task_id, req.message):
            yield f"event: {event['type']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
        logger.debug("Task event stream completed: %s", task_id)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/tasks/{task_id}/chat/cancel")
async def cancel_task_chat(task_id: str):
    logger.info("Cancelling task: %s", task_id)
    deps.task_agent.request_cancel(task_id)
    return {"ok": True}


@router.post("/tasks/{task_id}/pause")
async def pause_task_chat(task_id: str):
    """Pause a running task: save state and cancel execution."""
    logger.info("Pausing task: %s", task_id)
    # 只允许暂停运行中的任务：对非运行任务保存 state 会生成一份
    # 无 system prompt / 无工具上下文的退化快照，污染「恢复」入口。
    if not deps.task_agent.is_task_running(task_id):
        logger.warning("Pause rejected: task %s is not running", task_id)
        return JSONResponse(
            status_code=409,
            content={"error": "任务未在运行中，无需暂停", "code": "TASK_NOT_RUNNING"},
        )
    current_iteration = deps.task_agent.get_iteration(task_id)
    # Use in-memory messages (include tool_calls/tool_call_id) for correct resume context.
    # 任务在运行集内但消息尚未就绪（system prompt 构建窗口）时拒绝暂停——
    # 从 DB 重建的 user/assistant 纯文本快照恢复会丢失 system prompt，
    # 属于损坏状态，不可作为断点。
    messages = deps.task_agent.get_messages(task_id)
    if messages is None:
        logger.warning("Pause rejected: task %s has no in-memory messages yet", task_id)
        return JSONResponse(
            status_code=409,
            content={"error": "任务尚未进入可暂停的执行阶段，请稍后重试", "code": "TASK_NOT_PAUSABLE"},
        )
    ok = deps.task_agent.pause_task(task_id, messages, current_iteration, [])
    if not ok:
        logger.warning("Pause failed: task %s", task_id)
        return {"error": "暂停失败", "code": "PAUSE_FAILED"}
    logger.info("Task paused: %s at iteration %d", task_id, current_iteration)
    return {"ok": True}


@router.post("/tasks/{task_id}/confirm-shell")
async def confirm_shell(task_id: str, body: dict):
    """Respond to a pending shell command confirmation."""
    confirmed = body.get("confirmed", False)
    allow_session = body.get("allow_session", False)
    logger.info("Shell confirm: task=%s, confirmed=%s, allow_session=%s", task_id, confirmed, allow_session)
    deps.task_agent.respond_confirm(task_id, confirmed, allow_session=allow_session)
    return {"ok": True}


@router.post("/tasks/{task_id}/confirm-skill")
async def confirm_skill(task_id: str, body: dict):
    """Respond to a pending skill execution confirmation.

    found=False means the confirmation already timed out server-side; the
    frontend surfaces this instead of silently swallowing the click (1.1 §7.2).
    """
    confirmed = body.get("confirmed", False)
    logger.info("Skill confirm: task=%s, confirmed=%s", task_id, confirmed)
    found = deps.task_agent.respond_skill_confirm(task_id, confirmed)
    return {"ok": True, "found": found}


@router.get("/tasks/{task_id}/files")
async def get_task_files(task_id: str):
    """Return workspace file tree for a task."""
    task = deps.db.get_task(task_id)
    if not task:
        return {"error": "任务不存在", "code": "TASK_NOT_FOUND"}
    workspace = Path(task["workspace_path"])
    if not workspace.is_dir():
        return {"tree": []}
    return {"tree": _build_file_tree(workspace, workspace, max_depth=5)}


@router.get("/tasks/{task_id}/files/content")
async def get_file_content(task_id: str, path: str = "", b64: bool = Query(False)):
    """Read file content from task workspace for preview.

    b64=1（0.5 E2 T5 导入链路）：任意类型统一 base64 返回，与 upload 同款
    50MB 上限；路径校验与不带 b64 完全一致。
    """
    task = deps.db.get_task(task_id)
    if not task:
        return {"error": "任务不存在", "code": "TASK_NOT_FOUND"}
    if not path:
        return {"error": "缺少 path 参数", "code": "PATH_REQUIRED"}
    workspace = Path(task["workspace_path"])
    file_path = (workspace / path).resolve()
    # Security: ensure path is within workspace
    if not str(file_path).startswith(str(workspace.resolve()) + os.sep):
        return {"error": "路径超出工作区范围", "code": "PATH_OUTSIDE_WORKSPACE"}
    if not file_path.exists() or not file_path.is_file():
        return {"error": "文件不存在", "code": "FILE_NOT_FOUND"}
    size = file_path.stat().st_size
    if b64:
        if size > 50 * 1024 * 1024:  # 50MB limit，与 upload_task_files 一致
            return {"error": f"文件 {file_path.name} 超过 50MB 限制", "code": "FILE_TOO_LARGE"}
        import base64
        data = base64.b64encode(file_path.read_bytes()).decode("ascii")
        return {"type": "file", "data": data, "size": size, "path": path, "filename": file_path.name}
    # Determine file category
    ext = file_path.suffix.lower()
    image_exts = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".ico"}
    text_exts = {
        ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml", ".toml",
        ".md", ".txt", ".csv", ".html", ".css", ".sh", ".bat", ".xml", ".ini",
        ".cfg", ".conf", ".log", ".sql", ".r", ".java", ".c", ".cpp", ".h",
        ".rs", ".go", ".rb", ".php", ".swift", ".kt", ".scala", ".lua",
    }
    # Known binary extensions that should NOT be read as text
    binary_exts = {
        ".pptx", ".ppt", ".docx", ".doc", ".xlsx", ".xls",
        ".pdf", ".zip", ".tar", ".gz", ".7z", ".rar",
        ".exe", ".dll", ".so", ".dylib", ".bin",
        ".mp3", ".mp4", ".avi", ".mov", ".wav", ".flac",
        ".woff", ".woff2", ".ttf", ".otf", ".eot",
        ".pyc", ".pyo", ".class", ".o", ".obj",
    }
    if ext in image_exts:
        import base64
        data = file_path.read_bytes()
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".gif": "image/gif", ".svg": "image/svg+xml", ".webp": "image/webp",
                ".bmp": "image/bmp", ".ico": "image/x-icon"}.get(ext, "application/octet-stream")
        b64 = base64.b64encode(data).decode("ascii")
        return {"type": "image", "mime": mime, "data": b64, "size": size, "path": path, "abs_path": str(file_path)}
    elif ext in binary_exts:
        return {"type": "binary", "size": size, "path": path, "ext": ext, "abs_path": str(file_path)}
    elif ext in text_exts:
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            return {"type": "text", "content": content, "size": size, "path": path, "ext": ext, "abs_path": str(file_path)}
        except Exception:
            return {"type": "binary", "size": size, "path": path, "ext": ext, "abs_path": str(file_path)}
    else:
        # Unknown extension: try to detect by reading first bytes
        try:
            head = file_path.read_bytes()[:8192]
            if b'\x00' in head:
                # Contains null bytes → binary
                return {"type": "binary", "size": size, "path": path, "ext": ext, "abs_path": str(file_path)}
            # Try UTF-8 decode
            content = file_path.read_text(encoding="utf-8", errors="strict")
            return {"type": "text", "content": content, "size": size, "path": path, "ext": ext, "abs_path": str(file_path)}
        except (UnicodeDecodeError, Exception):
            return {"type": "binary", "size": size, "path": path, "ext": ext, "abs_path": str(file_path)}


@router.post("/tasks/{task_id}/files/upload")
async def upload_task_files(task_id: str, files: list[UploadFile] = File(...)):
    """Upload files to a task's workspace."""
    task = deps.db.get_task(task_id)
    if not task:
        return {"error": "任务不存在", "code": "TASK_NOT_FOUND"}
    workspace = Path(task["workspace_path"])
    workspace.mkdir(parents=True, exist_ok=True)

    uploaded = []
    for f in files:
        safe_name = os.path.basename(f.filename)
        if not safe_name:
            continue
        dest = workspace / safe_name
        dest_resolved = dest.resolve()
        if not str(dest_resolved).startswith(str(workspace.resolve()) + os.sep):
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        content = await f.read()
        if len(content) > 50 * 1024 * 1024:  # 50MB limit
            return {"error": f"文件 {safe_name} 超过 50MB 限制", "code": "FILE_TOO_LARGE"}
        dest.write_bytes(content)
        uploaded.append({"name": safe_name, "size": len(content)})

    # ── Feature 4: Notify agent about uploaded files ──
    if uploaded:
        file_list = ", ".join(
            f"{f['name']} ({f['size']}B)" for f in uploaded
        )
        notify_msg = (
            f"[系统] 用户上传了文件: {file_list}\n"
            f"文件已保存到工作区: {task['workspace_path']}"
        )
        deps.db.save_task_message(task_id, "user", notify_msg)

    return {"uploaded": uploaded}


@router.get("/tasks/{task_id}/logs")
async def get_task_logs(task_id: str, lines: int = Query(200, ge=1, le=2000)):
    """Read execution log for a task. Returns last N lines."""
    task = deps.db.get_task(task_id)
    if not task:
        return {"error": "任务不存在", "code": "TASK_NOT_FOUND"}
    log_dir = os.path.join(get_data_dir(), "tasks", task_id)
    log_path = os.path.join(log_dir, "execution.log")
    if not os.path.isfile(log_path):
        return {"lines": [], "total_lines": 0, "message": "暂无执行日志"}
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
        total = len(all_lines)
        # Return last N lines
        tail = all_lines[-lines:]
        return {
            "lines": [l.rstrip("\n") for l in tail],
            "total_lines": total,
            "showing": len(tail),
        }
    except Exception as e:
        return {"error": f"读取日志失败: {e}"}


def _build_file_tree(root: Path, current: Path, max_depth: int, _depth: int = 0) -> list[dict]:
    """Recursively build a file tree structure."""
    if _depth >= max_depth:
        return []
    entries = []
    try:
        items = sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        return []
    for item in items:
        if item.name.startswith("."):
            continue
        entry: dict = {"name": item.name, "type": "dir" if item.is_dir() else "file"}
        if item.is_file():
            entry["size"] = item.stat().st_size
        else:
            entry["children"] = _build_file_tree(root, item, max_depth, _depth + 1)
        entries.append(entry)
    return entries


def _create_workspace_zip(workspace: Path):
    """Create a zip of the workspace directory. Returns io.BytesIO or error dict."""
    MAX_ZIP_SIZE = 200 * 1024 * 1024  # 200MB
    buf = io.BytesIO()
    try:
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(workspace):
                # Skip hidden dirs, venv and heavyweight build artifacts —
                # node_modules/__pycache__ can blow past the size cap before
                # any user file is even zipped (L2)
                dirs[:] = [
                    d for d in dirs
                    if not d.startswith(".") and d not in ("venv", "node_modules", "__pycache__")
                ]
                for fname in files:
                    fpath = Path(root) / fname
                    if fpath.name.startswith("."):
                        continue
                    arcname = fpath.relative_to(workspace)
                    try:
                        zf.write(fpath, arcname)
                    except (PermissionError, OSError):
                        continue
                    if buf.tell() > MAX_ZIP_SIZE:
                        return {"error": f"工作区超过 {MAX_ZIP_SIZE // 1024 // 1024}MB 限制"}
    except Exception as e:
        return {"error": f"打包失败: {e}"}
    buf.seek(0)
    return buf
