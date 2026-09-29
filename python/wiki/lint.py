"""python/wiki/lint.py — Structural lint checks for Wiki pages.

Checks that don't require LLM (fast, free).
Per §12.2.5 of 2.6wiki-design.md.

Checks implemented:
  - empty-page: content is empty or very short
  - missing-source: page has content but no provenance records
  - orphan-review: review items whose pages were deleted
  - orphan: pages with no inbound wiki-links
  - broken-link: [[target]] where target page doesn't exist
  - no-outlinks: pages with no outbound wiki-links

Each finding includes a suggested fix (source or target page)
computed via token overlap / bigram overlap / Levenshtein similarity.
"""
import json
import logging
import math
import re
import unicodedata

logger = logging.getLogger(__name__)

# ── Scoring constants (§12.2.5) ──

BROKEN_LINK_SUGGESTION_MIN_SCORE = 0.74
RELATED_PAGE_SUGGESTION_MIN_SCORE = 0.08
SAME_FOLDER_SCORE_BONUS = 0.08
SINGLE_CJK_TOKEN_WEIGHT = 0.35
SAME_BASENAME_SCORE = 0.96
CONTAINS_TARGET_SCORE = 0.82
MAX_SUGGESTION_CANDIDATES = 64
SUGGESTION_TOKEN_WINDOW = 4000


# ── Text processing helpers ──


def _normalize_slug(text: str) -> str:
    """Normalize text to a slug for matching: lowercase, hyphens/spaces equivalent."""
    text = text.lower().strip()
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"[^\w\-]", "", text)
    return text


def _is_cjk(ch: str) -> bool:
    """Check if a character is CJK (Chinese/Japanese/Korean)."""
    try:
        name = unicodedata.name(ch)
        return any(
            kw in name
            for kw in ("CJK", "CHINESE", "HIRAGANA", "KATAKANA", "HANGUL")
        )
    except ValueError:
        return False


def _tokenize(text: str) -> set[str]:
    """Tokenize text into a set of tokens.

    - Split on whitespace/punctuation
    - CJK characters: single char + bigram
    - Non-CJK: whole word (lowercased)
    """
    tokens: set[str] = set()
    # Split on whitespace and common punctuation
    words = re.split(r"[\s,;:!?。，；：！？、]+", text.lower())
    for word in words:
        word = word.strip()
        if not word:
            continue
        # Check if word contains CJK characters
        has_cjk = any(_is_cjk(ch) for ch in word)
        if has_cjk:
            # Extract CJK chars for single-char tokens and bigrams
            cjk_chars = [ch for ch in word if _is_cjk(ch)]
            for ch in cjk_chars:
                tokens.add(ch)
            for i in range(len(cjk_chars) - 1):
                tokens.add(cjk_chars[i] + cjk_chars[i + 1])
            # Also add non-CJK parts as tokens
            non_cjk = re.sub(r"[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]", " ", word)
            for part in non_cjk.split():
                if len(part) > 1:
                    tokens.add(part)
        else:
            if len(word) > 1:
                tokens.add(word)
    return tokens


def _cjk_bigrams(text: str) -> list[str]:
    """Extract CJK bigrams from text."""
    bigrams: list[str] = []
    cjk_chars = [ch for ch in text if _is_cjk(ch)]
    for i in range(len(cjk_chars) - 1):
        bigrams.append(cjk_chars[i] + cjk_chars[i + 1])
    return bigrams


def _levenshtein_ratio(s1: str, s2: str) -> float:
    """Compute Levenshtein similarity ratio between two strings."""
    if not s1 or not s2:
        return 0.0
    len1, len2 = len(s1), len(s2)
    if len1 == 0 or len2 == 0:
        return 0.0
    # Quick check: if lengths differ too much, skip expensive computation
    if abs(len1 - len2) > max(len1, len2) * 0.5:
        return 0.0

    # DP matrix (optimized to two rows)
    prev = list(range(len2 + 1))
    curr = [0] * (len2 + 1)
    for i in range(1, len1 + 1):
        curr[0] = i
        for j in range(1, len2 + 1):
            cost = 0 if s1[i - 1] == s2[j - 1] else 1
            curr[j] = min(
                curr[j - 1] + 1,      # insert
                prev[j] + 1,          # delete
                prev[j - 1] + cost,   # substitute
            )
        prev, curr = curr, prev

    distance = prev[len2]
    max_len = max(len1, len2)
    return 1.0 - (distance / max_len)


