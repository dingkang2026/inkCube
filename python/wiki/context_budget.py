"""python/wiki/context_budget.py — Context budget allocation for Wiki + RAG.

Per §12.2.12 of 2.6wiki-design.md.

Allocates character budget across LLM context window:
  - index summary: 5%
  - Wiki page content: 50%
  - History + system prompt: ~30%
  - Response reservation: 15%

Single-page truncation:
  - max(pageBudget, max(5000, pageBudget × 30%))
"""
import logging

from llm.budget import CHARS_PER_TOKEN, MIN_CONTEXT_WINDOW

logger = logging.getLogger(__name__)

# Fallback context window (chars) = MIN_CONTEXT_WINDOW × CHARS_PER_TOKEN
# (131072 tok × 1.5 = 196,608)。旧值 400_000 隐含私设 3.1 chars/tok（低估中文
# token 占用），曾致 wiki 400k 超窗事故——兜底值只能经 budget 常量推导（D2）
DEFAULT_CONTEXT_WINDOW = int(MIN_CONTEXT_WINDOW * CHARS_PER_TOKEN)

# Budget ratios
INDEX_RATIO = 0.05
WIKI_RATIO = 0.50
HISTORY_SYSTEM_RATIO = 0.30
RESPONSE_RATIO = 0.15

# Minimum single-page budget (chars)
MIN_SINGLE_PAGE_BUDGET = 5000
MAX_SINGLE_PAGE_RATIO = 0.30


def compute_budgets(
    context_window: int = DEFAULT_CONTEXT_WINDOW,
) -> dict[str, int]:
    """Compute character budgets for each context section.

    Returns:
        {"index": int, "wiki": int, "history_system": int, "response": int}
    """
    return {
        "index": int(context_window * INDEX_RATIO),
        "wiki": int(context_window * WIKI_RATIO),
        "history_system": int(context_window * HISTORY_SYSTEM_RATIO),
        "response": int(context_window * RESPONSE_RATIO),
    }


def truncate_wiki_context(
    wiki_pages: list[dict],
    wiki_budget: int,
) -> list[dict]:
    """Truncate wiki page content to fit within budget.

    Each page is truncated to:
      max(min_page_budget, wiki_budget * MAX_SINGLE_PAGE_RATIO)
    where min_page_budget = MIN_SINGLE_PAGE_BUDGET.

    Pages are included until the cumulative content exceeds wiki_budget.

    Args:
        wiki_pages: List of page dicts with 'content' field.
        wiki_budget: Total character budget for wiki content.

    Returns:
        List of page dicts with truncated 'content'.
    """
    if not wiki_pages or wiki_budget <= 0:
        return []

    n_pages = len(wiki_pages)
    per_page_budget = max(
        MIN_SINGLE_PAGE_BUDGET,
        min(wiki_budget // max(n_pages, 1), int(wiki_budget * MAX_SINGLE_PAGE_RATIO)),
    )

    result: list[dict] = []
    used = 0
    for page in wiki_pages:
        content = page.get("content", "")
        if len(content) > per_page_budget:
            content = content[:per_page_budget] + "\n……（内容截断）"
        used += len(content)
        if used > wiki_budget and result:
            logger.info("Wiki context budget exceeded (%d > %d), stopping at %d pages",
                        used, wiki_budget, len(result))
            break
        result.append({**page, "content": content})

    return result
