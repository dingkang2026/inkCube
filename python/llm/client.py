"""python/llm/client.py — Lightweight LLM API client (OpenAI-compatible).

全后端 LLM 管线的唯一 HTTP 出口（agent / report_generator / wiki / form_fill /
knowledge）。构造期一次性注入 settings（D3 构造期单轨），预算取值经
llm/budget.py（D2 唯一入口）。

This module only handles HTTP communication with an OpenAI-compatible API.
It has NO dependency on agent or knowledge subsystems.
"""
import httpx
import json
import logging
from typing import Any

from llm.budget import context_window, output_tokens
from llm import usage as usage_record  # 2.10 §4.2 单点记录（零 DB 依赖，sink 由 main 注入）
from errors import (
    LLMInvalidKey, LLMNetworkError, LLMCallFailed,
    LLMRateLimited, LLMQuotaExhausted, LLMModelNotFound,
)

logger = logging.getLogger(__name__)

# Sentinel: key was absent before reasoning-param injection (see _apply_reasoning_params)
_ABSENT = object()


def _raise_api_error(status_code: int, body_text: str, has_tools: bool = False) -> None:
    """Map non-200 API responses to actionable InkCubeErrors (always raises).

    配置/凭据类（401 Key 失效 / 429 限流额度 / 402・403 欠费 / 404 模型名错）
    归入 CREDENTIAL 类错误，message+suggestion 自带设置页修正指引，
    供后台任务失败落库时经 errors.actionable() 产出可行动文案（0.4 §7.3 v3.6）。
    """
    if status_code == 401:
        logger.error("LLM auth failed (401): invalid API key")
        raise LLMInvalidKey(detail=body_text)
    if status_code == 429:
        logger.error("LLM rate limited (429)")
        raise LLMRateLimited(detail=body_text)
    if status_code in (402, 403):
        logger.error("LLM quota error (%d)", status_code)
        raise LLMQuotaExhausted(detail=body_text)
    if status_code == 404:
        logger.error("LLM model not found (404)")
        raise LLMModelNotFound(detail=body_text)
    detail = f"API error {status_code}: {body_text}"
    if status_code == 400 and has_tools:
        detail += "（模型可能不支持 function calling，可在设置中测试模型能力）"
    logger.error("LLM API error: status=%d, body=%s", status_code, body_text[:500])
    raise LLMCallFailed(detail=detail)


