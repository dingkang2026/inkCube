"""python/tests/test_task_pause.py — 任务暂停/恢复健壮性回归测试。

回归背景（review 发现）：
1. 暂停落在工具执行中途时，保存的 in-memory 消息尾部是 assistant(tool_calls)
   而 tool result 缺失/不全；恢复时该序列被 OpenAI 兼容 API 以 400 拒绝，
   「恢复」必然失败 → pause_task 保存前按段修剪未配对消息。
2. 对非运行中任务调用 pause 会生成无 system prompt 的退化快照，
   污染「恢复」入口 → 端点拒绝（409 TASK_NOT_RUNNING / TASK_NOT_PAUSABLE）。
3. 暂停后残留的确认弹窗仍可放行 shell/skill 执行 → respond_* 在
   cancel_event 已 set 时把「允许」视为拒绝。
"""
import asyncio
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI
from fastapi.testclient import TestClient

import deps
from db.database import Database  # noqa: E402
from agent.task_agent import TaskAgent  # noqa: E402
from routers.tasks import router

TASK_ID = "ce255c24-64e3-40d3-8285-60e85d3fb8f8"


@pytest.fixture()
def db():
    database = Database(":memory:")
    database.initialize()
    return database


@pytest.fixture()
def agent(db):
    return TaskAgent(db)


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture()
def mock_deps(monkeypatch):
    db_mock = MagicMock()
    agent_mock = MagicMock()
    monkeypatch.setattr(deps, "db", db_mock)
    monkeypatch.setattr(deps, "task_agent", agent_mock)
    return db_mock, agent_mock


def _assistant_tool_calls(*tc_ids: str) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": tc_id, "type": "function",
             "function": {"name": "shell", "arguments": "{}"}}
            for tc_id in tc_ids
        ],
    }


def _tool_msg(call_id: str) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": "ok"}


class TestPruneUnpairedToolMessages:
    def test_paired_tool_calls_kept(self):
        msgs = [
            {"role": "user", "content": "hi"},
            _assistant_tool_calls("a1"),
            _tool_msg("a1"),
        ]
        assert TaskAgent._prune_unpaired_tool_messages(msgs) == msgs

    def test_trailing_unpaired_dropped(self):
        """暂停落在工具执行中：尾部 assistant(tool_calls) 无 result → 整段丢弃。"""
        msgs = [
            {"role": "user", "content": "hi"},
            _assistant_tool_calls("a1"),
            _tool_msg("a1"),
            _assistant_tool_calls("a2"),
        ]
        pruned = TaskAgent._prune_unpaired_tool_messages(msgs)
        assert msgs[0] in pruned
        assert _assistant_tool_calls("a1") in pruned
        assert all(m.get("tool_calls") is None or m["role"] != "assistant" or
                   m["tool_calls"] == _assistant_tool_calls("a1")["tool_calls"]
                   for m in pruned)
        assert len(pruned) == 3  # user + a1 assistant + a1 tool

    def test_partial_pair_dropped(self):
        """双工具调用只完成一个：不完整配对整段丢弃（恢复后重做当轮）。"""
        msgs = [
            _assistant_tool_calls("a1", "a2"),
            _tool_msg("a1"),
        ]
        pruned = TaskAgent._prune_unpaired_tool_messages(msgs)
        assert pruned == []

    def test_mixed_segments(self):
        """中间段不配对丢弃，前后段保留。"""
        msgs = [
            {"role": "user", "content": "q"},
            _assistant_tool_calls("a1"),
            _tool_msg("a1"),
            _assistant_tool_calls("a2", "a3"),
            _tool_msg("a2"),  # a3 缺失
            {"role": "user", "content": "again"},
        ]
        pruned = TaskAgent._prune_unpaired_tool_messages(msgs)
        assert pruned == [
            {"role": "user", "content": "q"},
            _assistant_tool_calls("a1"),
            _tool_msg("a1"),
            {"role": "user", "content": "again"},
        ]

    def test_plain_messages_unchanged(self):
        msgs = [{"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"}]
        assert TaskAgent._prune_unpaired_tool_messages(msgs) == msgs

    def test_empty_tool_call_ids_dropped(self):
        """tool_call_id 缺失的调用无法配对验证 → 丢弃（保守处理）。"""
        msgs = [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "", "type": "function",
                 "function": {"name": "shell", "arguments": "{}"}},
            ]},
            _tool_msg(""),
        ]
        assert TaskAgent._prune_unpaired_tool_messages(msgs) == []


class TestPauseTaskPrunesState:
    def test_pause_saves_pruned_messages(self, agent: TaskAgent):
        """pause_task 保存到 DB 的 state 必须是配对完整的消息序列。"""
        msgs = [
            {"role": "user", "content": "hi"},
            _assistant_tool_calls("a1"),
            _tool_msg("a1"),
            _assistant_tool_calls("a2"),  # 工具执行中被打断
        ]
        ok = agent.pause_task("t1", msgs, 2, [])
        assert ok is True
        saved = agent.db.load_agent_state("t1")
        assert saved is not None
        saved_msgs = saved["messages"]
        # 尾部未配对的 a2 被修剪，配对的 a1 保留
        assert len(saved_msgs) == 3
        assert saved["iteration"] == 2
        assert saved["auto_saved"] is True

    def test_pause_requests_cancel_and_marks_paused(self, agent: TaskAgent):
        """pause 置 paused 标记并触发 cancel（事件由 run() 预注册，与真实时序一致）。"""
        agent._cancel_events["t1"] = asyncio.Event()  # run() 开头的 _reset_cancel 产物
        agent.pause_task("t1", [{"role": "user", "content": "hi"}], 0, [])
        assert "t1" in agent._paused_tasks
        assert agent._cancel_events["t1"].is_set()

    def test_original_list_not_mutated(self, agent: TaskAgent):
        """修剪不得改动运行中 agent 持有的同一消息列表对象。"""
        msgs = [
            {"role": "user", "content": "hi"},
            _assistant_tool_calls("a1"),
        ]
        agent.pause_task("t1", msgs, 1, [])
        assert len(msgs) == 2  # 原列表不变


