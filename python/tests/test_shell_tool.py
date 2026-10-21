"""Shell 工具安全与超时回归测试。

覆盖任务 c2f24e5c（2026-10-13）交互⑤复盘暴露的三类问题：
1. check_safe_command 只做首 token 前缀匹配 → "echo hi & rd /s /q" 借
   echo 前缀整条放行（白名单走私）；改为按链式分隔符拆段全段过闸。
2. 只读命令 tasklist/query 缺失 → "tasklist | findstr" 一类纯查询被
   误弹确认窗（一次任务连弹 2 次）。
3. 超时只 kill cmd.exe 外壳 → 分离启动的 GUI 子进程持有管道，
   communicate() 清理阶段挂起（30s 超时实际挂 ~80s）；超时后
   taskkill /T（Windows）/ killpg（POSIX）终止整棵进程树。
"""
import asyncio
import json
import os
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools.shell_tool as st
from tools.shell_tool import (
    ShellTool,
    _run_shell_sync,
    check_dangerous_command,
    check_safe_command,
)


class TestCheckSafeCommandSegments:
    """白名单按链式段判定：前缀走私被堵、纯查询放行。"""

    def test_single_segment_unchanged(self):
        """原有单段行为回归：仍放行。"""
        assert check_safe_command("ls -la")
        assert check_safe_command("cat a.txt")
        assert check_safe_command("git status")

    def test_safe_chain_all_segments_pass(self):
        assert check_safe_command("cd sub && cat a.txt")
        assert check_safe_command("tasklist | findstr python")
        assert check_safe_command("pwd & whoami")

    def test_prefix_smuggling_blocked(self):
        """回归 c2f24e5c：echo 前缀 + 危险尾部必须整体不放行（走确认链）。"""
        assert not check_safe_command("echo hi & rd /s /q build")
        assert not check_safe_command("echo start && rm -rf /")
        assert not check_safe_command("whoami; del /q a.txt")

    def test_new_readonly_commands(self):
        """回归 c2f24e5c：tasklist | findstr 曾连弹 2 次确认窗。"""
        assert check_safe_command("tasklist /v")
        assert check_safe_command("query session")
        assert check_safe_command("netstat -an")
        assert check_safe_command("ps aux")

    def test_find_destructive_modes_blocked(self):
        assert not check_safe_command("find . -name x -delete")
        assert not check_safe_command("find . -exec rm {} \\;")
        assert check_safe_command("find . -name x")  # 纯查找仍放行

    def test_command_substitution_blocked(self):
        """POSIX 命令替换可执行任意代码，一律不自动放行。"""
        assert not check_safe_command("echo $(rm -rf x)")
        assert not check_safe_command("ls `pwd`")

    def test_empty_command_not_safe(self):
        assert not check_safe_command("")
        assert not check_safe_command("   ")


class TestDangerousPatternsWindows:
    """黑名单补口：盘根/系统目录删除必拦，工作区清理不误伤。"""

    def test_drive_root_delete_blocked(self):
        assert check_dangerous_command("rd /s /q C:\\")
        assert check_dangerous_command("rmdir /s /q D:")

    def test_system_dir_delete_blocked(self):
        assert check_dangerous_command("del /f /s /q C:\\Windows\\system32\\config")
        assert check_dangerous_command("rd /s /q C:\\Program Files")

    def test_workspace_cleanup_not_blocked(self):
        """回归交互⑨：清理工作区属合法操作（smart 模式走确认链，黑名单不拦）。"""
        assert check_dangerous_command("rd /s /q build") is None
        assert check_dangerous_command("del temp.txt") is None
        assert check_dangerous_command("rd /s /q ./output") is None


class TestDecodeProcOutput:
    """输出解码链：UTF-8 优先，回退 ANSI 代码页。

    根因：后端带 PYTHONUTF8=1（python-manager.js），getpreferredencoding()
    返回 utf-8，直接用它解码 cmd 内置命令的 GBK 输出会乱码
    （c2f24e5c [87] 实证：dir 输出全乱）。
    """

    def test_utf8_bytes_decoded_directly(self):
        assert st._decode_proc_output("新能源".encode("utf-8")) == "新能源"
        assert st._decode_proc_output(b"plain ascii") == "plain ascii"

    def test_gbk_bytes_fallback(self):
        """GBK 字节流不是合法 UTF-8 → 回退 mbcs（中文 Windows = cp936）。"""
        assert st._decode_proc_output("新能源".encode("gbk")) == "新能源"

    def test_invalid_bytes_never_raises(self):
        out = st._decode_proc_output(b"\xff\x81\x40\xff")
        assert isinstance(out, str)


