"""python/tests/test_llm_error_classification.py — LLM API 错误分类单测（0.4 §7.3 v3.6）。

覆盖：chat_full / chat_stream 两路径对非 200 响应的结构化分类——
  401→LLMInvalidKey / 429→LLMRateLimited / 402·403→LLMQuotaExhausted /
  404→LLMModelNotFound（均 CREDENTIAL 类，suggestion 自带设置页修正指引）；
  500→LLMCallFailed；400+tools→LLMCallFailed 且 detail 含 function calling 提示。

HTTP 层用 httpx.MockTransport 替换 AsyncClient（手法同 test_reasoning_params）。
"""
import asyncio
import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import llm.client as client_module
from llm.client import LLMClient
from errors import (
    LLMInvalidKey, LLMRateLimited, LLMQuotaExhausted, LLMModelNotFound, LLMCallFailed,
)


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


_SSE_OK = (
    b'data: {"choices": [{"delta": {"content": "hi"}, "finish_reason": null}]}\n\n'
    b'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}\n\n'
    b"data: [DONE]\n\n"
)

# (status, 期望异常类, message 关键字, suggestion 是否必须指向设置页)：
# 429 限流/用量的修正动作在服务商控制台（查余额与限额），不强制设置页指引
CONFIG_CASES = [
    (401, LLMInvalidKey, "API Key 无效", True),
    (429, LLMRateLimited, "调用频率或用量超限", False),
    (402, LLMQuotaExhausted, "余额不足", True),
    (403, LLMQuotaExhausted, "余额不足", True),
    (404, LLMModelNotFound, "模型不存在", True),
]


class TestChatFullClassification:
    @pytest.mark.parametrize("status,err_cls,keyword,needs_settings", CONFIG_CASES)
    def test_chat_full_config_errors(self, monkeypatch, status, err_cls, keyword, needs_settings):
        _install_transport(monkeypatch, lambda request: httpx.Response(status, json={"error": "x"}))
        with pytest.raises(err_cls) as ei:
            asyncio.run(_client().chat_full([{"role": "user", "content": "hi"}]))
        assert keyword in ei.value.message
        assert ei.value.suggestion
        if needs_settings:
            assert "设置" in ei.value.suggestion

    def test_chat_full_500_stays_call_failed(self, monkeypatch):
        _install_transport(monkeypatch, lambda request: httpx.Response(500, json={"error": "boom"}))
        with pytest.raises(LLMCallFailed):
            asyncio.run(_client().chat_full([{"role": "user", "content": "hi"}]))


class TestChatStreamClassification:
    @pytest.mark.parametrize("status,err_cls,keyword,needs_settings", CONFIG_CASES)
    def test_stream_config_errors(self, monkeypatch, status, err_cls, keyword, needs_settings):
        _install_transport(monkeypatch, lambda request: httpx.Response(status, json={"error": "x"}))

        async def consume():
            return [c async for c in _client().chat_stream([{"role": "user", "content": "hi"}])]

        with pytest.raises(err_cls) as ei:
            asyncio.run(consume())
        assert keyword in ei.value.message
        assert ei.value.suggestion
        if needs_settings:
            assert "设置" in ei.value.suggestion

    def test_stream_success_still_works(self, monkeypatch):
        _install_transport(
            monkeypatch,
            lambda request: httpx.Response(200, content=_SSE_OK, headers={"Content-Type": "text/event-stream"}),
        )

        async def consume():
            return [c async for c in _client().chat_stream([{"role": "user", "content": "hi"}])]

        chunks = asyncio.run(consume())
        assert chunks[-1] == {"type": "finish", "finish_reason": "stop"}


class TestFunctionCallingHint:
    def test_400_with_tools_keeps_fc_hint(self, monkeypatch):
        """400 + tools → LLMCallFailed 且 detail 保留 function calling 提示（既有行为不回归）。"""
        tools = [{"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}]
        _install_transport(monkeypatch, lambda request: httpx.Response(400, json={"error": "bad"}))
        with pytest.raises(LLMCallFailed) as ei:
            asyncio.run(_client().chat_full([{"role": "user", "content": "hi"}], tools=tools))
        assert "function calling" in ei.value.detail