def _extract_wikilinks(content: str) -> list[str]:
    """Extract [[target]] wiki-links from content."""
    if not content:
        return []
    return re.findall(r"\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]", content)


def _extract_short_name(page_key: str) -> str:
    """Extract short name from page_key (last segment after hyphen/underscore)."""
    parts = re.split(r"[-_]", page_key)
    return parts[-1] if parts else page_key


# ── Page data collection ──


class _PageData:
    """Collected data for a single wiki page, used by lint checks."""

    def __init__(self, page: dict):
        self.page_id: str = page["id"]
        self.page_key: str = page["page_key"]
        self.title: str = page.get("title", "")
        self.content: str = page.get("content") or ""
        self.status: str = page.get("status", "draft")
        self.slug: str = _normalize_slug(self.page_key)
        self.short_name: str = _extract_short_name(self.page_key)

        # Parse refs (outbound wiki-links)
        refs_raw = page.get("refs")
        if isinstance(refs_raw, str):
            try:
                self.outlinks: list[str] = json.loads(refs_raw)
            except (json.JSONDecodeError, TypeError):
                self.outlinks = []
        elif isinstance(refs_raw, list):
            self.outlinks = refs_raw
        else:
            self.outlinks = []

        # Also extract [[links]] from content that might not be in refs
        content_links = _extract_wikilinks(self.content)
        for link in content_links:
            normalized = _normalize_slug(link)
            if normalized not in [_normalize_slug(ol) for ol in self.outlinks]:
                self.outlinks.append(link)

        # Token set: title + slug + first N chars of content
        token_text = f"{self.title} {self.slug} {self.content[:SUGGESTION_TOKEN_WINDOW]}"
        self.token_set: set[str] = _tokenize(token_text)


# ── Main lint function ──


