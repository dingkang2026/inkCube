"""python/knowledge/vlm_client.py"""
import base64
import json
import logging
from pathlib import Path
from typing import Optional
import aiohttp

from errors import (
    InkCubeError,
    VLMNotConfigured, VLMInvalidKey, VLMNetworkError,
    VLMRateLimited, VLMCallFailed,
)
from llm import usage as usage_record  # 2.10 §4.2 VLM 出口收口（零 DB 依赖）

logger = logging.getLogger(__name__)


class VLMClient:
    """
    Vision-Language Model client for image description.
    
    Uses Zhipu GLM-4V via OpenAI-compatible API.
    Credentials from SQLite settings table.
    """
    
    def __init__(
        self,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: str = "glm-4v"
    ):
        """
        Initialize VLM client.
        
        Args:
            api_url: API endpoint URL (e.g., https://open.bigmodel.cn/api/paas/v4/)
            api_key: API authentication key
            model: Model name (default: glm-4v)
        """
        self.api_url = api_url
        self.api_key = api_key
        self.model = model
    
    @classmethod
    def from_db(cls, db_conn):
        """Create client from database settings"""
        cursor = db_conn.execute(
            "SELECT key, value FROM settings WHERE key IN ('vlm_api_url', 'vlm_api_key', 'vlm_model')"
        )
        settings = {}
        for row in cursor.fetchall():
            val = row["value"]
            try:
                settings[row["key"]] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                settings[row["key"]] = val

        return cls(
            api_url=settings.get("vlm_api_url"),
            api_key=settings.get("vlm_api_key"),
            model=settings.get("vlm_model", "glm-4v"),
        )
    
    # Valid categories for auto-classification
    # invoice/certificate 为内容类（进报告插图池，不自动归档）；
    # qr_code/signature/seal 为修饰类（AUTO_ARCHIVE）
    CATEGORIES = ("chart", "photo", "diagram", "table", "formula", "logo", "invoice", "certificate", "qr_code", "signature", "seal", "other")

    # Categories that should be auto-archived (not useful for report illustration)
    AUTO_ARCHIVE_CATEGORIES = ("qr_code", "signature", "seal")

    async def describe_image(
        self, image_path: str, context_text: str | None = None
    ) -> dict:
        """Generate image description and auto-classify using VLM API.

        Returns:
            {"description": str, "category": str}

        Raises:
            InkCubeError subclass (VLMNotConfigured, VLMNetworkError, etc.)
        """
        if not self.api_url or not self.api_key:
            raise VLMNotConfigured()

        logger.info("VLM describe_image: path=%s, has_context=%s", image_path, context_text is not None)
        result = await self._call_external_api(image_path, context_text)
        # 2.10 §4.2: VLM 出口收口记录（API 返回的精确 usage；响应无 usage
        # 字段时 record 内部忽略不落库）。pop 保持返回形状 {description, category}
        # 不变，对既有消费方零影响
        usage_record.record(self.model, "vlm", result.pop("usage", None))
        logger.info("VLM result: category=%s, desc_len=%d", result.get("category", ""), len(result.get("description", "")))
        return result
    
    async def _call_external_api(
        self, image_path: str, context_text: str | None = None
    ) -> dict:
        """Call OpenAI-compatible VLM API, return {description, category}."""
        image_b64 = self._encode_image(image_path)

        cat_list = "/".join(self.CATEGORIES)
        prompt = (
            "请分析这张图片，完成两项任务：\n"
            f"1. 分类：从以下类别中选择一个：{cat_list}\n"
            "2. 描述：用中文详细描述图片内容，包括类型、数据趋势、关键信息等\n\n"
            "请严格按以下JSON格式返回（不要添加任何其他文字或markdown标记）：\n"
            '{"category": "类别", "description": "详细描述"}'
        )
        if context_text:
            prompt += f"\n\n以下是图片在文档中的上下文，供参考：\n{context_text}"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{image_b64}"
                            },
                        },
                    ],
                }
            ],
            "max_tokens": 800,
        }

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(
                    self.api_url.rstrip("/") + "/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    if resp.status == 401:
                        error_text = await resp.text()
                        logger.error("VLM auth failed (401): invalid API key")
                        raise VLMInvalidKey(detail=error_text)
                    if resp.status == 429:
                        error_text = await resp.text()
                        logger.warning("VLM rate limited (429)")
                        raise VLMRateLimited(detail=error_text)
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.error("VLM API error: status=%d, body=%s", resp.status, error_text[:200])
                        raise VLMCallFailed(
                            detail=f"API error {resp.status}: {error_text}"
                        )

                    data = await resp.json()
                    raw = data["choices"][0]["message"]["content"]
                    result = self._parse_vlm_response(raw)
                    # 2.10 §4.2: 提取 API 返回的精确 usage（原被丢弃），随结果
                    # 返回交由 describe_image 收口记录；无 usage 字段时置 None
                    api_usage = data.get("usage") or {}
                    result["usage"] = {
                        "prompt_tokens": api_usage.get("prompt_tokens", 0),
                        "completion_tokens": api_usage.get("completion_tokens", 0),
                        "total_tokens": api_usage.get("total_tokens", 0),
                    } if api_usage else None
                    return result
            except (aiohttp.ClientConnectionError, aiohttp.ServerTimeoutError) as e:
                logger.error("VLM network error: %s", str(e))
                raise VLMNetworkError(detail=str(e))
            except InkCubeError:
                raise
            except Exception as e:
                raise VLMCallFailed(detail=str(e))
    
    @staticmethod
    def _parse_vlm_response(raw: str) -> dict:
        """Parse VLM JSON response, with fallback for non-JSON output.

        Returns {"description": str, "category": str}.
        """
        # Try to extract JSON from the response
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
            if cat not in VLMClient.CATEGORIES:
                cat = "other"
            return {"description": desc, "category": cat}
        except (json.JSONDecodeError, AttributeError):
            # VLM didn't return valid JSON — treat entire response as description
            return {"description": raw.strip(), "category": "other"}

    def _encode_image(self, image_path: str) -> str:
        """Encode image to base64"""
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
