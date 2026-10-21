# -*- coding: utf-8 -*-
"""python/tests/test_task_resume.py — 迭代预算耗尽后的断点续跑回归（P2，2026-10-14）。

回归背景（天眼查 25 轮耗尽任务实证）：
1. 迭代耗尽 fallback 路径不清理 agent_state（有意保留），前端「恢复」横幅
   据 tasks:hasState 显示 —— 但恢复分支沿用已耗尽的计数
   （range(saved=25, max_iter=25) 为空），点恢复立即再次耗尽「恢复空转」。
   修复：恢复 = 新一轮运行、全新预算（对齐 Gemini/OpenClaw 续跑语义）。
2. 旧耗尽文案「请尝试换一种方式描述需求」误导用户（实际是预算耗尽且
   进展可续）—— 改为指明轮数 + 「恢复」续跑指引。
"""
import asyncio
import json
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.database import Database  # noqa: E402
from agent import task_agent as ta_mod  # noqa: E402
from agent.task_agent import BUDGET_WARNING_LEFT, TaskAgent  # noqa: E402


def _stop_turn(text="最终回答"):
    """一轮 LLM 调用：content + finish(stop) → 模型自认完成，任务 done。"""
    return [
        {"type": "content", "delta": text},
        {"type": "finish", "finish_reason": "stop"},
    ]


def _tool_call_turn(tc_id="c1", name="mystery_tool"):
    """一轮 LLM 调用：tool_calls + finish(tool_calls) → 执行后 continue。

    name 用未注册工具：tools={} 与 skill_registry 均查不到 → 「未知工具」
    字符串结果，不触发确认弹窗 / venv / stuck 检测，循环可控。
    """
    return [
        {"type": "tool_calls", "tool_calls": [
            {"id": tc_id, "type": "function",
             "function": {"name": name, "arguments": "{}"}},
        ]},
        {"type": "finish", "finish_reason": "tool_calls"},
    ]


class _FakeLLM:
    """每轮 chat_stream 按 turns 顺序回放一组 chunk；超出后重放最后一组。"""

    def __init__(self, turns):
        self.turns = [list(t) for t in turns]
        self.calls = 0

    async def chat_stream(self, messages, tools=None):
        chunks = self.turns[min(self.calls, len(self.turns) - 1)]
        self.calls += 1
        for c in chunks:
            yield c


class _CapturingLLM(_FakeLLM):
    """额外记录每次 chat_stream 收到的 messages（断言 LLM 实际看到的历史）。"""

    def __init__(self, turns):
        super().__init__(turns)
        self.last_messages = None

    async def chat_stream(self, messages, tools=None):
        self.last_messages = messages
        async for c in super().chat_stream(messages, tools):
            yield c


@pytest.fixture()
def db():
    database = Database(":memory:")
    database.initialize()
    return database


def _make_agent(db):
    return TaskAgent(db)


def _prepare_task(db, *, max_iterations) -> str:
    """建任务并配置预算。create_task 自动生成 id（不接受外部 id），必须用返回值。"""
    task = db.create_task("t", "", "", "coding")
    db.save_settings({"api_key": "test-key", "task_max_iterations": max_iterations})
    return task["id"]


async def _collect(agen):
    events = []
    async for ev in agen:
        events.append(ev)
        if ev.get("type") == "done":
            break
    return events


def _patch_heavy_deps():
    """屏蔽 run() 的重依赖（system prompt 构建 / 工具实例化 / 外部 skill 加载）。"""
    return patch.multiple(
        ta_mod.TaskAgent,
        _build_system_prompt=staticmethod(lambda *a, **k: "SYS"),
        _create_task_tools=lambda *a, **k: {},
        _register_external_skills=lambda self, registry, tools: None,
    )


