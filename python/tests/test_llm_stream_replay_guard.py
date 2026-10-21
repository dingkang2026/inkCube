"""python/tests/test_llm_stream_replay_guard.py — chat_stream 循环缺 break 回归单测。

实证（2026-10-14 天眼查任务 e57ce481）：任务最终回答 / thought / reasoning 均出现
两份独立措辞文本，且 LLM token 消耗翻倍。根因：`chat_stream` 的
`for stream_attempt in range(2)` 循环（400-reasoning-params 降级重试用）成功路径
缺 break——attempt=0 直接成功时循环进入第二轮，**同一请求发两次、两份完整流都被
yield**（temperature>0 → 两次采样措辞不同 → 拼接）。夹逼实证：既有
test_chat_stream_fallback（400 降级 → attempt=1 成功后 range 用完）恰为单请求；
本文件 test_success_makes_exactly_one_request（attempt=0 直接成功）修复前双请求。

HTTP 层用 httpx.MockTransport 替换 AsyncClient（手法同 test_llm_error_classification）。
"""
import asyncio
import json
import os
import sys

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import llm.client as client_module
from llm.client import LLMClient


def _client():
    return LLMClient({"api_url": "https://fake.api/v1", "api_key": "k", "model": "m", "max_tokens": 32})


def _install_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        client_module.httpx, "AsyncClient", lambda **kw: real_async_client(transport=transport, **kw)
    )


def _sse(*lines: bytes) -> bytes:
    return b"".join(ln + b"\n\n" for ln in lines) + b"data: [DONE]\n\n"


def _content_chunk(text: str) -> bytes:
    return ('data: {"choices": [{"delta": {"content": ' + json.dumps(text) + '}, "finish_reason": null}]}').encode()


def _finish_with_content_chunk(text: str) -> bytes:
    """罕见合法形态：finish 块自身携带 content（同块 fr+delta）。"""
    return ('data: {"choices": [{"delta": {"content": ' + json.dumps(text) + '}, "finish_reason": "stop"}]}').encode()


_FINISH_STOP = b'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}'


def _consume(monkeypatch, sse_body: bytes, calls: list):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(200, content=sse_body, headers={"Content-Type": "text/event-stream"})

    _install_transport(monkeypatch, handler)

    async def run():
        return [c async for c in _client().chat_stream([{"role": "user", "content": "hi"}])]

    return asyncio.run(run())


class TestStreamSingleRequest:
    def test_success_makes_exactly_one_request(self, monkeypatch):
        """attempt=0 直接成功 → 恰好 1 次 HTTP 请求、1 份流（修复前双请求双流）。"""
        calls = []
        chunks = _consume(monkeypatch, _sse(_content_chunk("回答。"), _FINISH_STOP), calls)
        assert len(calls) == 1
        contents = "".join(c["delta"] for c in chunks if c["type"] == "content")
        assert contents == "回答。"
        assert chunks[-1] == {"type": "finish", "finish_reason": "stop"}

    def test_finish_chunk_own_content_not_truncated(self, monkeypatch):
        """finish 块自身携带 content 的罕见形态：单请求下完整保留。"""
        calls = []
        chunks = _consume(monkeypatch, _sse(_content_chunk("回答主体。"), _finish_with_content_chunk("尾字。")), calls)
        assert len(calls) == 1
        contents = "".join(c["delta"] for c in chunks if c["type"] == "content")
        assert contents == "回答主体。尾字。"
        assert chunks[-1] == {"type": "finish", "finish_reason": "stop"}

    def test_normal_stream_unaffected(self, monkeypatch):
        """正常流逐 delta 原样透传，零改变。"""
        calls = []
        chunks = _consume(monkeypatch, _sse(_content_chunk("你好"), _content_chunk("！"), _FINISH_STOP), calls)
        assert len(calls) == 1
        assert [c["type"] for c in chunks] == ["content", "content", "finish"]
        assert chunks[0]["delta"] == "你好"
        assert chunks[1]["delta"] == "！"
        assert chunks[2]["finish_reason"] == "stop"


class TestStreamUsage:
    """D5：stream_options.include_usage 请求 + 流尾 usage chunk 事件化。"""

    def test_usage_chunk_yielded_before_finish(self, monkeypatch):
        """流尾 usage chunk（choices 空 + 顶层 usage）→ yield usage 事件，
        位于 finish 之前；旧实现在此处 continue 丢弃（token 记录缺失根因）。"""
        sse = _sse(
            _content_chunk("回答。"),
            _FINISH_STOP,
            b'data: {"choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}}',
        )
        calls = []
        chunks = _consume(monkeypatch, sse, calls)
        assert calls[0]["stream_options"] == {"include_usage": True}  # 请求侧声明
        types_seen = [c["type"] for c in chunks]
        assert types_seen == ["content", "usage", "finish"]
        assert chunks[1] == {"type": "usage", "usage": {
            "prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18,
        }}

    def test_no_usage_chunk_no_event(self, monkeypatch):
        """端点不理会 stream_options（无 usage chunk）→ 不 yield usage 事件
        （向后兼容：消费方必须把 usage 事件当可选）。"""
        calls = []
        chunks = _consume(monkeypatch, _sse(_content_chunk("回答。"), _FINISH_STOP), calls)
        assert [c["type"] for c in chunks] == ["content", "finish"]

    def test_stream_options_dropped_on_400(self, monkeypatch):
        """非标端点拒识别 stream_options（400）→ 去键重试一次成功（降级预算与
        reasoning 回退共享一轮，不额外增加请求数）。"""
        sse = _sse(_content_chunk("回答。"), _FINISH_STOP)
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            calls.append(body)
            if "stream_options" in body:
                return httpx.Response(400, json={"error": "unknown field: stream_options"})
            return httpx.Response(200, content=sse, headers={"Content-Type": "text/event-stream"})

        chunks = _consume_with_handler(monkeypatch, handler, calls)
        assert len(calls) == 2
        assert "stream_options" in calls[0]
        assert "stream_options" not in calls[1]
        assert [c["type"] for c in chunks] == ["content", "finish"]


def _consume_with_handler(monkeypatch, handler, calls: list):
    _install_transport(monkeypatch, handler)

    async def run():
        return [c async for c in _client().chat_stream([{"role": "user", "content": "hi"}])]

    return asyncio.run(run())
