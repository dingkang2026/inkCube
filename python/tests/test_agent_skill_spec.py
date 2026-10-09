"""Agent Skills 标准合规回归测试。

回归背景（1.1 §4.4）：为验证「外部标准 skill 拷来即用」承诺，仓库内置
python/tests/fixtures/demo-skills/color-conv（严格遵循 agentskills.io 规范：name 与目录一致、
第三人称 description、license/compatibility 标准语义、references/ 渐进式
披露、脚本不自知颜色名）。本文件覆盖由它暴露的四个平台侧问题：

1. compatibility 是自由文本环境说明，不是 pip 依赖清单 —— 旧实现会把
   "uses only the standard library" 解析成包名并 pip install，已删除；
2. SKILL.md 指南只在首次执行结果附带（激活时加载一次），不再每次注入；
3. references/ 通过 file_ops 只读根按需读取（L3 渐进式披露）；
4. 标准格式（license 等额外 frontmatter 字段）解析零报错；
5. 入口脚本契约（2026-10）：仅约定入口（main.py/cli.py）或唯一脚本才
   自动执行；多脚本工作流型 Skill（如 ppt-master：70+ 工具脚本由
   workflows/*.md 编排，无统一 CLI 入口）走 instructions 模式，否则会
   按字母序误跑无关脚本（analyze_images.py）；
6. requirements.txt 存在性注记（2026-10）：仅提示 LLM 按需自助安装，
   不解析、不自动安装。
"""
import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from skills.context import SkillContext
from skills.loader import AgentSkillAdapter
from skills.parser import parse_skill_md, validate_skill_md
from tools.file_ops import FileOpsTool

TESTS_DIR = Path(__file__).resolve().parent
DEMO_SKILL = TESTS_DIR / "fixtures" / "demo-skills" / "color-conv"

# 标准 compatibility 字段（旧实现会误解析出一堆英文词当包名）
STD_COMPAT = (
    "Requires Python 3.10+; uses only the standard library "
    "(argparse, colorsys). No third-party packages."
)

MINI_SKILL_MD = f"""---
name: mini-demo
description: "Mini skill for tests. Use when testing Agent Skill execution."
license: MIT
compatibility: "{STD_COMPAT}"
---

# Mini Demo

## Subcommands

| Subcommand | Arguments |
|------------|-----------|
| `hello` | `--name <str>` (optional) |

Run `scripts/mini.py hello --name World`.
"""

MINI_SCRIPT = """import argparse, json
p = argparse.ArgumentParser()
p.add_argument("cmd", choices=["hello"])
p.add_argument("--name", default="World")
a = p.parse_args()
print(json.dumps({"output": f"hello {a.name}"}))
"""


@pytest.fixture
def mini_skill(tmp_path: Path) -> Path:
    """Build a spec-compliant mini skill (scripts + references) in tmp_path."""
    skill_dir = tmp_path / "mini-demo"
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "references").mkdir()
    (skill_dir / "SKILL.md").write_text(MINI_SKILL_MD, encoding="utf-8")
    (skill_dir / "scripts" / "mini.py").write_text(MINI_SCRIPT, encoding="utf-8")
    (skill_dir / "references" / "TABLE.md").write_text(
        "| key | value |\n|-----|-------|\n| a | 1 |", encoding="utf-8",
    )
    return skill_dir


def _adapter(skill_dir: Path) -> AgentSkillAdapter:
    parsed = parse_skill_md(str(skill_dir / "SKILL.md"))
    return AgentSkillAdapter(str(skill_dir), parsed)


def _context() -> SkillContext:
    return SkillContext(
        workspace_path=os.getcwd(),
        venv_python=sys.executable,
        settings={},
    )


def _run(coro):
    """Synchronously drive a coroutine (project uses plain pytest, no pytest-asyncio)."""
    return asyncio.run(coro)


