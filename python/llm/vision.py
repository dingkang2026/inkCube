"""python/llm/vision.py — 图片理解（vision 工具 / 知识库图片描述共用）。

2026-10-21 架构收敛：独立 VLM 配置链路（settings 三键 + vlm_client.py 专属
HTTP 层 + thinking 参数端点特判）整体删除，图片理解统一走 LLMClient——
其 messages 为 OpenAI 兼容透传，天然承载 image_url content 数组；reasoning
兼容由既有机制承担（reasoning_override/effort + 400 一次性回退 + GLM-5 null
content 回退 reasoning_content）；max_tokens 走 budget floor 32K，思考型模型
reasoning 挤占输出预算的空正文缺陷不再成立。

前提契约：设置 > 外部模型 的标准/高配模型必须为多模态（支持图片输入），
设置页 hint 注明；本模块对空正文/调用失败统一附加多模态引导（_MULTIMODAL_HINT）。

usage：全走 LLMClient.chat_full 出口收口（2.10 §4.2），归属由调用方
push_context 声明（任务轮次 source=task / 知识库 worker source=knowledge）。
"""
import base64
import json
import logging
from pathlib import Path

from errors import LLMCallFailed

logger = logging.getLogger(__name__)

# 多模态引导：图片理解失败（空正文/端点拒绝）统一附加，指向配置修正
_MULTIMODAL_HINT = "请确认设置 > 外部模型 中的标准/高配模型支持图片输入（多模态）"

# data URL MIME 映射：严格校验的兼容端点依赖真实 MIME，谎报 PNG 会被拒收；
# 未知后缀兜底 image/png（提取层白名单已保证常规格式）
_MIME_BY_EXT = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".bmp": "image/bmp", ".webp": "image/webp",
}

# 图片自动分类类别（迁移自原 VLMClient.CATEGORIES）：
# invoice/certificate 为内容类（进报告插图池，不自动归档）；
# qr_code/signature/seal 为修饰类（AUTO_ARCHIVE）
CATEGORIES = (
    "chart", "photo", "diagram", "table", "formula", "logo",
    "invoice", "certificate", "qr_code", "signature", "seal", "other",
)

# Categories that should be auto-archived (not useful for report illustration)
AUTO_ARCHIVE_CATEGORIES = ("qr_code", "signature", "seal")


def _mime_for(image_path: str) -> str:
    return _MIME_BY_EXT.get(Path(image_path).suffix.lower(), "image/png")


def _encode_image(image_path: str) -> str:
    """Encode image to base64"""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _image_message(image_path: str, prompt: str) -> list[dict]:
    """构造 OpenAI 兼容多模态 user 消息（text + image_url content 数组）。"""
    return [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {"url": f"data:{_mime_for(image_path)};base64,{_encode_image(image_path)}"},
            },
        ],
    }]


def _parse_classification(raw: str) -> dict:
    """Parse LLM JSON response, with fallback for non-JSON output.

    Returns {"description": str, "category": str}.
    """
    text = raw.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.splitlines()
        # Remove first and last ``` lines
        start = 1
        end = len(lines)
        for i in range(len(lines) - 1, 0, -1):
            if lines[i].strip().startswith("```"):
                end = i
                break
        text = "\n".join(lines[start:end]).strip()

    try:
        obj = json.loads(text)
        desc = obj.get("description", "").strip()
        cat = obj.get("category", "other").strip().lower()
        if cat not in CATEGORIES:
            cat = "other"
        return {"description": desc, "category": cat}
    except (json.JSONDecodeError, AttributeError):
        # 模型未按 JSON 返回——整体作为描述，类别归 other
        return {"description": raw.strip(), "category": "other"}


def _with_multimodal_hint(e: LLMCallFailed) -> LLMCallFailed:
    """调用失败 detail 统一附加多模态引导（网络等非 LLMCallFailed 异常不包装）。"""
    return LLMCallFailed(detail=f"{e.detail}；{_MULTIMODAL_HINT}")


async def analyze_image(
    client, image_path: str, question: str | None = None
) -> str:
    """通用图片理解（vision 工具消费）：自然语言描述或按问题作答。

    与 describe_image（知识库图片库专用：分类 JSON + 自动归档语义）不同，
    本函数面向 agent 自由问答，返回纯文本，不做分类约束。

    Raises:
        LLMCallFailed（detail 附多模态引导）/ LLMNetworkError 等透传
    """
    if question and question.strip():
        prompt = (
            "请仔细观察这张图片，回答以下问题：\n"
            f"{question.strip()}\n\n用中文回答，图中可见的文字、数据请如实引用。"
        )
    else:
        prompt = (
            "请详细描述这张图片的内容，包括类型、可见文字、数据、"
            "关键信息等。用中文回答。"
        )

    logger.info("vision analyze: path=%s, has_question=%s", image_path, bool(question and question.strip()))
    try:
        result = await client.chat_full(_image_message(image_path, prompt))
    except LLMCallFailed as e:
        raise _with_multimodal_hint(e) from e
    text = (result.get("content") or "").strip()
    if result.get("finish_reason") == "length":
        if not text:
            # 零正文（思考耗尽预算或图片输入被拒）：显式失败，禁止「空描述+
            # 截断标记」冒充成功——agent 拿到空描述只能盲目重试（2026-10-21 缺陷）
            raise LLMCallFailed(
                detail=f"模型返回空正文（finish_reason=length）；{_MULTIMODAL_HINT}"
            )
        # max_tokens 触顶：显式标注，禁止静默缺尾
        text += "\n\n[回复因长度上限被截断]"
    return text


async def describe_image(
    client, image_path: str, context_text: str | None = None
) -> dict:
    """图片分类 + 描述（知识库图片库专用）。

    Returns:
        {"description": str, "category": str}

    Raises:
        LLMCallFailed（detail 附多模态引导）/ LLMNetworkError 等透传
    """
    logger.info("vision describe_image: path=%s, has_context=%s", image_path, context_text is not None)
    cat_list = "/".join(CATEGORIES)
    prompt = (
        "请分析这张图片，完成两项任务：\n"
        f"1. 分类：从以下类别中选择一个：{cat_list}\n"
        "2. 描述：用中文详细描述图片内容，包括类型、数据趋势、关键信息等\n\n"
        "请严格按以下JSON格式返回（不要添加任何其他文字或markdown标记）：\n"
        '{"category": "类别", "description": "详细描述"}'
    )
    if context_text:
        prompt += f"\n\n以下是图片在文档中的上下文，供参考：\n{context_text}"

    try:
        result = await client.chat_full(_image_message(image_path, prompt))
    except LLMCallFailed as e:
        raise _with_multimodal_hint(e) from e
    raw = (result.get("content") or "").strip()
    if not raw:
        # 空正文：显式失败走 worker 既有容错（图片标 error + 可行动文案），
        # 替代静默 {description: "", category: "other"}（图片标 done 却无描述）
        raise LLMCallFailed(detail=f"模型返回空正文；{_MULTIMODAL_HINT}")
    return _parse_classification(raw)
