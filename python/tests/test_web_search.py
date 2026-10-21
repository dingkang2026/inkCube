"""python/tests/test_web_search.py — 网络搜索工具单测（双供应商，2026-10-19 供应商化）。

覆盖 WebSearchTool（python/tools/web_search.py）：
  - 配置判定：未选供应商 / 所选 Key 为空 → 未配置，execute 返回引导文案不触网
  - Tavily 分支：请求体（include_answer CJK 关闭回归 c2f24e5c / language）、
    Bearer 认证、answer 摘要 + 结果条目渲染
  - 智谱分支：请求体（search_query 70 字符截断 / search_engine / search_intent /
    content_size）、search_result 渲染（响应无 score 无 answer，天然无翻译失真）
  - 引擎档位白名单归一（构造期回落）与未配置路径零联网护栏

HTTP 层用 httpx.MockTransport 替换 AsyncClient（手法同 test_reasoning_params）；
async 统一 asyncio.run 驱动，不触网。
"""
import asyncio
import json
import os
import sys
from urllib.parse import urlparse

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tools.web_search as ws_mod
from tools.web_search import WebSearchTool  # noqa: E402


def _mock_client(monkeypatch, handler, capture: list):
    """替换模块内 AsyncClient 为 MockTransport 版本，并捕获请求体到 capture。"""
    def handler_with_capture(request: httpx.Request) -> httpx.Response:
        capture.append({
            "url": str(request.url),
            "headers": dict(request.headers),
            "body": json.loads(request.content.decode("utf-8")) if request.content else {},
        })
        return handler(request)

    transport = httpx.MockTransport(handler_with_capture)
    real_async_client = httpx.AsyncClient

    def _factory(**kw):
        kw.pop("transport", None)
        return real_async_client(transport=transport, **kw)

    monkeypatch.setattr(ws_mod.httpx, "AsyncClient", _factory)


# ── 配置判定 ────────────────────────────────────────────────


class TestConfigured:
    def test_no_provider_not_configured(self):
        tool = WebSearchTool(provider="", tavily_key="k", zhipu_key="k")
        assert tool._configured is False

    def test_provider_without_key_not_configured(self):
        assert WebSearchTool(provider="tavily", tavily_key="")._configured is False
        assert WebSearchTool(provider="zhipu", zhipu_key="")._configured is False

    def test_provider_with_key_configured(self):
        assert WebSearchTool(provider="tavily", tavily_key="tvly-1")._configured is True
        assert WebSearchTool(provider="zhipu", zhipu_key="zp-1")._configured is True

    def test_execute_without_provider_returns_hint(self):
        """未选供应商 → 引导文案，不触网（无 HTTP 请求）。"""
        tool = WebSearchTool(provider="", tavily_key="", zhipu_key="")
        out = asyncio.run(tool.execute("query"))
        assert "网络搜索未配置" in out
        assert "网络搜索" in out  # 引导路径：设置 > 基础配置 > 网络搜索

    def test_execute_provider_key_mismatch_returns_hint(self):
        """选了 tavily 但只有智谱 Key（key 空）→ 同样按未配置处理。"""
        tool = WebSearchTool(provider="tavily", tavily_key="", zhipu_key="zp-1")
        out = asyncio.run(tool.execute("query"))
        assert "网络搜索未配置" in out

    def test_execute_never_touches_network_when_unconfigured(self, monkeypatch):
        """未配置路径零联网护栏：任何 AsyncClient 构造即失败。

        防「先建 client 后判配置」回归导致未配置时真实联网。
        """
        def _no_client(*args, **kwargs):
            raise AssertionError("unconfigured execute must not create httpx.AsyncClient")

        monkeypatch.setattr(ws_mod.httpx, "AsyncClient", _no_client)
        for tool in (
            WebSearchTool(provider="", tavily_key="", zhipu_key=""),
            WebSearchTool(provider="tavily", tavily_key="", zhipu_key="zp-1"),
            WebSearchTool(provider="zhipu", zhipu_key="", tavily_key="tvly-1"),
        ):
            out = asyncio.run(tool.execute("query"))
            assert "网络搜索未配置" in out


# ── Tavily 分支 ─────────────────────────────────────────────


