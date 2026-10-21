"""python/tests/test_llm_usage_client.py — LLM 出口记录点回归（2.10 §4.2/§8）。

MockTransport 手法同 test_llm_stream_replay_guard。锁定三个记录不变量：
① 每个成功出口恰记录一行（400 降级重试发生在 _post_json/流循环内部，
record 只在最终成功响应后执行，天然不重复计数）② 无 usage chunk 不落库
（宁缺勿估）③ 探针 test_connection 不经记录点（非业务消耗，§7）。
"""
import asyncio
import json
import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import llm.client as client_module
from llm.client import LLMClient
from llm.usage import configure


def _client():
    return LLMClient({"api_url": "https://fake.api/v1", "api_key": "k",
                      "model": "m", "max_tokens": 32})


def _client_reasoning():
    """带 reasoning_effort 配置：注入 reasoning 参数后 400 降级路径才触发。"""
    return LLMClient({"api_url": "https://fake.api/v1", "api_key": "k",
                      "model": "m", "max_tokens": 32,
                      "reasoning_effort": "high"})


def _install_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        client_module.httpx, "AsyncClient",
        lambda **kw: real_async_client(transport=transport, **kw),
    )


_FULL_BODY = {
    "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
}

_SSE_WITH_USAGE = (
    b'data: {"choices": [{"delta": {"content": "ok"}, "finish_reason": null}]}\n\n'
    b'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}\n\n'
    b'data: {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 2,'
    b' "total_tokens": 7}}\n\n'
    b"data: [DONE]\n\n"
)

_SSE_WITHOUT_USAGE = (
    b'data: {"choices": [{"delta": {"content": "ok"}, "finish_reason": null}]}\n\n'
    b'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}\n\n'
    b"data: [DONE]\n\n"
)

# 智谱 BigModel / DeepSeek 等兼容端点形态：无独立 usage-only chunk，
# usage 附在最后一个带 choices 的 chunk（与 finish_reason 同 chunk）。
# glm-5.3 实测 2026-10-16：只认 OpenAI usage-only 形态会整条流丢账。
_SSE_ZHIPU_STYLE = (
    b'data: {"choices": [{"delta": {"content": "ok"}, "finish_reason": null}]}\n\n'
    b'data: {"choices": [{"delta": {}, "finish_reason": "stop"}],'
    b' "usage": {"prompt_tokens": 17, "completion_tokens": 16, "total_tokens": 33}}\n\n'
    b"data: [DONE]\n\n"
)


@pytest.fixture()
def sink_rows():
    rows = []
    configure(rows.append)
    yield rows
    configure(None)


def test_chat_full_records_exact_usage(sink_rows, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_FULL_BODY)

    _install_transport(monkeypatch, handler)
    result = asyncio.run(_client().chat_full([{"role": "user", "content": "hi"}]))
    assert result["usage"]["total_tokens"] == 18
    assert len(sink_rows) == 1
    row = sink_rows[0]
    assert row["model"] == "m" and row["mode"] == "full"
    assert row["prompt_tokens"] == 11
    assert row["completion_tokens"] == 7
    assert row["total_tokens"] == 18


def test_chat_full_400_retry_records_once(sink_rows, monkeypatch):
    """400(reasoning 参数) → 降级重试成功：恰 1 行（首次 400 响应无 usage 不记）。"""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        if "reasoning_effort" in body:
            return httpx.Response(400, json={"error": "unknown field: reasoning_effort"})
        return httpx.Response(200, json=_FULL_BODY)

    _install_transport(monkeypatch, handler)
    asyncio.run(_client_reasoning().chat_full([{"role": "user", "content": "hi"}]))
    assert len(calls) == 2
    assert "reasoning_effort" in calls[0]
    assert "reasoning_effort" not in calls[1]
    assert len(sink_rows) == 1


