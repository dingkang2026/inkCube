"""python/activity_events.py — 终态事件统一入口（0.4lifecycle-design.md §6.4 E2/E3）。

业务模块在终态处向本模块声明 complete / fail，由框架一次完成事件存储（notifications
表，含 container 容器事实）+ 事件投递（activity:stream SSE，推为主 / 心跳拉兜底，
§6.4 E3）——业务代码只声明，不感知投递。

已收编 kind：file（顺序 3）、report / wiki_compile（顺序 4）、
task / model_download / 崩溃恢复遗留下载（E2 收尾，v3.9）、
form_template / form_fill（2.4 §3.4）——全部直调点清零。

2.11 §3.1：新增 notify 通用入口（信息类 kind，无实体行的扫描器/提醒直调，
不做 per-entity 包装——无消费方的包装是死代码）。

未读查重保证幂等（§3.2）：防御性双写（pipeline 内部 except 与 task_queue 外层兜底
各声明一次 fail）只产生一条通知，且查重跳过时同步跳过 SSE 投递（前端已见）。
"""
import asyncio
import logging
import threading

logger = logging.getLogger(__name__)


def complete_file(db, file_id: str, detail: str | None = None) -> None:
    """文件解析成功终态：写 completed 通知（文件不存在 / 已删除时静默跳过）。

    detail 可携带降级说明（如 Office→PDF 转换不可用回退内置解析），随通知 Toast
    显式呈现给用户而非仅落日志（0.4 §7.3 v3.6）；截 200 字符与 fail_file 一致。
    """
    _emit_file(db, file_id, "completed", (detail or "")[:200] or None)


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
        _store_and_publish(
            db,
            entity_type="file", entity_id=file_id,
            project_id=row["project_id"],
            container=row["container"],
            kind=kind,
            entity_title=row["filename"],
            detail=detail,
        )
    except Exception:
        logger.warning("file terminal event failed for %s", file_id, exc_info=True)


# ── report（顺序 4）────────────────────────────────────────────

def complete_report(db, report_id: str, detail: str | None = None) -> None:
    """报告生成成功终态：写 completed 通知（报告不存在 / 已删除时静默跳过）。"""
    _emit_report(db, report_id, "completed", (detail or "")[:200] or None)


def fail_report(db, report_id: str, error_message: str | None = None) -> None:
    """报告生成失败终态：写 failed 通知（detail 截 200 字符）。

    error_message 由调用方显式传入（reports.py 异常分支 / main.py 崩溃恢复），
    不回读 reports.error_message 列——旧 DB 钩子在 update_report_error 写入前
    触发，读列恒为 NULL，失败通知 detail 从未带出过错误信息（顺序 4 修复）。
    """
    _emit_report(db, report_id, "failed", (error_message or "")[:200] or None)


def pause_report(db, report_id: str, detail: str | None = None) -> None:
    """报告生成暂停（2.5 §3.1 修订）：章节边界协作生效后的稳定态通知。

    用户切走后也能被感知；未读查重保证重复声明只产生一条。
    """
    _emit_report(db, report_id, "paused", (detail or "")[:200] or None)


def _emit_report(db, report_id: str, kind: str, detail: str | None) -> None:
    """写 report 终态通知。事件写入失败不影响业务主流程（状态已落库，通知是增益）。"""
    try:
        row = db.conn.execute(
            "SELECT r.project_id, r.title, "
            "CASE WHEN p.kind = 'base_lib' THEN 'baselib' ELSE 'project' END AS container "
            "FROM reports r JOIN projects p ON p.id = r.project_id "
            "WHERE r.id = ?",
            (report_id,),
        ).fetchone()
        if not row:
            logger.info("report terminal event skipped: report %s gone", report_id)
            return
        _store_and_publish(
            db,
            entity_type="report", entity_id=report_id,
            project_id=row["project_id"],
            container=row["container"] or "project",
            kind=kind,
            entity_title=row["title"],
            detail=detail,
        )
    except Exception:
        logger.warning("report terminal event failed for %s", report_id, exc_info=True)


