"""端点级事前准入回归测试（0.4 §7.3 第三层 v3.7）。

核心断言：缺配置时，补齐的入口端点直接抛结构化 InkCubeError 子类，
而非进入函数体后被 except Exception 转 HTTPException(500)。

这同时验证调用约定：ensure_ready 必须在端点 try 块之外——
若被挪入 try 块，InkCubeError 被吞掉结构化字段，本测试即失败。

豁免项（不该被拦截）由 TestLintStructuralPassthrough 覆盖代表场景：
structural lint 纯本地，缺 LLM 时必须照常执行。
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import BackgroundTasks

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import readiness
from errors import EmbeddingModelMissing, LLMNotConfigured, ParsingModelMissing
from routers import knowledge, reports, wiki

_MISSING = {
    "llm": {"kind": "config", "id": "llm", "action": "settings:external"},
    "bge-m3": {"kind": "model", "id": "bge-m3", "action": "settings:internal"},
    "parsing": {"kind": "model", "id": "parsing", "action": "settings:internal"},
}


def _features(missing_key: str) -> dict:
    """全 feature 均缺同一依赖的就绪度矩阵（最小化 fixture）。"""
    missing = [_MISSING[missing_key]]
    return {
        f: {"ready": False, "missing": list(missing), "optional_missing": []}
        for f in ("file_parsing", "wiki_compile", "report", "task", "knowledge_search")
    }


@pytest.fixture
def no_llm(monkeypatch):
    monkeypatch.setattr(readiness, "get_features_cached", lambda: _features("llm"))


@pytest.fixture
def no_embedding(monkeypatch):
    monkeypatch.setattr(readiness, "get_features_cached", lambda: _features("bge-m3"))


@pytest.fixture
def no_parsing(monkeypatch):
    monkeypatch.setattr(readiness, "get_features_cached", lambda: _features("parsing"))


def _run(coro):
    return asyncio.run(coro)


class TestReportRetry:
    def test_retry_requires_llm(self, no_llm, monkeypatch):
        """报告重试与 generate 同等拦截（v3.7 前无校验，配置失效后起跑才失败）。"""
        # 队列暂停闸门（0.4 §6.7）位于 ensure_ready 之前，是函数首个 get_db 触点；
        # 补一个未暂停的 db 使测试聚焦在就绪度拦截本身
        db = MagicMock()
        db.get_flag.return_value = False
        monkeypatch.setattr(reports, "get_db", lambda: db)
        with pytest.raises(LLMNotConfigured):
            _run(reports.retry_report("r1", BackgroundTasks()))


class TestKnowledgeGateways:
    def test_chat_requires_embedding_first(self, no_embedding):
        """知识问答复合校验：先查 knowledge_search（embedding）。"""
        with pytest.raises(EmbeddingModelMissing):
            _run(knowledge.chat_project("p1", knowledge.ChatRequest(message="hi")))

    def test_chat_requires_llm(self, monkeypatch):
        """知识问答复合校验：embedding 通过后再查 task（llm）。"""
        feats = _features("llm")
        feats["knowledge_search"] = {"ready": True, "missing": [], "optional_missing": []}
        monkeypatch.setattr(readiness, "get_features_cached", lambda: feats)
        with pytest.raises(LLMNotConfigured):
            _run(knowledge.chat_project("p1", knowledge.ChatRequest(message="hi")))

    def test_retry_file(self, no_parsing):
        with pytest.raises(ParsingModelMissing):
            _run(knowledge.retry_file("p1", "f1"))

    def test_retry_failed_files(self, no_parsing):
        with pytest.raises(ParsingModelMissing):
            _run(knowledge.retry_failed_files("p1"))

    def test_batch_reprocess(self, no_parsing):
        with pytest.raises(ParsingModelMissing):
            _run(knowledge.batch_reprocess_files("p1", knowledge.BatchFileRequest(file_ids=[])))


class TestWikiGateways:
    @pytest.mark.parametrize("mode", ["semantic", "full"])
    def test_lint_llm_modes_blocked(self, no_llm, mode):
        with pytest.raises(LLMNotConfigured):
            _run(wiki.trigger_lint("p1", wiki.LintRequest(mode=mode)))

    def test_dedup_detect(self, no_llm):
        with pytest.raises(LLMNotConfigured):
            _run(wiki.detect_duplicate_pages("p1"))

    def test_dedup_merge(self, no_llm):
        req = wiki.DedupMergeRequest(canonical_key="a", duplicate_keys=["b"])
        with pytest.raises(LLMNotConfigured):
            _run(wiki.merge_duplicate_pages("p1", req))

    def test_enrich_wikilinks(self, no_llm):
        with pytest.raises(LLMNotConfigured):
            _run(wiki.enrich_page_wikilinks("p1", wiki.EnrichWikilinksRequest(page_key="a")))


class TestLintStructuralPassthrough:
    def test_structural_not_blocked_without_llm(self, no_llm, monkeypatch):
        """豁免代表：structural lint 纯本地，缺 LLM 必须照常执行（防误拦）。"""
        db = MagicMock()
        monkeypatch.setattr(wiki, "get_db", lambda: db)
        monkeypatch.setattr("wiki.lint.run_structural_lint", lambda db, pid: [])
        result = _run(wiki.trigger_lint("p1", wiki.LintRequest(mode="structural")))
        assert result["success"] is True
        assert result["mode"] == "structural"
        assert result["findings_count"] == 0
