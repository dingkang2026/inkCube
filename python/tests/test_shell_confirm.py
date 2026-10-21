# -*- coding: utf-8 -*-
"""python/tests/test_shell_confirm.py — shell 确认三档语义 + PPT 任务命令静默回归。

回归背景（2.3 §3.3，2026-10-16）：
1. E1 引入 PPT 任务命令静默时，task_origin 判定被插进 _handle_fc_iteration
   但变量定义在 _run_inner —— 任何 FC 任务执行到 shell 确认分支即 NameError。
   修复：task_origin 作为参数传入（默认 "chat"，调用点唯一且已传参）。
2. E1 验收要求 PPT 任务全程零命令确认弹窗：百轮级工作流下逐条确认不可用，
   origin='ppt' 必须强制 auto，覆盖用户 task_shell_confirm_mode 三档设置
   与 task_auto_confirm_shell 开关。
3. review 修复（m3）：skill_runner / git_ops commit / 外部 skill 三处确认
   同样纳入 origin=ppt 静默链（此前被泵丢弃后空等 120/180s 再 UserDenied，
   浪费轮次）；chat 任务的确认语义不变（对照组锁定）。
"""
import asyncio
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.database import Database  # noqa: E402
from agent import task_agent as ta_mod  # noqa: E402
from agent.task_agent import TaskAgent  # noqa: E402


def _stop_turn(text="最终回答"):
    """一轮 LLM 调用：content + finish(stop) → 模型自认完成，任务 done。"""
    return [
        {"type": "content", "delta": text},
        {"type": "finish", "finish_reason": "stop"},
    ]


def _shell_turn(command="npm install"):
    """一轮 shell tool_call（tools={} → 「未知工具」结果，不触发 venv/stuck）。"""
    return [
        {"type": "tool_calls", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "shell",
                          "arguments": '{"command": "%s"}' % command}},
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


@pytest.fixture()
def db():
    database = Database(":memory:")
    database.initialize()
    return database


def _prepare_task(db: Database, *, origin="chat", settings=None) -> str:
    """建任务（可指定 origin）并写入确认相关设置（save_settings 逐键合并）。"""
    task = db.create_task("t", "", "", "coding", origin=origin)
    db.save_settings({
        "api_key": "test-key",
        "task_max_iterations": 5,
        **(settings or {}),
    })
    return task["id"]


def _patch_heavy_deps():
    """屏蔽 run() 的重依赖（system prompt 构建 / 工具实例化 / 外部 skill 加载）。"""
    return patch.multiple(
        ta_mod.TaskAgent,
        _build_system_prompt=staticmethod(lambda *a, **k: "SYS"),
        _create_task_tools=lambda *a, **k: {},
        _register_external_skills=lambda self, registry, tools: None,
    )


class _FakeConfirmTool:
    """带确认分支的内置工具桩（skill_runner / git_ops 共用）。"""

    def __init__(self):
        self.calls = []

    def _refresh_cache(self):
        pass

    def _find_skill(self, name):
        return {"name": name, "needs_confirm": True}

    async def pre_execute(self, **params):
        return None

    async def execute(self, **params):
        self.calls.append(params)
        return "tool-ok"

    async def post_execute(self, result, **params):
        return result


def _skill_runner_turn(skill="third-party-skill"):
    return [
        {"type": "tool_calls", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "skill_runner",
                          "arguments": '{"skill": "%s"}' % skill}},
        ]},
        {"type": "finish", "finish_reason": "tool_calls"},
    ]


def _git_commit_turn(msg="init"):
    return [
        {"type": "tool_calls", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "git_ops",
                          "arguments": '{"action": "commit", "args": "%s"}' % msg}},
        ]},
        {"type": "finish", "finish_reason": "tool_calls"},
    ]


class _FakeRegistry:
    """SkillRegistry 桩：注册步骤 noop。被测面只是确认判定链——桩经
    tools.get() 直接命中，不经过注册表；真实包装（BuiltinToolAdapter）
    要求完整工具协议，桩不具备也不需要。"""

    def register_builtin_tools(self, tools):
        pass

    def get(self, name):
        return None

    def to_function_definitions(self):
        return []


def _run_with_tools(db: Database, task_id: str, turns, tools: dict):
    """同 _run，但注入自定义工具表（skill/git 确认分支需要桩实例）。"""
    agent = TaskAgent(db)
    with patch.multiple(
        ta_mod.TaskAgent,
        _build_system_prompt=staticmethod(lambda *a, **k: "SYS"),
        _create_task_tools=lambda *a, **k: tools,
        _register_external_skills=lambda self, registry, tools_: None,
    ), patch.object(ta_mod, "LLMClient", lambda settings: _FakeLLM(turns)), \
       patch.object(ta_mod, "SkillRegistry", _FakeRegistry):
        return asyncio.run(_collect(agent.run(task_id, ""), agent, task_id))


