"""python/tests/test_llm_vision.py — llm.vision 图片理解单测（LLM 统一链路）。

覆盖：
- 类别常量不变量（AUTO_ARCHIVE ⊆ CATEGORIES；内容类/修饰类边界）
- MIME 映射（_mime_for：真实扩展名 → data URL MIME；未知后缀兜底）
- messages 构造（_image_message：text + image_url content 数组、base64）
- 分类 JSON 解析（_parse_classification：纯 JSON/code fence/非法类别/非 JSON）
- analyze_image 截断标记与空正文显式抛错（2026-10-21 空描述缺陷回归）
- describe_image 空正文抛错、失败 detail 附多模态引导

client 全部用最小替身（async chat_full 脚本化响应），不依赖外部服务；
async 用 asyncio.run 驱动（项目惯例，不引入 pytest-asyncio）。
"""
import asyncio
import base64
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from errors import LLMCallFailed
from llm import vision


def _run(coro):
    """Synchronously drive a coroutine (project uses plain pytest, no pytest-asyncio)."""
    return asyncio.run(coro)


class FakeClient:
    """LLMClient 替身：chat_full 返回类属性预置的 result dict，记录入参。"""

    result: dict = {}
    calls: list[list[dict]] = []

    async def chat_full(self, messages, tools=None):
        type(self).calls.append(messages)
        return dict(type(self).result)

    @classmethod
    def reset(cls, result: dict):
        cls.result = result
        cls.calls = []


class FailingClient:
    """chat_full 恒抛 LLMCallFailed 的替身（多模态引导包装验证）。"""

    def __init__(self, detail: str = "上游 500"):
        self.detail = detail

    async def chat_full(self, messages, tools=None):
        raise LLMCallFailed(detail=self.detail)


@pytest.fixture(autouse=True)
def _reset_client():
    FakeClient.reset({"content": "描述", "finish_reason": "stop"})


@pytest.fixture()
def fake_png(tmp_path):
    p = tmp_path / "img.png"
    p.write_bytes(b"\x89PNG fake")
    return str(p)


class TestCategoryConstants:
    """迁移自 VLMClient 的类别清单不变量（原 test_vlm_client 守护点）。"""

    def test_seal_is_valid_and_auto_archived(self):
        assert "seal" in vision.CATEGORIES
        assert "seal" in vision.AUTO_ARCHIVE_CATEGORIES

    def test_invoice_and_certificate_are_content_categories(self):
        """票据/资质证书为内容类：进报告插图池，不进自动归档
        （与 qr_code/signature/seal 修饰类相对，商务场景是信任背书插图）。"""
        assert "invoice" in vision.CATEGORIES
        assert "certificate" in vision.CATEGORIES
        assert "invoice" not in vision.AUTO_ARCHIVE_CATEGORIES
        assert "certificate" not in vision.AUTO_ARCHIVE_CATEGORIES

    def test_auto_archive_categories_are_subset_of_categories(self):
        assert set(vision.AUTO_ARCHIVE_CATEGORIES) <= set(vision.CATEGORIES)


class TestMimeMapping:
    def test_known_extensions_map_real_mime(self):
        """兼容端点依赖真实 MIME，谎报 PNG 会被拒收（review L2 回归）。"""
        assert vision._mime_for("a.png") == "image/png"
        assert vision._mime_for("a.jpg") == "image/jpeg"
        assert vision._mime_for("a.JPEG") == "image/jpeg"  # 大小写不敏感
        assert vision._mime_for("a.webp") == "image/webp"

    def test_unknown_extension_falls_back_to_png(self):
        """未知后缀兜底 image/png（提取层白名单已保证常规格式）。"""
        assert vision._mime_for("a.tif") == "image/png"


class TestImageMessage:
    def test_content_array_text_then_image_url(self, fake_png):
        """OpenAI 兼容多模态消息：text + image_url content 数组。"""
        msgs = vision._image_message(fake_png, "描述这张图")
        assert len(msgs) == 1 and msgs[0]["role"] == "user"
        content = msgs[0]["content"]
        assert content[0] == {"type": "text", "text": "描述这张图"}
        assert content[1]["type"] == "image_url"
        url = content[1]["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")
        assert base64.b64decode(url.split(",", 1)[1]) == b"\x89PNG fake"

    def test_jpg_sends_real_mime(self, tmp_path):
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\xff\xd8 fake jpeg")
        url = vision._image_message(str(img), "q")[0]["content"][1]["image_url"]["url"]
        assert url.startswith("data:image/jpeg;base64,")


class TestParseClassification:
    def test_plain_json(self):
        out = vision._parse_classification('{"category": "chart", "description": "柱状图"}')
        assert out == {"description": "柱状图", "category": "chart"}

    def test_markdown_code_fence_stripped(self):
        raw = '```json\n{"category": "photo", "description": "照片"}\n```'
        assert vision._parse_classification(raw) == {"description": "照片", "category": "photo"}

    def test_unknown_category_normalized_to_other(self):
        out = vision._parse_classification('{"category": "painting", "description": "x"}')
        assert out["category"] == "other"

    def test_non_json_falls_back_to_whole_text(self):
        """模型未按 JSON 返回：整体作描述、类别归 other（不抛错）。"""
        out = vision._parse_classification("这是一张柱状图")
        assert out == {"description": "这是一张柱状图", "category": "other"}

    def test_missing_description_defaults_empty(self):
        out = vision._parse_classification('{"category": "table"}')
        assert out["description"] == ""


