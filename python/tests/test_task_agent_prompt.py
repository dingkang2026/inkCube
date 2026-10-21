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
    # count_tokens 已迁至 llm/budget.py（D2）；task_agent 模块级导入名是截断循环的实际消费点
    monkeypatch.setattr(
        task_agent_module,
        "count_tokens",
        lambda _text: tokens,
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

    def test_incremental_principle_and_chart_policy_both_modes(
            self, agent, monkeypatch, tmp_path):
        """增量修改原则与图表选型/尺寸契约在 coding/general 两模式均注入（2026-10-21）。

        - _PROMPT_INCREMENTAL 两模式共用（file_ops 两模式均含 search_replace；
          general 的研究/写作/文档生成场景恰是「长报告分章、不整体重生成」高发场景）；
        - _PROMPT_RICH_TEXT 图表选型准则（结构化图表优先 Mermaid）随富文本段两模式注入；
        - 尺寸契约（A4/PPT 目标介质）同段注入——根因在生成层，预览/导出层仅兜底。
        固定段不可被预算截断丢弃（仅可选 section 可丢，test_budget_overflow_drops_sections 已验）。
        """
        _patch_optional_sources(monkeypatch)
        _patch_tokens(monkeypatch, tokens=10)

        for mode in ("coding", "general"):
            prompt = agent._build_system_prompt(str(tmp_path), mode=mode)
            assert "增量修改原则" in prompt, mode
            assert "禁止推倒重来" in prompt, mode
            assert "长文档/报告按章节逐个写入独立文件" in prompt, mode
            assert "图表选型" in prompt, mode
            assert "一律优先 Mermaid" in prompt, mode
            assert "尺寸约束" in prompt, mode
            assert "A4 文档或 PPT" in prompt, mode
            assert "graph TD" in prompt, mode
