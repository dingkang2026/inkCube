"""python/tests/test_queue_pause.py — 后台队列暂停/恢复（0.4 §队列暂停）。

覆盖两类长时活动的暂停语义（wiki 编译无队列暂停 0.4 §6.7 修订、报告生成
无队列暂停 2.5 §3.1 修订——均已有任务/报告级等价出口）：暂停 = 不再开始新任务。
- worker 闸门（file/image）：暂停时任务挂起在闸门外，恢复后继续；
- /knowledge/queue/pause 端点：写 settings（持久化）+ 通知 worker。
报告级暂停（pause_requested + 章节边界协作生效）见 test_report_pause.py。
"""
import asyncio
import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.database import Database
from knowledge.task_queue import KnowledgeWorker


class TestWorkerPauseGates:
    def test_file_pause_gates_processing(self):
        async def scenario():
            # 真 DB（内存）：start() 里的 _reset_interrupted_items 会扫 files/images 表
            db = Database(db_path=":memory:")
            db.initialize()
            worker = KnowledgeWorker(db=db, data_dir=".")
            calls: list[str] = []

            async def fake_process(file_id):
                calls.append(file_id)

            worker._process_file = fake_process
            worker.set_file_paused(True)
            await worker.start()
            await worker.enqueue_file("f1")
            await asyncio.sleep(0.2)
            assert calls == []  # 暂停中：不开始新任务

            worker.set_file_paused(False)
            await asyncio.sleep(0.3)
            assert calls == ["f1"]  # 恢复后从闸门放行
            await worker.stop()

        asyncio.run(scenario())

    def test_image_pause_gates_processing(self):
        async def scenario():
            db = Database(db_path=":memory:")
            db.initialize()
            worker = KnowledgeWorker(db=db, data_dir=".")
            calls: list[str] = []

            async def fake_process(image_id, force=False):
                calls.append(image_id)

            worker._process_image = fake_process
            worker.set_image_paused(True)
            await worker.start()
            await worker.enqueue_image("img1")
            await asyncio.sleep(0.2)
            assert calls == []

            worker.set_image_paused(False)
            await asyncio.sleep(0.3)
            assert calls == ["img1"]
            await worker.stop()

        asyncio.run(scenario())

    def test_inflight_task_finishes_after_pause(self):
        """暂停不打断在飞任务：已过闸门的任务跑完为止。"""
        async def scenario():
            db = Database(db_path=":memory:")
            db.initialize()
            worker = KnowledgeWorker(db=db, data_dir=".")
            finished: list[str] = []
            release = asyncio.Event()

            async def slow_process(file_id):
                await release.wait()
                finished.append(file_id)

            worker._process_file = slow_process
            await worker.start()
            await worker.enqueue_file("slow1")
            await asyncio.sleep(0.2)  # 让任务进入 _process_file
            worker.set_file_paused(True)
            await worker.enqueue_file("f2")
            await asyncio.sleep(0.2)
            # 在飞任务未被打断（尚未 finish 但仍占用），f2 未开始
            release.set()
            await asyncio.sleep(0.2)
            assert finished == ["slow1"]  # 在飞跑完，暂停中的 f2 未动
            await worker.stop()

        asyncio.run(scenario())


class TestQueuePauseEndpoint:
    def test_pause_endpoint_writes_settings_and_notifies_worker(self, monkeypatch):
        database = Database(db_path=":memory:")
        database.initialize()
        import deps
        monkeypatch.setattr(deps, "db", database, raising=False)

        recorded: dict = {}

        class FakeWorker:
            def set_file_paused(self, p):
                recorded["parsing"] = p

            def set_image_paused(self, p):
                recorded["describe"] = p

        monkeypatch.setattr(deps, "worker", FakeWorker(), raising=False)

        from routers.knowledge import QueuePauseRequest, set_queue_pause

        result = asyncio.run(set_queue_pause(QueuePauseRequest(kind="parsing", paused=True)))
        assert result == {"kind": "parsing", "paused": True}
        assert database.get_flag("parsing_paused") is True
        assert recorded["parsing"] is True

        # 恢复
        asyncio.run(set_queue_pause(QueuePauseRequest(kind="describe", paused=False)))
        assert database.get_flag("describe_paused") is False
        assert recorded["describe"] is False

    def test_pause_endpoint_rejects_unknown_kind(self, monkeypatch):
        database = Database(db_path=":memory:")
        database.initialize()
        import deps
        monkeypatch.setattr(deps, "db", database, raising=False)
        monkeypatch.setattr(deps, "worker", None, raising=False)

        from routers.knowledge import QueuePauseRequest, set_queue_pause

        with pytest.raises(HTTPException) as ei:
            asyncio.run(set_queue_pause(QueuePauseRequest(kind="bogus", paused=True)))
        assert ei.value.status_code == 422

    def test_worker_none_is_tolerated(self, monkeypatch):
        """worker 未初始化（启动早期）时端点仍可写 settings。"""
        database = Database(db_path=":memory:")
        database.initialize()
        import deps
        monkeypatch.setattr(deps, "db", database, raising=False)
        monkeypatch.setattr(deps, "worker", None, raising=False)

        from routers.knowledge import QueuePauseRequest, set_queue_pause

        result = asyncio.run(set_queue_pause(QueuePauseRequest(kind="describe", paused=True)))
        assert result == {"kind": "describe", "paused": True}
        assert database.get_flag("describe_paused") is True
