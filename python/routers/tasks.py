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

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/tasks")
async def list_tasks(status: str | None = Query(None, description="Filter: active, archived, or None for all")):
    return deps.db.list_tasks(status=status)


@router.post("/tasks")
async def create_task(req: TaskCreateRequest):
    logger.info("Creating task: title=%s, mode=%s, workspace=%s", req.title, req.mode, req.workspace_path)
    from main import get_data_dir
    data_dir = get_data_dir()
    # Check task count quota
    settings = deps.db.get_settings()
    max_count = settings.get("task_max_count", 50)
    if max_count > 0:
        all_tasks = deps.db.list_tasks()
        active_count = sum(1 for t in all_tasks if t.get("status") != "deleted")
        if active_count >= max_count:
            logger.warning("Task creation blocked: quota reached (%d/%d)", active_count, max_count)
            return {"error": f"任务数量已达上限 ({max_count})，请删除或归档旧任务后再创建。"}
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
        return {"error": "任务不存在"}
    messages = deps.db.get_task_messages(task_id)
    return {"task": task, "messages": messages}


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str):
    logger.info("Deleting task: %s", task_id)
    task = deps.db.get_task(task_id)
    if not task:
        logger.warning("Delete failed: task %s not found", task_id)
        return {"error": "任务不存在"}
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
    deps.db.rename_task(task_id, body["title"])
    return {"ok": True}


@router.put("/tasks/{task_id}/mode")
async def set_task_mode(task_id: str, body: dict):
    """Set task mode to 'coding' or 'general'."""
    mode = body.get("mode", "coding")
    if mode not in ("coding", "general"):
        return {"error": "mode must be 'coding' or 'general'"}
    ok = deps.db.set_task_mode(task_id, mode)
    if not ok:
        return {"error": "任务不存在"}
    return {"ok": True, "mode": mode}


@router.post("/tasks/{task_id}/archive")
async def archive_task(task_id: str):
    """Archive a task (hide from active list, keep data)."""
    ok = deps.db.archive_task(task_id)
    if not ok:
        return {"error": "任务不存在或已删除"}
    return {"ok": True}


@router.post("/tasks/{task_id}/unarchive")
async def unarchive_task(task_id: str):
    """Restore an archived task to active."""
    ok = deps.db.unarchive_task(task_id)
    if not ok:
        return {"error": "任务不存在"}
    return {"ok": True}


@router.get("/tasks/{task_id}/export")
async def export_task(task_id: str):
    """Export task workspace as a zip file."""
    task = deps.db.get_task(task_id)
    if not task:
        return {"error": "任务不存在"}
    workspace = Path(task["workspace_path"])
    if not workspace.is_dir():
        return {"error": "工作区目录不存在"}

    loop = asyncio.get_event_loop()
    zip_buffer = await loop.run_in_executor(None, _create_workspace_zip, workspace, task["title"])
    if isinstance(zip_buffer, dict):
        return zip_buffer  # error response

    safe_name = task["title"].replace("/", "_").replace("\\", "_")[:50]
    filename = f"{safe_name}_{task_id[:8]}.zip"

    return Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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
        # Resume mode: check for saved state, skip saving user message
        state = deps.db.load_agent_state(task_id)
        if not state:
            return {"error": "没有可恢复的任务状态"}
        logger.info("Resuming task %s from iteration %d", task_id, state.get("iteration", 0))
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
    current_iteration = deps.task_agent.get_iteration(task_id)
    # Use in-memory messages (include tool_calls/tool_call_id) for correct resume context.
    # Fall back to DB messages only if agent runtime doesn't have them.
    messages = deps.task_agent.get_messages(task_id)
    if messages is None:
        msgs = deps.db.get_task_messages(task_id)
        messages = [
            {"role": m["role"], "content": m["content"]}
            for m in msgs if m["role"] in ("user", "assistant")
        ]
    ok = deps.task_agent.pause_task(task_id, messages, current_iteration, [])
    if not ok:
        logger.warning("Pause failed: task %s", task_id)
        return {"error": "暂停失败"}
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
    """Respond to a pending skill execution confirmation."""
    confirmed = body.get("confirmed", False)
    logger.info("Skill confirm: task=%s, confirmed=%s", task_id, confirmed)
    deps.task_agent.respond_skill_confirm(task_id, confirmed)
    return {"ok": True}


@router.get("/tasks/{task_id}/files")
async def get_task_files(task_id: str):
    """Return workspace file tree for a task."""
    task = deps.db.get_task(task_id)
    if not task:
        return {"error": "任务不存在"}
    workspace = Path(task["workspace_path"])
    if not workspace.is_dir():
        return {"tree": []}
    return {"tree": _build_file_tree(workspace, workspace, max_depth=5)}


@router.get("/tasks/{task_id}/files/content")
async def get_file_content(task_id: str, path: str = ""):
    """Read file content from task workspace for preview."""
    task = deps.db.get_task(task_id)
    if not task:
        return {"error": "任务不存在"}
    if not path:
        return {"error": "缺少 path 参数"}
    workspace = Path(task["workspace_path"])
    file_path = (workspace / path).resolve()
    # Security: ensure path is within workspace
    if not str(file_path).startswith(str(workspace.resolve()) + os.sep):
        return {"error": "路径超出工作区范围"}
    if not file_path.exists() or not file_path.is_file():
        return {"error": "文件不存在"}
    size = file_path.stat().st_size
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
        return {"error": "任务不存在"}
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
            return {"error": f"文件 {safe_name} 超过 50MB 限制"}
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
        return {"error": "任务不存在"}
    from main import get_data_dir
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


def _create_workspace_zip(workspace: Path, title: str):
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