class TestAnalyzeImage:
    def test_success_returns_text(self, fake_png):
        FakeClient.reset({"content": "图表显示月度趋势", "finish_reason": "stop"})
        text = _run(vision.analyze_image(FakeClient(), fake_png))
        assert text == "图表显示月度趋势"

    def test_length_with_content_marks_truncation(self, fake_png):
        """finish_reason=length 有正文 → 尾部显式截断标记，禁止静默缺尾。"""
        FakeClient.reset({"content": "半截描述", "finish_reason": "length"})
        text = _run(vision.analyze_image(FakeClient(), fake_png))
        assert text.startswith("半截描述")
        assert text.endswith("[回复因长度上限被截断]")

    def test_no_mark_when_complete(self, fake_png):
        FakeClient.reset({"content": "完整描述", "finish_reason": "stop"})
        assert _run(vision.analyze_image(FakeClient(), fake_png)) == "完整描述"

    def test_empty_content_with_length_raises(self, fake_png):
        """零正文 + length（思考耗尽预算缺陷现场）→ 显式抛错附多模态引导，
        禁止「空描述+截断标记」冒充成功（2026-10-21 vision 空描述缺陷）。"""
        FakeClient.reset({"content": "", "finish_reason": "length"})
        with pytest.raises(LLMCallFailed) as ei:
            _run(vision.analyze_image(FakeClient(), fake_png))
        assert "多模态" in ei.value.detail

    def test_null_content_treated_as_empty(self, fake_png):
        """content=None（端点思考耗尽形态）同样显式抛错，非 AttributeError。"""
        FakeClient.reset({"content": None, "finish_reason": "length"})
        with pytest.raises(LLMCallFailed):
            _run(vision.analyze_image(FakeClient(), fake_png))

    def test_call_failure_detail_gets_multimodal_hint(self, fake_png):
        """LLMCallFailed 包装透传：detail 附加多模态引导，原文保留。"""
        with pytest.raises(LLMCallFailed) as ei:
            _run(vision.analyze_image(FailingClient(), fake_png))
        assert "上游 500" in ei.value.detail
        assert "多模态" in ei.value.detail

    def test_question_forwarded_into_prompt(self, fake_png):
        FakeClient.reset({"content": "答案", "finish_reason": "stop"})
        _run(vision.analyze_image(FakeClient(), fake_png, "图中数据是什么"))
        prompt = FakeClient.calls[0][0]["content"][0]["text"]
        assert "图中数据是什么" in prompt

    def test_blank_question_uses_default_prompt(self, fake_png):
        FakeClient.reset({"content": "答案", "finish_reason": "stop"})
        _run(vision.analyze_image(FakeClient(), fake_png, "  "))
        prompt = FakeClient.calls[0][0]["content"][0]["text"]
        assert "详细描述" in prompt


class TestDescribeImage:
    def test_success_returns_two_keys(self, fake_png):
        FakeClient.reset({
            "content": json.dumps({"category": "chart", "description": "柱状图"}),
            "finish_reason": "stop",
        })
        out = _run(vision.describe_image(FakeClient(), fake_png))
        assert out == {"description": "柱状图", "category": "chart"}

    def test_context_text_appended_to_prompt(self, fake_png):
        FakeClient.reset({
            "content": json.dumps({"category": "chart", "description": "d"}),
            "finish_reason": "stop",
        })
        _run(vision.describe_image(FakeClient(), fake_png, context_text="第三章 财务"))
        prompt = FakeClient.calls[0][0]["content"][0]["text"]
        assert "第三章 财务" in prompt

    def test_empty_content_raises(self, fake_png):
        """空正文显式抛错（worker 容错标 error + 可行动文案），
        替代静默 {description: "", category: "other"} 标 done。"""
        FakeClient.reset({"content": "", "finish_reason": "length"})
        with pytest.raises(LLMCallFailed) as ei:
            _run(vision.describe_image(FakeClient(), fake_png))
        assert "多模态" in ei.value.detail

    def test_call_failure_gets_multimodal_hint(self, fake_png):
        with pytest.raises(LLMCallFailed) as ei:
            _run(vision.describe_image(FailingClient(), fake_png))
        assert "多模态" in ei.value.detail

    def test_prompt_lists_categories_dynamically(self, fake_png):
        """prompt 类别表由 CATEGORIES 动态拼接（新增类别自动生效）。"""
        FakeClient.reset({
            "content": json.dumps({"category": "chart", "description": "d"}),
            "finish_reason": "stop",
        })
        _run(vision.describe_image(FakeClient(), fake_png))
        prompt = FakeClient.calls[0][0]["content"][0]["text"]
        assert "/".join(vision.CATEGORIES) in prompt
