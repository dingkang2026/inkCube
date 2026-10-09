"""python/wiki/parser.py — PAGE/REVIEW block parser for Wiki ingest output.

Parses LLM output containing structured blocks:

V2 format (with YAML frontmatter):
  ---PAGE: {page_key}---
  type: {entity|concept|topic|structure|source}
  title: {页面标题}
  refs: [{page_key list}]
  tags: [{tag list}]

  {Markdown body with [[wiki-links]]}
  ---END PAGE---

  ---REVIEW: {type} | {title}---
  EVIDENCE: [[{page_key}]] 「{quote}」   (contradiction 契约，两侧各一行)
  {description}
  ---END REVIEW---

  ---RESOLVES: {缺口编号}---            (缺口闭环，§4.3，可逗号并列)

  ---OVERVIEW---
  {updated overview text}
  ---END OVERVIEW---

Per §4.3 of 2.6wiki-design.md.
"""
import logging
import re

logger = logging.getLogger(__name__)

# Regex patterns for block parsing
_REVIEW_PATTERN = re.compile(
    r"---REVIEW:\s*(.+?)\s*\|\s*(.+?)---\s*\n(.*?)---END REVIEW---",
    re.DOTALL,
)

# EVIDENCE line inside REVIEW body (§4.3, E2-2026-10): contradiction evidence,
# one line per side: EVIDENCE: [[page_key]] 「quote」 (中英引号容错)
_EVIDENCE_PATTERN = re.compile(
    r"^EVIDENCE:\s*\[\[(.+?)\]\]\s*[「『\"']?(.+?)[」』\"']?\s*$",
    re.MULTILINE,
)

# RESOLVES block (§4.3 gap closure, E2-2026-10): ---RESOLVES: 1--- or
# ---RESOLVES: 1, 3--- (LLM 格式漂移容忍：逗号并列与多余空白)
_RESOLVES_PATTERN = re.compile(r"---RESOLVES:\s*(.+?)---")

# LINT block format (per §12.2.9): ---LINT: type | severity | title---
_LINT_PATTERN = re.compile(
    r"---LINT:\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)---\s*\n(.*?)---END LINT---",
    re.DOTALL,
)


def _extract_evidence(body: str) -> tuple[str, list[dict]]:
    """Extract EVIDENCE lines from a REVIEW body (§4.3).

    Returns (clean_description, evidence) — evidence lines are stripped from
    the description and returned as [{page_key, quote}].
    """
    evidence = []
    for m in _EVIDENCE_PATTERN.finditer(body):
        page_key = m.group(1).strip()
        quote = m.group(2).strip()
        if page_key and quote:
            evidence.append({"page_key": page_key, "quote": quote})
    if not evidence:
        return body, []
    clean = _EVIDENCE_PATTERN.sub("", body).strip()
    return clean, evidence


def parse_review_blocks(llm_output: str) -> list[dict]:
    """Extract REVIEW blocks from LLM output.

    Returns list of:
      - type: str (missing-page / contradiction / suggestion / confirm)
      - title: str
      - description: str
      - evidence: list[{page_key, quote}] (empty unless EVIDENCE lines present;
        contradiction 契约要求两侧各一行，§4.3/§4.4)
    """
    results = []
    valid_types = {"missing-page", "contradiction", "suggestion", "confirm", "data-gap", "stale-claim"}
    for match in _REVIEW_PATTERN.finditer(llm_output):
        review_type = match.group(1).strip().lower()
        title = match.group(2).strip()
        body = match.group(3).strip()

        if review_type not in valid_types:
            logger.warning("Unknown REVIEW type: %s, skipping", review_type)
            continue

        description, evidence = _extract_evidence(body)
        # 纯 evidence 无描述（LLM 漂移跳过描述行）也保留——§4.4 宁可留待人工不误丢
        if title and (description or evidence):
            results.append({
                "type": review_type,
                "title": title,
                "description": description,
                "evidence": evidence,
            })
    return results


def parse_resolves_blocks(llm_output: str) -> list[str]:
    """Extract gap numbers from RESOLVES blocks (§4.3 gap closure, E2-2026-10).

    Accepts ---RESOLVES: 1--- / ---RESOLVES: 1, 3--- (comma-joined drift).
    Returns list of number strings in output order.
    """
    numbers: list[str] = []
    for m in _RESOLVES_PATTERN.finditer(llm_output):
        for part in m.group(1).split(","):
            n = part.strip()
            if n and n not in numbers:
                numbers.append(n)
    return numbers


def parse_lint_blocks(llm_output: str) -> list[dict]:
    """Extract LINT blocks from LLM output (§12.2.9 format).

    Format: ---LINT: type | severity | title---\n{description}\nPAGES: ...\n---END LINT---

    Returns list of:
      - type: str (contradiction / stale / missing-page / suggestion / explore-next)
      - severity: str (high / medium / low)
      - title: str
      - description: str
      - pages: list[str] (from PAGES: line, if present)
    """
    results = []
    valid_types = {"contradiction", "stale", "stale-claim", "missing-page", "suggestion", "data-gap", "explore-next"}
    for match in _LINT_PATTERN.finditer(llm_output):
        lint_type = match.group(1).strip().lower()
        severity = match.group(2).strip().lower()
        title = match.group(3).strip()
        body = match.group(4).strip()

        # Normalize type aliases
        if lint_type == "stale-claim":
            lint_type = "stale"
        elif lint_type == "data-gap":
            lint_type = "missing-page"

        if lint_type not in valid_types:
            logger.warning("Unknown LINT type: %s, skipping", lint_type)
            continue

        if severity not in ("high", "medium", "low"):
            severity = "medium"

        # Extract PAGES: line if present
        pages = []
        pages_match = re.search(r"^PAGES:\s*(.+)$", body, re.MULTILINE)
        if pages_match:
            pages = [p.strip() for p in pages_match.group(1).split(",") if p.strip()]
            # Remove PAGES line from description
            body = re.sub(r"^PAGES:\s*.+$", "", body, flags=re.MULTILINE).strip()

        if title:
            results.append({
                "type": lint_type,
                "severity": severity,
                "title": title,
                "description": body,
                "pages": pages,
            })
    return results