class TestSpecCompliance:
    """标准格式容错 + compatibility 语义。"""

    def test_repo_demo_skill_parses_with_zero_errors(self):
        """fixtures/demo-skills/color-conv（标准格式）解析校验零错误。"""
        assert DEMO_SKILL.is_dir(), "color-conv demo skill missing from repo"
        parsed = parse_skill_md(str(DEMO_SKILL / "SKILL.md"))
        assert validate_skill_md(parsed) == []
        fm = parsed["frontmatter"]
        assert fm["name"] == "color-conv"
        assert fm.get("license") == "MIT"  # 多余字段被容忍
        assert "third-party" in fm.get("compatibility", "")

    def test_mini_skill_extra_frontmatter_tolerated(self, mini_skill: Path):
        parsed = parse_skill_md(str(mini_skill / "SKILL.md"))
        assert validate_skill_md(parsed) == []
        assert parsed["frontmatter"]["license"] == "MIT"

    def test_parse_compatibility_removed(self):
        """旧的误装解析已删除：parser 不再暴露 parse_compatibility。"""
        import skills.parser as parser_mod

        assert not hasattr(parser_mod, "parse_compatibility")

    def test_execute_never_runs_pip(self, mini_skill: Path, monkeypatch):
        """标准 compatibility 文本绝不触发 pip 安装（回归 #2）。"""
        import skills.loader as loader_mod

        calls: list[list[str]] = []
        orig_exec = loader_mod.asyncio.create_subprocess_exec

        async def spy_exec(*cmd, **kwargs):
            calls.append(list(cmd))
            return await orig_exec(*cmd, **kwargs)

        monkeypatch.setattr(loader_mod.asyncio, "create_subprocess_exec", spy_exec)

        adapter = _adapter(mini_skill)
        result = json.loads(
            _run(adapter.execute({"command": "hello --name Spec"}, _context()))
        )

        assert result["output"] == "hello Spec"
        assert calls, "script should have been executed via subprocess"
        for cmd in calls:
            assert "pip" not in cmd and "install" not in cmd, f"unexpected pip call: {cmd}"


class TestProgressiveDisclosure:
    """指南首次附带（#1）+ references 注记（#3）。"""

    def test_guide_attached_only_on_first_execution(self, mini_skill: Path):
        adapter = _adapter(mini_skill)
        ctx = _context()

        first = json.loads(_run(adapter.execute({"command": "hello"}, ctx)))
        second = json.loads(_run(adapter.execute({"command": "hello"}, ctx)))

        assert "instructions" in first  # 首次：激活时加载
        assert "references" in first
        assert "instructions" not in second  # 之后不重复
        assert "references" not in second

    def test_mark_guide_sent_suppresses_first_attachment(self, mini_skill: Path):
        """slash hint 已带指南 → 首次执行结果不再重复（联动）。"""
        adapter = _adapter(mini_skill)
        adapter.mark_guide_sent()
        result = json.loads(_run(adapter.execute({"command": "hello"}, _context())))
        assert "instructions" not in result
        assert "references" not in result

    def test_reference_note_lists_files(self, mini_skill: Path):
        note = _adapter(mini_skill).reference_note()
        assert "references" in note
        assert "TABLE.md" in note
        assert "file_ops" in note  # 告知读取途径

    def test_reference_note_empty_without_dir(self, tmp_path: Path):
        skill_dir = tmp_path / "no-refs"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(MINI_SKILL_MD, encoding="utf-8")
        assert _adapter(skill_dir).reference_note() == ""

    def test_no_script_path_attaches_guide(self, tmp_path: Path):
        """无脚本 skill（指令型）首执结果同样附指南。"""
        skill_dir = tmp_path / "instr-only"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(MINI_SKILL_MD, encoding="utf-8")
        adapter = _adapter(skill_dir)
        result = json.loads(_run(adapter.execute({"command": "hi"}, _context())))
        assert "instructions" in result


