"""python/llm/client.py — Lightweight LLM API client (OpenAI-compatible).

Shared by:
  - agent/react_agent.py   (产业洞察 Chat)
  - knowledge/rag.py       (知识库 Chat)

This module only handles HTTP communication with an OpenAI-compatible API.
It has NO dependency on agent or knowledge subsystems.
"""
import httpx
import logging
from typing import Any

from errors import LLMInvalidKey, LLMNetworkError, LLMCallFailed

logger = logging.getLogger(__name__)


class LLMClient:
    def __init__(self, settings: dict):
        self.api_url = settings.get("api_url", "https://api.openai.com/v1").rstrip("/")
        self.api_key = settings.get("api_key", "")
        self.model = settings.get("model", "gpt-4o")
        self.temperature = settings.get("temperature", 0.7)
        self.max_tokens = settings.get("max_tokens", 4096)
        self.top_p = settings.get("top_p", 0.9)
        self.timeout = settings.get("llm_timeout", 120)

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
        logger.info("LLM chat request: model=%s, messages=%d, tools=%s, timeout=%ds",
                    self.model, len(messages), "yes" if tools else "no", self.timeout)
        request_body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "top_p": self.top_p,
            "stream": False,
        }
        if tools:
            request_body["tools"] = tools
            request_body["tool_choice"] = "auto"

        async with httpx.AsyncClient(timeout=float(self.timeout)) as client:
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
            except httpx.ConnectError as e:
                logger.error("LLM connection failed: %s", str(e))
                raise LLMNetworkError(detail=str(e))
            except httpx.TimeoutException as e:
                logger.error("LLM timeout: %s", str(e))
                raise LLMNetworkError(detail=f"Timeout: {e}")

            if response.status_code == 401:
                logger.error("LLM auth failed (401): invalid API key")
                raise LLMInvalidKey(detail=response.text)
            if response.status_code != 200:
                logger.error("LLM API error: status=%d, body=%s", response.status_code, response.text[:500])
                raise LLMCallFailed(
                    detail=f"API error {response.status_code}: {response.text}"
                )

            data = response.json()
            choice = data["choices"][0]
            msg = choice["message"]
            finish_reason = choice.get("finish_reason", "unknown")
            logger.debug("LLM response parsed: finish_reason=%s, has_tool_calls=%s",
                        finish_reason, "yes" if msg.get("tool_calls") else "no")

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
            reasoning_content = msg.get("reasoning_content")  # not used in legacy path
            # Some models return null content (e.g. refusal or tool-call edge cases)
            if content is None:
                logger.warning(
                    "LLM returned null content (model=%s, finish_reason=%s). "
                    "Full choice keys: %s",
                    self.model, finish_reason, list(choice.keys()),
                )
                return ""
            return content

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

        async with httpx.AsyncClient(timeout=float(self.timeout)) as client:
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
                    if response.status_code == 401:
                        raise LLMInvalidKey(detail="Unauthorized")
                    if response.status_code != 200:
                        body = await response.aread()
                        body_text = body.decode(errors='replace')
                        logger.error("LLM API error: status=%d, body=%s", response.status_code, body_text[:1000])
                        raise LLMCallFailed(
                            detail=f"API error {response.status_code}: {body_text}"
                        )

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

            except httpx.ConnectError as e:
                logger.error("Stream connection failed: %s", str(e))
                raise LLMNetworkError(detail=str(e))
            except httpx.TimeoutException as e:
                logger.error("Stream timeout: %s", str(e))
                raise LLMNetworkError(detail=f"Timeout: {e}")
