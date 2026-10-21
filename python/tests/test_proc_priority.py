"""python/tests/test_proc_priority.py — 2.11 §5.6 R3 子进程低优先级回归。

任务执行链 spawn 的 venv shell/python/uv 子进程必须以低优先级启动
（Windows BELOW_NORMAL_PRIORITY_CLASS / POSIX nice 10）：定时采集或
重装包运行中，前台交互天然抢占（验收「前台无可感知卡顿」）。

覆盖两层：
  1. helper 本体：low_priority_kwargs() 按平台返回正确键；
  2. 接线断言：五个 spawn 点（shell/code_executor/package_manager/
     venv_manager/skill loader）确实把低优先级 kwargs 传给 spawn 调用。
用户前台主动操作（skill 安装 git clone）不降级，不在断言范围。
"""
import asyncio
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.proc_priority import low_priority_kwargs


@pytest.mark.skipif(os.name != "nt", reason="Windows priority class")
def test_windows_kwargs_use_below_normal_class():
    kw = low_priority_kwargs()
    assert kw["creationflags"] == subprocess.BELOW_NORMAL_PRIORITY_CLASS
    assert "preexec_fn" not in kw          # Windows 不接受 preexec_fn


@pytest.mark.skipif(os.name == "nt", reason="POSIX nice")
def test_posix_kwargs_use_nice10(monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    kw = low_priority_kwargs()
    fn = kw.pop("preexec_fn")
    assert callable(fn) and not kw          # 仅 preexec_fn，无 creationflags
    before = os.nice(0)
    fn()                                     # fork 后回调：仅一次 nice 系统调用
    assert os.nice(0) >= before + 10


class _FakeProc:
    returncode = 0
    pid = 0

    def communicate(self, timeout=None):
        return (b"", b"")

    def kill(self):
        pass


def _fake_run(captured: dict, returncode: int = 0):
    def fake_run(*args, **kwargs):
        captured.update(kwargs)

        class R:
            stdout = ""
            stderr = ""

        R.returncode = returncode
        return R()

    return fake_run


@pytest.mark.skipif(os.name != "nt", reason="Windows creationflags 接线断言")
def test_shell_tool_spawns_low_priority(monkeypatch):
    import tools.shell_tool as st

    captured: dict = {}

    def fake_popen(*args, **kwargs):
        captured.update(kwargs)
        return _FakeProc()

    monkeypatch.setattr(st.subprocess, "Popen", fake_popen)
    st._run_shell_sync("echo hi", 10, ".", {})
    assert captured["creationflags"] == subprocess.BELOW_NORMAL_PRIORITY_CLASS


@pytest.mark.skipif(os.name != "nt", reason="Windows creationflags 接线断言")
def test_code_executor_spawns_low_priority(monkeypatch, tmp_path):
    import tools.code_executor as ce

    captured: dict = {}
    monkeypatch.setattr(ce.subprocess, "run", _fake_run(captured))
    ex = ce.CodeExecutorTool(workspace_dir=str(tmp_path))
    asyncio.run(ex.execute("print(1)"))
    assert captured["creationflags"] == subprocess.BELOW_NORMAL_PRIORITY_CLASS


@pytest.mark.skipif(os.name != "nt", reason="Windows creationflags 接线断言")
def test_package_manager_spawns_low_priority(monkeypatch, tmp_path):
    import tools.package_manager as pm

    captured: dict = {}
    monkeypatch.setattr(pm.subprocess, "run", _fake_run(captured, returncode=1))
    tool = pm.PackageManagerTool(workspace_dir=str(tmp_path),
                                 venv_path=str(tmp_path / ".venv"))
    monkeypatch.setattr(tool, "_find_uv", lambda: str(tmp_path / "uv.exe"))
    asyncio.run(tool.execute(action="list"))
    assert captured["creationflags"] == subprocess.BELOW_NORMAL_PRIORITY_CLASS


@pytest.mark.skipif(os.name != "nt", reason="Windows creationflags 接线断言")
def test_venv_manager_spawns_low_priority(monkeypatch, tmp_path):
    import tools.venv_manager as vm

    captured: dict = {}
    monkeypatch.setattr(vm.subprocess, "run", _fake_run(captured))
    monkeypatch.setattr(vm, "_find_uv", lambda: str(tmp_path / "uv.exe"))
    vm.create_venv(str(tmp_path / "task"))
    assert captured["creationflags"] == subprocess.BELOW_NORMAL_PRIORITY_CLASS


@pytest.mark.skipif(os.name != "nt", reason="Windows creationflags 接线断言")
def test_skill_loader_spawns_low_priority(tmp_path, monkeypatch):
    from skills.loader import AgentSkillAdapter, SkillContext
    from skills.parser import parse_skill_md

    skill_dir = tmp_path / "prio-demo"
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: prio-demo\ndescription: prio\n---\n", encoding="utf-8")
    (skill_dir / "scripts" / "main.py").write_text(
        "print('ok')\n", encoding="utf-8")

    import skills.loader as loader_mod

    captured: dict = {}

    class _AsyncProc:
        returncode = 0

        async def communicate(self):
            return (b"", b"")

    async def fake_exec(*cmd, **kwargs):
        captured.update(kwargs)
        return _AsyncProc()

    monkeypatch.setattr(loader_mod.asyncio, "create_subprocess_exec", fake_exec)
    adapter = AgentSkillAdapter(str(skill_dir),
                                parse_skill_md(str(skill_dir / "SKILL.md")))
    ctx = SkillContext(workspace_path=str(tmp_path),
                       venv_python=sys.executable, task_id="t1", settings={})
    asyncio.run(adapter._run_script(
        str(skill_dir / "scripts" / "main.py"), "", ctx))
    assert captured["creationflags"] == subprocess.BELOW_NORMAL_PRIORITY_CLASS
