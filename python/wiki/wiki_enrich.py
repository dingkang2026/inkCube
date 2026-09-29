"""python/wiki/wiki_enrich.py — Wiki-link auto-completion.

Per §12.2.4 of 2.6wiki-design.md.

Safe wiki-link enrichment:
  1. Collect all page_keys + titles from existing wiki pages as candidate terms
  2. Send content to LLM, get JSON replacement list
  3. Code executes replacements (LLM never rewrites page content)
  4. Skip frontmatter area + already-linked terms
  5. Validate target exists

Core principle: LLM only provides replacement suggestions,
code performs the actual text replacement — avoids LLM corrupting formatting.
"""
import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Max content chars to send to LLM for enrichment analysis
_MAX_CONTENT_CHARS = 8000
# Max links to add per enrichment call
_MAX_LINKS_PER_CALL = 15


async def enrich_wikilinks(
    content: str,
    page_key: str,
    all_pages: list[dict],
    llm_client: Any,
    llm_settings: dict,
) -> tuple[str, int]:
    """Add wiki-links to content for unlinked terms.

    Args:
        content: Page content (may include YAML frontmatter).
        page_key: The page's own key (don't self-link).
        all_pages: All wiki pages (list of {page_key, title}).
        llm_client: LLM client instance.
        llm_settings: LLM settings dict.

    Returns:
        (enriched_content, link_count)
    """
    # 1. Build candidate terms from existing pages
    candidates: dict[str, str] = {}  # normalized_term → page_key
    for p in all_pages:
        pk = p.get("page_key", "")
        if not pk or pk == page_key:
            continue
        # Add page_key as candidate (split by separators)
        for part in re.split(r"[-_ ]", pk):
            if len(part) >= 2:
                candidates[part.lower()] = pk
        # Add title as candidate
        title = p.get("title", "")
        if title and len(title) >= 2:
            candidates[title.lower()] = pk

    if not candidates:
        return content, 0

    # 2. Extract body (skip frontmatter)
    body_start = 0
    fm_match = re.match(r"^---\n.*?\n---\n", content, re.DOTALL)
    if fm_match:
        body_start = fm_match.end()

    frontmatter = content[:body_start]
    body = content[body_start:]

    # 3. Ask LLM to identify unlinked terms
    body_sample = body[:_MAX_CONTENT_CHARS]
    candidate_list = json.dumps(
        [{"term": t, "target": pk} for t, pk in list(candidates.items())[:200]],
        ensure_ascii=False,
    )

    messages = [
        {
            "role": "system",
            "content": (
                "你是 Wiki 链接补全工具。分析文本，找出未用 [[page_key]] 链接的关键术语。\n"
                "只返回 JSON，不要其他文字。\n"
                '格式: {"links": [{"term": "原文中的术语", "target": "page_key"}]}\n'
                "规则:\n"
                "- 只标注首次出现的关键术语（实体、概念、技术名词）\n"
                "- 跳过已经用 [[...]] 链接的术语\n"
                "- 跳过通用词汇（如'项目'、'分析'、'数据'等）\n"
                "- 每个术语只链接第一次出现的位置\n"
                f"- 最多返回 {_MAX_LINKS_PER_CALL} 个链接"
            ),
        },
        {
            "role": "user",
            "content": f"候选术语列表:\n{candidate_list}\n\n文本内容:\n{body_sample}",
        },
    ]

    try:
        output = await llm_client.chat(messages, llm_settings)
    except Exception as e:
        logger.warning("Wiki-link enrichment LLM call failed: %s", e)
        return content, 0

    # 4. Parse LLM response
    links = _parse_enrich_links(output)
    if not links:
        return content, 0

    # 5. Apply replacements in code (NOT by LLM)
    enriched_body = _apply_wikilinks(body, links, page_key)
    link_count = enriched_body.count("[[") - body.count("[[")

    return frontmatter + enriched_body, max(link_count, 0)


def _parse_enrich_links(llm_output: str) -> list[dict]:
    """Parse LLM JSON output for link suggestions."""
    try:
        # Try to extract JSON from output
        text = llm_output.strip()
        # Find JSON block
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
            links = data.get("links", [])
            if isinstance(links, list):
                return [
                    {"term": str(l["term"]), "target": str(l["target"])}
                    for l in links
                    if l.get("term") and l.get("target")
                ][:_MAX_LINKS_PER_CALL]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning("Failed to parse enrichment links: %s", e)
    return []


def _apply_wikilinks(body: str, links: list[dict], self_page_key: str) -> str:
    """Apply wiki-link replacements in body text.

    Only replaces the FIRST occurrence of each term.
    Skips terms already inside [[...]] links.
    """
    result = body
    linked_targets: set[str] = set()

    for link in links:
        term = link["term"]
        target = link["target"]

        if target == self_page_key or target in linked_targets:
            continue

        # Find first occurrence of term NOT inside [[...]]
        pattern = re.compile(re.escape(term), re.IGNORECASE)
        new_result = []
        replaced = False
        last_end = 0

        for m in pattern.finditer(result):
            start, end = m.start(), m.end()
            # Check if inside existing [[...]]
            if _is_inside_wikilink(result, start):
                continue
            # Check word boundary (don't link partial words)
            before_ok = start == 0 or not result[start - 1].isalnum()
            after_ok = end >= len(result) or not result[end].isalnum()
            if not (before_ok and after_ok):
                continue

            # Replace this occurrence
            new_result.append(result[last_end:start])
            new_result.append(f"[[{target}|{term}]]")
            last_end = end
            replaced = True
            linked_targets.add(target)
            break  # Only first occurrence

        if replaced:
            new_result.append(result[last_end:])
            result = "".join(new_result)

    return result


def _is_inside_wikilink(text: str, pos: int) -> bool:
    """Check if position is inside an existing [[...]] link."""
    # Look backward for [[ without closing ]]
    before = text[:pos]
    last_open = before.rfind("[[")
    if last_open == -1:
        return False
    after_open = before[last_open:]
    return "]]" not in after_open
