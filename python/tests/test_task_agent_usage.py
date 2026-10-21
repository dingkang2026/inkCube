"""python/tests/test_task_agent_usage.py — task_agent 轮次 usage 事件契约回归（2.10 §4.5）。

run() 包裹 _run_inner 事件流：done 前插入扁平 usage 事件（data 即 usage 字段集，
与 iteration/answer_chunk 等事件同构——曾多包一层 usage 导致前端 metadata.usage
双层嵌套、UsageText 拒渲染，2026-10-16 修复）；finally 把同数据 attach 到最后一条
assistant 消息；无记录时两者都跳过。

TaskAgent.__init__ 构造 15 个工具实例，测试经 __new__ 跳过、手工注入 run()
所需最小状态（与 LLM/工具全链路解耦）。
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent.task_agent as task_agent_module
from agent.task_agent import TaskAgent

USAGE = {
    "prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
    "calls": 2, "models": "glm-5.3",
}


class _Db:
    """run() 触点的最小 DB 替身。"""

    def __init__(self, round_usage):
        self._round_usage = round_usage
        self.attached = []

    def get_usage_by_round(self, round_id):
        return self._round_usage

    def attach_usage_to_last_assistant(self, task_id, usage):
        self.attached.append((task_id, usage))


def _make_agent(db, inner_events):
    agent = TaskAgent.__new__(TaskAgent)
    agent.db = db
    agent._paused_tasks = set()
    agent._running_task_ids = set()
    agent._task_iterations = {}
    agent._run_outcomes = {}
    agent._task_messages = {}
    agent._cancel_events = {}

    async def _run_inner(task_id, user_message):
        for ev in inner_events:
            yield ev

    agent._run_inner = _run_inner
    return agent


@pytest.fixture(autouse=True)
def _quiet_activity(monkeypatch):
    """终态通知与本次契约无关，替换为 stub 隔离。"""
    monkeypatch.setattr(task_agent_module.activity_events, "complete_task", lambda *a, **k: None)
    monkeypatch.setattr(task_agent_module.activity_events, "fail_task", lambda *a, **k: None)


def _collect(events):
    async def _run():
        return [ev async for ev in events]
    return asyncio.run(_run())


def test_usage_event_flat_payload_before_done(monkeypatch):
    """done 前插入 usage 事件：data 即 usage 字段集（扁平，不再包 usage 键）。"""
    db = _Db(USAGE)
    agent = _make_agent(db, [{"type": "answer_chunk", "data": {"content": "hi"}},
                             {"type": "done", "data": {}}])
    events = _collect(agent.run("t1", "hello"))

    assert [e["type"] for e in events] == ["answer_chunk", "usage", "done"]
    usage_ev = events[1]   # 紧邻 done 之前（done 前插入，非流首）
    assert usage_ev["type"] == "usage"
    assert usage_ev["data"] == USAGE          # 扁平载荷本身
    assert "usage" not in usage_ev["data"]    # 不再嵌套
    # 收尾附着同数据（历史回放数据源）
    assert db.attached == [("t1", USAGE)]


def test_no_round_usage_no_event_no_attach():
    """该轮无记录（流式端点无 usage）→ 不发 usage 事件、不附着。"""
    db = _Db({})
    agent = _make_agent(db, [{"type": "done", "data": {}}])
    events = _collect(agent.run("t1", "hello"))

    assert [e["type"] for e in events] == ["done"]
    assert db.attached == []
