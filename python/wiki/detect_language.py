"""python/wiki/detect_language.py — Auto-detect Wiki language for output matching.

Per §12.2.14 of 2.6wiki-design.md.

Detects the dominant language of Wiki pages based on character frequency analysis.
Used to:
  - Match Lint/Review output language to Wiki content
  - Inject language instructions into LLM prompts
"""
import logging
import unicodedata
from typing import Any

logger = logging.getLogger(__name__)

# Language labels for LLM prompt injection
LANGUAGE_LABELS = {
    "zh": "中文",
    "en": "English",
    "ja": "日本語",
    "ko": "한국어",
    "mixed": "混合语言",
    "unknown": "未知语言",
}


def _is_cjk(ch: str) -> bool:
    """Check if character is CJK (Chinese/Japanese/Korean)."""
    try:
        name = unicodedata.name(ch)
        return any(kw in name for kw in ("CJK", "CHINESE", "HIRAGANA", "KATAKANA", "HANGUL"))
    except ValueError:
        return False


def _is_cjk_chinese(ch: str) -> bool:
    """Check if character is specifically Chinese (CJK Unified Ideograph)."""
    cp = ord(ch)
    return (
        (0x4E00 <= cp <= 0x9FFF) or  # CJK Unified Ideographs
        (0x3400 <= cp <= 0x4DBF) or  # CJK Extension A
        (0x20000 <= cp <= 0x2A6DF)   # CJK Extension B
    )


def _is_japanese(ch: str) -> bool:
    """Check if character is Japanese-specific (Hiragana/Katakana)."""
    cp = ord(ch)
    return (
        (0x3040 <= cp <= 0x309F) or  # Hiragana
        (0x30A0 <= cp <= 0x30FF)     # Katakana
    )


def _is_korean(ch: str) -> bool:
    """Check if character is Korean (Hangul)."""
    cp = ord(ch)
    return (
        (0xAC00 <= cp <= 0xD7AF) or  # Hangul Syllables
        (0x1100 <= cp <= 0x11FF)     # Hangul Jamo
    )


def _is_latin(ch: str) -> bool:
    """Check if character is Latin alphabet."""
    return ch.isalpha() and ord(ch) < 0x0250


def detect_language(text: str) -> str:
    """Detect the dominant language of a text sample.

    Uses character frequency analysis:
      - CJK Chinese characters → zh
      - Hiragana/Katakana → ja
      - Hangul → ko
      - Latin alphabet → en
      - Mix of above → mixed

    Args:
        text: Text sample to analyze.

    Returns:
        Language code: "zh", "en", "ja", "ko", "mixed", or "unknown".
    """
    if not text or not text.strip():
        return "unknown"

    # Sample up to 2000 characters for efficiency
    sample = text[:2000]

    counts = {"zh": 0, "ja": 0, "ko": 0, "en": 0, "other": 0}

    for ch in sample:
        if ch.isspace() or not ch.isprintable():
            continue
        if _is_japanese(ch):
            counts["ja"] += 1
        elif _is_korean(ch):
            counts["ko"] += 1
        elif _is_cjk_chinese(ch):
            counts["zh"] += 1
        elif _is_latin(ch):
            counts["en"] += 1
        else:
            counts["other"] += 1

    total = sum(counts.values())
    if total == 0:
        return "unknown"

    # Find dominant language
    dominant = max(counts, key=lambda k: counts[k])
    dominant_count = counts[dominant]
    dominant_ratio = dominant_count / total

    # If dominant language is > 50% of alphabetic chars, it's clear
    if dominant_ratio > 0.5:
        if dominant == "other":
            return "unknown"
        return dominant

    # Check for Japanese (kanji + hiragana/katakana combined)
    jp_total = counts["ja"] + counts["zh"]  # Japanese uses both kanji and kana
    if counts["ja"] > 0 and jp_total / total > 0.5:
        return "ja"

    # Check if mixed (no single language > 30%)
    if dominant_ratio < 0.3:
        return "mixed"

    # Fallback to dominant
    if dominant == "other":
        return "unknown"
    return dominant


def detect_wiki_language(db: Any, project_id: str) -> str:
    """Detect the dominant language of a project's Wiki pages.

    Samples content from up to 10 pages for efficiency.

    Returns:
        Language code: "zh", "en", "ja", "ko", "mixed", or "unknown".
    """
    try:
        pages = db.list_wiki_pages(project_id)
        if not pages:
            return "unknown"

        # Sample up to 10 pages
        sample_pages = pages[:10]
        combined_text = ""
        for page in sample_pages:
            content = page.get("content") or ""
            title = page.get("title") or ""
            combined_text += title + " " + content + "\n"

        lang = detect_language(combined_text)
        logger.info("Wiki language for project %s: %s (sampled %d pages)",
                     project_id, lang, len(sample_pages))
        return lang
    except Exception as e:
        logger.warning("Language detection failed for project %s: %s", project_id, e)
        return "unknown"


def get_language_hint(lang_code: str) -> str:
    """Get a human-readable language hint for LLM prompt injection.

    Args:
        lang_code: Language code from detect_language().

    Returns:
        Human-readable language name, or empty string for unknown/mixed.
    """
    if lang_code in ("unknown", "mixed"):
        return ""
    return LANGUAGE_LABELS.get(lang_code, "")
