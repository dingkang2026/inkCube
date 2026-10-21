"""frequency_penalty 注入回归测试（S1 残留修复）。

背景：glm-5.3 在 finish=stop 的 content 里会把回答输出两遍（任务 a5a63df4
[5][18] 实证 2/8 中招），prompt 禁令（task_agent L110）只压住了标记词、
压不住重复。生成参数层修复：两处请求体（chat_full / chat_stream）
默认注入 frequency_penalty=0.3；settings 可覆盖；0 表示不注入（端点兼容
逃生门）。
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx

import llm.client as client_module
from llm.client import LLMClient


def _client(**overrides):
    base = {"api_url": "https://fake.api/v1", "api_key": "k", "model": "m", "max_tokens": 32}
    base.update(overrides)
    return LLMClient(base)


def _install_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        client_module.httpx, "AsyncClient", lambda **kw: real_async_client(transport=transport, **kw)
    )


def _ok_response():
    return httpx.Response(200, json={
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    })


def _capture_requests(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return _ok_response()

    _install_transport(monkeypatch, handler)
    return calls


_STREAM_BODY = (
    b'data: {"choices": [{"delta": {"content": "hi"}, "finish_reason": null}]}\n\n'
    b'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}\n\n'
    b"data: [DONE]\n\n"
)


class TestFrequencyPenaltyInjection:
    def test_default_0_3_in_chat_full(self, monkeypatch):
        calls = _capture_requests(monkeypatch)
        asyncio.run(_client().chat_full([{"role": "user", "content": "hi"}]))
        assert calls[0]["frequency_penalty"] == 0.3

    def test_default_0_3_in_chat_stream(self, monkeypatch):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(json.loads(request.content))
            return httpx.Response(200, content=_STREAM_BODY)

        _install_transport(monkeypatch, handler)

        async def _drain():
            async for _ in _client().chat_stream([{"role": "user", "content": "hi"}]):
                pass

        asyncio.run(_drain())
        assert calls[0]["frequency_penalty"] == 0.3

    def test_settings_override(self, monkeypatch):
        calls = _capture_requests(monkeypatch)
        asyncio.run(
            _client(frequency_penalty=0.5).chat_full([{"role": "user", "content": "hi"}]))
        assert calls[0]["frequency_penalty"] == 0.5

    def test_zero_disables_injection(self, monkeypatch):
        calls = _capture_requests(monkeypatch)
        asyncio.run(
            _client(frequency_penalty=0).chat_full([{"role": "user", "content": "hi"}]))
        assert "frequency_penalty" not in calls[0]

    def test_with_tools_still_injected(self, monkeypatch):
        """FC 路径（chat_full tools 形参）同样注入。"""
        calls = _capture_requests(monkeypatch)
        tools = [{"type": "function", "function": {
            "name": "t", "parameters": {"type": "object", "properties": {}}}}]
        asyncio.run(_client().chat_full([{"role": "user", "content": "hi"}], tools=tools))
        assert calls[0]["frequency_penalty"] == 0.3
