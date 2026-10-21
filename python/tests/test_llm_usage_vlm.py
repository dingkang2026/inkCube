"""python/tests/test_llm_usage_vlm.py — VLM 出口 usage 提取与收口（2.10 §4.2/§8）。

describe_image 从 API 响应提取精确 usage（原被丢弃）→ record 收口记录；
响应无 usage 字段不落库、返回形状不变（{description, category}）。
aiohttp.ClientSession 用最小替身替换（payload 类属性注入响应）。
"""
import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import knowledge.vlm_client as vlm_mod
from knowledge.vlm_client import VLMClient
from llm.usage import configure

_VLM_BODY = {
    "choices": [{"message": {"content": json.dumps(
        {"category": "chart", "description": "月度营收柱状图"})}}],
    "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
}

_VLM_BODY_NO_USAGE = {
    "choices": [{"message": {"content": json.dumps(
        {"category": "photo", "description": "会议现场照片"})}}],
}


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    async def text(self):
        return json.dumps(self._payload)

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _FakeSession:
    """aiohttp.ClientSession 最小替身：post() 返回类属性预置的响应。"""

    payload: dict = {}

    def __init__(self, *args, **kwargs):
        pass

    def post(self, url, **kwargs):
        return _FakeResponse(type(self).payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


@pytest.fixture()
def sink_rows():
    rows = []
    configure(rows.append)
    yield rows
    configure(None)


@pytest.fixture()
def fake_png(tmp_path):
    p = tmp_path / "img.png"
    p.write_bytes(b"\x89PNG fake")
    return str(p)


def _client():
    return VLMClient(api_url="https://fake.api/v1", api_key="k", model="glm-4v")


def test_describe_image_records_usage(sink_rows, fake_png, monkeypatch):
    """API 返回 usage → 提取收口恰 1 行（mode=vlm），返回形状保持两键。"""
    _FakeSession.payload = _VLM_BODY
    monkeypatch.setattr(vlm_mod.aiohttp, "ClientSession", _FakeSession)
    result = asyncio.run(_client().describe_image(fake_png))
    # pop 保持既有返回形状不变（消费方零感知）
    assert result == {"description": "月度营收柱状图", "category": "chart"}
    assert len(sink_rows) == 1
    row = sink_rows[0]
    assert row["model"] == "glm-4v" and row["mode"] == "vlm"
    assert row["prompt_tokens"] == 100
    assert row["completion_tokens"] == 20
    assert row["total_tokens"] == 120


def test_describe_image_without_usage_records_nothing(
        sink_rows, fake_png, monkeypatch):
    """响应无 usage 字段（api_usage={} → usage=None）→ record 忽略不落库，
    返回形状不变。"""
    _FakeSession.payload = _VLM_BODY_NO_USAGE
    monkeypatch.setattr(vlm_mod.aiohttp, "ClientSession", _FakeSession)
    result = asyncio.run(_client().describe_image(fake_png))
    assert result == {"description": "会议现场照片", "category": "photo"}
    assert sink_rows == []
