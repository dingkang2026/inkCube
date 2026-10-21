"""tests/test_vlm_client.py — VLM 分类常量不变量。

背景：seal（印章）加入分类清单并纳入自动归档（印章/签名/二维码均不适合报告配图）。
守护不变量：AUTO_ARCHIVE_CATEGORIES 必须是 CATEGORIES 的子集，否则归档分支永不触发
（或触发非法类别入库）。
"""

import asyncio
import inspect
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import knowledge.vlm_client as vlm_mod
from errors import VLMCallFailed
from knowledge.vlm_client import VLMClient


class TestVlmCategories:
    def test_seal_is_a_valid_category(self):
        assert "seal" in VLMClient.CATEGORIES

    def test_seal_is_auto_archived(self):
        assert "seal" in VLMClient.AUTO_ARCHIVE_CATEGORIES

    def test_invoice_and_certificate_are_content_categories(self):
        """票据/资质证书为内容类：进报告插图池，不进自动归档
        （与 qr_code/signature/seal 修饰类相对，商务场景是信任背书插图）。"""
        assert "invoice" in VLMClient.CATEGORIES
        assert "certificate" in VLMClient.CATEGORIES
        assert "invoice" not in VLMClient.AUTO_ARCHIVE_CATEGORIES
        assert "certificate" not in VLMClient.AUTO_ARCHIVE_CATEGORIES

    def test_auto_archive_categories_are_subset_of_categories(self):
        assert set(VLMClient.AUTO_ARCHIVE_CATEGORIES) <= set(VLMClient.CATEGORIES)

    def test_prompt_builds_category_list_dynamically(self):
        """prompt 类别表必须由 CATEGORIES 动态拼接（新增类别自动生效，防硬编码漂移）。"""
        source = inspect.getsource(VLMClient._call_external_api)
        assert '"/".join(self.CATEGORIES)' in source


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