class TestUtf8ConsolePreset:
    """Windows 控制台输出 CP 预设（2026-10 乱码根治）。

    本机实验证据（dev/_chcp_experiment.py，收尾时删除，结论沉淀于此）：
    - cmd.exe 在实例初始化时缓存控制台输出 CP：计划中的命令前缀
      `chcp 65001>nul&&dir` 同实例内执行，dir 仍输出 cp936 字节
      （b'\xb1\xa8\xb8\xe6...'，报告文件一）——前缀注入方案无效，已弃用；
    - CP 变更持久于共享控制台：SetConsoleOutputCP(65001) 后新起的 cmd
      实例 dir/echo 输出 UTF-8（b'\xe6\x8a\xa5...'）；CREATE_NO_WINDOW
      隐藏控制台（= 生产 libuv pipe-spawn 形态）下同样成立。
    故修复落在 Python 侧一次性预设（_ensure_utf8_console），
    _decode_proc_output 的 mbcs 回退保留为纵深防御。
    """

    @pytest.mark.skipif(os.name != "nt", reason="Windows console API")
    def test_preset_sets_cp_65001(self):
        import ctypes

        k32 = ctypes.windll.kernel32
        original = k32.GetConsoleOutputCP()
        try:
            monkey_flag = st._console_cp_done
            st._console_cp_done = False
            st._ensure_utf8_console()
            assert st._console_cp_done is True
            # 已附控制台（含隐藏）时置为 65001；真无控制台的极端环境 API
            # 返回 0 → 不强求断言，解码端纵深仍在
            if original:
                assert k32.GetConsoleOutputCP() == 65001
        finally:
            st._console_cp_done = monkey_flag
            if original:
                k32.SetConsoleOutputCP(original)

    @pytest.mark.skipif(os.name != "nt", reason="Windows console API")
    def test_idempotent_flag_skips_reset(self, monkeypatch):
        """标志已置位时早退：不重复调用 console API（真集成路径由上一用例覆盖）。"""
        import ctypes

        k32 = ctypes.windll.kernel32
        cp_before = k32.GetConsoleOutputCP()
        monkeypatch.setattr(st, "_console_cp_done", True)
        st._ensure_utf8_console()  # 早退，不改 CP
        assert k32.GetConsoleOutputCP() == cp_before

    def test_noop_flag_off_windows(self, monkeypatch):
        monkeypatch.setattr(st.os, "name", "posix")
        monkeypatch.setattr(st, "_console_cp_done", False)
        st._ensure_utf8_console()
        assert st._console_cp_done is False  # 早退不置位，不触碰 console API


class TestTimeoutProcessTree:
    """超时终止整棵进程树（根因：kill 只杀 shell 外壳，子进程持管道）。"""

    def test_timeout_kills_tree_and_returns_partial_output(self, monkeypatch):
        comm_calls: list = []
        taskkill_calls: list = []

        class FakeProc:
            """首次 communicate(timeout) 抛超时；树死后收尾调用返回部分输出。"""

            pid = 4242

            def __init__(self):
                self.calls = 0

            def communicate(self, timeout=None):
                comm_calls.append(timeout)
                self.calls += 1
                if self.calls == 1:
                    raise subprocess.TimeoutExpired(cmd="x", timeout=timeout)
                # 新契约：communicate 返回原始 bytes，由 _decode_proc_output 解码
                return b"partial-out", b"partial-err"

            def kill(self):
                pass

        monkeypatch.setattr(st.subprocess, "Popen", lambda *a, **kw: FakeProc())

        def fake_run(cmd, **kw):
            taskkill_calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(st.subprocess, "run", fake_run)

        stdout, stderr, returncode, timed_out = _run_shell_sync("cmd", 30, ".", {})

        assert timed_out is True
        assert returncode is None
        assert stdout == "partial-out"
        assert "命令执行超时" in stderr
        assert taskkill_calls[0][:4] == ["taskkill", "/PID", "4242", "/T"]
        # 两次 communicate：首次带超时抛 TimeoutExpired，收尾传清理上限 5s
        assert comm_calls == [30, 5]

    def test_posix_branch_uses_killpg(self, monkeypatch):
        import signal as signal_mod

        pg_calls: list = []

        class FakeProc:
            pid = 100

            def communicate(self, timeout=None):
                if timeout is not None:
                    raise subprocess.TimeoutExpired(cmd="x", timeout=timeout)
                return "", ""

            def kill(self):
                pass

        monkeypatch.setattr(st.subprocess, "Popen", lambda *a, **kw: FakeProc())
        monkeypatch.setattr(os, "name", "posix")
        monkeypatch.setattr(os, "getpgid", lambda pid: 100, raising=False)
        monkeypatch.setattr(
            os, "killpg", lambda pgid, sig: pg_calls.append((pgid, sig)),
            raising=False,
        )
        monkeypatch.setattr(signal_mod, "SIGKILL", 9, raising=False)

        _, _, _, timed_out = _run_shell_sync("cmd", 30, ".", {})

        assert timed_out is True
        assert pg_calls == [(100, 9)]

    def test_real_timeout_returns_promptly(self, tmp_path):
        """真实冒烟：超时后树被杀、立即返回且保留部分 stdout。

        修复前 subprocess.run 的清理阶段会等子进程退出（c2f24e5c 实证
        30s 超时挂 ~80s）；若回归，本用例将挂满 sleep 全程而超时失败。
        """
        tool = ShellTool(workspace_dir=str(tmp_path))
        tool.timeout = 2
        t0 = time.monotonic()
        result = json.loads(asyncio.run(tool.execute(
            f'"{sys.executable}" -c "print(\'started\', flush=True); '
            f'import time; time.sleep(30)"'
        )))
        elapsed = time.monotonic() - t0

        assert result["error_type"] == "TimeoutError"
        assert result["suggestion"]
        assert "started" in result["stdout"]
        assert elapsed < 15, f"timeout cleanup stalled: {elapsed:.1f}s"
