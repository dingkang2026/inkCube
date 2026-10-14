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


@pytest.fixture(autouse=True)
def _skip_venv_preinstall(monkeypatch):
    """默认隔离 CLI 首用同步预装（loader.AgentSkillAdapter._ensure_venv_ready）。

    本文件的 _context() 用 sys.executable 当 venv_python，反推出的 venv 根
    是真实 conda 环境目录——marker 探测大概率缺失，会真的触发预装命令。
    预装语义由 TestFirstUseVenvPreinstall 专测（自行覆盖 mock）。
    """
    import skills.loader as loader_mod

    monkeypatch.setattr(loader_mod, "is_preinstall_done", lambda venv_path: True)


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


class TestFirstUseVenvPreinstall:
    """CLI 模式 skill 首用同步补装（2.8 §5.5：缺就装、装完继续）。"""

    def test_missing_marker_triggers_blocking_install(self, mini_skill: Path, monkeypatch):
        """marker 缺失 → 调 ensure_venv_blocking（反推 venv 根）→ 装完继续执行。

        venv_python 用 sys.executable（可真执行）；blocking 已 mock 拦截，
        不会对真实解释器环境产生安装动作；反推正确性由
        test_venv_lazy.py::test_get_venv_path_from_python 覆盖。
        """
        import skills.loader as loader_mod

        expected_venv_dir = loader_mod.get_venv_path_from_python(sys.executable)
        blocking_calls: list[str] = []

        def fake_blocking(venv_path: str) -> bool:
            blocking_calls.append(venv_path)
            return True

        monkeypatch.setattr(loader_mod, "is_preinstall_done", lambda vp: False)
        monkeypatch.setattr(loader_mod, "ensure_venv_blocking", fake_blocking)
        adapter = _adapter(mini_skill)

        result = json.loads(_run(adapter.execute({"command": "hello --name Dep"}, _context())))

        assert result["output"] == "hello Dep"  # 装完继续执行，错误未抛给 LLM
        assert blocking_calls == [expected_venv_dir]  # 参数为反推的 venv 根目录

    def test_failed_install_returns_retryable_without_running_script(self, mini_skill: Path, tmp_path: Path, monkeypatch):
        """补装失败 → retryable JSON 交还 LLM，脚本不执行（不产出二次误导错误）。"""
        import skills.loader as loader_mod

        monkeypatch.setattr(loader_mod, "is_preinstall_done", lambda vp: False)
        monkeypatch.setattr(loader_mod, "ensure_venv_blocking", lambda vp: False)
        exec_calls: list[list[str]] = []

        async def spy_exec(*cmd, **kwargs):
            exec_calls.append(list(cmd))
            raise AssertionError("script must not run when preinstall failed")

        monkeypatch.setattr(loader_mod.asyncio, "create_subprocess_exec", spy_exec)
        ctx = SkillContext(
            workspace_path=os.getcwd(),
            venv_python=str(tmp_path / "task" / ".venv" / "Scripts" / "python.exe"),
            settings={},
        )
        adapter = _adapter(mini_skill)

        result = json.loads(_run(adapter.execute({"command": "hello"}, ctx)))

        assert result["retryable"] is True
        assert "安装失败" in result["error"]
        assert exec_calls == []


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


