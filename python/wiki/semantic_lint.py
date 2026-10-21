"""python/wiki/semantic_lint.py — Semantic (LLM-driven) Wiki quality checks.

Per §12.2.9 of 2.6wiki-design.md.
Upgraded from ---REVIEW--- to ---LINT--- block format.

Checks:
  - contradiction: cross-page information conflicts
  - stale: claims superseded by updated data (was stale-claim)
  - missing-page: important concept referenced but no dedicated page
  - suggestion: worth adding to Wiki
  - explore-next: LLM proactively suggests exploration directions and supplementary materials

Features:
  - missing-page post-filter: remove false positives for already-existing pages
  - Page summaries (frontmatter + first 500 chars) to reduce token usage
  - Auto language detection for output matching
"""
import logging
import unicodedata
from typing import Any

from wiki.parser import parse_lint_blocks
from llm.routing import get_task_settings

logger = logging.getLogger(__name__)


def _normalize_page_name(name: str) -> str:
    """Normalize page name for matching: NFKC + lowercase + strip whitespace/hyphens."""
    name = unicodedata.normalize("NFKC", name)
    name = name.lower().strip()
    # Remove common path prefixes and .md suffix
    for prefix in ("./", "../", "/"):
        if name.startswith(prefix):
            name = name[len(prefix):]
    if name.endswith(".md"):
        name = name[:-3]
    # Normalize hyphens/spaces/underscores
    name = name.replace("-", " ").replace("_", " ")
    return name.strip()


def _build_existing_page_names(pages: list[dict]) -> set[str]:
    """Build set of normalized existing page names (page_key + title variants)."""
    names: set[str] = set()
    for p in pages:
        # page_key
        names.add(_normalize_page_name(p.get("page_key", "")))
        # title
        title = p.get("title", "")
        if title:
            names.add(_normalize_page_name(title))
            # Also add basename of title (last path component)
            basename = title.rsplit("/", 1)[-1]
            names.add(_normalize_page_name(basename))
    return names


def _filter_missing_page_findings(
    findings: list[dict],
    existing_names: set[str],
) -> list[dict]:
    """Filter out missing-page findings where the suggested page already exists.

    LLM often reports missing-page for concepts that already have pages.
    Uses exact match (NFKC normalized) to avoid false filtering.
    """
    filtered = []
    for f in findings:
        if f["type"] != "missing-page":
            filtered.append(f)
            continue

        # Check if the title matches any existing page name
        title_normalized = _normalize_page_name(f["title"])
        if title_normalized in existing_names:
            logger.debug("Filtered missing-page false positive: %s", f["title"])
            continue

        # Also check if any page in the finding's pages list matches
        found_existing = False
        for page_name in f.get("pages", []):
            if _normalize_page_name(page_name) in existing_names:
                found_existing = True
                break
        if found_existing:
            logger.debug("Filtered missing-page false positive (via pages): %s", f["title"])
            continue

        filtered.append(f)
    return filtered


