"""LLM/VLM token 用量记录层（2.10 §4.1）。

单一收口：LLMClient / VLMClient 内部调用 record()（每出口一处），业务侧
只在入口 push_context() 声明「这次调用属于谁」；本模块零 DB 依赖——sink
由 main.py 组装期注入（deps.db.insert_llm_usage），llm/ 保持无
agent/knowledge/db 依赖的既有分层。

精确值原则：只落 API 返回的 usage，无 usage 不落库（宁缺勿估）。
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UsageContext:
    """一次业务调用的归属环境（task 轮次 / 项目 / 实体）。

    source 缺省 'other'：漏 push 上下文的调用在 record 时告警，可观测不缺账。
    """

    source: str = "other"
    purpose: str | None = None
    task_id: str | None = None
    round_id: str | None = None
    project_id: str | None = None
    entity_id: str | None = None
    detail: dict | None = None


_context: ContextVar = ContextVar("llm_usage_context", default=None)
_sink = None  # Callable[[dict], None]（db.insert_llm_usage），configure() 注入
_sink_lock = threading.Lock()


def configure(sink) -> None:
    """注入 DB 写入函数（main.py lifespan 组装期调用一次）。"""
    global _sink
    with _sink_lock:
        _sink = sink


@contextmanager
def push_context(ctx: UsageContext):
    """业务入口声明环境。

    contextvar 随 async 任务原生隔离（并发的两个任务互不可见）；
    不跨线程传播——run_in_executor 等线程入口须在线程函数体内自行 push。
    """
    token = _context.set(ctx)
    try:
        yield ctx
    finally:
        _context.reset(token)


def set_context(ctx: UsageContext):
    """token 式手动 push（无法用 with 包裹的大块执行体，如路由层
    async for 循环）；与 reset_context 成对使用。"""
    return _context.set(ctx)


def reset_context(token) -> None:
    """恢复 set_context 之前的环境（路由层 finally 中调用）。"""
    _context.reset(token)


@contextmanager
def push_purpose(purpose: str, detail: dict | None = None):
    """在既有环境上仅覆盖 purpose/detail（报告 runner 各调用点细分用）。"""
    base = _context.get() or UsageContext()
    token = _context.set(replace(base, purpose=purpose, detail=detail))
    try:
        yield
    finally:
        _context.reset(token)


def record(model: str, mode: str, usage: dict | None) -> None:
    """client 出口收口点：一次 API 调用写一行 llm_usage。

    失败仅日志，绝不影响业务主流程（INSERT 本身不会失败，此为纵深防御）。
    """
    if not usage:
        return  # 无精确值不落库（宁缺勿估，流式端点不支持 include_usage 的情形）
    sink = _sink
    if sink is None:
        return  # 未注入（测试环境/组装前）不落库
    ctx = _context.get() or UsageContext()
    if ctx.source == "other":
        logger.warning(
            "llm usage recorded without context (source=other): model=%s mode=%s",
            model, mode,
        )
    row = {
        "ts": datetime.now().isoformat(),
        "source": ctx.source,
        "purpose": ctx.purpose,
        "task_id": ctx.task_id,
        "round_id": ctx.round_id,
        "project_id": ctx.project_id,
        "entity_id": ctx.entity_id,
        "model": model,
        "mode": mode,
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens")
                            or (usage.get("prompt_tokens") or 0)
                            + (usage.get("completion_tokens") or 0)),
        "detail": ctx.detail,
    }
    try:
        sink(row)
    except Exception:
        logger.error(
            "llm usage sink failed (source=%s model=%s)", ctx.source, model,
            exc_info=True,
        )