class TestFileOpsReadonlyRoot:
    """file_ops 只读根（#3）：read/list 可达、写仍被拒。"""

    def _tool(self, workspace: Path) -> FileOpsTool:
        tool = FileOpsTool()
        tool.set_workspace(str(workspace))
        return tool

    def test_read_absolute_path_via_readonly_root(self, mini_skill: Path, tmp_path: Path):
        tool = self._tool(tmp_path / "ws")
        ref_abs = str(mini_skill / "references" / "TABLE.md")

        # 未注册：拒绝（沙箱仍然生效）
        denied = json.loads(_run(tool.execute(action="read", path=ref_abs)))
        assert "error" in denied

        tool.add_readonly_root(str(mini_skill))
        ok = json.loads(_run(tool.execute(action="read", path=ref_abs)))
        assert "content" in ok and "key" in ok["content"]

    def test_write_to_readonly_root_rejected(self, mini_skill: Path, tmp_path: Path):
        tool = self._tool(tmp_path / "ws")
        tool.add_readonly_root(str(mini_skill))
        target = str(mini_skill / "references" / "TABLE.md")
        result = json.loads(
            _run(tool.execute(action="write", path=target, content="x"))
        )
        assert "error" in result  # 写路径不接受只读根

    def test_list_readonly_root_dir(self, mini_skill: Path, tmp_path: Path):
        tool = self._tool(tmp_path / "ws")
        tool.add_readonly_root(str(mini_skill))
        result = json.loads(
            _run(tool.execute(action="list", path=str(mini_skill)))
        )
        names = [e["name"] for e in result.get("entries", [])]
        assert "SKILL.md" in names and "references" in names


class TestEntryScriptContract:
    """入口脚本契约（回归 #5）：CLI 模式 vs instructions 模式。"""

    @staticmethod
    def _skill(tmp_path: Path, scripts: list[str]) -> Path:
        skill_dir = tmp_path / "wf-skill"
        (skill_dir / "scripts").mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(MINI_SKILL_MD, encoding="utf-8")
        for name in scripts:
            (skill_dir / "scripts" / name).write_text(
                "print('ok')\n", encoding="utf-8"
            )
        return skill_dir

    def test_multi_script_without_convention_uses_instructions(
        self, tmp_path: Path, monkeypatch
    ):
        """多脚本无约定入口（ppt-master 形态）绝不自动执行任意脚本。"""
        import skills.loader as loader_mod

        calls: list[list[str]] = []

        async def spy_exec(*cmd, **kwargs):
            calls.append(list(cmd))
            raise AssertionError("workflow skill must not be auto-executed")

        monkeypatch.setattr(loader_mod.asyncio, "create_subprocess_exec", spy_exec)
        adapter = _adapter(self._skill(tmp_path, ["analyze_images.py", "svg_to_pptx.py"]))
        result = json.loads(
            _run(adapter.execute({"command": "生成一份 PPT"}, _context()))
        )

        assert calls == []
        assert "instructions" in result  # 指南随首执结果附带
        assert "workflow" in result["note"].lower() or "instructions" in result["note"].lower()

    def test_conventional_entry_main_py_is_chosen(self, tmp_path: Path, monkeypatch):
        """存在 main.py 约定入口时执行它，而非字母序首个脚本。"""
        import skills.loader as loader_mod

        picked: list[str] = []
        orig_exec = loader_mod.asyncio.create_subprocess_exec

        async def spy_exec(*cmd, **kwargs):
            picked.append(str(cmd[1]))
            return await orig_exec(*cmd, **kwargs)

        monkeypatch.setattr(loader_mod.asyncio, "create_subprocess_exec", spy_exec)
        adapter = _adapter(
            self._skill(tmp_path, ["analyze_images.py", "main.py", "util.py"])
        )
        result = json.loads(_run(adapter.execute({"command": ""}, _context())))

        assert len(picked) == 1 and picked[0].endswith("main.py")
        assert result["output"] == "ok"

    def test_single_script_still_executes(self, tmp_path: Path):
        """唯一脚本 = 可发现入口，CLI 模式保持不变（向后兼容）。"""
        adapter = _adapter(self._skill(tmp_path, ["tool.py"]))
        result = json.loads(_run(adapter.execute({"command": ""}, _context())))
        assert result["output"] == "ok"

    def test_underscore_helpers_do_not_qualify_as_entry(self, tmp_path: Path):
        """仅有 _ 前缀辅助脚本时不算可发现入口 → instructions 模式。"""
        adapter = _adapter(self._skill(tmp_path, ["_helper.py"]))
        result = json.loads(_run(adapter.execute({"command": "hi"}, _context())))
        assert "instructions" in result

    def test_command_schema_follows_mode(self, tmp_path: Path):
        """command 参数描述随模式分叉：CLI 模式要求 CLI 形态，工作流型接受自由文本。"""
        cli_adapter = _adapter(self._skill(tmp_path / "a", ["main.py"]))
        wf_adapter = _adapter(self._skill(tmp_path / "b", ["s1.py", "s2.py"]))

        assert "CLI arguments" in cli_adapter.input_schema["properties"]["command"]["description"]
        assert "free-form" in wf_adapter.input_schema["properties"]["command"]["description"]