async def _collect(agen, agent: TaskAgent, task_id: str):
    """收集事件直到 done；确认事件按通道自动「允许」放行（模拟用户点击）。

    shell/git commit 走 respond_confirm（_pending_confirms），
    skill 走 respond_skill_confirm（_pending_skill_confirms）——两个通道
    不互通，调错方法 event 永不置位（等 180s 超时）。
    """
    events = []
    async for ev in agen:
        events.append(ev)
        if ev.get("type") == "confirm_shell":
            agent.respond_confirm(task_id, True)
        elif ev.get("type") == "confirm_skill":
            agent.respond_skill_confirm(task_id, True)
        if ev.get("type") == "done":
            break
    return events


def _run(db: Database, task_id: str, turns):
    agent = TaskAgent(db)
    with _patch_heavy_deps(), patch.object(
            ta_mod, "LLMClient", lambda settings: _FakeLLM(turns)):
        return asyncio.run(_collect(agent.run(task_id, ""), agent, task_id))


class TestShellConfirmModes:
    def test_chat_strict_asks_confirmation(self, db):
        """chat + strict：shell 必弹确认（对照组：守 strict 语义不失效）。"""
        task_id = _prepare_task(db, settings={"task_shell_confirm_mode": "strict"})
        events = _run(db, task_id, [_shell_turn(), _stop_turn()])

        confirms = [e for e in events if e["type"] == "confirm_shell"]
        assert confirms and confirms[0]["data"]["command"] == "npm install"
        assert events[-1]["type"] == "done"

    def test_chat_auto_confirm_shell_overrides_strict(self, db):
        """chat + task_auto_confirm_shell=1：提升为 auto 不弹（三档覆盖顺序）。"""
        task_id = _prepare_task(db, settings={
            "task_shell_confirm_mode": "strict",
            "task_auto_confirm_shell": True,
        })
        events = _run(db, task_id, [_shell_turn(), _stop_turn()])

        assert not [e for e in events if e["type"] == "confirm_shell"]
        assert events[-1]["type"] == "done"

    def test_ppt_origin_silent_under_strict(self, db):
        """ppt + strict：零确认弹窗（2.3 §3.3 命令静默，覆盖用户三档设置）。

        回归锚点：task_origin 作用域断裂时此路径 NameError（全事件流失常）。
        """
        task_id = _prepare_task(db, origin="ppt", settings={
            "task_shell_confirm_mode": "strict",
        })
        events = _run(db, task_id, [_shell_turn(), _stop_turn()])

        assert not [e for e in events if e["type"] == "confirm_shell"]
        assert not [e for e in events if e["type"] == "confirm_expired"]
        assert events[-1]["type"] == "done"

    def test_ppt_origin_silent_even_auto_confirm_off(self, db):
        """ppt + task_auto_confirm_shell 显式 False：仍静默（origin 最高优先）。"""
        task_id = _prepare_task(db, origin="ppt", settings={
            "task_shell_confirm_mode": "strict",
            "task_auto_confirm_shell": False,
        })
        events = _run(db, task_id, [_shell_turn(), _stop_turn()])

        assert not [e for e in events if e["type"] == "confirm_shell"]
        assert events[-1]["type"] == "done"


class TestPptOriginSilentOtherConfirms:
    """m3：skill_runner / git commit 确认同样纳入 origin=ppt 静默链。

    此前这两处确认在 ppt 任务中触发后被泵丢弃（confirm 事件无人应答），
    空等 120s 超时再按 UserDenied 跳过——不卡死但浪费轮次与 token。
    """

    def test_ppt_skill_confirm_silent(self, db):
        """ppt + 未置信第三方 skill：无 confirm_skill，直接执行。"""
        task_id = _prepare_task(db, origin="ppt", settings={
            "task_shell_confirm_mode": "strict"})
        tool = _FakeConfirmTool()
        events = _run_with_tools(
            db, task_id, [_skill_runner_turn(), _stop_turn()],
            {"skill_runner": tool})

        assert not [e for e in events if e["type"] == "confirm_skill"]
        assert tool.calls and tool.calls[0]["skill"] == "third-party-skill"
        assert events[-1]["type"] == "done"

    def test_chat_skill_confirm_still_asks(self, db):
        """chat + 未置信第三方 skill：仍弹确认（对照：静默不误伤 chat）。"""
        task_id = _prepare_task(db, settings={"task_shell_confirm_mode": "strict"})
        tool = _FakeConfirmTool()
        events = _run_with_tools(
            db, task_id, [_skill_runner_turn(), _stop_turn()],
            {"skill_runner": tool})

        confirms = [e for e in events if e["type"] == "confirm_skill"]
        assert confirms and confirms[0]["data"]["skill_name"] == "third-party-skill"
        assert tool.calls  # _collect 自动放行后工具已执行
        assert events[-1]["type"] == "done"

    def test_ppt_git_commit_silent(self, db):
        """ppt + git_ops commit：无 confirm_shell，直接执行。"""
        task_id = _prepare_task(db, origin="ppt", settings={
            "task_shell_confirm_mode": "strict"})
        tool = _FakeConfirmTool()
        events = _run_with_tools(
            db, task_id, [_git_commit_turn(), _stop_turn()],
            {"git_ops": tool})

        assert not [e for e in events if e["type"] == "confirm_shell"]
        assert tool.calls and tool.calls[0]["action"] == "commit"
        assert events[-1]["type"] == "done"
