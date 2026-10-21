"""python/llm/budget.py — Token budget authority for all LLM pipelines.

拍板口径：
- D1: MIN_CONTEXT_WINDOW / MIN_OUTPUT_TOKENS 是最小限制，只能上调不能下调；
      配置值低于下限时读取自动抬升（floor）；默认值即下限，显示值=生效值。
- D2: 全管线唯一取值入口——禁止 settings.get("max_tokens", <硬编码>) 散落。
- 口径纪律: 预算判断用 token；内容切片用 chars；chars↔token 换算只用 CHARS_PER_TOKEN。

count_tokens 自 agent/context_manager.py 原样迁入（tiktoken 精确 / CJK、Latin 启发式）。
"""
import logging
import os
import threading

logger = logging.getLogger(__name__)

MIN_CONTEXT_WINDOW = 131_072   # 128K, minimum context window, raise-only
MIN_OUTPUT_TOKENS = 32_768     # 32K, minimum output cap, raise-only
# 中文主导内容的保守换算（1 tok ≈ 1.5 chars）。英文实际 ~3 chars/tok，
# 用 1.5 会高估 token 数 → 配额偏保守，错误方向安全；流式 usage 落地后可对账校准。
CHARS_PER_TOKEN = 1.5


def context_window(settings: dict) -> int:
    """Context window budget in tokens: max(configured, MIN_CONTEXT_WINDOW)."""
    try:
        value = int(settings.get("context_window") or 0)
    except (TypeError, ValueError):
        value = 0
    return max(value, MIN_CONTEXT_WINDOW)


def output_tokens(settings: dict) -> int:
    """Output cap in tokens: max(configured, MIN_OUTPUT_TOKENS)."""
    try:
        value = int(settings.get("max_tokens") or 0)
    except (TypeError, ValueError):
        value = 0
    return max(value, MIN_OUTPUT_TOKENS)


# ── Token counting ──
# Use tiktoken for precise counting when available; fallback to heuristic.
#
# The encoder is initialised in a daemon thread, NOT at import time:
# encoding_for_model("gpt-4o") needs the o200k_base data file, and when it is
# not in the local cache tiktoken downloads it from the OpenAI CDN via a
# requests.get() WITHOUT timeout. On networks where that CDN is unreachable
# the download blocks forever, which froze module import (agent.task_agent →
# deps → main) and made the whole backend fail its /health budget
# ("Python backend failed to start within 60000ms", 2026-10-13).
# Import below is local-only; the daemon thread fills the encoder in when
# (and if) it becomes available — count_tokens falls back to the heuristic
# until then.
_tiktoken_encoder = None

try:
    import tiktoken
except ImportError:
    tiktoken = None


def _init_tiktoken_encoder() -> None:
    global _tiktoken_encoder
    try:
        cache_dir = os.environ.get("TIKTOKEN_CACHE_DIR")
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
        _tiktoken_encoder = tiktoken.encoding_for_model("gpt-4o")
        logger.info("tiktoken encoder ready (gpt-4o / o200k_base)")
    except Exception as e:
        # Reachable only for fast failures (missing pkg data, corrupt cache);
        # a hung CDN download stays in the daemon thread and never blocks.
        logger.warning("tiktoken encoder unavailable, using heuristic fallback: %s", e)


if tiktoken is not None:
    threading.Thread(
        target=_init_tiktoken_encoder, daemon=True, name="tiktoken-init"
    ).start()


def count_tokens(text: str) -> int:
    """Count tokens for a text string.

    Uses tiktoken (precise) when available, otherwise falls back to
    a language-aware heuristic (CJK ~1.5 chars/token, Latin ~3 chars/token).
    """
    if not text:
        return 0
    if _tiktoken_encoder is not None:
        return len(_tiktoken_encoder.encode(text))

    # Heuristic: detect CJK characters for better estimation
    cjk_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    if cjk_count > len(text) * 0.3:
        # Mostly CJK: ~1.5 chars per token (cl100k_base ≈ 1 token/CJK char)
        return max(1, int(len(text) * 2 / 3))
    else:
        # Mostly Latin: ~3 chars per token
        return max(1, len(text) // 3)