# ── wiki_compile（顺序 4）──────────────────────────────────────

def complete_compile(db, task_id: str) -> None:
    """Wiki 编译成功终态：写 completed 通知。"""
    _emit_compile(db, task_id, "completed", None)


def fail_compile(db, task_id: str, error_message: str | None = None) -> None:
    """Wiki 编译失败终态：写 failed 通知（detail 截 200 字符）。"""
    _emit_compile(db, task_id, "failed", (error_message or "")[:200] or None)


def _emit_compile(db, task_id: str, kind: str, detail: str | None) -> None:
    """写 wiki_compile 终态通知。事件写入失败不影响业务主流程（状态已落库，通知是增益）。

    LEFT JOIN projects：项目已删（task_queue 失败分支）时任务行仍在，
    entity_title 回退 "Wiki"、container 回退默认，通知照发（告知编译失败）。
    """
    try:
        row = db.conn.execute(
            "SELECT wct.project_id, p.name AS project_name, "
            "CASE WHEN p.kind = 'base_lib' THEN 'baselib' ELSE 'project' END AS container "
            "FROM wiki_compile_tasks wct "
            "LEFT JOIN projects p ON p.id = wct.project_id "
            "WHERE wct.id = ?",
            (task_id,),
        ).fetchone()
        if not row:
            logger.info("compile terminal event skipped: task %s gone", task_id)
            return
        _store_and_publish(
            db,
            entity_type="wiki_compile", entity_id=task_id,
            project_id=row["project_id"],
            container=row["container"] or "project",
            kind=kind,
            entity_title=row["project_name"] or "Wiki",
            detail=detail,
        )
    except Exception:
        logger.warning("compile terminal event failed for %s", task_id, exc_info=True)


# ── task / model_download（E2 收尾，v3.9）─────────────────────

def complete_task(db, task_id: str) -> None:
    """任务成功终态：写 completed 通知（任务不存在 / 已删除时静默跳过）。"""
    _emit_task(db, task_id, "completed", None)


def fail_task(db, task_id: str, error_message: str | None = None) -> None:
    """任务失败终态：写 failed 通知（detail 截 200 字符）。"""
    _emit_task(db, task_id, "failed", (error_message or "")[:200] or None)


