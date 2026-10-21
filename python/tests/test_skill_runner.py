"""test_skill_runner.py — SkillRunnerTool 的 SkillContext 透传契约。

回归背景（2.8 §3.3，2026-10-14 final review Finding 1）：skill_runner 备选
路径此前漏传 task_id → SkillContext.task_id 恒空 → loader 不注入
INKCUBE_TASK_ID → browser-fetch 租约锁同 task 重入判据失效
（`if self.task_id and holder.get(...)` 空值短路）。

注意：_execute_agent_skill 为函数内 import（from skills.loader import
AgentSkillAdapter），每次执行时从源模块取属性——monkeypatch 目标是
skills.loader.AgentSkillAdapter，而非 tools.skill_runner 内的名字。
"""
import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.skill_runner import SkillRunnerTool

SKILL_MD = """---
name: ctx-probe
description: "Minimal probe skill. Use when testing runner context forwarding."
---

# Ctx Probe
"""


class _FakeDb:
    """list_skills 契约桩：enabled + install_path 两字段驱动 _refresh_cache。"""

    def __init__(self, install_path: Path):
        self._install_path = str(install_path)

    def list_skills(self):
        return [{
            "name": "ctx-probe",
            "enabled": 1,
            "install_path": self._install_path,
            "description": "probe",
        }]


@pytest.fixture
def skill_dir(tmp_path: Path) -> Path:
    d = tmp_path / "ctx-probe"
    d.mkdir()
    (d / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    return d


@pytest.fixture
def captured(monkeypatch):
    """FakeAdapter 捕获构造参数与 execute 收到的 (params, ctx)。"""
    seen: dict = {}

    class FakeAdapter:
        def __init__(self, install_path, parsed, skill_info):
            seen["install_path"] = install_path
            seen["skill_info"] = skill_info

        async def execute(self, params, ctx):
            seen["params"] = params
            seen["ctx"] = ctx
            return '{"output": "ok"}'

    import skills.loader as loader_mod
    monkeypatch.setattr(loader_mod, "AgentSkillAdapter", FakeAdapter)
    return seen


def _tool(skill_dir: Path, *, task_id: str) -> SkillRunnerTool:
    tool = SkillRunnerTool(db=_FakeDb(skill_dir))
    tool.task_venv_python = "C:/fake/task/.venv/Scripts/python.exe"
    tool.task_workspace_path = "C:/fake/ws"
    tool.task_id = task_id
    return tool


class TestSkillContextForwarding:
    """execute → _execute_agent_skill → SkillContext 字段透传。"""

    @staticmethod
    def _run(tool: SkillRunnerTool) -> dict:
        return json.loads(asyncio.run(tool.execute(skill="ctx-probe", input={"k": "v"})))

    def test_task_id_forwarded(self, skill_dir, captured):
        """task_id 必须进 ctx：loader 依它注入 INKCUBE_TASK_ID（租约锁身份）。"""
        result = self._run(_tool(skill_dir, task_id="task-abc"))

        assert result == {"output": "ok"}  # FakeAdapter 返回值原样上抛
        assert captured["ctx"].task_id == "task-abc"
        assert captured["ctx"].venv_python == "C:/fake/task/.venv/Scripts/python.exe"
        assert captured["ctx"].workspace_path == "C:/fake/ws"
        assert captured["params"] == {"k": "v"}  # input 原样传递

    def test_default_task_id_is_empty(self, skill_dir, captured):
        """未配置 task_id（共享表路径）→ ctx.task_id 为空，不误注入。"""
        result = self._run(SkillRunnerTool(db=_FakeDb(skill_dir)))

        assert result == {"output": "ok"}
        assert captured["ctx"].task_id == ""

    def test_adapter_receives_skill_info(self, skill_dir, captured):
        self._run(_tool(skill_dir, task_id="t"))
        assert captured["skill_info"]["name"] == "ctx-probe"


class TestPromptDescriptionFcDedup:
    """双通道去冗（2026-10-16 天眼查任务实证）：已注册为独立 FC 工具的
    Skill 不再进「可用 Skill」清单，改为标注直接按工具名调用。

    L1 同时列 skill_runner（参数约定 skill+input）与独立工具 browser-fetch
    （参数约定 command）时，模型会走 skill_runner「先激活」——空 input
    首调必错一轮。两个实例（per-task / __init__）都由 TaskAgent 标记。
    """

    def test_fc_registered_listed_as_standalone(self, skill_dir):
        tool = SkillRunnerTool(db=_FakeDb(skill_dir))
        tool.mark_fc_registered(["ctx-probe"])
        text = tool.prompt_description
        assert "独立工具" in text and "ctx-probe" in text
        # 不再以「可用 Skill: - **name**」的经本工具调用形态出现
        assert "- **ctx-probe**" not in text

    def test_unregistered_skill_keeps_listing(self, skill_dir):
        tool = SkillRunnerTool(db=_FakeDb(skill_dir))
        text = tool.prompt_description
        assert "- **ctx-probe**: probe" in text

    def test_configure_resets_fc_names(self, skill_dir):
        tool = SkillRunnerTool(db=_FakeDb(skill_dir))
        tool.mark_fc_registered(["ctx-probe"])
        tool.configure(_FakeDb(skill_dir))
        assert tool.fc_skill_names == set()
        assert "- **ctx-probe**" in tool.prompt_description