class TestResumeFreshBudget:
    def test_resume_resets_iteration_counter(self, db):
        """恢复不沿用已耗尽计数：saved=25 时循环从 0 重开（旧代码 range(25,25) 空转）。"""
        task_id = _prepare_task(db, max_iterations=25)
        db.save_agent_state(task_id, {
            "messages": [{"role": "user", "content": "hi"}],
            "iteration": 25,
            "recent_errors": [],
            "auto_saved": True,
        })
        agent = _make_agent(db)

        with _patch_heavy_deps(), patch.object(
                ta_mod, "LLMClient", lambda settings: _FakeLLM([_stop_turn()])):
            events = asyncio.run(_collect(agent.run(task_id, "")))

        thought = next(e for e in events if e["type"] == "thought")
        assert "从第 25 轮恢复" in thought["data"]["content"]
        assert "25 轮预算" in thought["data"]["content"]       # 新预算明示
        iters = [e["data"]["iteration"] for e in events if e["type"] == "iteration"]
        assert iters and iters[0] == 0                         # 计数重置（非 25）
        assert events[-1]["type"] == "done"

    def test_resume_after_exhaustion_can_run_full_budget(self, db):
        """耗尽(25)后恢复：新一轮预算内可再跑满 25 轮而非 0 轮（空转回归）。"""
        task_id = _prepare_task(db, max_iterations=2)
        db.save_agent_state(task_id, {
            "messages": [{"role": "user", "content": "hi"}],
            "iteration": 2,                                    # 已耗尽
            "recent_errors": [],
            "auto_saved": True,
        })
        agent = _make_agent(db)
        # 第 1 轮 stop 完成；若预算空转则 LLM 一次都不会被调用
        fake = _FakeLLM([_stop_turn()])

        with _patch_heavy_deps(), patch.object(ta_mod, "LLMClient", lambda settings: fake):
            events = asyncio.run(_collect(agent.run(task_id, "")))

        assert fake.calls == 1                                 # LLM 被真实调用过
        assert events[-1]["type"] == "done"


class TestExhaustionGuidance:
    def test_exhaustion_message_guides_resume(self, db):
        """耗尽文案指明轮数与恢复入口，不再误导「换一种方式描述需求」。"""
        task_id = _prepare_task(db, max_iterations=1)
        agent = _make_agent(db)

        with _patch_heavy_deps(), patch.object(
                ta_mod, "LLMClient", lambda settings: _FakeLLM([_tool_call_turn()])):
            events = asyncio.run(_collect(agent.run(task_id, "")))

        answers = [e for e in events if e["type"] == "answer_chunk"]
        assert answers, "耗尽必须有 answer_chunk"
        text = "".join(e["data"]["content"] for e in answers)
        assert "1 轮" in text                                  # 实际轮数
        assert "恢复" in text                                  # 续跑指引
        assert "换一种方式描述需求" not in text
        assert events[-1]["type"] == "done"

    def test_exhaustion_keeps_agent_state(self, db):
        """耗尽不清 agent_state：前端 tasks:hasState 依赖它显示「恢复」横幅。"""
        task_id = _prepare_task(db, max_iterations=1)
        agent = _make_agent(db)

        with _patch_heavy_deps(), patch.object(
                ta_mod, "LLMClient", lambda settings: _FakeLLM([_tool_call_turn()])):
            asyncio.run(_collect(agent.run(task_id, "")))

        saved = db.load_agent_state(task_id)
        assert saved is not None                               # 断点保留
        assert saved["messages"], "state 必须含完整消息序列"
        assert saved["iteration"] == 1                         # 最后一轮 auto-save


