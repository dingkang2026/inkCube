"""python/wiki/dedup.py — Duplicate page detection and merging.

Per §12.2.10 of 2.6wiki-design.md.

Three-stage flow:
  Stage 1: Extract summaries (no LLM)
  Stage 2: LLM duplicate detection (identify same-topic pages with different names)
  Stage 3: Merge (user confirms canonical page, LLM merges content)

Conservative strategy:
  - Only entity/concept pages are candidates for dedup
  - Different page_types are NOT merged
  - User must confirm before merge executes
"""
import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Only dedup these page types (entity and concept are most prone to duplicates)
_DEDUP_CANDIDATE_TYPES = {"entity", "concept"}
# Max description chars to send to LLM per page
_MAX_DESC_CHARS = 200
# Max pages per dedup batch
_MAX_DEDUP_PAGES = 100
# Max chars per page sent into the LLM content-merge prompt
_MAX_MERGE_CHARS = 4000


def extract_entity_summaries(db: Any, project_id: str) -> list[dict]:
    """Stage 1: Extract summaries from entity/concept pages.

    Returns list of {page_key, title, page_type, description, tags}.
    """
    pages = db.list_wiki_pages(project_id)
    summaries = []
    for p in pages:
        ptype = p.get("page_type", "structure")
        if ptype not in _DEDUP_CANDIDATE_TYPES:
            continue
        content = p.get("content", "")
        # Extract description: skip frontmatter, take first paragraph
        desc = _extract_description(content)
        summaries.append({
            "page_key": p["page_key"],
            "title": p.get("title", p["page_key"]),
            "page_type": ptype,
            "description": desc,
            "tags": _safe_json_loads(p.get("tags", "[]")),
        })
    logger.info("Dedup: extracted %d entity/concept summaries from project %s",
                len(summaries), project_id)
    return summaries[:_MAX_DEDUP_PAGES]


async def detect_duplicates(
    summaries: list[dict],
    llm_client: Any,
    llm_settings: dict,
) -> list[dict]:
    """Stage 2: LLM identifies duplicate page groups.

    Returns list of {slugs: [page_key, ...], reason: str, confidence: str}.
    """
    if len(summaries) < 2:
        return []

    # Build prompt with page summaries
    page_list = []
    for s in summaries:
        page_list.append(
            f"- [{s['page_type']}] {s['page_key']}: {s['title']}\n"
            f"  摘要: {s['description']}\n"
            f"  标签: {', '.join(s['tags'][:5]) if s['tags'] else '无'}"
        )
    pages_text = "\n".join(page_list)

    messages = [
        {
            "role": "system",
            "content": (
                "你是 Wiki 重复页面检测工具。识别描述同一主题但命名不同的页面。\n"
                "只返回 JSON，不要其他文字。\n"
                '格式: {"groups": [{"slugs": ["page_key_1", "page_key_2"], '
                '"reason": "原因", "confidence": "high|medium|low"}]}\n\n'
                "识别规则:\n"
                "- 同义不同名（如 'PAOs' vs '聚磷菌'、'VFA' vs '挥发性脂肪酸'）\n"
                "- 单复数变体（如 'DPAO' vs 'DPAOs'）\n"
                "- 缩写与全称（如 'ROI' vs '投资回报率'）\n"
                "- 拼写变体或翻译差异\n"
                "- 只匹配相同 page_type 的页面（entity 和 entity，concept 和 concept）\n"
                "- 不要匹配内容完全不同的页面\n"
                "- confidence='high' 仅用于明确的同义词；'medium' 用于可能重复；'low' 用于不确定"
            ),
        },
        {
            "role": "user",
            "content": f"以下是 Wiki 中的实体/概念页面:\n\n{pages_text}",
        },
    ]

    try:
        output = await llm_client.chat(messages, llm_settings)
    except Exception as e:
        logger.error("Dedup LLM detection failed: %s", e)
        return []

    return _parse_dedup_groups(output)


