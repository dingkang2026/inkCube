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
4. 确认超时回归（2.11 §5.3，2026-10-22）：定时任务遇需确认命令，120s
   超时瞬间 except 子句误写 asyncio.TimeoutExpired（不存在的属性）→
   AttributeError 杀死 agent 生成器 → 整期 run failed（run_3639c354
   实证）。修复为 asyncio.TimeoutError，五通道无人应答行为回归 +
   静态断言防复发。
5. 影子任务确认直行（2.11 §5.3）：schedule_id 非空的定时任务无人值守，
   五类确认（shell / code_executor / skill_runner / git commit / 外部
   skill）直行不弹窗，与 origin=ppt 静默链同语义。
"""
import asyncio
import inspect
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


def _run_with_tools(db: Database, task_id: str, turns, tools: dict, *,
                    registry=None):
    """同 _run，但注入自定义工具表（skill/git 确认分支需要桩实例）。

    registry：注入 SkillRegistry 实例（外部 skill 分发路径需捕获 execute）；
    缺省时退回无状态 _FakeRegistry（与旧行为一致）。
    """
    agent = TaskAgent(db)
    with patch.multiple(
        ta_mod.TaskAgent,
        _build_system_prompt=staticmethod(lambda *a, **k: "SYS"),
        _create_task_tools=lambda *a, **k: tools,
        _register_external_skills=lambda self, registry, tools_: None,
    ), patch.object(ta_mod, "LLMClient", lambda settings: _FakeLLM(turns)), \
       patch.object(ta_mod, "SkillRegistry",
                    lambda *a, **k: registry if registry is not None
                    else _FakeRegistry()), \
       patch.object(ta_mod.TaskAgent, "_ensure_task_venv", _venv_fastpath):
        return asyncio.run(_collect(agent.run(task_id, ""), agent, task_id))


async def _collect(agen, agent: TaskAgent, task_id: str):
    """收集事件直到 done；确认事件按通道自动「允许」放行（模拟用户点击）。

    shell/git commit 走 respond_confirm（_pending_confirms），
    skill 走 respond_skill_confirm（_pending_skill_confirms）——两个通道
    不互通，调错方法 event 永不置位（等 180s 超时）。
    """
    events = []
    try:
        async for ev in agen:
            events.append(ev)
            if ev.get("type") == "confirm_shell":
                agent.respond_confirm(task_id, True)
            elif ev.get("type") == "confirm_skill":
                agent.respond_skill_confirm(task_id, True)
            if ev.get("type") == "done":
                break
    finally:
        # 同 context 内规范关闭：留给 asyncio.run shutdown 清理会在跨
        # context 的 athrow 中触发 usage contextvar reset ValueError（纯
        # 噪音，但污染失败现场日志，2026-10-22 复盘）。
        await agen.aclose()
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


class TestConfirmPayloadTimeout:
    """确认事件载荷带 timeout（2026-10-22）：前端倒计时与后端等待窗口同步。

    历史缺陷：前端倒计时硬编码 30s/60s（shell/skill），与后端真实
    120s/180s 不符，且到 0 前端发 confirmed=false 把超时伪造成用户拒绝；
    载荷补 timeout 后前端按真实窗口倒计时，到 0 仅本地清理（超时留痕
    归后端 confirm_expired 分支）。
    """

    def test_shell_payload_carries_timeout(self, db):
        task_id = _prepare_task(db, settings={"task_shell_confirm_mode": "strict"})
        events = _run(db, task_id, [_shell_turn(), _stop_turn()])

        confirms = [e for e in events if e["type"] == "confirm_shell"]
        assert confirms and confirms[0]["data"]["timeout"] == 120

    def test_skill_payload_carries_timeout(self, db):
        task_id = _prepare_task(db, settings={"task_shell_confirm_mode": "strict"})
        events = _run_with_tools(
            db, task_id, [_skill_runner_turn(), _stop_turn()],
            {"skill_runner": _FakeConfirmTool()})

        confirms = [e for e in events if e["type"] == "confirm_skill"]
        assert confirms and confirms[0]["data"]["timeout"] == 180


# ══ 确认超时回归 + 影子任务直行（2.11 §5.3，2026-10-22）══


def _code_executor_turn(code="print('hi')"):
    """一轮 code_executor tool_call（strict 模式必走确认分支）。"""
    return [
        {"type": "tool_calls", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "code_executor",
                          "arguments": '{"code": "%s"}' % code}},
        ]},
        {"type": "finish", "finish_reason": "tool_calls"},
    ]


def _external_skill_turn(name="third-party-skill"):
    """一轮外部 skill tool_call（函数名即 skill 名，经 skill_registry 分发）。"""
    return [
        {"type": "tool_calls", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": name, "arguments": "{}"}},
        ]},
        {"type": "finish", "finish_reason": "tool_calls"},
    ]


class _FakeExternalRegistry(_FakeRegistry):
    """外部 skill 注册表桩：get 命中 → 走 skill_registry.execute 分发路径。"""

    def __init__(self):
        self.executed = []

    def get(self, name):
        return object() if name == "third-party-skill" else None

    async def execute(self, name, params, context):
        self.executed.append(name)
        return "ext-ok"


async def _venv_fastpath(self, task_id, venv_path):
    """外部 skill 执行链旁路真实 venv：create_task 会登记 venv_path
    （data_dir="" → 相对路径），执行必经 _ensure_task_venv——真实链路
    会卷入 uv 预装与杀毒白名单 gate（is_uv_exclusion_verified 依赖进程
    级 UV_CACHE_DIR 状态：未设置视为通过、已设置且 marker 缺失即拦截，
    test_security_p0 import main 的 setdefault 会污染进程环境），与被测
    的确认语义无关。直通返回 "" → SkillContext(venv_python="")。"""
    return ""


async def _collect_silent(agen):
    """无人值守采集器：不应答任何确认（对照 _collect 的自动放行）。"""
    events = []
    try:
        async for ev in agen:
            events.append(ev)
            if ev.get("type") == "done":
                break
    finally:
        await agen.aclose()   # 同 _collect：防 shutdown 跨 context 清理噪音
    return events


def _run_silent(db: Database, task_id: str, turns, tools=None, *,
                registry=None):
    """同 _run_with_tools，但不应答确认，且超时常量压缩为 0.05s。

    用于回归「确认超时 → UserDenied 跳过」路径：真实 120/180s 在测试中
    等不起，patch 模块级常量即可（wait_for 直接读该名字）。
    """
    agent = TaskAgent(db)
    with patch.multiple(
        ta_mod.TaskAgent,
        _build_system_prompt=staticmethod(lambda *a, **k: "SYS"),
        _create_task_tools=lambda *a, **k: tools or {},
        _register_external_skills=lambda self, registry, tools_: None,
    ), patch.object(ta_mod, "LLMClient", lambda settings: _FakeLLM(turns)), \
       patch.object(ta_mod, "SkillRegistry",
                    lambda *a, **k: registry if registry is not None
                    else _FakeRegistry()), \
       patch.object(ta_mod, "SHELL_CONFIRM_TIMEOUT", 0.05), \
       patch.object(ta_mod, "SKILL_CONFIRM_TIMEOUT", 0.05), \
       patch.object(ta_mod.TaskAgent, "_ensure_task_venv", _venv_fastpath):
        return asyncio.run(_collect_silent(agent.run(task_id, "")))


def _prepare_schedule_task(db: Database, settings=None) -> str:
    """建定时订阅影子任务（schedule_id 非空、origin=chat，2.11 D2 创建链）。

    tasks.schedule_id 外键 → task_schedules.id，须先建真实订阅行。
    """
    sch = db.create_schedule("s", "p", "general", "general", "daily", "09:00")
    task = db.create_task("t", "", "", "general", schedule_id=sch["id"])
    db.save_settings({
        "api_key": "test-key",
        "task_max_iterations": 5,
        **(settings or {}),
    })
    return task["id"]


class TestConfirmTimeoutUserDenied:
    """确认超时回归：五通道无人应答 → 超时 UserDenied 跳过，任务不崩。

    历史缺陷：except 子句误写 asyncio.TimeoutExpired（不存在的属性）→
    超时瞬间 AttributeError 杀死 agent 生成器 → 定时任务整期 failed
    （run_3639c354 实证，2026-10-22 修复）。
    """

    def test_shell_confirm_timeout_skips(self, db):
        task_id = _prepare_task(db, settings={"task_shell_confirm_mode": "strict"})
        events = _run_silent(db, task_id, [_shell_turn(), _stop_turn()])

        types = [e["type"] for e in events]
        assert "confirm_shell" in types
        assert "confirm_expired" in types
        assert events[-1]["type"] == "done"

    def test_code_executor_confirm_timeout_skips(self, db):
        task_id = _prepare_task(db, settings={"task_shell_confirm_mode": "strict"})
        events = _run_silent(db, task_id, [_code_executor_turn(), _stop_turn()])

        types = [e["type"] for e in events]
        assert "confirm_shell" in types          # 复用 shell 确认对话框
        assert "confirm_expired" in types
        assert events[-1]["type"] == "done"

    def test_skill_runner_confirm_timeout_skips(self, db):
        task_id = _prepare_task(db, settings={"task_shell_confirm_mode": "strict"})
        tool = _FakeConfirmTool()
        events = _run_silent(
            db, task_id, [_skill_runner_turn(), _stop_turn()],
            {"skill_runner": tool})

        types = [e["type"] for e in events]
        assert "confirm_skill" in types
        assert "confirm_expired" in types
        assert tool.calls == []                  # 未放行 → 未执行
        assert events[-1]["type"] == "done"

    def test_git_commit_confirm_timeout_skips(self, db):
        task_id = _prepare_task(db, settings={"task_shell_confirm_mode": "strict"})
        tool = _FakeConfirmTool()
        events = _run_silent(
            db, task_id, [_git_commit_turn(), _stop_turn()],
            {"git_ops": tool})

        types = [e["type"] for e in events]
        assert "confirm_shell" in types
        assert "confirm_expired" in types
        assert tool.calls == []
        assert events[-1]["type"] == "done"

    def test_external_skill_confirm_timeout_skips(self, db):
        task_id = _prepare_task(db, settings={"task_shell_confirm_mode": "strict"})
        registry = _FakeExternalRegistry()
        events = _run_silent(
            db, task_id, [_external_skill_turn(), _stop_turn()],
            {"skill_runner": _FakeConfirmTool()},   # 确认检查依赖其 DB 缓存
            registry=registry)

        types = [e["type"] for e in events]
        assert "confirm_skill" in types
        assert "confirm_expired" in types
        assert registry.executed == []
        assert events[-1]["type"] == "done"

    def test_no_timeoutexpired_typo_regression(self):
        """静态防复发：except 子句只允许 asyncio.TimeoutError（真实存在）。

        asyncio.TimeoutExpired 不存在 → except 求值即 AttributeError →
        agent 生成器静默死亡（定时任务 failed 的根因）。
        """
        assert "asyncio.TimeoutExpired" not in inspect.getsource(ta_mod)


class TestScheduleTaskConfirmSilent:
    """影子任务确认直行（2.11 §5.3）：schedule_id 非空 → 五类确认全静默。

    定时订阅无人值守：弹确认必空等 120/180s 再 UserDenied（浪费轮次与
    token，TimeoutExpired 缺陷期直接整期 failed）。与 origin=ppt 静默链
    同语义，覆盖用户三档设置；chat 任务确认语义不变（见上方对照组）。
    """

    def test_schedule_shell_silent_under_strict(self, db):
        task_id = _prepare_schedule_task(db, settings={
            "task_shell_confirm_mode": "strict"})
        events = _run(db, task_id, [_shell_turn(), _stop_turn()])

        assert not [e for e in events if e["type"] == "confirm_shell"]
        assert events[-1]["type"] == "done"

    def test_schedule_code_executor_silent_under_strict(self, db):
        task_id = _prepare_schedule_task(db, settings={
            "task_shell_confirm_mode": "strict"})
        events = _run(db, task_id, [_code_executor_turn(), _stop_turn()])

        types = [e["type"] for e in events]
        assert not [t for t in types if t in ("confirm_shell", "confirm_expired")]
        assert events[-1]["type"] == "done"

    def test_schedule_skill_runner_silent(self, db):
        task_id = _prepare_schedule_task(db, settings={
            "task_shell_confirm_mode": "strict"})
        tool = _FakeConfirmTool()
        events = _run_with_tools(
            db, task_id, [_skill_runner_turn(), _stop_turn()],
            {"skill_runner": tool})

        assert not [e for e in events if e["type"] == "confirm_skill"]
        assert tool.calls and tool.calls[0]["skill"] == "third-party-skill"
        assert events[-1]["type"] == "done"

    def test_schedule_git_commit_silent(self, db):
        task_id = _prepare_schedule_task(db, settings={
            "task_shell_confirm_mode": "strict"})
        tool = _FakeConfirmTool()
        events = _run_with_tools(
            db, task_id, [_git_commit_turn(), _stop_turn()],
            {"git_ops": tool})

        assert not [e for e in events if e["type"] == "confirm_shell"]
        assert tool.calls and tool.calls[0]["action"] == "commit"
        assert events[-1]["type"] == "done"

    def test_schedule_external_skill_silent(self, db):
        task_id = _prepare_schedule_task(db, settings={
            "task_shell_confirm_mode": "strict"})
        registry = _FakeExternalRegistry()
        events = _run_with_tools(
            db, task_id, [_external_skill_turn(), _stop_turn()],
            {"skill_runner": _FakeConfirmTool()}, registry=registry)

        assert not [e for e in events if e["type"] == "confirm_skill"]
        assert registry.executed == ["third-party-skill"]
        assert events[-1]["type"] == "done"