def test_chat_stream_usage_chunk_records_once(sink_rows, monkeypatch):
    """流尾 usage chunk（choices 空 + 顶层 usage）→ 恰 1 行 mode=stream。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_SSE_WITH_USAGE,
                              headers={"Content-Type": "text/event-stream"})

    _install_transport(monkeypatch, handler)

    async def run():
        return [c async for c in _client().chat_stream([{"role": "user", "content": "hi"}])]

    chunks = asyncio.run(run())
    assert [c["type"] for c in chunks] == ["content", "usage", "finish"]
    assert len(sink_rows) == 1
    assert sink_rows[0]["mode"] == "stream"
    assert sink_rows[0]["total_tokens"] == 7


def test_chat_stream_usage_on_last_choices_chunk_records_once(sink_rows, monkeypatch):
    """兼容端点形态（智谱/DeepSeek 等）：usage 附在末个带 choices 的 chunk → 恰 1 行。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_SSE_ZHIPU_STYLE,
                              headers={"Content-Type": "text/event-stream"})

    _install_transport(monkeypatch, handler)

    async def run():
        return [c async for c in _client().chat_stream([{"role": "user", "content": "hi"}])]

    chunks = asyncio.run(run())
    assert [c["type"] for c in chunks] == ["content", "usage", "finish"]
    assert len(sink_rows) == 1
    row = sink_rows[0]
    assert row["mode"] == "stream"
    assert row["prompt_tokens"] == 17
    assert row["completion_tokens"] == 16
    assert row["total_tokens"] == 33


def test_chat_stream_without_usage_chunk_records_nothing(sink_rows, monkeypatch):
    """端点不理会 stream_options（无 usage chunk）→ 不落库，usage 事件缺席。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_SSE_WITHOUT_USAGE,
                              headers={"Content-Type": "text/event-stream"})

    _install_transport(monkeypatch, handler)

    async def run():
        return [c async for c in _client().chat_stream([{"role": "user", "content": "hi"}])]

    chunks = asyncio.run(run())
    assert [c["type"] for c in chunks] == ["content", "finish"]
    assert sink_rows == []


def test_stream_400_degraded_retry_no_double_record(sink_rows, monkeypatch):
    """stream_options 400 降级重试：无 usage chunk → 0 行，且第二轮请求
    （去键后）只可能产生一轮记录尝试。"""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        if "stream_options" in body:
            return httpx.Response(400, json={"error": "unknown field: stream_options"})
        return httpx.Response(200, content=_SSE_WITHOUT_USAGE,
                              headers={"Content-Type": "text/event-stream"})

    _install_transport(monkeypatch, handler)

    async def run():
        return [c async for c in _client().chat_stream([{"role": "user", "content": "hi"}])]

    asyncio.run(run())
    assert len(calls) == 2
    assert "stream_options" not in calls[1]
    assert sink_rows == []


def test_stream_400_degraded_retry_then_zhipu_usage_records_once(sink_rows, monkeypatch):
    """400 降级 × 智谱形态组合：stream_options 被拒 → 降级重试成功 →
    末个带 choices 的 chunk 附 usage → 恰 1 行。双 attempt 的 usage_data
    等状态逐轮重初始化，降级路径不重复记录。"""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "stream_options" in body:
            return httpx.Response(400, json={"error": "unknown field: stream_options"})
        return httpx.Response(200, content=_SSE_ZHIPU_STYLE,
                              headers={"Content-Type": "text/event-stream"})

    _install_transport(monkeypatch, handler)

    async def run():
        return [c async for c in _client().chat_stream([{"role": "user", "content": "hi"}])]

    chunks = asyncio.run(run())
    assert [c["type"] for c in chunks] == ["content", "usage", "finish"]
    assert len(sink_rows) == 1
    assert sink_rows[0]["total_tokens"] == 33


def test_test_connection_not_recorded(sink_rows, monkeypatch):
    """探针调用（test_connection）不经记录点：非业务消耗天然不计（§7）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    _install_transport(monkeypatch, handler)
    assert asyncio.run(_client().test_connection()) is True
    assert sink_rows == []
