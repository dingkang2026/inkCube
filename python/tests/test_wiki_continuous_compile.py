"""python/tests/test_wiki_continuous_compile.py — 连续编译回归测试。

设计（0.4 §3 / 2.6 §4.7）：V2 逐文件摄入连续跑完所有 pending 文件（完成后
统一在对话框审阅摘要）；单文件失败隔离不中断队列；停止为协作式（当前文件
完成后生效）。
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from errors import LLMInvalidKey
from knowledge.task_queue import KnowledgeWorker


class StubWikiIngestor:
    """按脚本回放结果 / 异常的 WikiIngestor 替身，记录调用轨迹。

    脚本元素：
      - dict  → ingest_file 正常返回该值
      - Exception → ingest_file 抛出该异常
      - ("stop", dict) → 先 request_compile_stop 再返回 dict（模拟当前文件
        处理期间用户点了停止）
    """

    def __init__(self, db, data_dir, llm_client, llm_settings):
        self.db = db
        self.script = []
        self.calls: list[str] = []

    async def ingest_file(self, project_id, file_id, task_id):
        self.calls.append(file_id)
        step = self.script.pop(0)
        if isinstance(step, tuple) and step[0] == "stop":
            self.db.request_compile_stop(task_id)
            return step[1]
        if isinstance(step, Exception):
            raise step
        return step


class StubDB:
    def __init__(self, pending_files, existing_summaries=None):
        self._pending = list(pending_files)
        self._summaries = list(existing_summaries or [])
        self._stop_requested = False
        self.status_updates: list[tuple] = []

    def get_ingest_summaries(self, task_id):
        return list(self._summaries)

    def get_pending_wiki_files(self, project_id, exclude_file_ids=None):
        excl = set(exclude_file_ids or [])
        return [f for f in self._pending if f["id"] not in excl]

    def append_ingest_summary(self, task_id, summary):
        # 同 file_id 幂等替换（append_ingest_summary 契约）
        self._summaries = [
            s for s in self._summaries if s.get("file_id") != summary.get("file_id")
        ]
        self._summaries.append(summary)

    def is_compile_stop_requested(self, task_id):
        return self._stop_requested

    def request_compile_stop(self, task_id):
        self._stop_requested = True

    def update_compile_task_status(self, task_id, status, **kw):
        self.status_updates.append((status, kw))


@pytest.fixture
def wiki_data_dir(tmp_path):
    """隔离的 data_dir（tmp_path）：禁止硬编码 /tmp —— Windows 上会落到当前盘符根目录。"""
    return str(tmp_path / "inkcube-test")


def _make_worker(db, data_dir) -> KnowledgeWorker:
    worker = KnowledgeWorker.__new__(KnowledgeWorker)
    worker.db = db
    worker.data_dir = data_dir
    worker._running = True
    return worker


def _patch_ingestor(monkeypatch, db, script, data_dir):
    """用固定脚本的 StubWikiIngestor 替换 wiki.ingest.WikiIngestor。"""
    import wiki.ingest as ingest_mod

    ingestor = StubWikiIngestor(db, data_dir, None, {})
    ingestor.script = script
    monkeypatch.setattr(ingest_mod, "WikiIngestor", lambda *a: ingestor)
    return ingestor


FILES = [
    {"id": "f1", "filename": "a.pdf"},
    {"id": "f2", "filename": "b.pdf"},
    {"id": "f3", "filename": "c.pdf"},
]


class TestContinuousCompile:
    def test_runs_all_files_without_pause(self, monkeypatch, wiki_data_dir):
        """3 个文件连续跑完：直达 completed，无中间暂停。"""
        db = StubDB(FILES)
        w = _make_worker(db, wiki_data_dir)

        _patch_ingestor(monkeypatch, db, [{"pages_created": 1}] * 3, wiki_data_dir)
        asyncio.run(w._run_per_file_ingest("wc1", "p1", None, {}))

        assert db.status_updates[-1][0] == "completed"
        assert db.status_updates[-1][1] == {"pages_total": 3, "pages_completed": 3}
        assert len(db._summaries) == 3

    def test_failed_file_isolated_and_queue_continues(self, monkeypatch, wiki_data_dir):
        """单文件失败：error 记入 summaries，不中断，后续文件照常摄入。"""
        db = StubDB(FILES)
        w = _make_worker(db, wiki_data_dir)

        _patch_ingestor(monkeypatch, db, [
            {"pages_created": 1},
            RuntimeError("boom"),
            {"pages_created": 1},
        ], wiki_data_dir)
        asyncio.run(w._run_per_file_ingest("wc1", "p1", None, {}))

        assert db.status_updates[-1][0] == "completed"  # 失败不改变终态
        assert len(db._summaries) == 3
        failed = next(s for s in db._summaries if s["file_id"] == "f2")
        assert failed["error"] == "boom"
        assert not failed["skipped"]
        assert all(s["file_id"] in ("f1", "f2", "f3") for s in db._summaries)

    def test_credential_error_isolated_with_actionable_text(self, monkeypatch, wiki_data_dir):
        """凭据类错误（Key 失效等）：该文件失败记 actionable 文案，不中断队列。"""
        db = StubDB(FILES[:2])
        w = _make_worker(db, wiki_data_dir)

        _patch_ingestor(monkeypatch, db, [LLMInvalidKey(), {"pages_created": 1}], wiki_data_dir)
        asyncio.run(w._run_per_file_ingest("wc1", "p1", None, {}))

        assert db.status_updates[-1][0] == "completed"  # 队列不中断
        failed = next(s for s in db._summaries if s["file_id"] == "f1")
        assert "设置" in failed["error"]  # actionable 文案含设置页指引

    def test_progress_updated_per_file(self, monkeypatch, wiki_data_dir):
        """每个文件完成后刷新 ingesting 进度（前端进度条数据源）。"""
        db = StubDB(FILES)
        w = _make_worker(db, wiki_data_dir)

        _patch_ingestor(monkeypatch, db, [{"pages_created": 1}] * 3, wiki_data_dir)
        asyncio.run(w._run_per_file_ingest("wc1", "p1", None, {}))

        progress = [(s, kw) for s, kw in db.status_updates if s == "ingesting"]
        assert progress == [
            ("ingesting", {"pages_total": 3, "pages_completed": 1}),
            ("ingesting", {"pages_total": 3, "pages_completed": 2}),
            ("ingesting", {"pages_total": 3, "pages_completed": 3}),
        ]

    def test_stop_takes_effect_after_current_file(self, monkeypatch, wiki_data_dir):
        """协作式停止：当前文件完成后生效，未摄入文件不再处理。"""
        db = StubDB(FILES)
        w = _make_worker(db, wiki_data_dir)

        ingestor = _patch_ingestor(
            monkeypatch, db,
            [("stop", {"pages_created": 1}), {"pages_created": 1}],
            wiki_data_dir,
        )
        asyncio.run(w._run_per_file_ingest("wc1", "p1", None, {}))

        assert db.status_updates[-1][0] == "stopped"
        assert ingestor.calls == ["f1"]  # f2/f3 未再调用
        assert [s["file_id"] for s in db._summaries] == ["f1"]

    def test_resume_skips_already_ingested_files(self, monkeypatch, wiki_data_dir):
        """断点续跑（崩溃重启）：已有 summaries 的文件跳过，只补剩余。"""
        db = StubDB(FILES, existing_summaries=[
            {"file_id": "f1", "filename": "a.pdf", "pages_created": 1},
        ])
        w = _make_worker(db, wiki_data_dir)

        ingestor = _patch_ingestor(
            monkeypatch, db, [{"pages_created": 1}, {"pages_created": 1}],
            wiki_data_dir,
        )
        asyncio.run(w._run_per_file_ingest("wc1", "p1", None, {}))

        assert ingestor.calls == ["f2", "f3"]
        assert db.status_updates[-1] == (
            "completed", {"pages_total": 3, "pages_completed": 3},
        )