class TestBudgetSoftLanding:
    """预算软着陆（2026-10-16，1.1 §5.6）：剩 3 轮时一次性注入收束提醒。"""

    def _run_tool_loops(self, db, *, max_iterations):
        task_id = _prepare_task(db, max_iterations=max_iterations)
        agent = _make_agent(db)
        fake = _FakeLLM([_tool_call_turn()])
        with _patch_heavy_deps(), patch.object(
                ta_mod, "LLMClient", lambda settings: fake):
            events = asyncio.run(_collect(agent.run(task_id, "hi")))
        return events, db, task_id

    def test_warning_injected_exactly_once(self, db):
        """剩 3 轮时注入一次提醒（thought 事件 + 断点 state 各恰好一条）。"""
        events, db, task_id = self._run_tool_loops(db, max_iterations=5)

        warn_thoughts = [e for e in events if e["type"] == "thought"
                         and "迭代预算上限" in e["data"]["content"]]
        assert len(warn_thoughts) == 1, "thought 提醒必须恰好一次（不能逐轮重复膨胀上下文）"

        saved = db.load_agent_state(task_id)
        assert saved is not None, "耗尽不清断点（回归前提）"
        warn_msgs = [m for m in saved["messages"]
                     if m.get("role") == "user"
                     and str(m.get("content", "")).startswith("[系统提示] 迭代预算")]
        assert len(warn_msgs) == 1
        assert f"仅剩 {BUDGET_WARNING_LEFT} 轮" in warn_msgs[0]["content"]

    def test_no_warning_for_tiny_budget(self, db):
        """max_iter < 3：预算过小不注入（提醒本身浪费轮次）。"""
        events, _, _ = self._run_tool_loops(db, max_iterations=2)
        warn_thoughts = [e for e in events if e["type"] == "thought"
                         and "迭代预算上限" in e["data"]["content"]]
        assert not warn_thoughts

    def test_resume_filters_stale_budget_warning(self, db):
        """通道 A 恢复：上一轮预算的旧提醒不进入新回合（新预算下旧轮数提示失效）。"""
        task_id = _prepare_task(db, max_iterations=25)
        db.save_agent_state(task_id, {
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "user", "content":
                    "[系统提示] 迭代预算仅剩 3 轮。请：保存中间产物"},
            ],
            "iteration": 25,
            "recent_errors": [],
            "auto_saved": True,
        })
        agent = _make_agent(db)
        fake = _CapturingLLM([_stop_turn()])

        with _patch_heavy_deps(), patch.object(
                ta_mod, "LLMClient", lambda settings: fake):
            asyncio.run(_collect(agent.run(task_id, "")))

        user_contents = [m["content"] for m in fake.last_messages
                         if m["role"] == "user"]
        assert "hi" in user_contents, "普通历史正常回放"
        assert not any(str(c).startswith("[系统提示] 迭代预算") for c in user_contents), \
            "旧预算提醒必须被过滤"


class TestRebuildHistoryFromDb:
    """通道 B 重建摘要线（2026-10-16，1.1 §5.3）：observation 压单行路标。"""

    def test_replays_text_and_summarizes_observations(self):
        history = [
            {"role": "user", "content": "做 PPT"},
            {"role": "assistant", "content": "好的"},
            {"role": "observation", "content": "raw-full",
             "metadata": {"tool_name": "shell",
                          "tool_output": json.dumps(
                              {"exit_code": 0, "stdout": "wrote 01.svg\n..."})}},
            {"role": "assistant", "content": "完成"},
        ]
        rebuilt = TaskAgent._rebuild_history_from_db(history)

        assert rebuilt[0] == {"role": "user", "content": "做 PPT"}
        assert rebuilt[1] == {"role": "assistant", "content": "好的"}
        block = rebuilt[2]
        assert block["role"] == "user"
        assert "[上次运行工具活动摘要]" in block["content"]
        assert "shell → exit=0" in block["content"], "摘要行必须带工具名与状态"
        assert "wrote 01.svg" in block["content"]
        assert "raw-full" not in block["content"], "摘要不回传全文（tool_output 优先于 content）"
        assert rebuilt[3] == {"role": "assistant", "content": "完成"}

    def test_consecutive_observations_grouped_into_one_block(self):
        history = [
            {"role": "user", "content": "hi"},
            {"role": "observation", "content": "a",
             "metadata": {"tool_name": "shell", "tool_output": "{\"exit_code\": 0}"}},
            {"role": "observation", "content": "b",
             "metadata": {"tool_name": "file_ops", "tool_output": "{\"ok\": true}"}},
            {"role": "assistant", "content": "done"},
        ]
        rebuilt = TaskAgent._rebuild_history_from_db(history)
        blocks = [m for m in rebuilt if "[上次运行工具活动摘要]" in m["content"]]
        assert len(blocks) == 1, "连续 observation 聚合为单个摘要块"
        assert "shell" in blocks[0]["content"] and "file_ops" in blocks[0]["content"]

    def test_trailing_observations_flushed(self):
        history = [
            {"role": "user", "content": "hi"},
            {"role": "observation", "content": "tail",
             "metadata": {"tool_name": "file_ops", "tool_output": ""}},
        ]
        rebuilt = TaskAgent._rebuild_history_from_db(history)
        assert len(rebuilt) == 2, "尾部 observation 也必须冲刷为摘要块"
        assert "file_ops" in rebuilt[1]["content"]

    def test_filters_stale_budget_warning(self):
        """通道 B 重建同样过滤旧预算提醒（与通道 A 恢复一致）。"""
        history = [
            {"role": "user", "content": "hi"},
            {"role": "user", "content": "[系统提示] 迭代预算仅剩 3 轮"},
            {"role": "assistant", "content": "ok"},
        ]
        rebuilt = TaskAgent._rebuild_history_from_db(history)
        assert [m["content"] for m in rebuilt] == ["hi", "ok"]

    def test_non_text_roles_dropped(self):
        history = [
            {"role": "user", "content": "hi"},
            {"role": "reasoning", "content": "chain of thought"},
            {"role": "thought", "content": "status"},
            {"role": "assistant", "content": "ok"},
        ]
        rebuilt = TaskAgent._rebuild_history_from_db(history)
        assert [m["content"] for m in rebuilt] == ["hi", "ok"]


