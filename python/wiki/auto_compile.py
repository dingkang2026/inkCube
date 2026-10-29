"""python/wiki/auto_compile.py — 事件驱动自动汇编（2.6 §4.1 路径 A）.

文件解析完成（files.status='done'）边沿自动触发 Wiki 增量编译，缝合
「解析完成 → 无人触发 → 知识沉睡」的复利断点：设计理念是每次新资料
输入都增量编译（§1.1 知识复利），此前唯一触发路径是人工点击（通知模式）。

点火聚合（2026-10-28 A1）：解析边沿只登记点火意图（schedule_auto_compile），
由 5s QueueScanner 周期检查到期（DELAY_SECONDS）执行 try_auto_compile——
窗口内后续完成的文件由到期时守卫③的待编译集合一次性吸收，消除
「编译比解析快」时批量上传被拆成多次小编译的碎片化（重复 Stage 2 注入
已有页面 + UI 噪音 + 编译后挂点多次触发）。首完成起算不重置（最坏延迟
有界）；登记为进程内存态，重启丢失 = 该批次退化为手动入口（与冷却同语义）。

守卫六重（全过才创建任务，任一不过静默跳过并记日志）：
  ① 全局开关 settings.auto_wiki_compile（默认 True）
  ② 项目存在且 kind='project' 且未归档（archived_at IS NULL；基础库不编译，§1.3）
  ③ 待编译集合非空（get_pending_wiki_files）
  ④ 无活跃编译任务（与 trigger_compile 409 同口径——运行中编译逐轮重查
     待编译集合，新完成文件由当前轮自动吸收，无需点火）
  ⑤ 不在冷却期（mark_user_stop 写入；防止停止后下一文件解析完立刻重燃）
  ⑥ LLM/嵌入就绪（readiness 同源缓存判定；未就绪静默跳过，配置引导由
     人工入口 guard 承担，0.4 §7.3）

守卫检查与任务创建均为同步 DB 操作，事件循环内原子，无 check-then-act
竞态（同项目并发解析完成时后到者必然看到先到者创建的 queued 任务）。

冷却为进程内存态（重启清零）——会话级防重燃辅助，非持久承诺；手动
触发不受冷却限制。升级存量：settings kv 新键缺省即真（无迁移）；存量
积压不在启动时补点火（避免升级即全项目烧 token），仍走人工入口。
"""
import logging
import time

logger = logging.getLogger(__name__)

# 停止后自动点火冷却（秒）：会话级抑制，防止「停止 → 下一文件解析完 → 立刻重燃」
COOLDOWN_SECONDS = 30 * 60

# 点火聚合延迟（秒）：本地批量文件解析通常秒级完成，60s 足以聚合一波上传；
# 单文件自动汇编延后 60s 无感（增量编译非实时需求）。不暴露设置（避免
# 设置膨胀），后续有需求再开放。
DELAY_SECONDS = 60

# project_id -> 点火截止 epoch（time.time()）；首完成登记，到期由 scanner
# 执行后移除。进程内存态（重启清零），量级 = 项目数
_SCHEDULED: dict[str, float] = {}

# project_id -> 冷却截止 epoch（time.time()）；懒清理，量级 = 项目数，无需定时器
_COOLDOWN_UNTIL: dict[str, float] = {}

# 活跃编译状态（与 routers/wiki.py trigger_compile 409 判定同口径；字面量
# 本地副本，防 router → wiki 服务层反向依赖成环）
_ACTIVE_STATUSES = ("queued", "ingesting", "storing")


def schedule_auto_compile(project_id: str) -> None:
    """解析完成边沿登记点火意图（同步、幂等）。

    已登记不重置——首触发起算，窗口最坏延迟有界；到期执行由 QueueScanner
    调 pop_due_projects 取出后走 try_auto_compile（六重守卫原样）。"""
    if project_id in _SCHEDULED:
        return
    _SCHEDULED[project_id] = time.time() + DELAY_SECONDS
    logger.info("auto_compile: scheduled for %s (delay %ds)",
                project_id, DELAY_SECONDS)


def pop_due_projects(now: float | None = None) -> list[str]:
    """scanner 每轮调用：取出到期项目（取出即移除登记，失败不重试——
    错过退化手动入口）。now 参数供测试注入时钟。"""
    now = time.time() if now is None else now
    due = [pid for pid, until in _SCHEDULED.items() if now >= until]
    for pid in due:
        _SCHEDULED.pop(pid, None)
    return due


def mark_user_stop(project_id: str) -> None:
    """编译停止端点成功后调用：该项目进入冷却期，自动点火暂缓。

    幂等（覆盖写）；仅抑制自动路径，人工触发不受限。"""
    _COOLDOWN_UNTIL[project_id] = time.time() + COOLDOWN_SECONDS
    logger.info("auto_compile: cooldown %ds set for %s (user stop)",
                COOLDOWN_SECONDS, project_id)


def in_cooldown(project_id: str) -> bool:
    """冷却判定（顺带懒清理过期项）。"""
    until = _COOLDOWN_UNTIL.get(project_id)
    if until is None:
        return False
    if time.time() >= until:
        _COOLDOWN_UNTIL.pop(project_id, None)
        return False
    return True


def _readiness_ready(feature: str) -> bool:
    """就绪判定（readiness 10s TTL 缓存同源，§7.3 第三层数据同源）。

    判定异常视为未就绪并告警——自动点火是解析主流程的旁路附加动作，
    任何就绪判定侧异常都不允许打断解析链路。"""
    try:
        from readiness import get_features_cached
        info = get_features_cached().get(feature)
        return bool(info and info["ready"])
    except Exception:
        logger.warning("auto_compile: readiness check failed for %s",
                       feature, exc_info=True)
        return False


def try_auto_compile(db, project_id: str) -> dict | None:
    """到期点火意图的执行入口（同步；入队由调用方 enqueue_compile）。

    守卫①-⑥逐层判定，任一不过返回 None（跳过原因分记 debug/info 日志，
    便于「为什么没自动编」的日志溯源）；全过则创建 trigger_type='auto'
    的编译任务并返回任务行。enqueue_compile 失败由 5s QueueScanner 兜底
    重入队（2.6 §4.1，与人工路径同韧性），故本函数不接触队列。
    """
    settings = db.get_settings()
    if not settings.get("auto_wiki_compile", True):
        return None

    project = db.get_project(project_id)
    if not project or project.get("kind") != "project" or project.get("archived_at"):
        logger.debug("auto_compile: %s not an active project — skipped", project_id)
        return None

    if not db.get_pending_wiki_files(project_id):
        return None

    latest = db.get_latest_compile_task(project_id)
    if latest and latest["status"] in _ACTIVE_STATUSES:
        logger.debug("auto_compile: %s has active compile (%s) — running "
                     "loop will absorb new files", project_id, latest["status"])
        return None

    if in_cooldown(project_id):
        logger.info("auto_compile: %s in cooldown — skipped", project_id)
        return None

    if not _readiness_ready("wiki_compile"):
        logger.info("auto_compile: %s skipped — LLM/embedding not ready "
                    "(guide via manual entry guard, 0.4 §7.3)", project_id)
        return None

    task = db.create_wiki_compile_task(project_id, trigger_type="auto")
    logger.info("auto_compile: fired for %s (task %s)", project_id, task["id"])
    return task
