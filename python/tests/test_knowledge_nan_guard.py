# -*- coding: utf-8 -*-
"""python/tests/test_knowledge_nan_guard.py — 检索链路 NaN 三层消毒回归。

事故 2026-10-14/15：fp16 CUDA 推理偶发产生非有限向量，裸传到 FastAPI
响应序列化（json.dumps 遇 NaN 抛 ValueError → 500，日志 app.log 实证
两例）。三层防线各有回归：
  - 入库边界：BgeM3EmbeddingFunction 非有限行 → 单位基向量占位
  - 查询边界：_embed_query 非有限 → None（检索降级 FTS-only）
  - 出口守卫：search_dense 跳过非有限距离（兜存量库遗留毒化向量）
"""
import math
import os
import sys

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest

from knowledge import knowledge_search as ks_mod
from knowledge import vector_store as vs
from knowledge.hybrid_retriever import HybridRetriever


class _FlakyModel:
    """encode 返回预设 dense_vecs 的假模型（模拟 fp16 溢出产生 NaN）。"""

    def __init__(self, vecs):
        self._vecs = np.array(vecs, dtype=np.float32)

    def encode(self, texts, **kwargs):
        return {"dense_vecs": self._vecs}


class TestIngestBoundary:
    """入库边界：BgeM3EmbeddingFunction 非有限行消毒。"""

    def test_nonfinite_rows_replaced_with_unit_placeholder(self, monkeypatch):
        ef = vs.BgeM3EmbeddingFunction("unused-path")
        monkeypatch.setattr(
            ef, "_get_model",
            lambda: _FlakyModel([[1.0, 0.0, 0.5], [float("nan"), 0.5, 0.0]]),
        )
        out = ef(["good", "poisoned"])
        # chromadb 基类会把 __call__ 返回包装为 [np.array(row)]，按值断言
        rows = [[float(x) for x in row] for row in out]

        assert len(rows) == 2
        assert all(math.isfinite(x) for row in rows for x in row)
        assert rows[0] == [1.0, 0.0, 0.5]  # 正常行原样保留
        assert rows[1] == [1.0, 0.0, 0.0]  # 毒化行 → 单位基向量 e0（模长 1）

    def test_finite_batch_untouched(self, monkeypatch):
        ef = vs.BgeM3EmbeddingFunction("unused-path")
        monkeypatch.setattr(ef, "_get_model", lambda: _FlakyModel([[0.6, 0.8]]))
        rows = [[float(x) for x in row] for row in ef(["ok"])]
        # float32 往返精度：用 approx 比较
        assert rows == [[pytest.approx(0.6), pytest.approx(0.8)]]


class TestQueryBoundary:
    """查询边界：_embed_query 非有限 → None。"""

    def _make_tool(self, monkeypatch, model):
        class _FakeVectorStore:
            def __init__(self, *a, **k):
                pass

            def _get_embedding_model(self):
                return model

        monkeypatch.setattr(ks_mod, "VectorStore", _FakeVectorStore)
        monkeypatch.setattr(ks_mod, "FTSStore", lambda db: None)
        monkeypatch.setattr(ks_mod, "HybridRetriever", lambda **k: None)
        return ks_mod.KnowledgeSearchTool(db=None, chroma_persist_dir="x")

    def test_nonfinite_query_embedding_returns_none(self, monkeypatch):
        tool = self._make_tool(monkeypatch, _FlakyModel([[float("nan"), 1.0]]))
        assert tool._embed_query("q") is None

    def test_finite_query_embedding_passthrough(self, monkeypatch):
        tool = self._make_tool(monkeypatch, _FlakyModel([[0.5, 0.5]]))
        assert tool._embed_query("q") == [0.5, 0.5]


class TestSearchDenseGuard:
    """出口守卫：search_dense 跳过非有限距离（兜存量库遗留毒化向量）。"""

    def test_skips_nonfinite_distance_keeps_finite(self, monkeypatch, tmp_path):
        class _StubCollection:
            def query(self, query_embeddings, n_results, where, include):
                return {
                    "ids": [["c_bad", "c_ok"]],
                    "documents": [["毒化块", "正常块"]],
                    "metadatas": [[{"file_id": "f"}, {"file_id": "f"}]],
                    "distances": [[float("nan"), 0.25]],
                }

        monkeypatch.setattr(vs, "get_chroma_client", lambda p: object())
        store = vs.VectorStore(str(tmp_path / "chroma"), bge_m3_path=str(tmp_path))
        monkeypatch.setattr(
            store, "get_or_create_collection", lambda pid: _StubCollection())

        out = store.search_dense("p1", [0.1], top_k=5)

        assert [r["id"] for r in out] == ["c_ok"]
        assert out[0]["distance"] == 0.25
        assert math.isfinite(out[0]["_score"])


class TestHybridDegradesToFtsOnly:
    """检索降级：query_vector=None 时跳过 dense 腿，FTS 腿照常出结果。"""

    def test_none_query_vector_skips_dense_leg(self):
        class _BoomVectorStore:
            def search_dense(self, **kwargs):
                raise AssertionError("dense leg must be skipped")

            def get_metadata_by_ids(self, project_id, ids):
                return {}

        class _StubFTS:
            def search(self, project_id, query, top_k,
                       content_type_filter=None, file_id=None):
                return [{"id": "c1", "content": "命中", "metadata": {}, "_score": 3.2}]

        retriever = HybridRetriever(
            vector_store=_BoomVectorStore(), fts_store=_StubFTS(), rrf_k=60)
        out = retriever.search(
            project_id="p1", query_text="融资", query_vector=None, top_k=3)

        assert len(out) == 1
        assert out[0]["id"] == "c1"
        assert out[0]["_score"] > 0
