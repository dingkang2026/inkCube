"""python/tests/test_task_agent_prompt.py — _build_system_prompt 组装回归测试。

回归背景（2026-10-13）：可选上下文 section 的值是片段列表（list[str]），
预算拼接处误将列表整体当作字符串并入 full_parts，"\n".join 抛
TypeError: sequence item 19: expected str instance, list found。
凡 workspace 检测到任一可选上下文（git / 项目技术栈 / 项目配置 /
repo-map）的任务，对话在首轮迭代前即失败。
"""
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import agent.task_agent as task_agent_module
from agent.task_agent import TaskAgent


GIT_CTX = "📂 Git: 分支 `main`\n最近提交:\nabc1234 fix demo"


class _StubRepoMap:
    """RepoMapTool 替身：返回预置摘要（None 表示无可注入内容）。"""

    def __init__(self, summary):
        self._summary = summary

    def generate_summary_for_prompt(self, token_budget: int = 0):
        return self._summary


@pytest.fixture
def agent():
    from db.database import Database

    database = Database(db_path=":memory:")
    database.initialize()
    return TaskAgent(database)


def _patch_optional_sources(monkeypatch, git_ctx="", project_ctx="", repo_summary=None):
    """把可选上下文来源替换为确定性替身（不跑真实 git / 文件扫描）。"""
    monkeypatch.setattr(task_agent_module, "build_git_context", lambda _ws: git_ctx)
    monkeypatch.setattr(task_agent_module, "detect_project_context", lambda _ws: project_ctx)
    monkeypatch.setattr(
        task_agent_module, "RepoMapTool", lambda **_kw: _StubRepoMap(repo_summary)
    )


def _patch_tokens(monkeypatch, tokens):
    monkeypatch.setattr(
        task_agent_module.ContextManager,
        "count_tokens",
        staticmethod(lambda _text: tokens),
    )


class TestBuildSystemPrompt:
    def test_optional_sections_flattened_into_prompt(self, agent, monkeypatch, tmp_path):
        """回归主用例：可选上下文非空时必须展平拼接为完整字符串。

        修复前：section 片段列表被当字符串 join → TypeError: sequence item 19。
        """
        _patch_optional_sources(monkeypatch, git_ctx=GIT_CTX)
        _patch_tokens(monkeypatch, tokens=10)  # 低于预算，不触发截断

        prompt = agent._build_system_prompt(str(tmp_path), mode="coding")

        assert isinstance(prompt, str)
        assert GIT_CTX in prompt  # git 上下文内容完整注入

    def test_no_optional_sections_returns_base_prompt(self, agent, monkeypatch, tmp_path):
        """可选上下文全空时返回基础骨架（工具描述 + 工作目录）。"""
        _patch_optional_sources(monkeypatch)
        _patch_tokens(monkeypatch, tokens=10)

        prompt = agent._build_system_prompt(str(tmp_path), mode="coding")

        assert isinstance(prompt, str)
        assert "可用工具:" in prompt
        assert f"工作目录: {tmp_path}" in prompt
        assert GIT_CTX not in prompt

    def test_budget_overflow_drops_sections(self, agent, monkeypatch, tmp_path, caplog):
        """超预算截断路径：逐段丢弃可选 section，返回纯骨架字符串。

        修复前：截断循环内同一处 join 亦抛 TypeError（被预算内路径先炸遮蔽）。
        """
        _patch_optional_sources(monkeypatch, git_ctx=GIT_CTX, project_ctx="项目类型: Python")
        _patch_tokens(monkeypatch, tokens=99999)  # 恒超预算 → 走截断循环
        caplog.set_level(logging.WARNING, logger="agent.task_agent")

        prompt = agent._build_system_prompt(str(tmp_path), mode="coding")

        assert isinstance(prompt, str)
        assert GIT_CTX not in prompt  # 可选 section 全部被丢弃
        assert "项目类型" not in prompt
        assert "可用工具:" in prompt  # 基础骨架保留
        assert any("System prompt truncated" in r.message for r in caplog.records)