class TestConfirmDeniedAfterPause:
    def test_shell_confirm_denied_when_cancel_set(self, agent: TaskAgent):
        """暂停后残留弹窗点「允许」视为拒绝（不执行命令）。"""
        event = asyncio.Event()
        agent._pending_confirms["t1"] = {
            "command": "echo hi", "confirmed": False, "event": event,
        }
        agent._cancel_events["t1"] = asyncio.Event()
        agent._cancel_events["t1"].set()
        agent.respond_confirm("t1", True)
        assert event.is_set()
        assert agent._pending_confirms["t1"]["confirmed"] is False

    def test_shell_confirm_allowed_when_running(self, agent: TaskAgent):
        """正常运行（cancel 未 set）确认行为不变。"""
        event = asyncio.Event()
        agent._pending_confirms["t2"] = {
            "command": "echo hi", "confirmed": False, "event": event,
        }
        agent._cancel_events["t2"] = asyncio.Event()
        agent.respond_confirm("t2", True, allow_session=True)
        assert agent._pending_confirms["t2"]["confirmed"] is True
        assert "t2" in agent._session_auto_allow

    def test_skill_confirm_denied_when_cancel_set(self, agent: TaskAgent):
        event = asyncio.Event()
        agent._pending_skill_confirms["t3"] = {
            "skill_name": "x", "confirmed": False, "event": event,
        }
        agent._cancel_events["t3"] = asyncio.Event()
        agent._cancel_events["t3"].set()
        agent.respond_skill_confirm("t3", True)
        assert event.is_set()
        assert agent._pending_skill_confirms["t3"]["confirmed"] is False


class TestPauseEndpointGuards:
    def test_pause_non_running_rejected(self, client, mock_deps):
        """非运行任务暂停被拒绝：避免生成无 system prompt 的退化快照。"""
        db_mock, agent_mock = mock_deps
        agent_mock.is_task_running.return_value = False

        resp = client.post(f"/tasks/{TASK_ID}/pause")

        assert resp.status_code == 409
        assert resp.json()["code"] == "TASK_NOT_RUNNING"
        agent_mock.pause_task.assert_not_called()

    def test_pause_without_inmemory_messages_rejected(self, client, mock_deps):
        """运行集内但消息未就绪（system prompt 构建窗口）：拒绝而非 DB 退化快照。"""
        db_mock, agent_mock = mock_deps
        agent_mock.is_task_running.return_value = True
        agent_mock.get_messages.return_value = None

        resp = client.post(f"/tasks/{TASK_ID}/pause")

        assert resp.status_code == 409
        assert resp.json()["code"] == "TASK_NOT_PAUSABLE"
        agent_mock.pause_task.assert_not_called()

    def test_pause_running_task_ok(self, client, mock_deps):
        db_mock, agent_mock = mock_deps
        agent_mock.is_task_running.return_value = True
        agent_mock.get_iteration.return_value = 3
        agent_mock.get_messages.return_value = [{"role": "user", "content": "hi"}]
        agent_mock.pause_task.return_value = True

        resp = client.post(f"/tasks/{TASK_ID}/pause")

        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        agent_mock.pause_task.assert_called_once_with(
            TASK_ID, [{"role": "user", "content": "hi"}], 3, [],
        )


class TestCreateTaskQuota:
    def test_quota_reached_returns_409_with_code(self, client, mock_deps):
        """配额满返回 409 + TASK_QUOTA_REACHED（结构化错误，前端 i18n 渲染）。"""
        db_mock, _agent_mock = mock_deps
        db_mock.get_settings.return_value = {"task_max_count": 2}
        db_mock.count_tasks.return_value = 2

        resp = client.post("/tasks", json={"title": "t", "workspace_path": "", "mode": "coding"})

        assert resp.status_code == 409
        body = resp.json()
        assert body["code"] == "TASK_QUOTA_REACHED"
        db_mock.create_task.assert_not_called()

    def test_count_tasks_real_db_counts_non_deleted(self):
        """count_tasks 真实 SQL：与 list_tasks() 同可见性（非 deleted 全计入）。

        回归背景：配额检查原用 list_tasks() 全表载入取 count，已改 COUNT(*)。
        delete_task 为硬删，status != 'deleted' 过滤为防御性写法，保持同语义。"""
        db = Database(":memory:")
        db.initialize()
        db.create_task("a", "", "", "coding")
        db.create_task("b", "", "", "coding")
        db.archive_task(db.list_tasks()[0][0]["id"])
        assert db.count_tasks() == 2

    def test_quota_disabled_allows_create(self, client, mock_deps):
        db_mock, _agent_mock = mock_deps
        db_mock.get_settings.return_value = {"task_max_count": 0}
        db_mock.create_task.return_value = {"id": "t-new"}

        resp = client.post("/tasks", json={"title": "t", "workspace_path": "", "mode": "coding"})

        assert resp.status_code == 200
        assert resp.json() == {"id": "t-new"}
