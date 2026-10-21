"""python/transfer/service.py — 项目导出/导入的进程内活动状态（2.9 §7 / coding-habits §2.7）。

统一长时活动模型：本模块只维护 run 状态（start/update/finish/fetch），由
routers/activities.py 注册为 project_export / project_import 两个 ActivitySource；
终态通知经 activity_events.complete_transfer / fail_transfer。
进程内存态（同 ledger_service._RUNNING 先例）：导出/导入不跨重启恢复，重启后
残留 run 随进程消失——导入中的半成品项目由 importer 的补偿回滚兜底，不留半项目。
"""
import threading
import time
import uuid

_LOCK = threading.Lock()
_RUNS: dict[str, dict] = {}   # run_id -> {kind, project_id, project_name, progress, message, started_at}


def try_start_run(kind: str, project_id: str, project_name: str,
                  message: str = "", dedupe: str = "project") -> str | None:
    """原子「检查+登记」，返回 run_id；重复时返回 None（CodeReview M1）。

    dedupe="project"（默认）：同 kind+project 已有 run → None（导出防重入，2.9 §5.1）；
    dedupe="kind"：同 kind 任一 run 在飞 → None（导入全局单飞，2.9 §6.1）。
    FastAPI 同步端点在线程池并发执行，分离的检查/登记之间隔着 DB IO 窗口，
    两个并发请求可双双通过——判定与登记必须同一临界区。"""
    with _LOCK:
        if dedupe == "kind":
            dup = any(r["kind"] == kind for r in _RUNS.values())
        else:
            dup = any(r["kind"] == kind and r["project_id"] == project_id
                      for r in _RUNS.values())
        if dup:
            return None
        run_id = str(uuid.uuid4())
        _RUNS[run_id] = {
            "kind": kind,
            "project_id": project_id,
            "project_name": project_name,
            "progress": 0,
            "message": message,
            "started_at": time.time(),
        }
        return run_id


def update_run(run_id: str, *, progress: int | None = None, message: str | None = None) -> None:
    """推进度（未知 run_id 静默——终态清理与慢回调的竞态无害）。"""
    with _LOCK:
        run = _RUNS.get(run_id)
        if run is None:
            return
        if progress is not None:
            run["progress"] = max(0, min(100, int(progress)))
        if message is not None:
            run["message"] = message


def finish_run(run_id: str) -> None:
    """终态：run 从表中移除（终态项不进活跃快照，通知由 activity_events 负责）。"""
    with _LOCK:
        _RUNS.pop(run_id, None)


def fetch_running(kind: str) -> list[dict]:
    """ActivitySource fetch（只读快照，0.4 §2.2 契约：无副作用）。"""
    with _LOCK:
        return [
            {
                "entity_type": kind,
                "entity_id": rid,
                "project_id": r["project_id"],
                "project_name": r["project_name"],
                "entity_title": r["project_name"],
                "status": "running",
                "progress": r["progress"],
                "progress_text": r["message"],
            }
            for rid, r in sorted(_RUNS.items(), key=lambda kv: kv[1]["started_at"])
            if r["kind"] == kind
        ]
