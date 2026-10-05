"""python/wiki/wiki_search.py — Wiki page search engine.

Three-path hybrid search + graph neighbor expansion.
Per §12.2.7 of 2.6wiki-design.md.

Currently implemented:
  ① Token-based keyword search (title/page_key/content scoring)
  ④ Graph neighbor expansion (wiki-link adjacency, one-hop)

Future:
  ② Dense vector search (ChromaDB wiki collection — requires embedding generation)
  ③ RRF fusion (when both paths are available)
"""
import json
import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

# ── Scoring constants (§12.2.7) ──

RRF_K = 60.0
FILENAME_EXACT_BONUS = 200.0
PHRASE_IN_TITLE_BONUS = 50.0
PHRASE_IN_CONTENT_PER_OCC = 20.0
MAX_PHRASE_OCC_COUNTED = 10
TITLE_TOKEN_WEIGHT = 5.0
CONTENT_TOKEN_WEIGHT = 1.0
MIN_GRAPH_RESULT_RATIO = 0.15
MAX_GRAPH_RESULT_RATIO = 0.30
MAX_GRAPH_SEEDS = 20

# ── Stop words ──

_STOP_WORDS = frozenset(
    "的 是 了 在 有 和 与 或 及 等 中 对 为 被 将 从 到 以 而 但 "
    "the is a an what how why where when who which this that "
    "it its are was were be been being do does did has have had "
    "can could should would may might will shall not no nor "
    "by on at in to for of as".split()
)


# ── Text processing ──


def _is_cjk(ch: str) -> bool:
    """Check if a character is CJK."""
    try:
        name = unicodedata.name(ch)
        return any(kw in name for kw in ("CJK", "CHINESE", "HIRAGANA", "KATAKANA", "HANGUL"))
    except ValueError:
        return False


def tokenize_query(query: str) -> list[str]:
    """Tokenize a query string into search tokens.

    - Split on whitespace/punctuation
    - CJK: single char + bigram
    - Non-CJK: whole word lowercased
    - Remove stop words (single CJK chars kept)
    """
    tokens: list[str] = []
    words = re.split(r"[\s,;:!?。，；：！？、\(\)\[\]{}()（）【】]+", query.lower())
    for word in words:
        word = word.strip()
        if not word:
            continue
        has_cjk = any(_is_cjk(ch) for ch in word)
        if has_cjk:
            cjk_chars = [ch for ch in word if _is_cjk(ch)]
            # Single CJK chars (kept for CJK)
            for ch in cjk_chars:
                tokens.append(ch)
            # CJK bigrams
            for i in range(len(cjk_chars) - 1):
                tokens.append(cjk_chars[i] + cjk_chars[i + 1])
            # Non-CJK parts
            non_cjk = re.sub(r"[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]", " ", word)
            for part in non_cjk.split():
                if len(part) > 1 and part not in _STOP_WORDS:
                    tokens.append(part)
        else:
            if len(word) > 1 and word not in _STOP_WORDS:
                tokens.append(word)
    return tokens


# ── Graph neighbor expansion ──


def _build_adjacency(pages: list[dict]) -> dict[str, set[str]]:
    """Build undirected adjacency map from wiki pages' refs and backlinks."""
    adj: dict[str, set[str]] = {}
    page_keys = {p["page_key"] for p in pages}

    for page in pages:
        pk = page["page_key"]
        if pk not in adj:
            adj[pk] = set()
        refs_raw = page.get("refs") or "[]"
        if isinstance(refs_raw, str):
            try:
                refs_list = json.loads(refs_raw)
            except (json.JSONDecodeError, TypeError):
                refs_list = []
        else:
            refs_list = refs_raw
        for ref in refs_list:
            if ref in page_keys and ref != pk:
                adj[pk].add(ref)
                if ref not in adj:
                    adj[ref] = set()
                adj[ref].add(pk)
    return adj


def _graph_neighbor_expansion(
    base_results: list[dict],
    all_pages: list[dict],
    top_k: int,
) -> list[dict]:
    """Expand search results with graph neighbors (§12.2.7 step ④).

    Takes top-K base results as seeds, finds their one-hop neighbors,
    and adds them with weighted scores.
    """
    if not base_results or not all_pages:
        return base_results

    # Build adjacency
    adj = _build_adjacency(all_pages)
    page_map = {p["page_key"]: p for p in all_pages}
    base_keys = {r["page_key"] for r in base_results}

    # Seeds: top results (up to MAX_GRAPH_SEEDS)
    seeds = base_results[:min(MAX_GRAPH_SEEDS, len(base_results))]

    # One-hop expansion with weighted scoring
    neighbor_scores: dict[str, float] = {}
    neighbor_seeds: dict[str, list[str]] = {}
    for seed_rank, seed in enumerate(seeds):
        seed_key = seed["page_key"]
        neighbors = adj.get(seed_key, set())
        for nb in neighbors:
            if nb in base_keys:
                continue  # Already in base results
            score = 1.0 / (seed_rank + 1)
            neighbor_scores[nb] = neighbor_scores.get(nb, 0.0) + score
            if nb not in neighbor_seeds:
                neighbor_seeds[nb] = []
            neighbor_seeds[nb].append(seed_key)

    if not neighbor_scores:
        return base_results

    # Sort neighbors by score descending
    sorted_neighbors = sorted(neighbor_scores.items(), key=lambda x: x[1], reverse=True)

    # Quota: reserve 15-30% of top_k for graph neighbors
    # Higher vector coverage → less quota needed
    base_coverage = len(base_results) / top_k if top_k > 0 else 1.0
    if base_coverage > 0.7:
        ratio = MIN_GRAPH_RESULT_RATIO
    else:
        ratio = MAX_GRAPH_RESULT_RATIO
    graph_quota = max(1, int(top_k * ratio))

    # Build graph result entries
    graph_results: list[dict] = []
    for nb_key, score in sorted_neighbors[:graph_quota]:
        page = page_map.get(nb_key)
        if not page:
            continue
        entry = {
            **page,
            "_score": score * 0.5,  # Scale down graph scores
            "_source": "graph",
            "graph_related_to": neighbor_seeds.get(nb_key, []),
        }
        graph_results.append(entry)

    return graph_results