def run_structural_lint(db, project_id: str) -> list[dict]:
    """Run structural lint checks on all Wiki pages for a project.

    Returns a list of findings, each with:
      - type: lint check type
      - title: short description
      - description: detailed explanation
      - affected_page_ids: list of page IDs affected
      - suggested_source / suggested_target: optional fix suggestion
    """
    findings: list[dict] = []
    pages = db.list_wiki_pages(project_id)

    if not pages:
        return findings

    # Step 1: Collect page data
    page_data_list = [_PageData(p) for p in pages]
    page_key_set = {pd.page_key for pd in page_data_list}
    slug_to_page = {}
    for pd in page_data_list:
        slug_to_page[pd.slug] = pd
        slug_to_page[_normalize_slug(pd.title)] = pd
        slug_to_page[_normalize_slug(pd.short_name)] = pd

    # Step 2: Build indexes
    # Token index: token → [page_data indices]
    token_index: dict[str, list[int]] = {}
    for idx, pd in enumerate(page_data_list):
        for token in pd.token_set:
            if token not in token_index:
                token_index[token] = []
            token_index[token].append(idx)

    # Bigram index: bigram → [page_data indices]
    fragment_index: dict[str, list[int]] = {}
    for idx, pd in enumerate(page_data_list):
        text = f"{pd.title} {pd.slug} {pd.content[:1000]}"
        for bg in _cjk_bigrams(text):
            if bg not in fragment_index:
                fragment_index[bg] = []
            fragment_index[bg].append(idx)

    # Inbound counts (from outlinks)
    inbound_counts: dict[int, int] = {i: 0 for i in range(len(page_data_list))}
    slug_to_idx = {}
    for idx, pd in enumerate(page_data_list):
        slug_to_idx[pd.slug] = idx
        slug_to_idx[_normalize_slug(pd.title)] = idx

    for idx, pd in enumerate(page_data_list):
        for link in pd.outlinks:
            target_slug = _normalize_slug(link)
            if target_slug in slug_to_idx:
                target_idx = slug_to_idx[target_slug]
                if target_idx != idx:
                    inbound_counts[target_idx] = inbound_counts.get(target_idx, 0) + 1

    # Step 3: Run checks
    for idx, pd in enumerate(page_data_list):
        # Check: empty-page
        content_stripped = pd.content.strip()
        if not content_stripped:
            findings.append({
                "type": "empty-page",
                "title": f"页面内容为空: {pd.title}",
                "description": f"页面 '{pd.page_key}' 内容为空，建议重新编译或补充资料。",
                "affected_page_ids": [pd.page_id],
            })
        elif len(content_stripped) < 50:
            findings.append({
                "type": "empty-page",
                "title": f"页面内容过短: {pd.title}",
                "description": f"页面 '{pd.page_key}' 内容仅 {len(content_stripped)} 字符，可能编译不完整。",
                "affected_page_ids": [pd.page_id],
            })

        # Check: missing-source
        try:
            sources = db.list_wiki_page_sources(pd.page_id)
            if not sources and content_stripped:
                findings.append({
                    "type": "missing-source",
                    "title": f"缺少来源追溯: {pd.title}",
                    "description": f"页面 '{pd.page_key}' 有内容但没有任何来源追溯记录。",
                    "affected_page_ids": [pd.page_id],
                })
        except Exception:
            pass  # Source check failure is non-critical

        # Check: orphan (no inbound links)
        if inbound_counts.get(idx, 0) == 0 and len(page_data_list) > 1:
            suggestion = _related_candidate(
                idx, page_data_list, token_index, direction="inbound",
            )
            finding: dict = {
                "type": "orphan",
                "title": f"孤立页面: {pd.title}",
                "description": f"页面 '{pd.page_key}' 没有任何其他页面链接到它。",
                "affected_page_ids": [pd.page_id],
            }
            if suggestion:
                finding["suggested_source"] = suggestion
            findings.append(finding)

        # Check: broken-link (outlinks to non-existent pages)
        for link in pd.outlinks:
            target_slug = _normalize_slug(link)
            if target_slug not in slug_to_idx and target_slug not in {
                _normalize_slug(pk) for pk in page_key_set
            }:
                suggestion = _broken_candidate(
                    link, page_data_list, fragment_index,
                )
                finding = {
                    "type": "broken-link",
                    "title": f"死链: [[{link}]] (来自 {pd.title})",
                    "description": f"页面 '{pd.page_key}' 引用了不存在的页面 '[[{link}]]'。",
                    "affected_page_ids": [pd.page_id],
                }
                if suggestion:
                    finding["suggested_target"] = suggestion
                findings.append(finding)

        # Check: no-outlinks (no outbound links)
        if not pd.outlinks and len(page_data_list) > 1:
            suggestion = _related_candidate(
                idx, page_data_list, token_index, direction="outbound",
            )
            finding = {
                "type": "no-outlinks",
                "title": f"无出链: {pd.title}",
                "description": f"页面 '{pd.page_key}' 没有链接到任何其他页面。",
                "affected_page_ids": [pd.page_id],
            }
            if suggestion:
                finding["suggested_target"] = suggestion
            findings.append(finding)

    # Check: orphan-review (review items whose pages were deleted)
    try:
        reviews = db.list_wiki_reviews(project_id, resolved=False)
        all_page_ids = {p["id"] for p in pages}
        for review in reviews:
            affected = review.get("affected_page_ids") or []
            if affected and not any(pid in all_page_ids for pid in affected):
                findings.append({
                    "type": "orphan-review",
                    "title": f"孤立发现项: {review['title']}",
                    "description": "此发现项关联的页面已被删除，建议标记为已解决。",
                    "affected_page_ids": [],
                })
    except Exception:
        pass  # Review check failure is non-critical

    logger.info("Structural lint for project %s: %d findings", project_id, len(findings))
    return findings


# ── Suggestion algorithms (§12.2.5) ──