class TestSkillEnvSetupVisibility:
    """task_agent 首用环境可见性纯函数（2.8 §5.5 step_note 事件）。

    预检（_venv_env_needs_setup，tool 链与 skill 链共用）决定是否向流推
    「正在准备运行环境」提示；终态（_skill_env_setup_failed，skill 链专属）
    决定收尾文案语义——业务失败（exit_code≠0）不算环境失败，retryable
    预装失败才算。
    """

    def test_needs_setup_false_when_no_venv(self):
        """venv_path 空（chat-only 任务）不会发生预装，不推 note。"""
        from agent.task_agent import _venv_env_needs_setup

        assert _venv_env_needs_setup("") is False

    def test_needs_setup_true_when_venv_dir_missing(self, tmp_path: Path):
        """venv 目录不存在（首用）→ 预检为真 → 应推 note。"""
        from agent.task_agent import _venv_env_needs_setup

        venv_path = str(tmp_path / "venv")
        assert _venv_env_needs_setup(venv_path) is True

    def test_needs_setup_false_when_ready(self, tmp_path: Path):
        """venv python + marker 均在位（快路径）→ 不推 note。"""
        from agent.task_agent import _venv_env_needs_setup
        from tools.venv_manager import get_venv_python, _PREINSTALL_MARKER

        venv_dir = tmp_path / "venv"
        venv_dir.mkdir()
        py = Path(get_venv_python(str(venv_dir)))
        py.parent.mkdir(parents=True, exist_ok=True)
        py.write_text("", encoding="utf-8")
        (venv_dir / _PREINSTALL_MARKER).write_text("", encoding="utf-8")
        assert _venv_env_needs_setup(str(venv_dir)) is False

    def test_needs_setup_true_when_marker_missing_but_python_exists(self, tmp_path: Path):
        """python 在位但 marker 缺失（预装中断/被删）→ 仍要补装 → 推 note。"""
        from agent.task_agent import _venv_env_needs_setup
        from tools.venv_manager import get_venv_python

        venv_dir = tmp_path / "venv"
        py = Path(get_venv_python(str(venv_dir)))
        py.parent.mkdir(parents=True, exist_ok=True)
        py.write_text("", encoding="utf-8")
        assert _venv_env_needs_setup(str(venv_dir)) is True

    def test_setup_failed_when_venv_python_empty(self):
        """_ensure_task_venv 失败回退空串 → 环境未就绪。"""
        from agent.task_agent import _skill_env_setup_failed

        assert _skill_env_setup_failed("", '{"output": "x"}') is True

    def test_setup_failed_when_retryable(self):
        """loader 预装失败专属契约 retryable=true → 环境失败。"""
        from agent.task_agent import _skill_env_setup_failed

        assert _skill_env_setup_failed("py", '{"error": "install failed", "retryable": true}') is True

    def test_setup_ok_when_business_error(self):
        """exit_code≠0 的业务失败不算环境失败——环境已就绪，只是脚本没跑成。"""
        from agent.task_agent import _skill_env_setup_failed

        assert _skill_env_setup_failed("py", '{"error": "boom", "exit_code": 1}') is False

    def test_setup_ok_when_non_json(self):
        """非 JSON 结果（如纯文本 output）不算环境失败。"""
        from agent.task_agent import _skill_env_setup_failed

        assert _skill_env_setup_failed("py", "plain text") is False


class TestTaskIdInjection:
    """2.8 §3.3/§7.1：_run_script 注入 INKCUBE_TASK_ID（租约锁 owner）。

    注入发生在 spawn env 层（一处改动全 skill 受益）：有 task_id 才注入，
    空值保持环境干净——锁 owner 判定不会误把无主执行记到某个 task 名下。
    """

    @staticmethod
    def _fake_exec(captured: dict):
        class FakeProc:
            returncode = 0

            async def communicate(self):
                return (b'{"output": "ok"}', b"")

        async def fake_exec(*cmd, **kwargs):
            captured["env"] = kwargs.get("env")
            return FakeProc()

        return fake_exec

    def test_task_id_injected_into_spawn_env(self, mini_skill: Path, monkeypatch):
        import skills.loader as loader_mod

        captured: dict = {}
        monkeypatch.setattr(
            loader_mod.asyncio, "create_subprocess_exec", self._fake_exec(captured)
        )
        ctx = SkillContext(
            workspace_path=os.getcwd(),
            venv_python=sys.executable,
            task_id="task-abc",
            settings={},
        )
        result = json.loads(
            _run(_adapter(mini_skill).execute({"command": "hello"}, ctx))
        )

        assert result["output"] == "ok"  # FakeProc stdout 被正常解析
        assert captured["env"]["INKCUBE_TASK_ID"] == "task-abc"

    def test_empty_task_id_not_injected(self, mini_skill: Path, monkeypatch):
        import skills.loader as loader_mod

        captured: dict = {}
        monkeypatch.setattr(
            loader_mod.asyncio, "create_subprocess_exec", self._fake_exec(captured)
        )
        ctx = SkillContext(
            workspace_path=os.getcwd(),
            venv_python=sys.executable,
            task_id="",
            settings={},
        )
        json.loads(_run(_adapter(mini_skill).execute({"command": "hello"}, ctx)))

        assert "INKCUBE_TASK_ID" not in captured["env"]
