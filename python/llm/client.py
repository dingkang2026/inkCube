"""python/llm/client.py — Lightweight LLM API client (OpenAI-compatible).

Shared by:
  - agent/react_agent.py   (产业洞察 Chat)
  - knowledge/rag.py       (知识库 Chat)

This module only handles HTTP communication with an OpenAI-compatible API.
It has NO dependency on agent or knowledge subsystems.
"""
import httpx
import json
import logging
from typing import Any

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
        self.model = settings.get("model", "gpt-4o")
        self.temperature = settings.get("temperature", 0.7)
        self.max_tokens = settings.get("max_tokens", 4096)
        self.top_p = settings.get("top_p", 0.9)
        self.connect_timeout = settings.get("llm_connect_timeout", 30)
        self.read_timeout = settings.get("llm_read_timeout", 300)
        # Instance-level memory: this endpoint rejected reasoning params with 400
        # → skip injection for the client's lifetime (avoids per-call 400→fallback
        # double requests in agent loops / report pipelines).
        self._reasoning_rejected = False

    def _apply_reasoning_params(self, request_body: dict, settings: dict) -> dict:
        """Inject reasoning/thinking parameters from settings into request_body.

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
        override_raw = str(settings.get("reasoning_override") or "").strip()
        if override_raw:
            try:
                override = json.loads(override_raw)
            except ValueError:
                logger.warning("reasoning_override is not valid JSON — ignored: %.100s", override_raw)
            else:
                if isinstance(override, dict) and override:
                    originals = {k: request_body.get(k, _ABSENT) for k in override}
                    request_body.update(override)
                    return originals
                if not isinstance(override, dict):
                    logger.warning("reasoning_override must be a JSON object — ignored")
        effort = str(settings.get("reasoning_effort") or "").strip()
        if effort:
            originals = {"reasoning_effort": request_body.get("reasoning_effort", _ABSENT)}
            request_body["reasoning_effort"] = effort
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

    async def chat(self, messages: list[dict], settings: dict, tools: list[dict] | None = None) -> "str | dict":
        """Send a chat completion request to the LLM API.

        Args:
            messages: Conversation history in OpenAI message format.
            settings: Application settings dict.
            tools: Optional list of function/tool definitions for native
                   function calling.  When provided, the return type changes
                   from ``str`` to ``dict`` with keys ``content``,
                   ``tool_calls``, and ``finish_reason``.

        Returns:
            - ``str`` when *tools* is ``None`` (backward-compatible).
            - ``dict`` when *tools* is provided:
              ``{"content": str|None, "tool_calls": list|None, "finish_reason": str}``
        """
        # Log per-message breakdown for debugging
        for i, msg in enumerate(messages):
            role = msg.get("role", "?")
            content = msg.get("content", "")
            content_len = len(content) if isinstance(content, str) else 0
            logger.info("LLM message [%d/%d]: role=%s, content_len=%d", i + 1, len(messages), role, content_len)
        logger.info("LLM chat request: model=%s, messages=%d, tools=%s, connect_timeout=%ds, read_timeout=%ds",
                    self.model, len(messages), "yes" if tools else "no", self.connect_timeout, self.read_timeout)
        # Allow per-call override of max_tokens via settings parameter
        max_tokens = int(settings.get("max_tokens", self.max_tokens))
        request_body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": max_tokens,
            "top_p": self.top_p,
            "stream": False,
        }
        if tools:
            request_body["tools"] = tools
            request_body["tool_choice"] = "auto"
        reasoning_params = self._apply_reasoning_params(request_body, settings)

        import asyncio
        last_err = None
        for attempt in range(2):  # max 2 attempts (1 retry on transient network errors)
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
                    last_err = LLMNetworkError(detail=f"请求超时（{self.read_timeout}s），模型可能仍在处理中，请稍后重试")
                    if attempt == 0:
                        await asyncio.sleep(2)
                        continue
                    raise last_err
                except httpx.ConnectError as e:
                    logger.error("LLM connection failed (attempt %d/2): %s", attempt + 1, str(e))
                    last_err = LLMNetworkError(detail=f"连接失败: {e}")
                    if attempt == 0:
                        await asyncio.sleep(2)
                        continue
                    raise last_err

                if response.status_code != 200:
                    # 400 with reasoning params injected → retry once without them
                    # (model doesn't support the param; see settings > 思考深度)
                    if response.status_code == 400 and reasoning_params and attempt == 0:
                        logger.warning(
                            "LLM 400 with reasoning params %s — retrying without them",
                            list(reasoning_params),
                        )
                        self._rollback_reasoning_params(request_body, reasoning_params)
                        reasoning_params = {}
                        self._reasoning_rejected = True  # remember for this client's lifetime
                        continue
                    _raise_api_error(response.status_code, response.text, has_tools=bool(tools))

                data = response.json()
                choice = data["choices"][0]
                msg = choice["message"]
                finish_reason = choice.get("finish_reason", "unknown")
                usage = data.get("usage", {})
                logger.info("LLM response: finish_reason=%s, msg_keys=%s, usage=%s",
                            finish_reason, list(msg.keys()), usage)

                # ── Function Calling path (tools requested) ──
                if tools is not None:
                    return {
                        "content": msg.get("content"),  # may be None when model only returns tool_calls
                        "tool_calls": msg.get("tool_calls"),  # [{id, function: {name, arguments}}]
                        "reasoning_content": msg.get("reasoning_content"),  # model internal thinking
                        "finish_reason": finish_reason,
                    }

                # ── Legacy text path (no tools) — always return str for backward compatibility
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
                        return reasoning_content
                    return ""
                return content

    async def chat_full(self, messages: list[dict], settings: dict) -> dict:
        """Chat completion that returns content, finish_reason and precise token usage.

        Unlike chat() which returns str for backward compatibility, this method
        always returns a dict with content and usage information from the API.

        Returns:
            {"content": str, "finish_reason": str,
             "usage": {"prompt_tokens": int, "completion_tokens": int, "total_tokens": int}}
            finish_reason 为 "length" 时表示输出被 max_tokens 截断，调用方应自检
        """
        logger.info("LLM chat_full request: model=%s, messages=%d", self.model, len(messages))
        request_body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "top_p": self.top_p,
            "stream": False,
        }
        reasoning_params = self._apply_reasoning_params(request_body, settings)

        import asyncio
        last_err = None
        for attempt in range(2):  # max 2 attempts (1 retry on transient network errors)
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
                except httpx.TimeoutException as e:
                    logger.error("LLM timeout (attempt %d/2): %s", attempt + 1, str(e))
                    last_err = LLMNetworkError(detail=f"请求超时（{self.read_timeout}s），模型可能仍在处理中，请稍后重试")
                    if attempt == 0:
                        await asyncio.sleep(2)
                        continue
                    raise last_err
                except httpx.ConnectError as e:
                    logger.error("LLM connection failed (attempt %d/2): %s", attempt + 1, str(e))
                    last_err = LLMNetworkError(detail=f"连接失败: {e}")
                    if attempt == 0:
                        await asyncio.sleep(2)
                        continue
                    raise last_err

                if response.status_code != 200:
                    if response.status_code == 400 and reasoning_params and attempt == 0:
                        logger.warning(
                            "LLM 400 with reasoning params %s — retrying without them",
                            list(reasoning_params),
                        )
                        self._rollback_reasoning_params(request_body, reasoning_params)
                        reasoning_params = {}
                        self._reasoning_rejected = True  # remember for this client's lifetime
                        continue
                    # chat_full 不支持 function calling，无 tools 参数（FC 提示仅 chat/chat_stream 适用）
                    _raise_api_error(response.status_code, response.text, has_tools=False)

                data = response.json()
                choice = data["choices"][0]
                content = choice["message"].get("content") or ""
                finish_reason = choice.get("finish_reason", "unknown")
                usage = data.get("usage", {})

                return {
                    "content": content,
                    "finish_reason": finish_reason,
                    "usage": {
                        "prompt_tokens": usage.get("prompt_tokens", 0),
                        "completion_tokens": usage.get("completion_tokens", 0),
                        "total_tokens": usage.get("total_tokens", 0),
                    },
                }

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
        self, messages: list[dict], settings: dict, tools: list[dict] | None = None,
    ):
        """Streaming chat completion — yields chunks as they arrive.

        Yields dicts with one of:
        - {"type": "content", "delta": "..."}       — text chunk
        - {"type": "tool_calls", "tool_calls": [...]} — accumulated tool calls (final)
        - {"type": "finish", "finish_reason": "..."}  — stream complete

        For text mode (tools=None), only yields content + finish.
        For FC mode (tools provided), accumulates tool_call deltas and
        yields the complete tool_calls list at the end.
        """
        logger.info("LLM stream request: model=%s, messages=%d, tools=%s",
                    self.model, len(messages), "yes" if tools else "no")
        if tools:
            import json as _json
            # Log each tool separately for better debugging
            for i, tool in enumerate(tools):
                tool_name = tool.get("function", {}).get("name", "unknown")
                tool_type = tool.get("type", "")
                logger.info("Tool [%d]: name=%s, type=%s", i, tool_name, tool_type)
            logger.info("Total tools: %d", len(tools))
        request_body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "top_p": self.top_p,
            "stream": True,
        }
        if tools:
            request_body["tools"] = tools
            request_body["tool_choice"] = "auto"
        reasoning_params = self._apply_reasoning_params(request_body, settings)

        # 400 with reasoning params → one retry without them (same fallback as chat)
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
                            if response.status_code == 400 and reasoning_params and stream_attempt == 0:
                                logger.warning(
                                    "LLM stream 400 with reasoning params %s — retrying without them",
                                    list(reasoning_params),
                                )
                                self._rollback_reasoning_params(request_body, reasoning_params)
                                reasoning_params = {}
                                self._reasoning_rejected = True  # remember for this client's lifetime
                                continue
                            _raise_api_error(response.status_code, body_text, has_tools=bool(tools))

                        # FC mode: accumulate tool call deltas
                        tc_accum: dict[int, dict] = {}  # index -> {id, function: {name, arguments}}
                        finish_reason = "unknown"

                        async for line in response.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            data_str = line[6:]
                            if data_str.strip() == "[DONE]":
                                break
                            try:
                                import json as _json
                                chunk = _json.loads(data_str)
                            except Exception:
                                continue

                            if not chunk.get("choices"):
                                continue
                            choice = chunk["choices"][0]
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
                        yield {"type": "finish", "finish_reason": finish_reason}

                except httpx.TimeoutException as e:
                    logger.error("Stream timeout: %s", str(e))
                    raise LLMNetworkError(detail=f"流式响应超时（{self.read_timeout}s），模型可能仍在处理中，请稍后重试")
                except httpx.ConnectError as e:
                    logger.error("Stream connection failed: %s", str(e))
                    raise LLMNetworkError(detail=f"连接失败: {e}")

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