def _related_candidate(
    page_idx: int,
    page_data_list: list[_PageData],
    token_index: dict[str, list[int]],
    direction: str = "inbound",
) -> str | None:
    """Find the best page that should link to / be linked from the given page.

    Uses token overlap scoring with cosine normalization.
    direction='inbound': find a page that should link TO this page (suggested_source)
    direction='outbound': find a page that this page should link TO (suggested_target)
    """
    page = page_data_list[page_idx]
    total_pages = len(page_data_list)
    max_common = max(20, int(total_pages * 0.25))

    scores: dict[int, float] = {}
    for token in page.token_set:
        matches = token_index.get(token, [])
        if len(matches) > max_common:
            continue  # Skip overly common tokens
        weight = 1.0 if len(token) > 1 else SINGLE_CJK_TOKEN_WEIGHT
        for candidate_idx in matches:
            if candidate_idx == page_idx:
                continue
            scores[candidate_idx] = scores.get(candidate_idx, 0.0) + weight

    if not scores:
        return None

    # Cosine normalization
    page_norm = math.sqrt(len(page.token_set)) if page.token_set else 1.0
    best_key: int | None = None
    best_score = 0.0
    for candidate_idx, raw_score in scores.items():
        candidate = page_data_list[candidate_idx]
        candidate_norm = math.sqrt(len(candidate.token_set)) if candidate.token_set else 1.0
        normalized = raw_score / (page_norm * candidate_norm) if (page_norm * candidate_norm) > 0 else 0.0
        if normalized > best_score:
            best_score = normalized
            best_key = candidate_idx

    if best_key is not None and best_score >= RELATED_PAGE_SUGGESTION_MIN_SCORE:
        return page_data_list[best_key].page_key
    return None


def _broken_candidate(
    target: str,
    page_data_list: list[_PageData],
    fragment_index: dict[str, list[int]],
) -> str | None:
    """Find the best matching page for a broken link target.

    Uses bigram overlap + Levenshtein similarity.
    """
    target_lower = target.lower()
    target_slug = _normalize_slug(target)
    target_bigrams = _cjk_bigrams(target_lower)

    scores: dict[int, float] = {}

    # Bigram overlap scoring
    for bg in target_bigrams:
        for candidate_idx in fragment_index.get(bg, []):
            scores[candidate_idx] = scores.get(candidate_idx, 0.0) + 1.0

    # Find best by string similarity
    best_key: int | None = None
    best_score = 0.0

    # Check all pages for string similarity (limited to candidates with bigram overlap + capped)
    candidates_to_check = set(scores.keys())
    # Also check pages without bigram overlap, but cap total to MAX_SUGGESTION_CANDIDATES
    if len(candidates_to_check) < MAX_SUGGESTION_CANDIDATES:
        for idx in range(len(page_data_list)):
            if idx not in candidates_to_check:
                candidates_to_check.add(idx)
                if len(candidates_to_check) >= MAX_SUGGESTION_CANDIDATES:
                    break

    for candidate_idx in candidates_to_check:
        if candidate_idx >= len(page_data_list):
            continue
        candidate = page_data_list[candidate_idx]

        # String similarity: max of slug, short_name, title comparisons
        sim = max(
            _levenshtein_ratio(target_slug, candidate.slug),
            _levenshtein_ratio(target_slug, _normalize_slug(candidate.short_name)),
            _levenshtein_ratio(target_lower, candidate.title.lower()),
        )

        # Special: basename match
        if target_slug == candidate.slug:
            sim = max(sim, SAME_BASENAME_SCORE)
        # Containment check
        if target_slug in candidate.slug or candidate.slug in target_slug:
            sim = max(sim, CONTAINS_TARGET_SCORE)

        # Combine bigram score and string similarity
        bigram_bonus = min(scores.get(candidate_idx, 0.0) * 0.02, 0.15)
        combined = max(sim, bigram_bonus + sim * 0.5)

        if combined > best_score:
            best_score = combined
            best_key = candidate_idx

    if best_key is not None and best_score >= BROKEN_LINK_SUGGESTION_MIN_SCORE:
        return page_data_list[best_key].page_key
    return None