# ── Keyword search ──


def search_wiki_keyword(
    query: str,
    project_id: str,
    db,
    top_k: int = 20,
    pages: list[dict] | None = None,
) -> list[dict]:
    """Token-based keyword search over Wiki pages.

    Scoring per page:
      - page_key exact match → +FILENAME_EXACT_BONUS
      - title contains full phrase → +PHRASE_IN_TITLE_BONUS
      - content contains phrase per occurrence → +PHRASE_IN_CONTENT_PER_OCC (max 10)
      - title token matches × TITLE_TOKEN_WEIGHT
      - content token matches × CONTENT_TOKEN_WEIGHT

    Args:
        pages: Pre-fetched page list. If None, fetched from DB.

    Returns pages sorted by score descending, up to top_k.
    """
    if pages is None:
        pages = db.list_wiki_pages(project_id)
    if not pages:
        return []

    tokens = tokenize_query(query)
    query_lower = query.lower().strip()
    if not tokens and not query_lower:
        return []

    scored: list[tuple[float, dict]] = []
    for page in pages:
        title_lower = (page.get("title") or "").lower()
        key_lower = (page.get("page_key") or "").lower()
        content_lower = (page.get("content") or "").lower()

        score = 0.0

        # Filename (page_key) exact match
        if query_lower and query_lower.replace(" ", "-") == key_lower:
            score += FILENAME_EXACT_BONUS

        # Phrase in title
        if query_lower and query_lower in title_lower:
            score += PHRASE_IN_TITLE_BONUS

        # Phrase in content (capped)
        if query_lower and len(query_lower) > 2:
            occ = content_lower.count(query_lower)
            score += min(occ, MAX_PHRASE_OCC_COUNTED) * PHRASE_IN_CONTENT_PER_OCC

        # Token matching
        for token in tokens:
            if token in title_lower:
                score += TITLE_TOKEN_WEIGHT
            # Content token count (capped per token)
            token_occ = content_lower.count(token)
            score += min(token_occ, MAX_PHRASE_OCC_COUNTED) * CONTENT_TOKEN_WEIGHT

        if score > 0:
            scored.append((score, page))

    # Sort by score descending
    scored.sort(key=lambda x: x[0], reverse=True)

    results: list[dict] = []
    for score, page in scored[:top_k]:
        entry = {**page, "_score": score, "_source": "keyword"}
        results.append(entry)

    return results


# ── Main search function ──


def search_wiki(
    query: str,
    project_id: str,
    db,
    top_k: int = 20,
    enable_graph: bool = True,
    pages: list[dict] | None = None,
) -> list[dict]:
    """Search Wiki pages using hybrid retrieval.

    Currently: keyword search + graph neighbor expansion.
    Returns list of page dicts with _score, _source, and optional graph_related_to.

    Args:
        pages: Pre-fetched page list to search over. If None, fetched from DB via
            list_wiki_pages(project_id). Callers retrieving multiple queries in a
            batch (e.g. report retrieval) can fetch once and reuse to avoid
            repeated full-page DB loads.
    """
    if not query or not query.strip():
        return []

    try:
        # Fetch pages once, share between keyword search and graph expansion
        all_pages = pages if pages is not None else db.list_wiki_pages(project_id)
        if not all_pages:
            return []

        # ① Keyword search
        keyword_results = search_wiki_keyword(
            query, project_id, db, top_k=top_k, pages=all_pages,
        )
        logger.info("Wiki keyword search: %d results for project %s",
                     len(keyword_results), project_id)

        if not keyword_results:
            return []

        # ④ Graph neighbor expansion
        if enable_graph:
            graph_results = _graph_neighbor_expansion(
                keyword_results, all_pages, top_k,
            )
        else:
            graph_results = []

        # Merge: keyword results first, then graph neighbors
        seen_keys = set()
        merged: list[dict] = []
        for r in keyword_results:
            if r["page_key"] not in seen_keys:
                seen_keys.add(r["page_key"])
                merged.append(r)
        for r in graph_results:
            if r["page_key"] not in seen_keys:
                seen_keys.add(r["page_key"])
                merged.append(r)

        logger.info("Wiki search final: %d results (%d keyword + %d graph)",
                     len(merged), len(keyword_results), len(graph_results))
        return merged[:top_k]

    except Exception as e:
        logger.error("Wiki search failed for project %s: %s", project_id, e)
        return []
