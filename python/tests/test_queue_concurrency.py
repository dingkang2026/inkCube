"""python/tests/test_queue_concurrency.py — 解析并发热更新双向生效回归。

回归背景（review 2026-10-20 K-1）：update_concurrency 只换 Semaphore，worker
数量在 start() 按启动时设置固定——实际并发 = min(worker 数, semaphore)，
设置页把解析并发 2→4 保存后实际并发仍为 2（「调大无效」），重启才生效；
调小方向虽生效，但设置保存的热更新承诺（settings.py 保存即调用）双向失信。

锁定行为：semaphore 换新即时约束；运行中调大方向补足 worker，调小方向由
semaphore 自然限流（worker 供给只增不减）；stop→start 复用实例时在岗计数
归零，补足判据不失真。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.database import Database
from knowledge.task_queue import KnowledgeWorker


def _make_db() -> Database:
    db = Database(db_path=":memory:")
    db.initialize()
    return db


class TestConcurrencyHotReload:
    def test_increase_spawns_workers_to_reach_new_limit(self):
        """运行中调大 2→4：补足 file worker，新上限真实可达（K-1 根因回归）。"""
        async def scenario():
            worker = KnowledgeWorker(db=_make_db(), data_dir=".",
                                     max_file_concurrency=2, max_image_concurrency=1,
                                     max_compile_concurrency=1)
            await worker.start()
            assert worker._file_worker_count == 2

            worker.update_concurrency(4, 1, 1)
            await asyncio.sleep(0)  # 让新 worker task 得以调度

            assert worker._file_worker_count == 4, "调大方向必须补足 worker"
            assert worker._image_worker_count == 1
            assert worker._compile_worker_count == 1
            # _tasks = file4 + img1 + compile1 + scanner1
            assert len(worker._tasks) == 7
            await worker.stop()

        asyncio.run(scenario())

    def test_decrease_swaps_semaphore_without_killing_workers(self):
        """运行中调小 4→1：semaphore 立即约束，多余 worker 空闲（无害）。"""
        async def scenario():
            worker = KnowledgeWorker(db=_make_db(), data_dir=".",
                                     max_file_concurrency=4, max_image_concurrency=1,
                                     max_compile_concurrency=1)
            await worker.start()
            worker.update_concurrency(1, 1, 1)

            assert worker._file_sem._value == 1, "调小方向由 semaphore 即时生效"
            assert worker._file_worker_count == 4, "不回收 worker（只增不减）"
            await worker.stop()

        asyncio.run(scenario())

    def test_update_before_start_only_swaps_semaphore(self):
        """未启动（无事件循环）：只换 semaphore，不尝试 create_task。"""
        worker = KnowledgeWorker(db=_make_db(), data_dir=".",
                                 max_file_concurrency=2, max_image_concurrency=2,
                                 max_compile_concurrency=2)
        worker.update_concurrency(5, 5, 5)

        assert worker._file_sem._value == 5
        assert worker._file_worker_count == 0, "未启动不得 spawn"
        assert worker._max_file_concurrency == 5

    def test_stop_resets_worker_counts_for_reuse(self):
        """stop→start 复用实例：在岗计数归零，重启后补足判据不失真。"""
        async def scenario():
            worker = KnowledgeWorker(db=_make_db(), data_dir=".",
                                     max_file_concurrency=2, max_image_concurrency=1,
                                     max_compile_concurrency=1)
            await worker.start()
            assert worker._file_worker_count == 2
            await worker.stop()
            assert worker._file_worker_count == 0, "stop 必须归零在岗计数"

            await worker.start()
            assert worker._file_worker_count == 2, "重启后重新按当前配置起 worker"
            await worker.stop()

        asyncio.run(scenario())

    def test_shrink_then_grow_reconciles_to_latest_setting(self):
        """先调小再调大：补足以最新配置为准（计数不因调小路径膨胀）。"""
        async def scenario():
            worker = KnowledgeWorker(db=_make_db(), data_dir=".",
                                     max_file_concurrency=2, max_image_concurrency=1,
                                     max_compile_concurrency=1)
            await worker.start()
            worker.update_concurrency(1, 1, 1)   # 调小：不 spawn 也不回收
            worker.update_concurrency(3, 1, 1)   # 调大：从 2（在岗）补到 3

            assert worker._file_worker_count == 3
            await worker.stop()

        asyncio.run(scenario())