class TestTavilyBranch:
    def test_request_shape_and_render(self, monkeypatch):
        capture = []

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "answer": "AI summary",
                "results": [
                    {"title": "T1", "content": "C1", "url": "https://a.example/1"},
                    {"title": "T2", "content": "C2", "url": "https://a.example/2"},
                ],
            })

        _mock_client(monkeypatch, handler, capture)
        tool = WebSearchTool(provider="tavily", tavily_key="tvly-x")
        out = asyncio.run(tool.execute("python news", max_results=3))

        # 请求体：非 CJK → include_answer 开启；language=zh-cn
        assert len(capture) == 1
        req = capture[0]
        assert req["url"].startswith("https://api.tavily.com/search")
        assert req["headers"].get("authorization") == "Bearer tvly-x"
        assert req["body"]["query"] == "python news"
        assert req["body"]["max_results"] == 3
        assert req["body"]["include_answer"] is True
        assert req["body"]["language"] == "zh-cn"

        # 渲染：answer 摘要在前 + 编号条目 + 来源
        assert out.startswith("摘要: AI summary")
        assert "[1] T1" in out and "[2] T2" in out
        assert "https://a.example/1" in out

    def test_cjk_query_disables_answer(self, monkeypatch):
        """CJK 查询关闭 include_answer（c2f24e5c：answer 段不受 language 控制，
        中文查询返回英文摘要且数量级翻译失真）。"""
        capture = []

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"answer": None, "results": []})

        _mock_client(monkeypatch, handler, capture)
        tool = WebSearchTool(provider="tavily", tavily_key="tvly-x")
        asyncio.run(tool.execute("智能制造 招商政策"))

        assert capture[0]["body"]["include_answer"] is False

    def test_empty_results_message(self, monkeypatch):
        capture = []

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"answer": None, "results": []})

        _mock_client(monkeypatch, handler, capture)
        tool = WebSearchTool(provider="tavily", tavily_key="tvly-x")
        out = asyncio.run(tool.execute("no hit"))
        assert out == "未找到相关结果"


# ── 智谱分支 ────────────────────────────────────────────────


class TestZhipuBranch:
    def test_request_shape_and_render(self, monkeypatch):
        capture = []

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "search_result": [
                    {"title": "S1", "content": "K1", "link": "https://b.example/1",
                     "media": "新华网", "publish_date": "2026-10-01"},
                    {"title": "S2", "content": "K2", "link": "https://b.example/2"},
                ],
            })

        _mock_client(monkeypatch, handler, capture)
        tool = WebSearchTool(provider="zhipu", zhipu_key="zp-x", zhipu_engine="search_std")
        out = asyncio.run(tool.execute("智能制造政策", max_results=5))

        req = capture[0]
        assert req["url"].startswith("https://open.bigmodel.cn/api/paas/v4/web_search")
        assert req["headers"].get("authorization") == "Bearer zp-x"
        # 请求体：显式搜索（search_intent=False）+ 详细摘要档 + 所选引擎档位
        assert req["body"]["search_query"] == "智能制造政策"
        assert req["body"]["search_engine"] == "search_std"
        assert req["body"]["search_intent"] is False
        assert req["body"]["count"] == 5
        assert req["body"]["content_size"] == "high"

        # 渲染：编号条目 + 来源（media | link）；无 answer 段
        assert "[1] S1" in out and "[2] S2" in out
        assert "https://b.example/1" in out
        assert "新华网" in out
        assert not out.startswith("摘要")

    def test_query_truncated_to_70_chars(self, monkeypatch):
        """search_query 上限 70 字符（API 硬限），超长截断。"""
        capture = []

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"search_result": []})

        _mock_client(monkeypatch, handler, capture)
        tool = WebSearchTool(provider="zhipu", zhipu_key="zp-x")
        long_query = "长" * 100
        asyncio.run(tool.execute(long_query))

        body = capture[0]["body"]
        assert len(body["search_query"]) == 70
        assert body["search_query"] == "长" * 70

    def test_default_engine_is_search_pro(self, monkeypatch):
        capture = []

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"search_result": []})

        _mock_client(monkeypatch, handler, capture)
        tool = WebSearchTool(provider="zhipu", zhipu_key="zp-x")
        asyncio.run(tool.execute("q"))
        assert capture[0]["body"]["search_engine"] == "search_pro"

    def test_engine_whitelist_fallback_on_construction(self):
        """构造期 engine 白名单归一：空值 / 非法档 → 回落 search_pro。

        settings:save 全量 PUT 可透传任意字符串，入口归一防脏值直传 API。
        """
        assert WebSearchTool(provider="zhipu", zhipu_key="k").zhipu_engine == "search_pro"
        assert WebSearchTool(provider="zhipu", zhipu_key="k", zhipu_engine="").zhipu_engine == "search_pro"
        assert WebSearchTool(provider="zhipu", zhipu_key="k", zhipu_engine="bogus").zhipu_engine == "search_pro"
        assert WebSearchTool(provider="zhipu", zhipu_key="k", zhipu_engine="search_std").zhipu_engine == "search_std"

    def test_empty_results_message(self, monkeypatch):
        capture = []

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"search_result": None})

        _mock_client(monkeypatch, handler, capture)
        tool = WebSearchTool(provider="zhipu", zhipu_key="zp-x")
        out = asyncio.run(tool.execute("q"))
        assert out == "未找到相关结果"
