"""python/tests/test_shadow_task_isolation.py — 2.11 E3 T3: 影子任务隔离（D2）。

影子任务（定时订阅执行）三重隔离：
①create_task 带 schedule_id 落列可查；
②list_tasks 任何参数组合恒排除影子行（服务端排除，前端零特判），
  get_task 直查保留（只读回放数据源）；
③agent 终态对影子任务不发 task 通知（通知由调度器 schedule_run 负责，
  防双通知；普通任务与 ppt 跳过语义并列）。

agent 终态分支用 mock _run_inner 驱动真实 run() 的 finally 路径，
monkeypatch activity_events 记录调用，不触真实 LLM。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import activity_events
from agent.task_agent import TaskAgent
from db.database import Database


@pytest.fixture
def tmp_db():
    database = Database(db_path=":memory:")
    database.initialize()
    yield database
    database.conn.close()


@pytest.fixture
def agent(tmp_db):
    return TaskAgent(tmp_db)


def test_create_task_schedule_id_persists(tmp_db, tmp_path):
    s = tmp_db.create_schedule("a", "p", "general", "general", "daily", "09:00")
    shadow = tmp_db.create_task("影子", data_dir=str(tmp_path),
                                mode="general", schedule_id=s["id"])
    human = tmp_db.create_task("人工", data_dir=str(tmp_path))
    assert tmp_db.get_task(shadow["id"])["schedule_id"] == s["id"]
    assert tmp_db.get_task(human["id"])["schedule_id"] is None


def test_list_tasks_excludes_shadow_every_param(tmp_db, tmp_path):
    s = tmp_db.create_schedule("a", "p", "general", "general", "daily", "09:00")
    shadow = tmp_db.create_task("影子", data_dir=str(tmp_path),
                                mode="general", schedule_id=s["id"])
    tmp_db.create_task("人工", data_dir=str(tmp_path))
    combos = [{}, {"status": "active"}, {"origin": "chat"},
              {"limit": 10}, {"status": "active", "origin": "chat", "limit": 10}]
    for kwargs in combos:
        rows, _ = tmp_db.list_tasks(**kwargs)
        assert all(r["id"] != shadow["id"] for r in rows), kwargs
    # get_task 直查保留（ReadView「打开原任务」/ 执行回放数据源）
    assert tmp_db.get_task(shadow["id"])["title"] == "影子"


def _run_once(agent, task_id):
    """mock _run_inner 驱动真实 run() 的 finally 终态通知分支。"""
    async def fake_inner(tid, msg):
        yield {"type": "done", "data": {}}
    agent._run_inner = fake_inner

    async def scenario():
        async for _ev in agent.run(task_id, "go"):
            pass
    asyncio.run(scenario())


def test_agent_terminal_notify_skips_shadow(tmp_db, tmp_path, monkeypatch, agent):
    calls = []
    monkeypatch.setattr(activity_events, "complete_task",
                        lambda db, tid: calls.append(("complete", tid)))
    monkeypatch.setattr(activity_events, "fail_task",
                        lambda db, tid, error_message=None:
                        calls.append(("fail", tid)))
    # 对照组：普通任务照常发终态通知
    human = tmp_db.create_task("人工", data_dir=str(tmp_path))
    _run_once(agent, human["id"])
    assert ("complete", human["id"]) in calls
    # 影子任务不发 task 通知（防双通知：调度器已发 schedule_run）
    s = tmp_db.create_schedule("a", "p", "general", "general", "daily", "09:00")
    shadow = tmp_db.create_task("影子", data_dir=str(tmp_path),
                                mode="general", schedule_id=s["id"])
    _run_once(agent, shadow["id"])
    assert not any(tid == shadow["id"] for _, tid in calls)
