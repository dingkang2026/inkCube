"""python/wiki/sweep_reviews.py — Two-stage automatic review resolution.

Per §12.2.8 of 2.6wiki-design.md.

Triggered after ingestion queue is drained (all files processed).

Stage 1: Rule matching (fast, free)
  - missing-page: title corresponds to an already-created page → auto resolve
  - broken-link: target page now exists → auto resolve
  - orphan: page now has inbound links → auto resolve

Stage 2: LLM semantic judgment (remaining unresolved)
  - Batch 40 reviews per call
  - LLM returns JSON: { "resolved": [review_id, ...], "reason": "..." }
  - Conservative: any error → keep entire batch pending
  - Max 5 batches (200 reviews) to limit LLM cost
"""
import json
import logging
import re
import unicodedata
from typing import Any

logger = logging.getLogger(__name__)

# ── Constants ──

LLM_BATCH_SIZE = 40
MAX_LLM_BATCHES = 5


# ── Helpers ──


def _normalize_key(s: str) -> str:
    """Normalize a page reference key for matching."""
    s = unicodedata.normalize("NFKC", s)
    s = s.lower().strip()
    # Remove path prefixes and .md suffix
    for prefix in ("./", "../", "/"):
        if s.startswith(prefix):
            s = s[len(prefix):]
    if s.endswith(".md"):
        s = s[:-3]
    # Normalize separators
    s = s.replace("-", " ").replace("_", " ").strip()
    return s


def _extract_page_ref_from_title(title: str) -> str:
    """Extract a page reference from a review title.

    Examples:
      - "Missing page: PAOs" → "paos"
      - "Broken link: [[DPAO]]" → "dpao"
      - "Orphan: VFA uptake" → "vfa uptake"
    """
    # Try [[wiki-link]] pattern first
    wiki_link_match = re.search(r"\[\[(.+?)(?:\|.+?)?\]\]", title)
    if wiki_link_match:
        return _normalize_key(wiki_link_match.group(1))

    # Try "Type: Name" pattern
    colon_match = re.search(r"[:：]\s*(.+)$", title)
    if colon_match:
        return _normalize_key(colon_match.group(1).strip())

    return _normalize_key(title)


# ── Stage 1: Rule matching ──


def _stage1_rule_match(
    pending_reviews: list[dict],
    existing_page_keys: set[str],
    page_inbound_counts: dict[str, int],
) -> tuple[list[str], list[dict]]:
    """Stage 1: Fast rule-based resolution.

    Returns:
        (resolved_ids, remaining_reviews) tuple.
    """
    resolved_ids: list[str] = []
    remaining: list[dict] = []

    # Build normalized key set for fast lookup
    normalized_existing = {_normalize_key(k) for k in existing_page_keys}

    for review in pending_reviews:
        review_id = review["id"]
        review_type = review.get("type", "")
        title = review.get("title", "")
        ref_key = _extract_page_ref_from_title(title)

        auto_resolved = False

        if review_type == "missing-page":
            # If a page with this name now exists → auto resolve
            if ref_key and ref_key in normalized_existing:
                auto_resolved = True
                logger.debug("Stage1 resolved missing-page: %s → %s", review_id, ref_key)

        elif review_type in ("broken-link", "broken-link-no-outlinks"):
            # If the link target page now exists → auto resolve
            if ref_key and ref_key in normalized_existing:
                auto_resolved = True
                logger.debug("Stage1 resolved broken-link: %s → %s", review_id, ref_key)

        elif review_type == "orphan":
            # If the page now has inbound links → auto resolve
            if ref_key and page_inbound_counts.get(ref_key, 0) > 0:
                auto_resolved = True
                logger.debug("Stage1 resolved orphan: %s → %s (inbound=%d)",
                             review_id, ref_key, page_inbound_counts.get(ref_key, 0))

        if auto_resolved:
            resolved_ids.append(review_id)
        else:
            remaining.append(review)

    return resolved_ids, remaining


# ── Stage 2: LLM semantic judgment ──


def _build_llm_prompt(
    reviews: list[dict],
    page_index: list[dict],
) -> list[dict]:
    """Build LLM messages for batch review resolution."""
    system = (
        "你是 Wiki Review 清扫 Agent。根据当前 Wiki 页面索引，判断以下待处理 Review 是否已经过时或不再相关。\n\n"
        "## 判断规则\n"
        "- 如果 Review 描述的问题已经不存在（例如缺失的页面已创建、死链已修复），标记为 resolved\n"
        "- 如果 Review 描述的问题仍然存在，保持 pending\n"
        "- 保守策略：不确定时保持 pending\n\n"
        "## 输出格式\n"
        "返回 JSON：\n"
        '```json\n'
        '{"resolved": [{"id": "review_xxx", "reason": "页面已存在"}], "pending": ["review_yyy"]}\n'
        '```\n\n'
        "只返回 JSON，不要其他内容。"
    )

    # Build page index summary
    index_lines = [f"- {p['page_key']}: {p.get('title', '')}" for p in page_index[:200]]
    index_text = "\n".join(index_lines) if index_lines else "（无页面）"

    # Build reviews list
    review_lines = []
    for r in reviews:
        review_lines.append(
            f"- [{r['id']}] ({r['type']}) {r['title']}: {(r.get('description') or '')[:200]}"
        )
    reviews_text = "\n".join(review_lines)

    user_content = (
        f"## Wiki 页面索引（共 {len(page_index)} 页）\n{index_text}\n\n"
        f"## 待处理 Review（共 {len(reviews)} 项）\n{reviews_text}"
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]