def _build_semantic_lint_prompt(
    pages: list[dict],
    schemas: list[dict],
    language_hint: str = "",
) -> list[dict]:
    """Build messages for semantic lint LLM call.

    Uses page summaries (first 500 chars) to reduce token usage.
    """
    lang_instruction = ""
    if language_hint:
        lang_instruction = f"\n请使用{language_hint}输出所有 LINT 报告。\n"

    system = (
        "你是 Wiki 质量审查 Agent。阅读以下 Wiki 页面摘要，发现深层质量问题并主动建议探索方向。\n\n"
        "## 检查维度\n"
        "1. **contradiction** — 不同页面间信息矛盾（如数据不一致、说法冲突）\n"
        "2. **stale** — 页面中的声明/数据可能已过时或已被更新资料取代\n"
        "3. **missing-page** — 重要概念被频繁引用但无专属页面（注意：已存在页面的概念不要报告）\n"
        "4. **suggestion** — 值得加入 Wiki 的问题或来源\n"
        "5. **explore-next** — 主动建议下一步探索方向：\n"
        "   - 建议补充的资料（如“建议补充 XX 行业的最新报告，当前覆盖较薄”）\n"
        "   - 值得深入的问题（如“建议查找 XX 公司最新财报，当前数据已滞后”）\n"
        "   - 页面合并建议（如“‘A’和‘B’两个概念页内容高度重叠，建议合并”）\n\n"
        "## 输出格式\n"
        "每个发现项用 LINT block 包裹：\n"
        "---LINT: {type} | {severity} | {Short title}---\n"
        "{Description of the issue.}\n"
        "PAGES: {page1}, {page2}\n"
        "---END LINT---\n\n"
        "type: contradiction / stale / missing-page / suggestion / explore-next\n"
        "severity: high / medium / low\n"
        "PAGES 行列出涉及的页面 page_key（用逗号分隔）。\n"
        "如果没有发现问题，不输出任何 LINT block。"
        f"{lang_instruction}"
    )

    # Build page summaries (frontmatter + first 500 chars)
    pages_parts = []
    for p in pages:
        title = p.get("title", p["page_key"])
        page_type = p.get("page_type", "entity")
        content = p.get("content") or ""
        # Use first 500 chars as summary
        if len(content) > 500:
            content = content[:500] + "\n……"
        tags = p.get("tags", [])
        if isinstance(tags, str):
            tags_str = tags
        elif isinstance(tags, list):
            tags_str = ", ".join(str(t) for t in tags[:5])
        else:
            tags_str = ""

        summary = f"### [{page_type}] {p['page_key']}: {title}"
        if tags_str:
            summary += f" (tags: {tags_str})"
        summary += f"\n{content}"
        pages_parts.append(summary)

    pages_text = "\n\n".join(pages_parts) if pages_parts else "（无已有页面）"

    # Schema context
    schema_lines = []
    for s in schemas:
        hint = s.get("compile_hint", "")
        schema_lines.append(f"- {s['page_key']}: {s.get('title', '')} — {hint}")
    schema_text = "\n".join(schema_lines) if schema_lines else "（无 Schema 定义）"

    user_content = (
        f"## Wiki 页面摘要（共 {len(pages)} 页）\n{pages_text}\n\n"
        f"## Schema 定义\n{schema_text}"
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]


async def run_semantic_lint(
    db: Any,
    project_id: str,
    llm_client: Any,
    llm_settings: dict,
    language_hint: str = "",
) -> list[dict]:
    """Run semantic lint checks on all Wiki pages for a project.

    Uses LLM to detect contradictions, stale claims, missing pages, and suggestions.
    Post-filters missing-page findings against existing page names.

    Args:
        db: Database instance.
        project_id: Target project.
        llm_client: LLMClient instance.
        llm_settings: LLM settings dict.
        language_hint: Optional language hint for output (e.g. "中文", "English").

    Returns:
        List of findings, each with type/severity/title/description/pages.
    """
    pages = db.list_wiki_pages(project_id)
    compiled_pages = [
        p for p in pages
        if p.get("status") in ("compiled", "user_edited") and (p.get("content") or "").strip()
    ]

    if not compiled_pages:
        logger.info("Semantic lint: no compiled pages for project %s, skipping", project_id)
        return []

    project = db.get_project(project_id)
    project_type = project.get("type", "general") if project else "general"
    schemas = db.get_wiki_schemas(project_type)
    if not schemas:
        schemas = db.get_wiki_schemas("general")

    messages = _build_semantic_lint_prompt(compiled_pages, schemas, language_hint)

    logger.info("Semantic lint: calling LLM for project %s (%d pages)", project_id, len(compiled_pages))

    # Apply task routing: use lint-specific model if configured
    effective_settings = get_task_settings(llm_settings, "lint")
    effective_llm = llm_client
    if effective_settings.get("model") != llm_settings.get("model"):
        from llm import LLMClient
        effective_llm = LLMClient(effective_settings)

    try:
        output = (await effective_llm.chat_full(messages))["content"]
    except Exception as e:
        logger.error("Semantic lint LLM call failed for project %s: %s", project_id, e)
        raise  # Let API layer return 500 so frontend can show error to user

    findings = parse_lint_blocks(output)

    # Post-filter: remove missing-page false positives
    existing_names = _build_existing_page_names(compiled_pages)
    findings = _filter_missing_page_findings(findings, existing_names)

    logger.info(
        "Semantic lint for project %s: %d findings (after filter, %d raw blocks parsed)",
        project_id, len(findings), len(parse_lint_blocks(output)),
    )
    return findings