class TestRequirementsNote:
    """requirements.txt 存在性注记（回归 #6）：只提示，不解析不安装。"""

    @staticmethod
    def _skill(tmp_path: Path, with_requirements: bool) -> Path:
        skill_dir = tmp_path / "dep-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(MINI_SKILL_MD, encoding="utf-8")
        if with_requirements:
            (skill_dir / "requirements.txt").write_text(
                "python-pptx>=0.6.21\n", encoding="utf-8"
            )
        return skill_dir

    def test_note_attached_on_first_result_only(self, tmp_path: Path):
        adapter = _adapter(self._skill(tmp_path, with_requirements=True))
        ctx = _context()

        first = json.loads(_run(adapter.execute({"command": "hi"}, ctx)))
        second = json.loads(_run(adapter.execute({"command": "hi"}, ctx)))

        assert "dependencies" in first
        assert "requirements.txt" in first["dependencies"]
        assert "dependencies" not in second  # 与指南同批，仅首次

    def test_note_absent_without_file(self, tmp_path: Path):
        adapter = _adapter(self._skill(tmp_path, with_requirements=False))
        result = json.loads(_run(adapter.execute({"command": "hi"}, _context())))
        assert "dependencies" not in result


class TestGuideBudget:
    """L2 指南预算：SKILL_GUIDE_MAX_CHARS（4000 → 8000，2026-10）。"""

    def test_long_body_truncated_to_budget(self, tmp_path: Path):
        """超长正文截断到预算内——大工作流 Skill（如 ppt-master ≈ 4.8K 字符）完整保留。"""
        from skills.loader import SKILL_GUIDE_MAX_CHARS

        skill_dir = tmp_path / "long-skill"
        skill_dir.mkdir()
        long_body = "# Long\n\n" + "x" * (SKILL_GUIDE_MAX_CHARS + 1000)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: long-skill\ndescription: t\n---\n\n{long_body}",
            encoding="utf-8",
        )
        adapter = _adapter(skill_dir)
        result = json.loads(_run(adapter.execute({"command": "hi"}, _context())))

        assert len(result["instructions"]) == SKILL_GUIDE_MAX_CHARS

    def test_ppt_master_sized_body_not_truncated(self, tmp_path: Path):
        """4800 字符级正文（旧 4000 预算会截尾）现在完整保留。"""
        skill_dir = tmp_path / "mid-skill"
        skill_dir.mkdir()
        body = "# Mid\n\n" + "y" * 4800
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: mid-skill\ndescription: t\n---\n\n{body}",
            encoding="utf-8",
        )
        adapter = _adapter(skill_dir)
        result = json.loads(_run(adapter.execute({"command": "hi"}, _context())))

        assert result["instructions"] == body