async def _stage2_llm_resolve(
    remaining_reviews: list[dict],
    page_index: list[dict],
    llm_client: Any,
    llm_settings: dict,
) -> list[str]:
    """Stage 2: LLM-based batch resolution.

    Returns list of review IDs that LLM determined can be resolved.
    """
    resolved_ids: list[str] = []
    batch_count = 0

    for i in range(0, len(remaining_reviews), LLM_BATCH_SIZE):
        if batch_count >= MAX_LLM_BATCHES:
            logger.info("Stage2: reached max batches (%d), stopping", MAX_LLM_BATCHES)
            break

        batch = remaining_reviews[i:i + LLM_BATCH_SIZE]
        messages = _build_llm_prompt(batch, page_index)

        try:
            output = await llm_client.chat(messages, llm_settings)
        except Exception as e:
            logger.warning("Stage2 LLM call failed for batch %d: %s, keeping pending", batch_count, e)
            break  # Conservative: stop processing on LLM failure

        # Parse JSON response
        try:
            # Extract JSON from possible code blocks
            json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", output, re.DOTALL)
            if json_match:
                output = json_match.group(1)
            result = json.loads(output.strip())
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Stage2: failed to parse LLM JSON for batch %d: %s", batch_count, e)
            break  # Conservative: keep batch pending

        # Extract resolved IDs
        batch_resolved = result.get("resolved", [])
        for item in batch_resolved:
            if isinstance(item, dict):
                rid = item.get("id", "")
                if rid:
                    resolved_ids.append(rid)
                    logger.debug("Stage2 resolved: %s (%s)", rid, item.get("reason", ""))
            elif isinstance(item, str):
                resolved_ids.append(item)

        batch_count += 1
        logger.info("Stage2 batch %d: %d/%d resolved",
                     batch_count, len(batch_resolved), len(batch))

    return resolved_ids


# ── Main entry point ──


async def sweep_reviews(
    db: Any,
    project_id: str,
    llm_client: Any = None,
    llm_settings: dict | None = None,
) -> dict:
    """Run two-stage review sweep for a project.

    Stage 1: Rule matching (always runs).
    Stage 2: LLM judgment (runs if llm_client provided and Stage 1 leaves unresolved reviews).

    Args:
        db: Database instance.
        project_id: Target project.
        llm_client: Optional LLMClient for Stage 2.
        llm_settings: Optional LLM settings.

    Returns:
        {"stage1_resolved": int, "stage2_resolved": int, "remaining": int}
    """
    # Get all pending reviews
    pending_reviews = db.list_wiki_reviews(project_id, resolved=False)
    if not pending_reviews:
        logger.info("Sweep reviews: no pending reviews for project %s", project_id)
        return {"stage1_resolved": 0, "stage2_resolved": 0, "remaining": 0}

    logger.info("Sweep reviews: %d pending reviews for project %s", len(pending_reviews), project_id)

    # Build existing page key set + inbound counts
    all_pages = db.list_wiki_pages(project_id)
    existing_page_keys = {p["page_key"] for p in all_pages}

    # Build inbound link counts from refs
    page_inbound_counts: dict[str, int] = {}
    for page in all_pages:
        refs_raw = page.get("refs") or "[]"
        if isinstance(refs_raw, str):
            try:
                refs_list = json.loads(refs_raw)
            except (json.JSONDecodeError, TypeError):
                refs_list = []
        else:
            refs_list = refs_raw
        for ref in refs_list:
            page_inbound_counts[ref] = page_inbound_counts.get(ref, 0) + 1

    # Stage 1: Rule matching
    stage1_resolved, remaining = _stage1_rule_match(
        pending_reviews, existing_page_keys, page_inbound_counts,
    )

    # Auto-resolve Stage 1 matches in DB
    for review_id in stage1_resolved:
        try:
            db.resolve_wiki_review(review_id, "auto_resolved")
        except Exception as e:
            logger.warning("Failed to auto-resolve review %s: %s", review_id, e)

    logger.info("Sweep Stage1: %d resolved, %d remaining", len(stage1_resolved), len(remaining))

    # Stage 2: LLM judgment (if client available and remaining reviews exist)
    stage2_resolved: list[str] = []
    if llm_client and llm_settings and remaining:
        stage2_resolved = await _stage2_llm_resolve(
            remaining, all_pages, llm_client, llm_settings or {},
        )
        for review_id in stage2_resolved:
            try:
                db.resolve_wiki_review(review_id, "auto_resolved")
            except Exception as e:
                logger.warning("Failed to auto-resolve review %s: %s", review_id, e)

        logger.info("Sweep Stage2: %d resolved", len(stage2_resolved))

    final_remaining = len(remaining) - len(stage2_resolved)
    return {
        "stage1_resolved": len(stage1_resolved),
        "stage2_resolved": len(stage2_resolved),
        "remaining": final_remaining,
    }