class LLMClient:
    def __init__(self, settings: dict):
        self.api_url = settings.get("api_url", "https://api.openai.com/v1").rstrip("/")
        self.api_key = settings.get("api_key", "")
        # 档位制（llm/routing.py）：model 键由消费点经路由解析注入；缺键 →
        # 空串（请求被网关拒，快速显性失败）。禁止硬编码模型名兜底——否则
        # 路由漏网变成「向网关请求不存在的模型」的误导性故障（2026-10-20
        # 实证：gpt-4o 兜底打到智谱端点报 1211 模型不存在）
        self.model = settings.get("model", "")
        self.temperature = settings.get("temperature", 0.7)
        # 构造期单轨注入（D3）：预算两键经 budget 唯一入口取值，floor 一次到位；
        # 方法签名不再收 settings——差异化需求用「派生 dict → 构造新 client」表达
        self.max_tokens = output_tokens(settings)
        self.context_window = context_window(settings)
        self.top_p = settings.get("top_p", 0.9)
        # Suppresses the GLM tendency to emit the answer twice in one stop
        # response (task复盘: prompt bans alone left a ~25% repeat rate).
        # 0 disables injection — escape hatch for endpoints rejecting the param.
        self.frequency_penalty = float(settings.get("frequency_penalty", 0.3))
        self.connect_timeout = settings.get("llm_connect_timeout", 30)
        self.read_timeout = settings.get("llm_read_timeout", 300)
        # Reasoning params snapshot（D3）：原 _apply_reasoning_params 从 settings 现读，
        # 签名收窄后改读构造期快照
        self._reasoning_override_raw = str(settings.get("reasoning_override") or "").strip()
        self._reasoning_effort = str(settings.get("reasoning_effort") or "").strip()
        # Instance-level memory: this endpoint rejected reasoning params with 400
        # → skip injection for the client's lifetime (avoids per-call 400→fallback
        # double requests in agent loops / report pipelines).
        self._reasoning_rejected = False
        # Instance-level memory: stream ended without usage chunk（端点不支持
        # stream_options.include_usage）→ 只告警一次，后续同状态降 debug 不刷屏
        self._stream_usage_missing_logged = False

    def _apply_reasoning_params(self, request_body: dict) -> dict:
        """Inject reasoning/thinking parameters (constructor snapshot) into request_body.

        Priority: ``reasoning_override`` (free-form JSON object merged into the
        request body top-level — for vendor-specific params like GLM ``thinking``,
        Qwen ``thinking_budget``) > ``reasoning_effort`` (standard
        OpenAI-compatible effort level: low/medium/high/max/...).

        Returns a map of injected keys to their pre-injection values (or
        _ABSENT), consumed by ``_rollback_reasoning_params`` when the API
        rejects the params with HTTP 400.
        """
        if self._reasoning_rejected:
            logger.debug("Reasoning params skipped — endpoint rejected them earlier (400 fallback memory)")
            return {}
        if self._reasoning_override_raw:
            try:
                override = json.loads(self._reasoning_override_raw)
            except ValueError:
                logger.warning("reasoning_override is not valid JSON — ignored: %.100s", self._reasoning_override_raw)
            else:
                if isinstance(override, dict) and override:
                    originals = {k: request_body.get(k, _ABSENT) for k in override}
                    request_body.update(override)
                    return originals
                if not isinstance(override, dict):
                    logger.warning("reasoning_override must be a JSON object — ignored")
        if self._reasoning_effort:
            originals = {"reasoning_effort": request_body.get("reasoning_effort", _ABSENT)}
            request_body["reasoning_effort"] = self._reasoning_effort
            return originals
        return {}

    @staticmethod
    def _rollback_reasoning_params(request_body: dict, originals: dict) -> None:
        """Restore request_body to its pre-injection state (HTTP 400 fallback)."""
        for key, original in originals.items():
            if original is _ABSENT:
                request_body.pop(key, None)
            else:
                request_body[key] = original

    def _build_request_body(
        self, messages: list[dict], tools: list[dict] | None = None, stream: bool = False,
    ) -> tuple[dict, dict]:
        """Build the shared chat-completions request body (D4 内部去重).

        Returns ``(request_body, reasoning_params)`` where reasoning_params is
        the injection map consumed by the HTTP-400 fallback paths.
        """
        request_body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "top_p": self.top_p,
            "stream": stream,
        }
        if self.frequency_penalty:
            request_body["frequency_penalty"] = self.frequency_penalty
        if tools:
            request_body["tools"] = tools
            request_body["tool_choice"] = "auto"
        reasoning_params = self._apply_reasoning_params(request_body)
        return request_body, reasoning_params

    def _fallback_reasoning(self, request_body: dict, reasoning_params: dict) -> dict:
        """Rollback injected reasoning params after HTTP 400 (one-shot fallback).

        Marks the rejection in instance memory so subsequent calls on this
        client skip injection entirely. Returns the new (empty) state.
        """
        logger.warning(
            "LLM 400 with reasoning params %s — retrying without them",
            list(reasoning_params),
        )
        self._rollback_reasoning_params(request_body, reasoning_params)
        self._reasoning_rejected = True  # remember for this client's lifetime
        return {}

    async def _post_json(
        self, request_body: dict, reasoning_params: dict, has_tools: bool,
    ) -> httpx.Response:
        """POST a non-streaming chat completion with retry/fallback (D4 内部去重).

        Shared by chat_full()/chat_stream(): 2 attempts total — transient network
        errors (timeout / connection) retry once after 2s; HTTP 400 caused by
        injected reasoning params retries once without them.
        """
        import asyncio
        for attempt in range(2):
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=self.connect_timeout, read=self.read_timeout, write=self.connect_timeout, pool=self.connect_timeout)) as client:
                try:
                    response = await client.post(
                        f"{self.api_url}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                        },
                        json=request_body,
                    )
                    logger.debug("LLM response: status=%d", response.status_code)
                except httpx.TimeoutException as e:
                    logger.error("LLM timeout (attempt %d/2): %s", attempt + 1, str(e))
                    if attempt == 0:
                        await asyncio.sleep(2)
                        continue
                    raise LLMNetworkError(detail=f"请求超时（{self.read_timeout}s），模型可能仍在处理中，请稍后重试")
                except httpx.ConnectError as e:
                    logger.error("LLM connection failed (attempt %d/2): %s", attempt + 1, str(e))
                    if attempt == 0:
                        await asyncio.sleep(2)
                        continue
                    raise LLMNetworkError(detail=f"连接失败: {e}")

                if response.status_code != 200:
                    # 400 with reasoning params injected → retry once without them
                    # (model doesn't support the param; see settings > 思考深度)
                    if response.status_code == 400 and reasoning_params and attempt == 0:
                        reasoning_params = self._fallback_reasoning(request_body, reasoning_params)
                        continue
                    _raise_api_error(response.status_code, response.text, has_tools=has_tools)
                return response

    async def chat_full(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        """Non-streaming completion — the single implementation (former chat() merged in, P3).

        Always returns a dict with ``content``, ``finish_reason`` and precise
        token ``usage``. With *tools* provided (function calling) the dict
        additionally carries ``tool_calls`` / ``reasoning_content``; content
        may be None when the model only returns tool calls.

        Text path hardening: some deep-thinking models (GLM-5...) put the
        answer in ``reasoning_content`` with null ``content`` — fall back to
        it so text consumers always receive a str (behavior inherited from
        the former chat(); chat_full previously returned "" here).

        Returns:
            {"content": str|None, "finish_reason": str, "usage": {...}
             [, "tool_calls": list|None, "reasoning_content": str|None]}
            finish_reason 为 "length" 时表示输出被 max_tokens 截断，调用方应自检
        """
        logger.info("LLM chat_full request: model=%s, messages=%d, tools=%s",
                    self.model, len(messages), "yes" if tools else "no")
        request_body, reasoning_params = self._build_request_body(messages, tools=tools)
        response = await self._post_json(request_body, reasoning_params, has_tools=tools is not None)

        data = response.json()
        choice = data["choices"][0]
        msg = choice["message"]
        finish_reason = choice.get("finish_reason", "unknown")
        usage = data.get("usage", {})
        result = {
            "finish_reason": finish_reason,
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
        }
        # 2.10 §4.2: 出口收口记录（API 返回的精确 usage；400 降级重试发生在
        # _post_json 内部，此处只在最终成功响应后执行一次，天然不重复计数）
        usage_record.record(self.model, "full", result["usage"])

        # ── Function Calling path (tools requested) ──
        if tools is not None:
            result["content"] = msg.get("content")  # may be None when model only returns tool_calls
            result["tool_calls"] = msg.get("tool_calls")  # [{id, function: {name, arguments}}]
            result["reasoning_content"] = msg.get("reasoning_content")
            return result

        # ── Text path — content is always a str ──
        content = msg.get("content")
        reasoning_content = msg.get("reasoning_content")

        # Log when content is empty or null for debugging
        if not content:
            logger.warning(
                "LLM empty content (model=%s, finish_reason=%s, "
                "content_type=%s, content_repr=%r, reasoning_len=%d, "
                "msg_keys=%s, usage=%s)",
                self.model, finish_reason,
                type(content).__name__, content,
                len(reasoning_content) if reasoning_content else 0,
                list(msg.keys()), usage,
            )

        # Some models return null content (e.g. refusal or tool-call edge cases)
        if content is None:
            # GLM-5 deep-thinking models may put output in reasoning_content
            if reasoning_content:
                logger.info(
                    "LLM content is null, falling back to reasoning_content "
                    "(model=%s, finish_reason=%s, reasoning_len=%d)",
                    self.model, finish_reason, len(reasoning_content),
                )
                content = reasoning_content
        result["content"] = content or ""
        return result

    async def test_connection(self) -> bool:
        logger.info("Testing LLM connection: model=%s, url=%s", self.model, self.api_url)
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.post(
                    f"{self.api_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": [{"role": "user", "content": "Hi"}],
                        "max_tokens": 5,
                    },
                )
                logger.debug("Test connection response: status=%d", response.status_code)
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                logger.error("Test connection failed: %s", str(e))
                raise

            if response.status_code == 401:
                logger.error("Test connection auth failed (401)")
                raise LLMInvalidKey(detail=response.text)
            return response.status_code == 200

    async def chat_stream(
        self, messages: list[dict], tools: list[dict] | None = None,
    ):
        """Streaming chat completion — yields chunks as they arrive.

        Yields dicts with one of:
        - {"type": "content", "delta": "..."}       — text chunk
        - {"type": "reasoning", "delta": "..."}     — thinking chunk (DeepSeek R1 / GLM-5...)
        - {"type": "tool_calls", "tool_calls": [...]} — accumulated tool calls (final)
        - {"type": "usage", "usage": {...}}         — token usage (optional: OpenAI
          usage-only chunk, or trailing usage on the last choices chunk per
          2.10 §4.2; consumers must treat it optional)
        - {"type": "finish", "finish_reason": "..."}  — stream complete

        For text mode (tools=None), only yields content + finish.
        For FC mode (tools provided), accumulates tool_call deltas and
        yields the complete tool_calls list at the end.
        """
        logger.info("LLM stream request: model=%s, messages=%d, tools=%s",
                    self.model, len(messages), "yes" if tools else "no")
        if tools:
            # Log each tool separately for better debugging
            for i, tool in enumerate(tools):
                tool_name = tool.get("function", {}).get("name", "unknown")
                tool_type = tool.get("type", "")
                logger.info("Tool [%d]: name=%s, type=%s", i, tool_name, tool_type)
            logger.info("Total tools: %d", len(tools))
        request_body, reasoning_params = self._build_request_body(messages, tools=tools, stream=True)
        # D5: request the trailing usage chunk (OpenAI stream_options extension).
        # Endpoints that don't recognize the key 400 → retried once without it
        # below (same one-shot fallback budget as the reasoning rollback).
        request_body["stream_options"] = {"include_usage": True}

        # 400 fallback → one retry without the rejected optional keys
        for stream_attempt in range(2):
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=self.connect_timeout, read=self.read_timeout, write=self.connect_timeout, pool=self.connect_timeout)) as client:
                try:
                    async with client.stream(
                        "POST",
                        f"{self.api_url}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                        },
                        json=request_body,
                    ) as response:
                        if response.status_code != 200:
                            body = await response.aread()
                            body_text = body.decode(errors='replace')
                            if response.status_code == 400 and stream_attempt == 0:
                                # One-shot retry budget shared by both optional keys:
                                # drop whichever is present (reasoning params /
                                # stream_options) and retry once
                                retried = False
                                if reasoning_params:
                                    reasoning_params = self._fallback_reasoning(request_body, reasoning_params)
                                    retried = True
                                if "stream_options" in request_body:
                                    logger.warning("LLM stream 400 with stream_options — retrying without it")
                                    request_body.pop("stream_options", None)
                                    retried = True
                                if retried:
                                    continue
                            _raise_api_error(response.status_code, body_text, has_tools=bool(tools))

                        # FC mode: accumulate tool call deltas
                        tc_accum: dict[int, dict] = {}  # index -> {id, function: {name, arguments}}
                        finish_reason = "unknown"
                        usage_data: dict | None = None  # trailing usage chunk (D5)

                        async for line in response.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            data_str = line[6:]
                            if data_str.strip() == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data_str)
                            except Exception:
                                continue

                            choices = chunk.get("choices")
                            # usage 采集不依赖 choices 形态：OpenAI 官方是 choices=[]
                            # 的 usage-only chunk；智谱 BigModel / DeepSeek 等兼容端
                            # 点则附在最后一个带 choices 的 chunk 上（glm-5.3 实测：
                            # usage 与 finish_reason 同 chunk，无独立 usage chunk，
                            # 2026-10-16）。只认前者会整条流丢账。
                            if chunk.get("usage"):
                                usage_data = chunk["usage"]
                            if not choices:
                                continue
                            choice = choices[0]
                            delta = choice.get("delta", {})
                            fr = choice.get("finish_reason")
                            if fr:
                                finish_reason = fr

                            # Reasoning/thinking content (DeepSeek R1, GLM-5, etc.)
                            reasoning_delta = delta.get("reasoning_content")
                            if reasoning_delta:
                                yield {"type": "reasoning", "delta": reasoning_delta}

                            # Text content delta
                            content_delta = delta.get("content")
                            if content_delta:
                                yield {"type": "content", "delta": content_delta}

                            # Tool call deltas (FC mode)
                            if "tool_calls" in delta:
                                for tc_delta in delta["tool_calls"]:
                                    idx = tc_delta.get("index", 0)
                                    if idx not in tc_accum:
                                        tc_accum[idx] = {
                                            "id": tc_delta.get("id", ""),
                                            "type": "function",
                                            "function": {
                                                "name": tc_delta.get("function", {}).get("name", ""),
                                                "arguments": tc_delta.get("function", {}).get("arguments", ""),
                                            },
                                        }
                                    else:
                                        # Accumulate arguments
                                        existing = tc_accum[idx]
                                        if tc_delta.get("id"):
                                            existing["id"] = tc_delta["id"]
                                        fn_delta = tc_delta.get("function", {})
                                        if fn_delta.get("name"):
                                            existing["function"]["name"] = fn_delta["name"]
                                        if fn_delta.get("arguments"):
                                            existing["function"]["arguments"] += fn_delta["arguments"]

                        # If FC mode, yield accumulated tool calls
                        if tools is not None and tc_accum:
                            tool_calls_list = [
                                tc_accum[idx] for idx in sorted(tc_accum.keys())
                            ]
                            logger.debug("Stream FC mode: %d tool calls accumulated", len(tool_calls_list))
                            yield {"type": "tool_calls", "tool_calls": tool_calls_list}

                        logger.debug("Stream finished: finish_reason=%s", finish_reason)
                        # 2.10 §4.2: real token usage before finish (optional event —
                        # absent only when the stream carried no usage chunk at all;
                        # §7 宁缺勿估，告警一次可观测)
                        if usage_data:
                            usage_result = {
                                "prompt_tokens": usage_data.get("prompt_tokens", 0),
                                "completion_tokens": usage_data.get("completion_tokens", 0),
                                "total_tokens": usage_data.get("total_tokens", 0),
                            }
                            # 2.10 §4.2: 收口记录在 yield 之前——usage chunk 一旦
                            # 生成即已计费，消费者中途断开也不丢账
                            usage_record.record(self.model, "stream", usage_result)
                            yield {"type": "usage", "usage": usage_result}
                        elif not self._stream_usage_missing_logged:
                            # 端点不支持 include_usage（或 400 降级后 pop 了
                            # stream_options）：无精确值不落库，告警一次可观测
                            logger.warning(
                                "LLM stream ended without usage chunk "
                                "(endpoint may not support stream_options.include_usage) "
                                "— tokens NOT recorded for this endpoint's streams"
                            )
                            self._stream_usage_missing_logged = True
                        else:
                            logger.debug(
                                "LLM stream without usage chunk — tokens not recorded")
                        yield {"type": "finish", "finish_reason": finish_reason}

                except httpx.TimeoutException as e:
                    logger.error("Stream timeout: %s", str(e))
                    raise LLMNetworkError(detail=f"流式响应超时（{self.read_timeout}s），模型可能仍在处理中，请稍后重试")
                except httpx.ConnectError as e:
                    logger.error("Stream connection failed: %s", str(e))
                    raise LLMNetworkError(detail=f"连接失败: {e}")
            # 成功消费完毕即退出循环。continue（400 降级重试）与 raise（网络错误）
            # 均不经过此处；缺此 break 时 attempt=0 直接成功的调用会进入第二轮，
            # 同一请求发两次、两份完整流都被 yield（temperature>0 → 两次采样措辞
            # 不同 → 上层拼接出两份回答 + token 翻倍；2026-10-14 天眼查任务实证，
            # 回归：test_llm_stream_replay_guard.py）
            break

    async def probe(self, extra_body: dict) -> bool | None:
        """Send a minimal probe request to test parameter support.

        Returns True (200 — supported), False (400 — parameter rejected), or
        None (network/other failure — unknown). Used by /settings/personal_test
        capability probing.
        """
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 5,
            **extra_body,
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self.api_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
        except httpx.HTTPError as e:
            logger.warning("Capability probe failed (network): %s", str(e))
            return None
        if response.status_code == 200:
            return True
        if response.status_code == 400:
            return False
        logger.warning("Capability probe inconclusive: status=%d", response.status_code)
        return None
