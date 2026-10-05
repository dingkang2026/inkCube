"""python/activity_events.py — 终态事件统一入口（0.4lifecycle-design.md §6.4 E2，随 §8.5 顺序 3 引入）。

业务模块在终态处向本模块声明 complete / fail，由框架完成事件存储（notifications 表，
含 container 容器事实）；E3 演进时在同处追加事件投递（activity:stream SSE），业务代码不变。

当前收编 kind：file（§8.5 顺序 3）。报告 / Wiki 编译钩子随顺序 4 迁入；
过渡期各 kind 写入位置盘点见 0.4 §8.2。

未读查重保证幂等（§3.2）：防御性双写（pipeline 内部 except 与 task_queue 外层兜底
各声明一次 fail）只产生一条通知。
"""
import logging

logger = logging.getLogger(__name__)


def complete_file(db, file_id: str) -> None:
    """文件解析成功终态：写 completed 通知（文件不存在 / 已删除时静默跳过）。"""
    _emit_file(db, file_id, "completed", None)


def fail_file(db, file_id: str, error_message: str | None = None) -> None:
    """文件解析失败终态：写 failed 通知（detail 截 200 字符）。"""
    _emit_file(db, file_id, "failed", (error_message or "")[:200] or None)


def _emit_file(db, file_id: str, kind: str, detail: str | None) -> None:
    """写 file 终态通知。事件写入失败不影响业务主流程（状态已落库，通知是增益）。"""
    try:
        row = db.conn.execute(
            "SELECT f.project_id, f.filename, "
            "CASE WHEN p.kind = 'base_lib' THEN 'baselib' ELSE 'project' END AS container "
            "FROM files f JOIN projects p ON p.id = f.project_id "
            "WHERE f.id = ?",
            (file_id,),
        ).fetchone()
        if not row:
            logger.info("file terminal event skipped: file %s gone", file_id)
            return
        db.add_notification(
            entity_type="file", entity_id=file_id,
            project_id=row["project_id"],
            container=row["container"],
            kind=kind,
            entity_title=row["filename"],
            detail=detail,
        )
    except Exception:
        logger.warning("file terminal event failed for %s", file_id, exc_info=True)
