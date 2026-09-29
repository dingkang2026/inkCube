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
  {description}
  ---END REVIEW---

  ---OVERVIEW---
  {updated overview text}
  ---END OVERVIEW---

Per §4.3 of 2.6wiki-design.md.
"""
import logging
import re

logger = logging.getLogger(__name__)

# Regex patterns for block parsing
_PAGE_PATTERN = re.compile(
    r"---PAGE:\s*(.+?)---\s*\n(.*?)---END PAGE---",
    re.DOTALL,
)

_REVIEW_PATTERN = re.compile(
    r"---REVIEW:\s*(.+?)\s*\|\s*(.+?)---\s*\n(.*?)---END REVIEW---",
    re.DOTALL,
)

# LINT block format (per §12.2.9): ---LINT: type | severity | title---
_LINT_PATTERN = re.compile(
    r"---LINT:\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)---\s*\n(.*?)---END LINT---",
    re.DOTALL,
)


def parse_page_blocks(llm_output: str) -> list[dict]:
    """Extract PAGE blocks from LLM output.

    Returns list of:
      - page_key: str
      - content: str (Markdown content)
    """
    results = []
    for match in _PAGE_PATTERN.finditer(llm_output):
        page_key = match.group(1).strip()
        content = match.group(2).strip()
        if page_key and content:
            results.append({
                "page_key": page_key,
                "content": content,
            })
        else:
            logger.warning("Empty PAGE block: key=%s, content_len=%d", page_key, len(content))
    return results


def parse_review_blocks(llm_output: str) -> list[dict]:
    """Extract REVIEW blocks from LLM output.

    Returns list of:
      - type: str (missing-page / contradiction / suggestion / confirm)
      - title: str
      - description: str
    """
    results = []
    valid_types = {"missing-page", "contradiction", "suggestion", "confirm", "data-gap", "stale-claim"}
    for match in _REVIEW_PATTERN.finditer(llm_output):
        review_type = match.group(1).strip().lower()
        title = match.group(2).strip()
        description = match.group(3).strip()

        if review_type not in valid_types:
            logger.warning("Unknown REVIEW type: %s, skipping", review_type)
            continue

        if title and description:
            results.append({
                "type": review_type,
                "title": title,
                "description": description,
            })
    return results


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


def parse_compile_output(llm_output: str) -> tuple[list[dict], list[dict]]:
    """Parse both PAGE and REVIEW blocks from LLM output (legacy v1).

    Returns (pages, reviews) tuple.
    """
    pages = parse_page_blocks(llm_output)
    reviews = parse_review_blocks(llm_output)
    logger.info(
        "Parsed compile output: %d pages, %d reviews (output_len=%d)",
        len(pages), len(reviews), len(llm_output),
    )
    if not pages:
        logger.warning(
            "Parser found 0 pages. Full LLM output:\n%s",
            llm_output,
        )
        stripped = llm_output.strip()
        if stripped.startswith("```"):
            logger.warning("LLM output appears to be wrapped in code blocks (```)")
        if "---PAGE" not in llm_output and "PAGE:" not in llm_output:
            logger.warning("No '---PAGE' or 'PAGE:' markers found in output at all")
        if "page_key" in llm_output.lower():
            logger.warning("Output contains 'page_key' text — LLM may have echoed the format spec")
    return pages, reviews


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

    # Extract metadata lines from the top of the block
    for i, line in enumerate(lines):
        if _META_LINE_PATTERN.match(line.strip()):
            meta_lines.append(line.strip())
            body_start = i + 1
        elif line.strip() == "" and i == body_start:
            # Skip blank line after metadata
            body_start = i + 1
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
