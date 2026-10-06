"""python/tests/test_slash_routing.py — slash 命令路由回归测试（1.1 §4.5）。

回归背景：旧版对已安装 Agent Skill 的 params 做 CLI/NL 启发式预检
（取首 token 匹配 SKILL.md 行首），「回声测试」命中 SKILL.md「何时使用」
清单行被判 CLI → 直调把自然语言原样传给脚本 → argparse invalid choice
报错。现行路由（_slash_routing）只区分「是否已安装」，参数语义一律交
LLM 翻译；直调入口（_execute_slash_command）仅保留 /help 与未知报告。
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.task_agent import TaskAgent
from db.database import Database


class FakeSkillRunner:
    """skill_runner 替身：内存缓存 + 按名查找（与 SkillRunnerTool 同接口）。"""

    def __init__(self, skills):
        self._skill_cache = skills

    def _refresh_cache(self):
        pass

    def _find_skill(self, name):
        for s in self._skill_cache:
            if s["name"] == name:
                return s
        return None


SKILLS = [
    {"name": "hello-world", "install_path": "X", "enabled": 1, "needs_confirm": 0},
]


@pytest.fixture
def db():
    database = Database(db_path=":memory:")
    database.initialize()
    return database


@pytest.fixture
def agent(db):
    return TaskAgent(db)


def _tools(skills=SKILLS):
    return {"skill_runner": FakeSkillRunner(skills)}


def _collect(agen):
    """同步消费 async generator，返回事件列表（不依赖 pytest-asyncio）。"""
    async def _consume():
        return [e async for e in agen]
    return asyncio.run(_consume())


class TestSlashRouting:
    def test_nl_params_fallthrough(self, agent):
        """回归核心：NL 参数（旧启发式误判为 CLI 的场景）必须进 agent 循环。"""
        parsed, direct = agent._slash_routing("/hello-world 回声测试", _tools())
        assert parsed == ("hello-world", "回声测试")
        assert direct is False

    def test_cli_params_also_fallthrough(self, agent):
        """CLI 形态参数同样交 LLM（路由不再区分参数形态）。"""
        _, direct = agent._slash_routing("/hello-world timestamp", _tools())
        assert direct is False

    def test_empty_params_fallthrough(self, agent):
        """无参数 slash 也进 agent 循环（直调空 command 必然 argparse 报错）。"""
        parsed, direct = agent._slash_routing("/hello-world", _tools())
        assert parsed == ("hello-world", "")
        assert direct is False

    def test_help_direct(self, agent):
        parsed, direct = agent._slash_routing("/help", _tools())
        assert parsed == ("help", "")
        assert direct is True

    def test_help_with_params_direct(self, agent):
        _, direct = agent._slash_routing("/help something", _tools())
        assert direct is True

    def test_unknown_skill_direct(self, agent):
        parsed, direct = agent._slash_routing("/no-such xxx", _tools())
        assert parsed == ("no-such", "xxx")
        assert direct is True

    def test_non_slash_message(self, agent):
        parsed, direct = agent._slash_routing("普通消息", _tools())
        assert parsed is None
        assert direct is False

    def test_no_skill_runner_direct(self, agent):
        _, direct = agent._slash_routing("/hello-world 回声测试", {})
        assert direct is True


class TestSlashHint:
    """hint 构造回归（首次调用准确机制：附 SKILL.md 指南）。"""

    def test_hint_with_guide_embeds_instructions(self):
        guide = "# Hello World\n## 脚本说明\n| `timestamp` | (无) |"
        hint = TaskAgent._build_slash_hint("hello-world", "今天星期几", guide)
        assert "hello-world" in hint
        assert "今天星期几" in hint
        assert "<guide>" in hint and guide in hint  # 指南全文嵌入

    def test_hint_without_guide_omits_guide_block(self):
        hint = TaskAgent._build_slash_hint("demo", "需求")
        assert "<guide>" not in hint
        assert "使用指南" in hint  # 仍引导参考（结果里会附）

    def test_hint_empty_params(self):
        hint = TaskAgent._build_slash_hint("demo", "", "guide")
        assert "未提供具体参数" in hint

    def test_hint_guide_truncated_to_4000(self):
        hint = TaskAgent._build_slash_hint("demo", "x", "A" * 9000)
        assert hint.count("A") == 4000  # 与 Level 2 结果注入同一上限

    def test_hint_with_refs_note_points_to_references(self):
        """L3：hint 携带 references/ 指引（按需读取，不预加载）。"""
        note = "该 Skill 的详细参考文件位于 D:/skills/demo/references（TABLE.md）"
        hint = TaskAgent._build_slash_hint("demo", "需求", "guide", note)
        assert note in hint

    def test_hint_without_refs_note_omits_pointer(self):
        hint = TaskAgent._build_slash_hint("demo", "需求", "guide", "")
        assert "参考文件" not in hint


class TestAgentSkillSchema:
    """command 参数描述必须是 CLI 语义。

    回归：曾写 "Describe what you want to do in natural language" 误导 LLM
    直传自然语言 → argparse 报错；修正后明确 CLI 形态并附示例。
    """

    def _make_adapter(self):
        from skills.loader import AgentSkillAdapter
        parsed = {"frontmatter": {"name": "demo-skill", "description": "d"}, "body": "body text"}
        return AgentSkillAdapter("X", parsed, None)

    def test_command_param_demands_cli_form(self):
        desc = self._make_adapter().input_schema["properties"]["command"]["description"]
        assert "CLI arguments" in desc
        assert "greet --name" in desc  # 带示例
        assert "Describe what you want to do in natural language" not in desc  # 旧误导文案已删

    def test_usage_instructions_returns_body(self):
        assert self._make_adapter().usage_instructions == "body text"


class TestExecuteSlashCommand:
    def test_help_answers_directly(self, agent, db):
        t = db.create_task("帮助", "/tmp", "", "general")
        events = _collect(agent._execute_slash_command(t["id"], "help", _tools()))
        answer = "".join(e["data"]["content"] for e in events if e["type"] == "answer_chunk")
        assert "可用命令" in answer
        assert "`/hello-world`" in answer  # 已安装 skill 出现在清单
        assert events[-1]["type"] == "done"
        assert db.get_task_messages(t["id"])[-1]["role"] == "assistant"

    def test_unknown_skill_reports_available(self, agent, db):
        t = db.create_task("未知", "/tmp", "", "general")
        events = _collect(agent._execute_slash_command(t["id"], "no-such", _tools()))
        answer = "".join(e["data"]["content"] for e in events if e["type"] == "answer_chunk")
        assert "未找到 Skill" in answer
        assert "hello-world" in answer

    def test_installed_skill_never_direct_runs(self, agent, db):
        """防御分支：已安装 skill 意外到达直调入口时只提示，不执行脚本。"""
        t = db.create_task("防御", "/tmp", "", "general")
        events = _collect(agent._execute_slash_command(t["id"], "hello-world", _tools()))
        types = [e["type"] for e in events]
        assert "action" not in types  # 不产生工具执行事件
        assert "observation" not in types
        answer = "".join(e["data"]["content"] for e in events if e["type"] == "answer_chunk")
        assert "已安装" in answer