class TestResumeGateLeakRouter:
    """resume 早退不得占用任务闸门（2026-10-20 review T-1 回归，router 层）。

    旧实现：try_acquire_task 之后才 load_agent_state，早退 return 不经过
    run() 的 finally —— _running_task_ids 永久残留该任务：后续 chat 恒 409、
    并发槽被侵占、Activity 假报运行中、scheduler R2 永久让路，唯一恢复手段
    是重启进程。损坏 state_json（json.loads 异常 → 500）是同族泄漏路径。
    锁定行为：resume 校验前置到 acquire 之前，任何早退都不占用闸门。
    """

    @pytest.fixture()
    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routers.tasks import router as tasks_router

        app = FastAPI()
        app.include_router(tasks_router)
        return TestClient(app)

    @pytest.fixture()
    def wired(self, monkeypatch, db):
        """真实 db（可断言闸门/异常路径）+ MagicMock agent（可断言未 acquire）。"""
        from unittest.mock import MagicMock
        import deps
        import routers.tasks as tasks_mod

        agent_mock = MagicMock()
        monkeypatch.setattr(deps, "db", db)
        monkeypatch.setattr(deps, "task_agent", agent_mock)
        # 跳过 LLM 配置门（get_features_cached 有 TTL 缓存，跨测试污染）
        monkeypatch.setattr(tasks_mod, "ensure_ready", lambda feature: None)
        return agent_mock

    def _post_chat(self, client, task_id):
        return client.post(
            f"/tasks/{task_id}/chat",
            json={"task_id": task_id, "message": "", "resume": True},
        )

    def test_resume_without_state_returns_409_without_acquiring_gate(self, client, wired, db):
        """无 state 的 resume：409 结构化错误，且 try_acquire_task 从未被调用。"""
        task_id = db.create_task("t", "", "", "coding")["id"]
        resp = self._post_chat(client, task_id)

        assert resp.status_code == 409
        assert resp.json()["code"] == "NO_RESUMABLE_STATE"
        wired.try_acquire_task.assert_not_called()
        assert task_id not in wired._running_task_ids

    def test_resume_with_corrupted_state_returns_409_not_500(self, client, wired, db):
        """损坏 state_json：acquire 前兜住 → 409（旧实现 500 且泄漏闸门）。"""
        task_id = db.create_task("t", "", "", "coding")["id"]
        db.save_agent_state(task_id, {"messages": [], "iteration": 0})
        db.conn.execute(
            "UPDATE task_agent_state SET state_json = '{broken-json' WHERE task_id = ?",
            (task_id,),
        )
        db.conn.commit()

        resp = self._post_chat(client, task_id)

        assert resp.status_code == 409
        assert resp.json()["code"] == "NO_RESUMABLE_STATE"
        wired.try_acquire_task.assert_not_called()

    def test_resume_with_valid_state_still_acquires_gate(self, client, wired, db):
        """合法 state 的 resume 照常走 acquire（守卫不得误伤正常恢复）。"""
        task_id = db.create_task("t", "", "", "coding")["id"]
        db.save_agent_state(task_id, {
            "messages": [{"role": "user", "content": "hi"}],
            "iteration": 3,
        })
        resp = self._post_chat(client, task_id)

        wired.try_acquire_task.assert_called_once_with(task_id)
        # StreamingResponse 已开始（200）；SSE 体由 agent.run 产出，mock 下为空流
        assert resp.status_code == 200
