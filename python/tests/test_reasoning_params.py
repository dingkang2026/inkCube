"""python/tests/test_reasoning_params.py — 思考参数注入 / 400 降级 / 能力探测单测。

覆盖：
  A. _apply_reasoning_params：档位注入 / override 优先（JSON 对象 merge）/
     非法 JSON 忽略 / 非对象忽略 / 双空不注入 / rollback 恢复与删除
  B. 400 降级：chat / chat_full / chat_stream 收到 400 且含注入参数 →
     回滚参数重试一次成功；400 持续或无注入参数时直接抛错
  C. probe()：200→True / 400→False / 网络异常（含读阶段断连）→None
  D. /settings/personal_test：连通 + 三项能力探测结构 / 连接失败 /
     网络失败中止后续探测
  E. 回归：400 降级后同实例记忆（后续调用不再注入）；
     db.get_settings() 白名单含 reasoning 字段（C1 回归）

HTTP 层用 httpx.MockTransport 替换 AsyncClient；async 统一 asyncio.run 驱动。
"""
import asyncio
import json
import os
import sys

import httpx
import pytest

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import llm.client as client_module
from llm.client import LLMClient, _ABSENT
from errors import LLMCallFailed
from schemas import SettingsModel
from routers import settings as settings_router


def _client(**overrides):
    base = {"api_url": "https://fake.api/v1", "api_key": "k", "model": "m", "max_tokens": 32}
    base.update(overrides)
    return LLMClient(base)


def _install_transport(monkeypatch, handler):
    """Route all httpx.AsyncClient calls inside llm.client through a MockTransport."""
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


# ── A. 注入 / 回滚 ──────────────────────────────────────────


class TestApplyReasoningParams:
    def test_effort_injected(self):
        c = _client()
        body = {"model": "m"}
        originals = c._apply_reasoning_params(body, {"reasoning_effort": "low"})
        assert body["reasoning_effort"] == "low"
        assert originals == {"reasoning_effort": _ABSENT}

    def test_override_takes_priority_over_effort(self):
        c = _client()
        body = {"model": "m"}
        originals = c._apply_reasoning_params(
            body,
            {"reasoning_effort": "low", "reasoning_override": '{"thinking": {"type": "disabled"}}'},
        )
        assert body["thinking"] == {"type": "disabled"}
        assert "reasoning_effort" not in body
        assert originals == {"thinking": _ABSENT}

    def test_invalid_json_override_falls_back_to_effort(self):
        c = _client()
        body = {"model": "m"}
        c._apply_reasoning_params(body, {"reasoning_effort": "low", "reasoning_override": "{oops"})
        assert body["reasoning_effort"] == "low"

    def test_non_object_override_ignored(self):
        # 合法 JSON 但非对象（字符串/数组）→ 忽略，回落档位
        c = _client()
        body = {"model": "m"}
        c._apply_reasoning_params(body, {"reasoning_effort": "high", "reasoning_override": '"xhigh"'})
        assert body["reasoning_effort"] == "high"

    def test_empty_settings_inject_nothing(self):
        c = _client()
        body = {"model": "m"}
        assert c._apply_reasoning_params(body, {}) == {}
        assert "reasoning_effort" not in body

    def test_rollback_restores_preexisting_value(self):
        c = _client()
        body = {"temperature": 0.7}
        originals = c._apply_reasoning_params(
            body, {"reasoning_override": '{"temperature": 1.5, "thinking": "on"}'}
        )
        assert body["temperature"] == 1.5
        c._rollback_reasoning_params(body, originals)
        assert body["temperature"] == 0.7      # 覆盖已有键 → 恢复原值
        assert "thinking" not in body           # 新增键 → 删除


# ── B. 400 降级（chat / chat_full / chat_stream）────────────


