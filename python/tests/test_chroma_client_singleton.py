"""python/tests/test_chroma_client_singleton.py — ChromaDB 客户端单例回归测试。

背景事故（2026-10-07）：task_queue._reset_interrupted_items 用裸 PersistentClient()
（默认 settings）打开 data/chroma，而 vector_store.VectorStore 用
ChromaSettings(anonymized_telemetry=False)。chromadb 进程内按 (path, settings)
缓存后端实例，settings 撞车后所有文件的索引环节抛
"An instance of Chroma already exists ... with different settings"——
重启后 181 个文件连环标 error。

回归保证：
  - get_chroma_client 同目录返回同一实例、只真正创建一次；
  - 统一 settings（anonymized_telemetry=False）；
  - task_queue 启动清理不再直接调 chromadb.PersistentClient。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import knowledge.vector_store as vs
from knowledge.vector_store import get_chroma_client


@pytest.fixture(autouse=True)
def _isolated_cache(monkeypatch):
    """每个用例独立客户端缓存，避免跨用例污染。"""
    monkeypatch.setattr(vs, "_chroma_clients", {})


class TestGetChromaClientSingleton:
    def test_same_dir_returns_same_instance(self, monkeypatch, tmp_path):
        """同目录多次获取 → 同一实例，PersistentClient 只被调一次。"""
        calls = []

        def _fake_persistent_client(path, settings=None):
            calls.append(path)
            return object()  # 每次全新对象，身份断言靠单例缓存

        monkeypatch.setattr(vs.chromadb, "PersistentClient", _fake_persistent_client)

        d = str(tmp_path / "chroma")
        c1 = get_chroma_client(d)
        c2 = get_chroma_client(d)
        assert c1 is c2
        assert len(calls) == 1

    def test_noncanonical_path_spelling_reuses_client(self, monkeypatch, tmp_path):
        """同目录不同写法（含 . 分量）→ 复用同一实例（路径先 resolve 再作 key）。"""
        calls = []

        def _fake(path, settings=None):
            calls.append(path)
            return object()

        monkeypatch.setattr(vs.chromadb, "PersistentClient", _fake)

        base = tmp_path / "chroma"
        c1 = get_chroma_client(str(base))
        c2 = get_chroma_client(str(base.parent / "." / "chroma"))
        assert c1 is c2
        assert len(calls) == 1

    def test_settings_telemetry_off(self, monkeypatch, tmp_path):
        """创建时统一携带 anonymized_telemetry=False（与 VectorStore 原设置一致）。"""
        captured = {}

        def _fake(path, settings=None):
            captured["settings"] = settings
            return object()

        monkeypatch.setattr(vs.chromadb, "PersistentClient", _fake)
        get_chroma_client(str(tmp_path / "chroma"))
        assert captured["settings"].anonymized_telemetry is False

    def test_different_dirs_get_different_clients(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            vs.chromadb, "PersistentClient", lambda path, settings=None: object()
        )
        a = get_chroma_client(str(tmp_path / "a"))
        b = get_chroma_client(str(tmp_path / "b"))
        assert a is not b


# ── task_queue 启动清理集成回归 ──


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConn:
    """按 SQL 关键字返回不同 rows：files 查询返回 1 个 parsing 文件。"""

    def __init__(self):
        self.executed = []

    def execute(self, sql, *args):
        self.executed.append(sql)
        if "FROM files" in sql:
            return _FakeCursor([{"id": "file_x", "project_id": "p1"}])
        return _FakeCursor([])

    def commit(self):
        pass


class _FakeDb:
    def __init__(self):
        self.conn = _FakeConn()

    def reset_file_for_retry(self, file_id):
        pass

    def reset_interrupted_compile_tasks(self):
        return 0


class TestStartupResetUsesSharedClient:
    def test_reset_never_opens_bare_persistent_client(self, monkeypatch, tmp_path):
        """启动清理必须走 get_chroma_client——裸 PersistentClient 会污染进程内
        settings 缓存（2026-10-07 事故：181 文件连环失败）。"""

        def _boom(*args, **kwargs):
            raise AssertionError(
                "reset must not call chromadb.PersistentClient directly"
            )

        monkeypatch.setattr("chromadb.PersistentClient", _boom)

        shared = object()
        used_paths = []

        def _fake_shared(path):
            used_paths.append(path)
            return shared

        monkeypatch.setattr("knowledge.vector_store.get_chroma_client", _fake_shared)

        from knowledge.task_queue import KnowledgeWorker

        worker = KnowledgeWorker.__new__(KnowledgeWorker)  # 跳过 __init__（不起队列）
        worker.db = _FakeDb()
        worker.data_dir = str(tmp_path)

        worker._reset_interrupted_items()

        # 清理用了共享客户端，且指向 data/chroma
        assert used_paths == [str((tmp_path / "chroma").resolve())]
        # FTS / images 清理与状态重置仍然执行
        assert any("chunks_fts" in sql for sql in worker.db.conn.executed)
        assert any("images" in sql for sql in worker.db.conn.executed)