# ── V2 parser: supports YAML frontmatter + OVERVIEW block ──

# V2 PAGE block: captures metadata lines + body
_PAGE_V2_PATTERN = re.compile(
    r"---PAGE:\s*(.+?)---\s*\n(.*?)---END PAGE---",
    re.DOTALL,
)

# Metadata lines at top of PAGE block: key: value
_META_LINE_PATTERN = re.compile(r"^(type|title|refs|tags)\s*:\s*(.+)$", re.MULTILINE)

# OVERVIEW block
_OVERVIEW_PATTERN = re.compile(
    r"---OVERVIEW---\s*\n(.*?)---END OVERVIEW---",
    re.DOTALL,
)


def _parse_yaml_list(value: str) -> list[str]:
    """Parse a YAML-like list value: '[a, b, c]' or '- a\\n- b'."""
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1]
        items = [item.strip().strip('"').strip("'") for item in inner.split(",")]
        return [i for i in items if i]
    # Handle '- item' format
    items = []
    for line in value.split("\n"):
        line = line.strip()
        if line.startswith("- "):
            items.append(line[2:].strip().strip('"').strip("'"))
    return items


def _parse_page_v2(page_key: str, raw_block: str) -> dict:
    """Parse a V2 PAGE block with optional YAML frontmatter.

    Returns dict with: page_key, title, page_type, refs, tags, content.
    """
    lines = raw_block.split("\n")
    meta_lines = []
    body_start = 0

    # Extract metadata lines from the top of the block.
    # 块首/元数据行之间的空行是 LLM 输出格式漂移的常态：空行不能终止 metadata 区，
    # 否则整块 type/title/refs 静默丢失（图谱断边、backlinks 缺失、broken-link 漏检）。
    # 规则：遇空行向后看，跳过空行后若仍是 meta 行则继续收集；否则 metadata 区
    # 止于最后一个 meta 行，首个非空非 meta 行即 body 起点。
    n = len(lines)
    i = 0
    while i < n:
        stripped = lines[i].strip()
        if _META_LINE_PATTERN.match(stripped):
            meta_lines.append(stripped)
            body_start = i + 1
            i += 1
        elif stripped == "":
            j = i + 1
            while j < n and lines[j].strip() == "":
                j += 1
            if j < n and _META_LINE_PATTERN.match(lines[j].strip()):
                i = j
            else:
                break
        else:
            break

    # Parse metadata
    page_type = "entity"  # default
    title = page_key
    refs = []
    tags = []

    for ml in meta_lines:
        m = _META_LINE_PATTERN.match(ml)
        if m:
            key = m.group(1)
            val = m.group(2).strip()
            if key == "type":
                page_type = val.lower()
            elif key == "title":
                title = val.strip('"').strip("'")
            elif key == "refs":
                refs = _parse_yaml_list(val)
            elif key == "tags":
                tags = _parse_yaml_list(val)

    # Body is everything after metadata
    body = "\n".join(lines[body_start:]).strip()

    return {
        "page_key": page_key,
        "title": title,
        "page_type": page_type,
        "refs": refs,
        "tags": tags,
        "content": body,
    }


def parse_compile_output_v2(llm_output: str) -> tuple[list[dict], list[dict], str]:
    """Parse V2 PAGE + REVIEW + OVERVIEW blocks from LLM output.

    Returns (pages, reviews, overview_update) tuple.
    - pages: list of page dicts with page_key, title, page_type, refs, tags, content
    - reviews: list of review dicts with type, title, description
    - overview_update: updated overview text (empty string if not provided)
    """
    pages = []
    for match in _PAGE_V2_PATTERN.finditer(llm_output):
        page_key = match.group(1).strip()
        raw_block = match.group(2)
        if page_key and raw_block.strip():
            page = _parse_page_v2(page_key, raw_block)
            if page["content"]:  # Only include pages with actual content
                pages.append(page)
            else:
                logger.warning("Empty PAGE body for key=%s, skipping", page_key)

    reviews = parse_review_blocks(llm_output)

    # Parse OVERVIEW block
    overview_update = ""
    overview_match = _OVERVIEW_PATTERN.search(llm_output)
    if overview_match:
        overview_update = overview_match.group(1).strip()

    logger.info(
        "Parsed V2 compile output: %d pages, %d reviews, overview_len=%d (output_len=%d)",
        len(pages), len(reviews), len(overview_update), len(llm_output),
    )

    if not pages:
        logger.warning(
            "V2 parser found 0 pages. Full LLM output:\n%s",
            llm_output,
        )

    return pages, reviews, overview_update