class TestChat400Fallback:
    def test_chat_retries_without_reasoning_params(self, monkeypatch):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            calls.append(body)
            if "reasoning_effort" in body:
                return httpx.Response(400, json={"error": "unknown parameter"})
            return _ok_response()

        _install_transport(monkeypatch, handler)
        result = asyncio.run(
            _client().chat([{"role": "user", "content": "hi"}], {"reasoning_effort": "low"})
        )
        assert result == "ok"
        assert len(calls) == 2
        assert "reasoning_effort" in calls[0]
        assert "reasoning_effort" not in calls[1]

    def test_chat_override_400_rollback_multi_keys(self, monkeypatch):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            calls.append(body)
            if "thinking" in body:
                return httpx.Response(400, json={"error": "thinking rejected"})
            return _ok_response()

        _install_transport(monkeypatch, handler)
        result = asyncio.run(
            _client().chat(
                [{"role": "user", "content": "hi"}],
                {"reasoning_override": '{"thinking": {"type": "disabled"}, "thinking_budget": 4000}'},
            )
        )
        assert result == "ok"
        assert len(calls) == 2
        assert calls[0]["thinking_budget"] == 4000
        assert "thinking" not in calls[1]
        assert "thinking_budget" not in calls[1]

    def test_chat_raises_when_400_persists(self, monkeypatch):
        _install_transport(monkeypatch, lambda request: httpx.Response(400, json={"error": "bad"}))
        with pytest.raises(LLMCallFailed):
            asyncio.run(
                _client().chat([{"role": "user", "content": "hi"}], {"reasoning_effort": "low"})
            )

    def test_chat_400_without_injected_params_no_retry(self, monkeypatch):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(json.loads(request.content))
            return httpx.Response(400, json={"error": "bad"})

        _install_transport(monkeypatch, handler)
        with pytest.raises(LLMCallFailed):
            asyncio.run(_client().chat([{"role": "user", "content": "hi"}], {}))
        assert len(calls) == 1  # 无注入参数 → 不降级重试

    def test_chat_full_fallback(self, monkeypatch):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            calls.append(body)
            if "reasoning_effort" in body:
                return httpx.Response(400, json={"error": "unsupported"})
            return _ok_response()

        _install_transport(monkeypatch, handler)
        result = asyncio.run(
            _client().chat_full([{"role": "user", "content": "hi"}], {"reasoning_effort": "low"})
        )
        assert result["content"] == "ok"
        assert len(calls) == 2
        assert "reasoning_effort" not in calls[1]

    def test_fallback_memory_skips_injection(self, monkeypatch):
        # 同一 client 实例：首次 400 降级后，后续调用直接不注入（实例级记忆）
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            calls.append(body)
            if "reasoning_effort" in body:
                return httpx.Response(400, json={"error": "unknown parameter"})
            return _ok_response()

        _install_transport(monkeypatch, handler)
        c = _client()
        settings = {"reasoning_effort": "low"}
        first = asyncio.run(c.chat([{"role": "user", "content": "hi"}], settings))
        second = asyncio.run(c.chat([{"role": "user", "content": "hi"}], settings))
        assert first == second == "ok"
        assert len(calls) == 3  # 1st call: inject→400→retry ok; 2nd call: straight ok
        assert "reasoning_effort" not in calls[2]

    def test_chat_stream_fallback(self, monkeypatch):
        sse = (
            b'data: {"choices": [{"delta": {"content": "hi"}, "finish_reason": null}]}\n\n'
            b'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}\n\n'
            b"data: [DONE]\n\n"
        )
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            calls.append(body)
            if "reasoning_effort" in body:
                return httpx.Response(400, json={"error": "unsupported"})
            return httpx.Response(200, content=sse, headers={"Content-Type": "text/event-stream"})

        _install_transport(monkeypatch, handler)
        c = _client()

        async def consume():
            return [chunk async for chunk in c.chat_stream(
                [{"role": "user", "content": "hi"}], {"reasoning_effort": "low"}
            )]

        chunks = asyncio.run(consume())
        assert len(calls) == 2
        assert "reasoning_effort" not in calls[1]
        assert chunks[0] == {"type": "content", "delta": "hi"}
        assert chunks[-1] == {"type": "finish", "finish_reason": "stop"}


