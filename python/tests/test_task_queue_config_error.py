"""python/tests/test_task_queue_config_error.py — Wiki 编译配置类错误跳过重试单测（0.4 §7.3 v3.6）。

覆盖 KnowledgeWorker._process_compile：
  - 四类配置/凭据错误（Key 失效/限流/欠费/模型名错）→ 直接 failed 终态 + actionable
    文案（含设置页指引），不进入退避重试；
  - 普通异常 → 保持退避重试（increment + queued，文案带 Retry 进度）；
  - 重试耗尽 → failed 终态。

KnowledgeWorker 用 __new__ 跳过 __init__，db 用 MagicMock；WikiCompiler 经
monkeypatch 函数内 import 点注入（compile 按用例抛出目标异常）。
"""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from knowledge.task_queue import KnowledgeWorker
from errors import (
    LLMInvalidKey, LLMRateLimited, LLMQuotaExhausted, LLMModelNotFound, actionable,
)
import activity_events

CONFIG_ERRORS = [LLMInvalidKey(), LLMRateLimited(), LLMQuotaExhausted(), LLMModelNotFound()]


def _worker(retry_count: int = 0, max_retries: int = 3) -> KnowledgeWorker:
    w = KnowledgeWorker.__new__(KnowledgeWorker)
    w.db = MagicMock()
    w.data_dir = "/tmp/data"
    w.db.get_wiki_compile_task.return_value = {
        "task_id": "t1", "project_id": "p1", "status": "queued",
        "retry_count": retry_count, "max_retries": max_retries,
    }
    w.db.get_project.return_value = {"id": "p1"}
    # backoff_base 调小：重试分支的 create_task(_requeue_after_backoff) 立即到期
    w.db.get_settings.return_value = {
        "compile_max_retries": max_retries, "compile_backoff_base": 0.01,
    }
    w.db.is_compile_supervised.return_value = False
    w.db.increment_compile_task_retry.return_value = retry_count + 1
    w.enqueue_compile = AsyncMock()
    return w


def _patch_compiler(monkeypatch, exc: Exception | None):
    class _FakeCompiler:
        def __init__(self, **kwargs):
            pass

        async def compile(self, **kwargs):
            if exc is not None:
                raise exc
            return "ok"

    monkeypatch.setattr("wiki.compiler.WikiCompiler", _FakeCompiler)


class TestConfigErrorSkipsRetry:
    @pytest.mark.parametrize("err", CONFIG_ERRORS, ids=lambda e: e.code)
    def test_goes_straight_to_failed(self, monkeypatch, err):
        w = _worker()
        _patch_compiler(monkeypatch, err)
        fail_compile = MagicMock()
        monkeypatch.setattr(activity_events, "fail_compile", fail_compile)
        asyncio.run(w._process_compile("t1"))

        w.db.update_compile_task_status.assert_called_once_with(
            "t1", "failed", error_message=actionable(err),
        )
        # 终态事件经 activity_events 显式声明（E2，顺序 4），detail 携带可行动文案
        fail_compile.assert_called_once_with(w.db, "t1", actionable(err))
        # 配置类错误重试无意义：不得进入退避重试
        w.db.increment_compile_task_retry.assert_not_called()
        w.enqueue_compile.assert_not_called()

    def test_error_message_carries_guidance(self, monkeypatch):
        w = _worker()
        _patch_compiler(monkeypatch, LLMInvalidKey())
        asyncio.run(w._process_compile("t1"))

        error_message = w.db.update_compile_task_status.call_args.kwargs["error_message"]
        assert "设置 > 外部模型" in error_message


class TestTransientErrorRetries:
    def test_retries_with_backoff(self, monkeypatch):
        w = _worker(retry_count=0, max_retries=3)
        _patch_compiler(monkeypatch, RuntimeError("boom"))
        asyncio.run(w._process_compile("t1"))

        w.db.increment_compile_task_retry.assert_called_once_with("t1")
        call = w.db.update_compile_task_status.call_args
        assert call.args[1] == "queued"
        assert "Retry 1/3" in call.kwargs["error_message"]

    def test_fails_after_exhaustion(self, monkeypatch):
        w = _worker(retry_count=3, max_retries=3)
        _patch_compiler(monkeypatch, RuntimeError("boom"))
        fail_compile = MagicMock()
        monkeypatch.setattr(activity_events, "fail_compile", fail_compile)
        asyncio.run(w._process_compile("t1"))

        w.db.increment_compile_task_retry.assert_not_called()
        call = w.db.update_compile_task_status.call_args
        assert call.args[1] == "failed"
        # 重试耗尽分支同样显式声明 fail_compile（E2，顺序 4）
        fail_compile.assert_called_once_with(w.db, "t1", actionable(RuntimeError("boom")))
