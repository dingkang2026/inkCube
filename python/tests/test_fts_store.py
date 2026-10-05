"""Regression tests for FTSStore query handling (bug 2026-10-03).

Bug: ``POST /knowledge/base-libs/{id}/chat`` returned 500 with
``fts5: syntax error near "?"``. Two root causes found:

1. The FTS5 ``MATCH`` parameter is a query *expression* (DSL), not plain
   text — user messages containing syntax characters (?, *, ", (, :,
   AND/OR/NOT …) crashed the parser even though the value was bound as a
   SQL parameter.
2. Deeper: the unicode61 tokenizer merges consecutive CJK chars into ONE
   token, so FTS5 MATCH could never hit a Chinese word (only the exact
   full sentence) — CJK full-text search has been dead since launch,
   masked by the dense vector leg of hybrid retrieval.

Fix: ``FTSStore.search()`` routes by script — CJK queries run on a LIKE
substring path (bigram patterns, escaped wildcards, hit-count ranking);
pure latin queries run on FTS5 MATCH with phrase-escaped tokens (BM25).
``HybridRetriever`` additionally degrades to dense-only results when the
FTS leg fails for any reason.
"""
import pytest

from db.database import Database
from knowledge.fts_store import (
    FTSStore, _bigrams, _like_escape, _sanitize_fts_query,
)
from knowledge.hybrid_retriever import HybridRetriever


@pytest.fixture()
def fts():
    db = Database(db_path=":memory:")
    db.initialize()
    store = FTSStore(db)
    store.add_chunk(
        "chunk_f1_0", "公司招聘流程包括简历筛选、笔试和三轮面试",
        "proj_t", "a.docx", "text",
    )
    store.add_chunk(
        "chunk_f1_1", "融资计划书说明了A轮融资的估值与股份稀释",
        "proj_t", "b.pdf", "text",
    )
    store.add_chunk(
        "chunk_f1_2", "Series A funding plan and valuation dilution",
        "proj_t", "c.pdf", "text",
    )
    store.add_chunk(
        "chunk_f1_3", "员工持股50%的激励计划说明",
        "proj_t", "d.docx", "text",
    )
    store.add_chunk(
        "img_f1_0", "组织架构图(总部-分公司)?v2",
        "proj_t", "b.pdf", "image",
    )
    return store


# ── Helper unit tests ──


def test_bigrams_sliding_window():
    assert _bigrams("招聘流程是什么?") == ["招聘", "聘流", "流程", "程是", "是什", "什么", "么?"]
    assert _bigrams("招聘") == ["招聘"]
    assert _bigrams("A") == ["A"]


def test_like_escape_wildcards():
    assert _like_escape("50%_a\\b") == "50\\%\\_a\\\\b"


def test_sanitize_fts_query_phrases_and_quote_stripping():
    # Latin path: tokens become literal phrases; embedded quotes dropped
    assert _sanitize_fts_query(["series-A?", "round"]) == '"series-A?" "round"'
    assert _sanitize_fts_query(['he said "hi"']) == '"he said hi"'


# ── CJK LIKE-path regression tests (bug repro) ──


def test_search_with_question_mark_does_not_crash(fts):
    # Repro of the reported 500: "fts5: syntax error near \"?\""
    results = fts.search(project_id="proj_t", query="招聘流程是什么?")
    assert any(r["id"] == "chunk_f1_0" for r in results)


def test_search_cjk_word_matches(fts):
    # Was silently dead before: unicode61 merges CJK runs into one token,
    # MATCH '"招聘"' hit nothing. LIKE path must find the word.
    results = fts.search(project_id="proj_t", query="招聘")
    assert any(r["id"] == "chunk_f1_0" for r in results)


def test_search_cjk_multi_token_and_semantics(fts):
    results = fts.search(project_id="proj_t", query="融资 估值")
    assert any(r["id"] == "chunk_f1_1" for r in results)


def test_search_cjk_multi_token_excludes_partial_hits(fts):
    # AND across tokens: only chunks matching BOTH tokens are returned
    results = fts.search(project_id="proj_t", query="融资 面试")
    assert results == []


def test_search_cjk_and_mixed_ascii_token(fts):
    results = fts.search(project_id="proj_t", query="A轮融资 稀释")
    assert any(r["id"] == "chunk_f1_1" for r in results)


def test_search_like_wildcards_escaped(fts):
    # "%" in user text must match literally, not as a wildcard
    results = fts.search(project_id="proj_t", query="50% 激励")
    assert any(r["id"] == "chunk_f1_3" for r in results)


def test_search_fts_operators_in_cjk_treated_as_literal(fts):
    results = fts.search(project_id="proj_t", query="架构(总部-分公司)?v2")
    assert any(r["id"] == "img_f1_0" for r in results)


def test_search_cjk_respects_file_filter(fts):
    results = fts.search(project_id="proj_t", query="架构", file_id="f1")
    assert all(r["id"].startswith(("chunk_f1_", "img_f1_")) for r in results)
    assert any(r["id"] == "img_f1_0" for r in results)


# ── Latin MATCH-path tests ──


def test_search_english_keywords_still_match(fts):
    results = fts.search(project_id="proj_t", query="valuation")
    assert any(r["id"] == "chunk_f1_2" for r in results)


def test_search_english_multi_word(fts):
    results = fts.search(project_id="proj_t", query="funding valuation")
    assert any(r["id"] == "chunk_f1_2" for r in results)


def test_search_english_special_chars_no_crash(fts):
    # Hyphens/question marks must not raise "fts5: syntax error"
    results = fts.search(project_id="proj_t", query="series-A?")
    assert any(r["id"] == "chunk_f1_2" for r in results)


# ── Empty / noise queries ──


def test_search_blank_query_returns_empty(fts):
    assert fts.search(project_id="proj_t", query="   ") == []
    assert fts.search(project_id="proj_t", query='" "') == []


def test_search_pure_syntax_noise_returns_empty(fts):
    assert fts.search(project_id="proj_t", query="???") == []


# ── HybridRetriever degradation tests ──


class _StubVectorStore:
    def search_dense(self, project_id, query_vector, top_k, where=None):
        return [{
            "id": "chunk_f1_0", "content": "dense hit",
            "metadata": {"markdown_content": "dense hit"}, "_score": 0.9,
        }]

    def get_metadata_by_ids(self, project_id, ids):
        return {}


class _BoomFTSStore:
    def search(self, **kwargs):
        raise RuntimeError("fts5: syntax error")


def test_hybrid_degrades_to_dense_when_fts_fails():
    retriever = HybridRetriever(
        vector_store=_StubVectorStore(), fts_store=_BoomFTSStore(),
    )
    results = retriever.search(
        project_id="proj_t", query_text="招聘?", query_vector=[0.1],
        top_k=5,
    )
    assert len(results) == 1
    assert results[0]["id"] == "chunk_f1_0"