class _RecordingSession:
    """记录 post() payload 的 aiohttp.ClientSession 替身（payload 类属性注入响应）。"""

    payload: dict = {}
    posted: list[dict] = []

    def __init__(self, *args, **kwargs):
        pass

    def post(self, url, **kwargs):
        type(self).posted.append({"url": url, **kwargs})
        return _FakeResponse(type(self).payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def _client() -> VLMClient:
    return VLMClient(api_url="https://fake.api/v1", api_key="k", model="glm-4v")


def _image_url_of(session_cls) -> str:
    payload = session_cls.posted[0]["json"]
    return payload["messages"][0]["content"][1]["image_url"]["url"]


@pytest.fixture()
def fake_png(tmp_path):
    p = tmp_path / "img.png"
    p.write_bytes(b"\x89PNG fake")
    return str(p)


class TestVlmMimeAndTruncation:
    """review 修复回归：data URL 真实 MIME（L2）、finish_reason 截断标记（L1）。"""

    def _setup(self, monkeypatch, payload: dict):
        _RecordingSession.payload = payload
        _RecordingSession.posted = []
        monkeypatch.setattr(vlm_mod.aiohttp, "ClientSession", _RecordingSession)

    def test_analyze_sends_real_mime_for_jpg(self, tmp_path, monkeypatch):
        """jpg 输入 data URL 用真实 image/jpeg，不再谎报 PNG（L2）。"""
        self._setup(monkeypatch, {
            "choices": [{"message": {"content": "描述"}, "finish_reason": "stop"}],
        })
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\xff\xd8 fake jpeg")

        asyncio.run(_client().analyze(str(img)))

        assert _image_url_of(_RecordingSession).startswith("data:image/jpeg;base64,")

    def test_analyze_png_keeps_png_mime(self, fake_png, monkeypatch):
        """png 输入映射基线不变：data:image/png。"""
        self._setup(monkeypatch, {
            "choices": [{"message": {"content": "描述"}, "finish_reason": "stop"}],
        })

        asyncio.run(_client().analyze(fake_png))

        assert _image_url_of(_RecordingSession).startswith("data:image/png;base64,")

    def test_describe_image_sends_real_mime(self, tmp_path, monkeypatch):
        """describe 路径同样按扩展名映射 MIME（同类问题一次清理）。"""
        self._setup(monkeypatch, {
            "choices": [{"message": {"content": json.dumps(
                {"category": "chart", "description": "柱状图"}
            )}, "finish_reason": "stop"}],
        })
        img = tmp_path / "chart.webp"
        img.write_bytes(b"RIFF fake webp")

        asyncio.run(_client().describe_image(img))

        assert _image_url_of(_RecordingSession).startswith("data:image/webp;base64,")

    def test_analyze_marks_truncated_reply(self, fake_png, monkeypatch):
        """finish_reason=length → 返回尾部显式截断标记，禁止静默缺尾（L1）。"""
        self._setup(monkeypatch, {
            "choices": [{"message": {"content": "半截描述"}, "finish_reason": "length"}],
        })

        text = asyncio.run(_client().analyze(fake_png))

        assert text.startswith("半截描述")
        assert text.endswith("[回复因长度上限被截断]")

    def test_analyze_no_mark_when_complete(self, fake_png, monkeypatch):
        """finish_reason=stop → 无截断标记（不误伤完整回复）。"""
        self._setup(monkeypatch, {
            "choices": [{"message": {"content": "完整描述"}, "finish_reason": "stop"}],
        })

        text = asyncio.run(_client().analyze(fake_png))

        assert text == "完整描述"


class TestVlmEmptyContentGuard:
    """思考型 VLM（GLM-4.6V 系）reasoning 挤占输出预算：零正文 + length
    曾被包装成 ok:true 返回（2026-10-21 vision 空描述缺陷），守护修复不变量：
    空正文一律显式抛错，禁止「空描述+截断标记」冒充成功。"""

    def _setup(self, monkeypatch, payload: dict):
        _RecordingSession.payload = payload
        _RecordingSession.posted = []
        monkeypatch.setattr(vlm_mod.aiohttp, "ClientSession", _RecordingSession)

    def test_analyze_empty_content_with_length_raises(self, fake_png, monkeypatch):
        """零正文 + length（思考耗尽预算缺陷现场）→ 显式抛 VLMCallFailed。"""
        self._setup(monkeypatch, {
            "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
        })

        with pytest.raises(VLMCallFailed):
            asyncio.run(_client().analyze(fake_png))

    def test_analyze_null_content_raises(self, fake_png, monkeypatch):
        """content 为 null（部分端点思考耗尽后的返回形态）→ 归一化后同样抛错，
        而非 AttributeError。"""
        self._setup(monkeypatch, {
            "choices": [{"message": {"content": None}, "finish_reason": "length"}],
        })

        with pytest.raises(VLMCallFailed):
            asyncio.run(_client().analyze(fake_png))

    def test_describe_image_empty_content_raises(self, tmp_path, monkeypatch):
        """describe 路径空正文同样显式抛错，替代静默空描述标 done。"""
        self._setup(monkeypatch, {
            "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
        })
        img = tmp_path / "chart.png"
        img.write_bytes(b"\x89PNG fake")

        with pytest.raises(VLMCallFailed):
            asyncio.run(_client().describe_image(img))

    def test_zhipu_endpoint_disables_thinking(self, fake_png, monkeypatch):
        """智谱端点 payload 附 thinking disabled（思考挤占输出预算的根因修复）。"""
        self._setup(monkeypatch, {
            "choices": [{"message": {"content": "描述"}, "finish_reason": "stop"}],
        })
        client = VLMClient(
            api_url="https://open.bigmodel.cn/api/paas/v4/", api_key="k",
        )

        asyncio.run(client.analyze(fake_png))

        payload = _RecordingSession.posted[0]["json"]
        assert payload["thinking"] == {"type": "disabled"}

    def test_non_zhipu_endpoint_keeps_payload_clean(self, fake_png, monkeypatch):
        """非智谱 OpenAI 兼容端点不附加 thinking（unknown 参数兼容风险归零）。"""
        self._setup(monkeypatch, {
            "choices": [{"message": {"content": "描述"}, "finish_reason": "stop"}],
        })

        asyncio.run(_client().analyze(fake_png))

        payload = _RecordingSession.posted[0]["json"]
        assert "thinking" not in payload