# ── C. probe() ──────────────────────────────────────────────


class TestProbe:
    def test_supported_returns_true(self, monkeypatch):
        _install_transport(monkeypatch, lambda request: _ok_response())
        assert asyncio.run(_client().probe({"reasoning_effort": "low"})) is True

    def test_rejected_returns_false(self, monkeypatch):
        _install_transport(monkeypatch, lambda request: httpx.Response(400, json={}))
        assert asyncio.run(_client().probe({"reasoning_effort": "low"})) is False

    def test_other_status_returns_none(self, monkeypatch):
        _install_transport(monkeypatch, lambda request: httpx.Response(500, json={}))
        assert asyncio.run(_client().probe({"reasoning_effort": "low"})) is None

    def test_network_error_returns_none(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom")

        _install_transport(monkeypatch, handler)
        assert asyncio.run(_client().probe({"reasoning_effort": "low"})) is None

    def test_read_error_returns_none(self, monkeypatch):
        # M1 回归：响应读取阶段断连（非连接阶段）不穿透为 500
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadError("connection reset mid-read")

        _install_transport(monkeypatch, handler)
        assert asyncio.run(_client().probe({"reasoning_effort": "low"})) is None


# ── E. C1 回归：settings 白名单读写闭环 ─────────────────────


class TestSettingsWhitelist:
    """reasoning 字段必须同时在 get_settings 白名单中，否则写得出读不回。"""

    def test_reasoning_fields_roundtrip(self, tmp_path):
        from db.database import Database

        db = Database(str(tmp_path / "test.db"))
        db.initialize()
        try:
            db.save_settings({"reasoning_effort": "high", "reasoning_override": '{"thinking_budget": 4000}'})
            settings = db.get_settings()
            assert settings["reasoning_effort"] == "high"
            assert settings["reasoning_override"] == '{"thinking_budget": 4000}'
        finally:
            db.conn.close()

    def test_defaults_include_reasoning_fields(self, tmp_path):
        from db.database import Database

        db = Database(str(tmp_path / "test.db"))
        db.initialize()
        try:
            settings = db.get_settings()  # empty DB → pure defaults
            assert settings["reasoning_effort"] == "low"
            assert settings["reasoning_override"] == ""
        finally:
            db.conn.close()


# ── D. /settings/personal_test 端点 ─────────────────────────


class TestPersonalTestEndpoint:
    def _fake_client_class(self, connect_result, probe_results=None):
        probes = list(probe_results or [])

        class FakeClient:
            def __init__(self, settings):
                pass

            async def test_connection(self):
                if isinstance(connect_result, Exception):
                    raise connect_result
                return connect_result

            async def probe(self, extra):
                return probes.pop(0) if probes else None

        return FakeClient

    def test_capabilities_reported(self, monkeypatch):
        fake = self._fake_client_class(True, [True, False, True])
        monkeypatch.setattr("llm.client.LLMClient", fake)
        result = asyncio.run(settings_router.test_settings(SettingsModel()))
        assert result["ok"] is True
        assert result["capabilities"] == {
            "function_calling": True,
            "reasoning_effort": False,
            "thinking_toggle": True,
        }

    def test_connectivity_failure_skips_probes(self, monkeypatch):
        fake = self._fake_client_class(RuntimeError("no route"))
        monkeypatch.setattr("llm.client.LLMClient", fake)
        result = asyncio.run(settings_router.test_settings(SettingsModel()))
        assert result["ok"] is False
        assert "capabilities" in result
        assert all(v is None for v in result["capabilities"].values())

    def test_network_probe_failure_aborts_remaining(self, monkeypatch):
        # 第一个探测返回 None（网络失败）→ 后续探测不执行，保持 None
        fake = self._fake_client_class(True, [None])
        monkeypatch.setattr("llm.client.LLMClient", fake)
        result = asyncio.run(settings_router.test_settings(SettingsModel()))
        assert result["ok"] is True
        assert result["capabilities"] == {
            "function_calling": None,
            "reasoning_effort": None,
            "thinking_toggle": None,
        }