def _emit_task(db, task_id: str, kind: str, detail: str | None) -> None:
    """写 task 终态通知。任务不隶属项目（project_id / container 恒空，
    前端导航固定任务页）。事件写入失败不影响业务主流程。
    """
    try:
        row = db.conn.execute(
            "SELECT title FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if not row:
            logger.info("task terminal event skipped: task %s gone", task_id)
            return
        _store_and_publish(
            db,
            entity_type="task", entity_id=task_id,
            project_id=None,
            container="project",
            kind=kind,
            entity_title=row["title"] or task_id,
            detail=detail,
        )
    except Exception:
        logger.warning("task terminal event failed for %s", task_id, exc_info=True)


def complete_download(db, name: str) -> None:
    """模型下载成功终态：写 completed 通知。"""
    _emit_download(db, name, "completed", None)


def fail_download(db, name: str, error_message: str | None = None) -> None:
    """模型下载失败终态：写 failed 通知（detail 截 200 字符）。"""
    _emit_download(db, name, "failed", (error_message or "")[:200] or None)


def _emit_download(db, name: str, kind: str, detail: str | None) -> None:
    """写 model_download 终态通知（无项目实体，导航固定设置页内部模型锚点）。"""
    try:
        _store_and_publish(
            db,
            entity_type="model_download", entity_id=name,
            project_id=None,
            container="project",
            kind=kind,
            entity_title=name,
            detail=detail,
        )
    except Exception:
        logger.warning("download terminal event failed for %s", name, exc_info=True)


# ── form_template / form_fill（2.4 §3.4）──────────────

def complete_form_template(db, template_id: str, detail: str | None = None) -> None:
    """模板分析成功终态：写 completed 通知（模板不存在 / 已删除时静默跳过）。"""
    _emit_form_template(db, template_id, "completed", (detail or "")[:200] or None)


def fail_form_template(db, template_id: str, error_message: str | None = None) -> None:
    """模板分析失败终态（failed / invalid）：写 failed 通知（detail 截 200 字符）。"""
    _emit_form_template(db, template_id, "failed", (error_message or "")[:200] or None)


def _emit_form_template(db, template_id: str, kind: str, detail: str | None) -> None:
    """写 form_template 终态通知。模板是全局资产不隶属项目（2.4 §5 职责划分），
    project_id / container 恒空对齐 _emit_task 模式，前端导航固定模板管理页。"""
    try:
        row = db.conn.execute(
            "SELECT name FROM form_templates WHERE id = ?", (template_id,)
        ).fetchone()
        if not row:
            logger.info("form_template terminal event skipped: template %s gone", template_id)
            return
        _store_and_publish(
            db,
            entity_type="form_template", entity_id=template_id,
            project_id=None,
            container="project",
            kind=kind,
            entity_title=row["name"] or template_id,
            detail=detail,
        )
    except Exception:
        logger.warning("form_template terminal event failed for %s", template_id, exc_info=True)


def complete_form_fill(db, fill_id: str, detail: str | None = None) -> None:
    """填表成功终态：写 completed 通知（实例不存在 / 已删除时静默跳过）。"""
    _emit_form_fill(db, fill_id, "completed", (detail or "")[:200] or None)


def fail_form_fill(db, fill_id: str, error_message: str | None = None) -> None:
    """填表失败终态：写 failed 通知（detail 截 200 字符）。"""
    _emit_form_fill(db, fill_id, "failed", (error_message or "")[:200] or None)


def _emit_form_fill(db, fill_id: str, kind: str, detail: str | None) -> None:
    """写 form_fill 终态通知。JOIN projects 补 container（基础库项目同报告语义）。"""
    try:
        row = db.conn.execute(
            "SELECT f.project_id, f.title, "
            "CASE WHEN p.kind = 'base_lib' THEN 'baselib' ELSE 'project' END AS container "
            "FROM form_fills f JOIN projects p ON p.id = f.project_id "
            "WHERE f.id = ?",
            (fill_id,),
        ).fetchone()
        if not row:
            logger.info("form_fill terminal event skipped: fill %s gone", fill_id)
            return
        _store_and_publish(
            db,
            entity_type="form_fill", entity_id=fill_id,
            project_id=row["project_id"],
            container=row["container"] or "project",
            kind=kind,
            entity_title=row["title"],
            detail=detail,
        )
    except Exception:
        logger.warning("form_fill terminal event failed for %s", fill_id, exc_info=True)


# ── ppt（2.3 §3.5，E1）──────────────────────────

def complete_ppt(db, ppt_id: str, detail: str | None = None) -> None:
    """PPT 生成成功终态：写 completed 通知（ppt 不存在 / 已删除时静默跳过）。"""
    _emit_ppt(db, ppt_id, "completed", (detail or "")[:200] or None)


def fail_ppt(db, ppt_id: str, error_message: str | None = None) -> None:
    """PPT 失败终态：写 failed 通知（detail 截 200 字符）。"""
    _emit_ppt(db, ppt_id, "failed", (error_message or "")[:200] or None)


def pause_ppt(db, ppt_id: str, detail: str | None = None) -> None:
    """PPT 任务被用户手动暂停：稳定态通知，未读查重保证重复声明只产生一条。"""
    _emit_ppt(db, ppt_id, "paused", (detail or "")[:200] or None)


def gate_ppt(db, ppt_id: str, detail: str | None = None) -> None:
    """PPT 门禁待确认（awaiting_input，2.3 §3.4）：用户可任意时刻应答，
    不计超时；恢复运行后不再补发 running 通知（无终态语义）。"""
    _emit_ppt(db, ppt_id, "awaiting_input", (detail or "")[:200] or None)


def _emit_ppt(db, ppt_id: str, kind: str, detail: str | None) -> None:
    """写 ppt 终态/门禁通知。事件写入失败不影响业务主流程（状态已落库，通知是增益）。

    LEFT JOIN：模板工坊任务（kind='template'）是全局资产不隶属项目（2.3 §4.2），
    project_id 为空串 JOIN 不到 → project_id/container 按 kind 分流
    （container='templates'，前端导航到模板管理页），对齐 _emit_form_template 先例。"""
    try:
        row = db.conn.execute(
            "SELECT x.title, x.kind, "
            "CASE WHEN x.kind = 'template' THEN NULL ELSE x.project_id END AS project_id, "
            "CASE WHEN x.kind = 'template' THEN 'templates' "
            "     WHEN p.kind = 'base_lib' THEN 'baselib' ELSE 'project' END AS container "
            "FROM ppts x LEFT JOIN projects p ON p.id = x.project_id "
            "WHERE x.id = ?",
            (ppt_id,),
        ).fetchone()
        if not row:
            logger.info("ppt terminal event skipped: ppt %s gone", ppt_id)
            return
        _store_and_publish(
            db,
            entity_type="ppt", entity_id=ppt_id,
            project_id=row["project_id"],
            container=row["container"] or "project",
            kind=kind,
            entity_title=row["title"],
            detail=detail,
        )
    except Exception:
        logger.warning("ppt terminal event failed for %s", ppt_id, exc_info=True)


# ── transfer（2.9 §7）：导出/导入终态（run 是进程内存态不落库，
#    同 _emit_form_fill 无行查询模式，上下文由调用方显式传入）──

def complete_transfer(db, kind: str, entity_id: str, *, project_id: str | None,
                      entity_title: str, detail: str | None = None) -> None:
    """项目导出/导入成功终态：写 completed 通知。kind ∈ {project_export, project_import}。"""
    _emit_transfer(db, kind, entity_id, "completed", project_id=project_id,
                   entity_title=entity_title, detail=(detail or "")[:200] or None)


def fail_transfer(db, kind: str, entity_id: str, *, project_id: str | None,
                  entity_title: str, error_message: str | None = None) -> None:
    """项目导出/导入失败终态：写 failed 通知（detail 截 200 字符）。"""
    _emit_transfer(db, kind, entity_id, "failed", project_id=project_id,
                   entity_title=entity_title, detail=(error_message or "")[:200] or None)


def _emit_transfer(db, entity_type: str, entity_id: str, event_kind: str, *,
                   project_id: str | None, entity_title: str, detail: str | None) -> None:
    """写 transfer 终态通知。事件写入失败不影响业务主流程（通知是增益）。"""
    try:
        _store_and_publish(
            db,
            entity_type=entity_type, entity_id=entity_id,
            project_id=project_id,
            container="project",
            kind=event_kind,
            entity_title=entity_title,
            detail=detail,
        )
    except Exception:
        logger.warning("transfer terminal event failed for %s/%s",
                       entity_type, entity_id, exc_info=True)


# ── schedule_run（2.11 §5.5，E3）──────────────────────────

def complete_schedule_run(db, run_id: str, name: str) -> None:
    """订阅期次完成终态（R4 默认静默：通知中心记录 + 速览更新，不弹 Toast）。"""
    _emit_schedule_run(db, run_id, "completed", name, None)


def fail_schedule_run(db, run_id: str, name: str,
                      error: str | None = None) -> None:
    """订阅期次失败终态（R4：失败/熔断必弹 Toast，kind='failed'；熔断文案
    由调度器拼入 error）。"""
    _emit_schedule_run(db, run_id, "failed", name, (error or "")[:200] or None)


def _emit_schedule_run(db, run_id: str, kind: str, name: str,
                       detail: str | None) -> None:
    """写 schedule_run 终态通知。订阅是全局资产不隶属项目（2.11 §2），
    project_id 恒空对齐 _emit_task 模式，前端导航固定订阅管理台。"""
    try:
        _store_and_publish(
            db,
            entity_type="schedule_run", entity_id=run_id,
            project_id=None,
            container="project",
            kind=kind,
            entity_title=name,
            detail=detail,
        )
    except Exception:
        logger.warning("schedule_run terminal event failed for %s", run_id,
                       exc_info=True)


# ── 信息类通知通用入口（2.11 §3.1）───────────────

def notify(db, *, entity_type: str, entity_id: str, project_id: str | None,
           container: str, kind: str, entity_title: str,
           detail: str | None = None) -> None:
    """信息类通知通用入口：专用语义 kind（file_changed / due / overdue / …）优先，
    通用 info / warning 仅作无专用语义场景的兑底。与 _emit_transfer 同模式——
    信息类无实体行可查，上下文由调用方显式传入；走 _store_and_publish 收口，
    未读查重与 SSE 投递同终态事件实现；写入失败仅告警不影响业务主流程。"""
    try:
        _store_and_publish(
            db,
            entity_type=entity_type, entity_id=entity_id,
            project_id=project_id,
            container=container,
            kind=kind,
            entity_title=entity_title,
            detail=(detail or "")[:200] or None,
        )
    except Exception:
        logger.warning("notify event failed for %s/%s",
                       entity_type, entity_id, exc_info=True)


# ── 事件投递（E3 activity:stream，v3.9）────────────────────────

# 终态事件发布给 /activities/stream SSE 订阅者（推为主，心跳拉兜底 0.4 §6.4）。
# 写入方多为 worker 线程（pipeline / task_queue / task_agent / 下载线程），而
# asyncio.Queue 非线程安全——按 (loop, queue) 注册，经 loop.call_soon_threadsafe
# 投递；慢消费者队列满即丢（心跳拉兜底），不反压业务路径。
_subscribers: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
_subscribers_lock = threading.Lock()
_QUEUE_MAX = 200


def subscribe() -> asyncio.Queue:
    """注册当前事件循环上的一个 SSE 订阅者队列（须在 async 上下文调用）。"""
    q: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
    with _subscribers_lock:
        _subscribers.append((asyncio.get_running_loop(), q))
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    """反注册（SSE 断连 / 端点 generator 退出时调用，不存在时静默）。"""
    with _subscribers_lock:
        _subscribers[:] = [(loop, qq) for (loop, qq) in _subscribers if qq is not q]


def _publish(payload: dict) -> None:
    """向全部订阅者投递事件；单订阅者异常不影响其余。"""
    with _subscribers_lock:
        targets = list(_subscribers)
    for loop, q in targets:

        def _put(q: asyncio.Queue = q) -> None:
            try:
                q.put_nowait(payload)
            except Exception:
                pass  # 队列满（慢消费者）：丢弃，心跳拉兜底

        try:
            if loop.is_closed():
                continue
            loop.call_soon_threadsafe(_put)
        except RuntimeError:
            pass  # loop 已关闭（SSE 断连清理竞态窗口）：无害


def _store_and_publish(
    db,
    *,
    entity_type: str,
    entity_id: str,
    project_id: str | None,
    container: str,
    kind: str,
    entity_title: str,
    detail: str | None,
) -> None:
    """统一收口：写通知 + E3 事件投递。查重跳过时同步跳过投递（前端已见或即将见到）。"""
    notif_id = db.add_notification(
        entity_type=entity_type, entity_id=entity_id,
        project_id=project_id, container=container,
        kind=kind, entity_title=entity_title, detail=detail,
    )
    if notif_id is None:
        return
    _publish({
        "id": notif_id,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "project_id": project_id,
        "container": container,
        "kind": kind,
        "entity_title": entity_title,
        "detail": detail,
    })
