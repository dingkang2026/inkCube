"""python/tests/test_concurrency_hardening.py — 并发默认值 2 的安全加固回归测试。

背景（2026-10-07）：max_concurrent_file_parsing / max_compile_concurrency
默认值 1→2。文件解析并行 2 的收益在 docling CPU 段与 embedding GPU 段重叠；
Wiki 编译的并发单位是项目（项目内文件本就串行），2 = 两个项目并行编译。
并发打开后必须保证：

  1. bge-m3 encode 全局锁：FlagEmbedding 1.3 首次 encode 才把权重搬上 CUDA，
     两个 worker 并发首发会竞态搬运 → 一方 device mismatch → 文件误标 error；
  2. docling 首次转换独占：pipeline 模型懒加载发生在首次 convert() 内部
     （无锁内部 cache），并发首发同理；
  3. 默认值多层一致：DB defaults（get_settings）、SettingsModel（POST 校验）、
     KnowledgeWorker 签名默认（worker 协程数在 start() 时按它固定）必须同步，
     漂移即缺陷；
  4. Wiki 同项目防重：retry_page 与 trigger_compile 同样拒绝活跃编译任务
     （见 test_wiki_retry_guard.py）。
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import knowledge.doc_parser as dp
import knowledge.vector_store as vs
from db.database import Database
from schemas import SettingsModel


# ── 默认值两层一致 ──


def test_default_file_parsing_concurrency_is_two():
    """空 DB → get_settings 返回纯 defaults，文件解析并发默认 2。"""
    db = Database(db_path=":memory:")
    db.initialize()
    try:
        assert db.get_settings()["max_concurrent_file_parsing"] == 2
    finally:
        db.conn.close()


def test_default_compile_concurrency_is_two():
    """空 DB → get_settings 返回纯 defaults，Wiki 编译并发默认 2。"""
    db = Database(db_path=":memory:")
    db.initialize()
    try:
        assert db.get_settings()["max_compile_concurrency"] == 2
    finally:
        db.conn.close()


def test_settings_model_default_matches_db_default():
    """SettingsModel（POST 校验）默认值必须与 DB defaults 同步（并发三项均为 2）。"""
    m = SettingsModel()
    assert m.max_concurrent_file_parsing == 2
    assert m.max_concurrent_image_description == 2
    assert m.max_compile_concurrency == 2


def test_worker_signature_defaults_are_two():
    """KnowledgeWorker 签名默认（不传并发参数）必须与 DB/Schema 同步为 2。

    worker 协程数在 start() 时按该值固定：签名默认漂移会让新库实际并发
    低于配置值（「调大需重启」的根因），此处锁死三层一致。
    """
    from knowledge.task_queue import KnowledgeWorker

    w = KnowledgeWorker(db=None, data_dir="")
    assert w._max_file_concurrency == 2
    assert w._max_image_concurrency == 2
    assert w._max_compile_concurrency == 2


# ── bge-m3 encode 全局锁 ──


def test_encode_runs_under_global_lock(monkeypatch):
    """encode 必须在模块级锁内执行（懒搬运竞态由该锁关闭）。

    确定性断言：fake encode 执行时锁处于持有态，且不可重入再获取。
    """
    seen = {}

    class _FakeModel:
        # 签名对齐生产调用(2026-10-09 TDR 修复后 encode 固定传 batch_size/max_length)
        def encode(self, texts, return_dense=True, batch_size=8, max_length=1024):
            seen["held"] = vs._bge_encode_lock.locked()
            # Lock 不可重入：已在锁内则再次 acquire(blocking=False) 必失败
            seen["non_reentrant"] = not vs._bge_encode_lock.acquire(blocking=False)
            # 与真实 FlagEmbedding 一致：dense_vecs 为 numpy 数组（.tolist()）
            return {"dense_vecs": np.zeros((len(texts), 2))}

    monkeypatch.setattr(vs, "_get_bge_m3_model", lambda path: _FakeModel())
    out = vs.BgeM3EmbeddingFunction("fake/path")(["a", "b"])
    # chromadb 的 EmbeddingFunction.__init_subclass__ 会包装子类 __call__，
    # 返回值被 normalize 成 list[float32 ndarray] —— 断言与表示解耦
    rows = [[float(v) for v in row] for row in out]
    assert rows == [[0.0, 0.0], [0.0, 0.0]]
    assert seen == {"held": True, "non_reentrant": True}


# ── docling 首次转换独占 ──


class _FakeConverter:
    """记录每次 convert 执行时首次转换锁是否被持有。"""

    def __init__(self):
        self.calls = []

    def convert(self, path):
        self.calls.append((path, dp._docling_first_convert_lock.locked()))
        return f"result({path})"


@pytest.fixture(autouse=True)
def _reset_warmed():
    """模块级 warmed 标志在用例间隔离（测后复位）。"""
    dp._docling_warmed = False
    yield
    dp._docling_warmed = False


def test_first_convert_runs_serialized():
    """冷启动首次 convert 在独占锁内执行，完成后置 warmed。"""
    conv = _FakeConverter()
    out = dp._convert_threadsafe(conv, "a.pdf")
    assert out == "result(a.pdf)"
    assert conv.calls == [("a.pdf", True)]  # 锁内执行
    assert dp._docling_warmed is True


def test_subsequent_convert_bypasses_lock():
    """已 warmed → 锁外直通（稳态零开销，不阻塞并发转换）。"""
    dp._docling_warmed = True
    conv = _FakeConverter()
    out = dp._convert_threadsafe(conv, "a.pdf")
    assert out == "result(a.pdf)"
    assert conv.calls == [("a.pdf", False)]  # 锁外执行


def test_failure_still_marks_warmed():
    """首次 convert 抛异常也置 warmed——懒加载状态已被污染/初始化，
    重试竞态初始化路径只会重新打开竞态窗口。"""
    class _Boom:
        def convert(self, path):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        dp._convert_threadsafe(_Boom(), "a.pdf")
    assert dp._docling_warmed is True