async def merge_pages(
    db: Any,
    project_id: str,
    canonical_key: str,
    duplicate_keys: list[str],
    llm_client: Any,
    llm_settings: dict,
) -> dict:
    """Stage 3: Merge duplicate pages into canonical page.

    - LLM merges content (deduplicated)
    - Code merges tags/refs/sources (union)
    - Rewrites all wiki-links pointing to duplicates → canonical
    - Deletes duplicate pages
    - Records change log

    Returns:
        {"merged": int, "canonical": str, "rewritten_links": int,
         "failed": [{"page_key", "reason"}], "truncated": [page_key]}

    合并失败（LLM 异常/超时）的 dup 页保留不删，通过 failed 上报；
    超出 _MAX_MERGE_CHARS 截断的页面通过 truncated 上报。
    """
    canonical = db.get_wiki_page(project_id, canonical_key)
    if not canonical:
        return {"merged": 0, "canonical": canonical_key, "rewritten_links": 0,
                "failed": [], "truncated": []}

    canonical_content = canonical.get("content", "")
    merged_tags = set(_safe_json_loads(canonical.get("tags", "[]")))
    merged_refs = set(_safe_json_loads(canonical.get("refs", "[]")))
    rewritten = 0
    merged_keys: list[str] = []
    failed_merges: list[dict] = []
    truncated: list[str] = []

    for dup_key in duplicate_keys:
        dup_page = db.get_wiki_page(project_id, dup_key)
        if not dup_page:
            continue

        dup_content = dup_page.get("content", "")

        # LLM merge content。合并失败（LLM 异常/超时）时保留 dup 页：删除不可逆，
        # dup 的独特内容不能因一次瞬时失败而静默丢失。
        merged_content = await _llm_merge_content(
            canonical_content, dup_content, canonical_key, llm_client, llm_settings,
        )
        if not merged_content:
            logger.error(
                "Dedup: content merge failed for '%s' → '%s', keeping duplicate page",
                dup_key, canonical_key,
            )
            failed_merges.append({"page_key": dup_key, "reason": "llm_merge_failed"})
            continue

        # 超出上下文的部分不会参与合并——如实上报供 UI 提示
        if len(canonical_content) > _MAX_MERGE_CHARS or len(dup_content) > _MAX_MERGE_CHARS:
            truncated.append(dup_key)

        canonical_content = merged_content
        merged_keys.append(dup_key)

        # Union tags and refs
        merged_tags.update(_safe_json_loads(dup_page.get("tags", "[]")))
        merged_refs.update(_safe_json_loads(dup_page.get("refs", "[]")))

        # Rewrite wiki-links in all pages pointing to this duplicate
        rewritten += _rewrite_wikilinks(db, project_id, dup_key, canonical_key)

        # Delete duplicate page
        db.delete_wiki_page(project_id, dup_key)
        logger.info("Dedup: merged '%s' into '%s'", dup_key, canonical_key)

    # Update canonical page with merged content
    db.update_wiki_page_content(
        page_id=canonical["id"],
        content=canonical_content,
        status=canonical.get("status", "compiled"),
    )

    # Record change log (只记录实际发生的合并)
    if merged_keys:
        db.add_wiki_change_log(
            project_id=project_id,
            action="dedup_merge",
            summary=f"合并 {len(merged_keys)} 个重复页面到 {canonical_key}: {', '.join(merged_keys)}",
        )

    return {
        "merged": len(merged_keys),
        "canonical": canonical_key,
        "rewritten_links": rewritten,
        "failed": failed_merges,
        "truncated": truncated,
    }


# ── Internal helpers ──


def _extract_description(content: str) -> str:
    """Extract first paragraph from page content (skip frontmatter)."""
    body = content
    fm_match = re.match(r"^---\n.*?\n---\n", content, re.DOTALL)
    if fm_match:
        body = content[fm_match.end():]
    # Take first non-empty paragraph
    paragraphs = re.split(r"\n\n+", body.strip())
    for p in paragraphs:
        p = p.strip()
        if p and not p.startswith("#"):
            return p[:_MAX_DESC_CHARS]
    return body[:_MAX_DESC_CHARS]


def _safe_json_loads(raw: str) -> list:
    """Safely parse JSON array, return empty list on failure."""
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        result = json.loads(raw)
        return result if isinstance(result, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _parse_dedup_groups(llm_output: str) -> list[dict]:
    """Parse LLM JSON output for duplicate groups."""
    try:
        text = llm_output.strip()
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
            groups = data.get("groups", [])
            if isinstance(groups, list):
                return [
                    {
                        "slugs": g["slugs"],
                        "reason": g.get("reason", ""),
                        "confidence": g.get("confidence", "medium"),
                    }
                    for g in groups
                    if isinstance(g.get("slugs"), list) and len(g["slugs"]) >= 2
                ]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning("Failed to parse dedup groups: %s", e)
    return []


async def _llm_merge_content(
    canonical: str, duplicate: str, canonical_key: str,
    llm_client: Any, llm_settings: dict,
) -> str:
    """Use LLM to merge two page contents, removing duplication."""
    messages = [
        {
            "role": "system",
            "content": (
                "你是 Wiki 页面合并工具。将两个页面的内容合并为一个，去除重复信息。\n"
                "保留所有独特信息，不要丢失任何有价值的内容。\n"
                "如果信息矛盾，保留两个说法并标注来源差异。\n"
                "输出合并后的完整 Markdown 内容（不含 frontmatter）。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"## 主页面 ({canonical_key})\n{canonical[:_MAX_MERGE_CHARS]}\n\n"
                f"## 待合并页面\n{duplicate[:_MAX_MERGE_CHARS]}"
            ),
        },
    ]

    try:
        return await llm_client.chat(messages, llm_settings)
    except Exception as e:
        logger.error("Dedup content merge LLM failed: %s", e)
        return ""


def _rewrite_wikilinks(db: Any, project_id: str, old_key: str, new_key: str) -> int:
    """Rewrite all [[old_key]] references to [[new_key]] across all pages.

    Returns count of pages modified.
    """
    pages = db.list_wiki_pages(project_id)
    rewritten = 0
    pattern = re.compile(r"\[\[" + re.escape(old_key) + r"(?:\|([^\]]+))?\]\]")

    for p in pages:
        if p["page_key"] == old_key:
            continue
        content = p.get("content", "")
        if f"[[{old_key}]]" not in content and f"[[{old_key}|" not in content:
            continue

        new_content = pattern.sub(lambda m: f"[[{new_key}|{m.group(1)}]]" if m.group(1) else f"[[{new_key}]]", content)
        if new_content != content:
            db.update_wiki_page_content(
                page_id=p["id"],
                content=new_content,
                status=p.get("status", "compiled"),
            )
            rewritten += 1

    return rewritten
