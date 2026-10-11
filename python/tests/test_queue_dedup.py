"""python/tests/test_queue_dedup.py — 队列防重与 archived 出队守卫。

事故背景（2026-10-09/10）：QueueScanner 每 5s 全量补投递 pending 条目且
enqueue_file/enqueue_image 无防重，积压期间同一图片被重复入队数十次；出队
守卫又无条件放行 archived，seal/qr_code/signature 类图片（解析成功即自动
归档的终态）被真实重复调用 VLM——单张最多 45 次，两日浪费约 4700 次付费调用。

守护两个不变量：
1. 队列中同一 file/image 任一时刻至多一个非 force 条目（force 为用户显式
   重试，始终放行）；
2. archived 图片仅 force=True 可重新处理，普通出队跳过。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from db.database import Database
from knowledge import task_queue as tq
from knowledge.task_queue import KnowledgeWorker


def _make_db() -> Database:
    db = Database(db_path=":memory:")
    db.initialize()
    return db


def _insert_file_with_images(db: Database, file_id: str, image_ids: list[str],
                             status: str = "pending"):
    """插入最小关联行：project → file → images。"""
    db.conn.execute("INSERT INTO projects (id, name) VALUES ('p1', 'P')")
    db.conn.execute(
        "INSERT INTO files (id, project_id, filename, file_type, file_size, "
        "file_hash, status) VALUES (?, 'p1', 'a.pdf', 'pdf', 1, 'h', ?)",
        (file_id, status),
    )
    for img_id in image_ids:
        db.conn.execute(
            "INSERT INTO images (id, file_id, page_no, local_path, status, "
            "width_px, height_px) VALUES (?, ?, 0, 'x.png', ?, 200, 200)",
            (img_id, file_id, status),
        )
    db.conn.commit()


class StubVLM:
    """VLMClient 替身：记录调用，返回 seal（走自动归档分支，不触 ChromaDB）。"""
    calls: list[str] = []
    AUTO_ARCHIVE_CATEGORIES = ("qr_code", "signature", "seal")

    def __init__(self, api_url=None, api_key=None, model=None):
        pass

    async def describe_image(self, image_path: str, context_text=None) -> dict:
        StubVLM.calls.append(image_path)
        return {"description": "测试描述", "category": "seal"}


@pytest.fixture(autouse=True)
def _reset_stub():
    StubVLM.calls = []


class TestEnqueueDedup:
    def test_enqueue_file_dedup(self):
        async def scenario():
            worker = KnowledgeWorker(db=_make_db(), data_dir=".")
            await worker.enqueue_file("f1")
            await worker.enqueue_file("f1")
            assert worker._file_queue.qsize() == 1
            assert worker._file_enqueued == {"f1"}

        asyncio.run(scenario())

    def test_enqueue_image_dedup(self):
        async def scenario():
            worker = KnowledgeWorker(db=_make_db(), data_dir=".")
            await worker.enqueue_image("img1")
            await worker.enqueue_image("img1")
            assert worker._image_queue.qsize() == 1

        asyncio.run(scenario())

    def test_enqueue_image_force_bypasses_dedup(self):
        """force = 用户显式重试，始终入队（retry 端点依赖此语义）。"""
        async def scenario():
            worker = KnowledgeWorker(db=_make_db(), data_dir=".")
            await worker.enqueue_image("img1")
            await worker.enqueue_image("img1", force=True)
            assert worker._image_queue.qsize() == 2

        asyncio.run(scenario())


class TestScannerNoDuplicateEnqueue:
    def test_multi_round_scan_enqueues_each_pending_once(self):
        """扫描器多轮补投递，队列不随轮数增长（事故主根因回归）。"""
        async def scenario():
            db = _make_db()
            _insert_file_with_images(db, "f1", ["i1", "i2", "i3"])
            worker = KnowledgeWorker(
                db=db, data_dir=".",
                scan_initial_delay=0.01, scan_interval=0.02,
            )
            worker._running = True
            scanner = asyncio.create_task(worker._queue_scanner())
            await asyncio.sleep(0.2)  # 覆盖 ≥3 轮扫描
            scanner.cancel()
            await asyncio.gather(scanner, return_exceptions=True)

            assert worker._image_queue.qsize() == 3  # 不随轮数增长

        asyncio.run(scenario())


class TestArchivedDequeueGuard:
    def _image_row(self, db: Database, status: str):
        _insert_file_with_images(db, "f1", ["img1"], status=status)
        image_path = os.path.abspath(__file__)
        db.conn.execute(
            "UPDATE images SET local_path = ? WHERE id = 'img1'", (image_path,)
        )
        db.conn.commit()

    def test_archived_not_reprocessed_without_force(self, monkeypatch):
        """普通出队遇 archived 直接跳过——不调 VLM、状态不变（事故根因之二）。"""
        async def scenario():
            db = _make_db()
            self._image_row(db, status="archived")
            worker = KnowledgeWorker(db=db, data_dir=".")
            monkeypatch.setattr(tq, "VLMClient", StubVLM)

            await worker._process_image("img1", force=False)

            assert StubVLM.calls == []
            assert db.get_image("img1")["status"] == "archived"

        asyncio.run(scenario())

    def test_archived_reprocessed_with_force(self, monkeypatch):
        """force=True（用户显式重试）放行 archived，走完整解析。"""
        async def scenario():
            db = _make_db()
            self._image_row(db, status="archived")
            worker = KnowledgeWorker(db=db, data_dir=".")
            monkeypatch.setattr(tq, "VLMClient", StubVLM)

            await worker._process_image("img1", force=True)

            assert len(StubVLM.calls) == 1
            img = db.get_image("img1")
            assert img["vlm_description"] == "测试描述"
            assert img["status"] == "archived"  # seal → 自动归档终态

        asyncio.run(scenario())

    def test_pending_processed_without_force(self, monkeypatch):
        """普通路径不受守卫影响：pending 图片正常解析。"""
        async def scenario():
            db = _make_db()
            self._image_row(db, status="pending")
            worker = KnowledgeWorker(db=db, data_dir=".")
            monkeypatch.setattr(tq, "VLMClient", StubVLM)

            await worker._process_image("img1", force=False)

            assert len(StubVLM.calls) == 1

        asyncio.run(scenario())


class TestDedupSlotRelease:
    def test_worker_dequeue_releases_dedup_slot(self):
        """出队即释放防重槽位：处理期间 scanner 可重新投递仍为 pending 的条目。"""
        async def scenario():
            db = _make_db()
            _insert_file_with_images(db, "f1", ["img1"])
            worker = KnowledgeWorker(db=db, data_dir=".", max_image_concurrency=1)

            async def fake_process(image_id, force=False):
                pass

            worker._process_image = fake_process
            await worker.start()
            await worker.enqueue_image("img1")
            await asyncio.sleep(0.3)
            await worker.stop()

            assert "img1" not in worker._image_enqueued

        asyncio.run(scenario())

    def test_stop_clears_dedup_sets(self):
        """stop 清空防重集合：stop→start 复用实例时残留条目不挡新投递。"""
        async def scenario():
            worker = KnowledgeWorker(db=_make_db(), data_dir=".")
            await worker.start()
            await worker.enqueue_file("f1")
            await worker.enqueue_image("img1")
            await worker.stop()

            assert worker._file_enqueued == set()
            assert worker._image_enqueued == set()

        asyncio.run(scenario())
