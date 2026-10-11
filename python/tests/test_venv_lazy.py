"""python/tests/test_venv_lazy.py — 任务 venv 懒创建回归测试。

回归背景：create_task 此前即在 data_dir/tasks/<id> 下建 .venv 并后台预装
6 个包——纯聊天任务也占 20~100MB/个（uv 缓存跨盘符时硬链接失效、整包复制）。
改为懒创建后：
  1. create_task 只登记确定性 venv 路径，不落盘；
  2. task_agent 首次执行代码/装包/跑 Skill 时经 venv_manager.ensure_venv 落盘；
  3. 预装以 marker 文件保证每 venv 至多一次，失败可重试；
  4. main.py 将 UV_CACHE_DIR 指到数据目录同盘，同版本包硬链接零增量。
"""
import asyncio
import os
import subprocess as _real_subprocess
import sys
import threading
import time
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tools.venv_manager as vm
from agent.task_agent import TaskAgent
from db.database import Database


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "inkcube.db"))
    database.initialize()
    return database


@pytest.fixture
def agent(db):
    return TaskAgent(db)


def _fake_venv_skeleton(venv_path: str):
    """按当前平台构造最小 venv 骨架（get_venv_python 指向的文件存在）。"""
    py = vm.get_venv_python(venv_path)
    os.makedirs(os.path.dirname(py), exist_ok=True)
    with open(py, "w", encoding="utf-8") as f:
        f.write("")


def _wait_preinstall_done(venv_path: str, timeout: float = 10.0) -> None:
    """确定性等待：预装在 daemon 线程异步执行，断言前须等线程退出临界区。
    轮询该 venv 的预装锁——acquire(blocking=False) 成功即线程已 release；
    超时直接失败而非挂死。"""
    lock = vm._get_preinstall_lock(venv_path)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if lock.acquire(blocking=False):
            lock.release()  # 恢复未占用状态，后续 ensure 语义不变
            return
        time.sleep(0.01)
    raise AssertionError("preinstall thread did not finish in 10s")


# ── 1. create_task 懒登记 ──────────────────────────────────


class TestCreateTaskLazyVenv:
    def test_managed_task_records_venv_path_without_materializing(self, db, tmp_path):
        """自动建目录任务：登记确定性 .venv 路径，但不落盘 venv。"""
        data_dir = tmp_path / "data"
        task = db.create_task("t", str(data_dir), "")

        venv_path = task["venv_path"]
        assert venv_path == str(data_dir / "tasks" / task["id"] / ".venv")
        assert not os.path.isdir(venv_path)  # 懒创建：venv 目录不存在
        # workspace 照旧落盘（任务工作区是任务本体，venv 才是延迟项）
        assert os.path.isdir(data_dir / "tasks" / task["id"] / "workspace")

    def test_external_workspace_has_no_venv(self, db, tmp_path):
        """用户自选工作区：不登记 venv（与既有行为一致）。"""
        ws = tmp_path / "proj" / "ws"
        ws.mkdir(parents=True)
        task = db.create_task("t", str(tmp_path / "data"), str(ws))
        assert task["venv_path"] == ""

    def test_sibling_dir_prefix_not_matched(self, db, tmp_path):
        """data2 与 data 仅前缀同名时不误判为子目录（normcase + os.sep 边界，
        Windows 大小写不敏感、POSIX 区分大小写，两平台语义各自正确）。"""
        data_dir = tmp_path / "data"
        ws = tmp_path / "data2" / "ws"
        ws.mkdir(parents=True)
        task = db.create_task("t", str(data_dir), str(ws))
        assert task["venv_path"] == ""


# ── 2. ensure_venv 懒落盘 + 预装 marker ────────────────────


class TestEnsureVenv:
    def _patch_stdlib_venv(self, monkeypatch):
        """让真实 create_venv 走 stdlib 分支且不调真 Python：mock subprocess 造骨架。"""
        monkeypatch.setattr(vm, "_find_uv", lambda: None)

        def fake_run(cmd, **kwargs):
            venv_path = cmd[-1]  # [python, -m, venv, <venv_path>]
            _fake_venv_skeleton(venv_path)
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(vm, "subprocess", types.SimpleNamespace(
            run=fake_run,
            CalledProcessError=_real_subprocess.CalledProcessError,
            TimeoutExpired=_real_subprocess.TimeoutExpired,
        ))

    def test_creates_and_preinstalls_once(self, tmp_path, monkeypatch):
        """首次 ensure：建 venv + 预装 + 写 marker；再次 ensure 不重复预装。"""
        self._patch_stdlib_venv(monkeypatch)
        installs = []
        monkeypatch.setattr(
            vm, "preinstall_packages", lambda py: installs.append(py) or True)

        venv_path = vm.ensure_venv(str(tmp_path))
        assert os.path.isfile(vm.get_venv_python(venv_path))
        _wait_preinstall_done(venv_path)
        assert len(installs) == 1
        assert os.path.isfile(os.path.join(venv_path, vm._PREINSTALL_MARKER))

        # marker 在位 → 不再预装（重启/重复执行不重装）
        vm.ensure_venv(str(tmp_path))
        _wait_preinstall_done(venv_path)
        assert len(installs) == 1

    def test_preinstall_failure_retries_next_ensure(self, tmp_path, monkeypatch):
        """预装失败不写 marker → 下次 ensure 重试（网络恢复后自愈）。"""
        venv_path = str(tmp_path / ".venv")
        _fake_venv_skeleton(venv_path)  # venv 已存在 → create_venv no-op
        calls = []

        def flaky(py):
            calls.append(py)
            return len(calls) > 1  # 首次失败，其后成功

        monkeypatch.setattr(vm, "preinstall_packages", flaky)

        vm.ensure_venv(str(tmp_path))
        _wait_preinstall_done(venv_path)
        assert len(calls) == 1
        assert not os.path.isfile(os.path.join(venv_path, vm._PREINSTALL_MARKER))

        vm.ensure_venv(str(tmp_path))
        _wait_preinstall_done(venv_path)
        assert len(calls) == 2
        assert os.path.isfile(os.path.join(venv_path, vm._PREINSTALL_MARKER))

    def test_existing_venv_with_marker_skips_preinstall(self, tmp_path, monkeypatch):
        """已有 marker 的存量 venv：ensure 直接返回，零预装（旧任务兼容）。"""
        venv_path = str(tmp_path / ".venv")
        _fake_venv_skeleton(venv_path)
        with open(os.path.join(venv_path, vm._PREINSTALL_MARKER), "w",
                  encoding="utf-8") as f:
            f.write("ok")
        called = []
        monkeypatch.setattr(
            vm, "preinstall_packages", lambda py: called.append(py) or True)

        assert vm.ensure_venv(str(tmp_path)) == venv_path
        assert called == []


# ── 2b. ensure_venv_blocking：skill CLI 首用同步补装 ────────


class TestEnsureVenvBlocking:
    def test_playwright_in_main_package_list(self):
        """防回归：playwright 必须在主预装清单（一条命令一个 marker 一套重试），
        禁止再次拆成独立旁路（旁路曾被主命令失败级联跳过、失败无重试）。"""
        assert "playwright" in vm.PREINSTALLED_PACKAGES

    def test_missing_marker_installs_and_writes_marker(self, tmp_path, monkeypatch):
        """marker 缺失 → 同步预装 → 写 marker → True（缺就装、装完继续）。"""
        venv_path = str(tmp_path / ".venv")
        _fake_venv_skeleton(venv_path)
        installs = []
        monkeypatch.setattr(
            vm, "preinstall_packages", lambda py: installs.append(py) or True)

        assert vm.ensure_venv_blocking(venv_path) is True
        assert len(installs) == 1
        assert os.path.isfile(os.path.join(venv_path, vm._PREINSTALL_MARKER))

    def test_marker_present_is_zero_cost_fast_path(self, tmp_path, monkeypatch):
        """marker 在位 → 直接 True，不触发预装（每次 skill 执行的常态路径）。"""
        venv_path = str(tmp_path / ".venv")
        _fake_venv_skeleton(venv_path)
        with open(os.path.join(venv_path, vm._PREINSTALL_MARKER), "w",
                  encoding="utf-8") as f:
            f.write("ok")
        called = []
        monkeypatch.setattr(
            vm, "preinstall_packages", lambda py: called.append(py) or True)

        assert vm.ensure_venv_blocking(venv_path) is True
        assert called == []

    def test_install_failure_returns_false_without_marker(self, tmp_path, monkeypatch):
        """预装失败 → False 且不写 marker（调用方转 retryable，下次调用重试）。"""
        venv_path = str(tmp_path / ".venv")
        _fake_venv_skeleton(venv_path)
        monkeypatch.setattr(vm, "preinstall_packages", lambda py: False)

        assert vm.ensure_venv_blocking(venv_path) is False
        assert not os.path.isfile(os.path.join(venv_path, vm._PREINSTALL_MARKER))

    def test_waits_for_inflight_background_preinstall(self, tmp_path, monkeypatch):
        """后台预装线程持锁进行中 → blocking 等待后双检 marker 受益，不重复装
        （互斥语义：后台线程与执行侧并发时只有一方真正跑安装命令）。"""
        venv_path = str(tmp_path / ".venv")
        _fake_venv_skeleton(venv_path)
        calls = []
        lock = vm._get_preinstall_lock(venv_path)

        def bg():
            with lock:
                time.sleep(0.2)
                vm._write_preinstall_marker(venv_path)

        t = threading.Thread(target=bg)
        t.start()
        time.sleep(0.05)  # 确保 bg 先持锁，进入等待分支
        monkeypatch.setattr(
            vm, "preinstall_packages", lambda py: calls.append(py) or True)

        assert vm.ensure_venv_blocking(venv_path) is True
        t.join()
        assert calls == []  # 双检发现 marker，未重复安装
        assert os.path.isfile(os.path.join(venv_path, vm._PREINSTALL_MARKER))

    def test_get_venv_path_from_python(self):
        """解释器路径反推 venv 根目录（win: Scripts/.. 与 posix: bin/.. 同构）。"""
        assert vm.get_venv_path_from_python(
            os.path.join("d", "x", "Scripts", "python.exe")) == os.path.join("d", "x")
        assert vm.get_venv_path_from_python(
            os.path.join("d", "x", "bin", "python")) == os.path.join("d", "x")


# ── 2c. preinstall_packages 自愈重试编排 ─────────────────


class TestPreinstallFallback:
    """预装自愈编排：cached → 修复 archive 改名 + 重试 → --no-cache 绕行
    → 镜像 + --no-cache（官方源不可达/极慢时的可达性兑底）。

    背景：Windows 上 uv 缓存的解压→archive-v0 改名可被实时防护稳定阻断
    （os error 5，与 uv 版本无关，2.8 §12 F2），瞬态网络失败与官方源慢网
    也会导致预装退出；单次尝试的 preinstall 在这类环境下永远无法自愈。
    """

    def _patch_uv(self, monkeypatch, results, sleeps):
        """mock uv 存在、_run_preinstall 按序返回 results、time.sleep 记录。

        results 元素可为 bool（默认错误文案）或 (ok, err) 元组。
        返回 (cmds, repairs)：cmds 为各次安装命令，repairs 为每轮修复拿到的错误全文。
        """
        monkeypatch.setattr(vm, "resolve_tool",
                            lambda name: "uv.exe" if name == "uv" else None)
        repairs: list[str] = []
        monkeypatch.setattr(
            vm, "_repair_uv_archive_rename",
            lambda err: repairs.append(err) or 0)
        cmds = []

        def fake_run(cmd):
            cmds.append(cmd)
            idx = len(cmds) - 1
            r = results[idx] if idx < len(results) else False
            if isinstance(r, tuple):
                return r
            return (r, "" if r else "exit 1: boom")

        monkeypatch.setattr(vm, "_run_preinstall", fake_run)
        monkeypatch.setattr(vm.time, "sleep", lambda s: sleeps.append(s))
        return cmds, repairs

    def test_first_attempt_success_no_retry(self, monkeypatch):
        """首次即成功 → 只调 1 次、无等待、无修复（常态零开销）。"""
        sleeps: list = []
        cmds, repairs = self._patch_uv(monkeypatch, [True], sleeps)

        assert vm.preinstall_packages("fake/py") is True
        assert len(cmds) == 1
        assert "--no-cache" not in cmds[0]
        assert sleeps == []
        assert repairs == []

    def test_transient_failure_repaired_then_retry_succeeds(self, monkeypatch):
        """首次失败 → 等待后修复 archive 再重试成功（零系统操作自愈）。"""
        sleeps: list = []
        cmds, repairs = self._patch_uv(monkeypatch, [False, True], sleeps)

        assert vm.preinstall_packages("fake/py") is True
        assert len(cmds) == 2
        assert all("--no-cache" not in c for c in cmds)
        assert sleeps == [vm._PREINSTALL_RETRY_DELAY]
        assert len(repairs) == 1
        assert "exit 1: boom" in repairs[0]  # 修复拿到上轮错误全文供解析

    def test_repair_blocked_rename_from_stderr(self, tmp_path):
        """从 uv stderr 提取改名对并亲手完成：tmp 存在且 dest 不存在 → rename。"""
        tmp_dir = tmp_path / ".tmpUQVz67"
        tmp_dir.mkdir()
        (tmp_dir / "marker.txt").write_text("x", encoding="utf-8")
        dest_dir = tmp_path / "archive-v0" / "WKFokGJGUE5kmkb-lsKZH"
        stderr = (f"error: Failed to write to the distribution cache\n"
                  f"  cause: failed to rename file from {tmp_dir} to "
                  f"{dest_dir}: 拒绝访问。\u0028os error 5\u0029")

        assert vm._repair_uv_archive_rename(stderr) == 1
        assert dest_dir.is_dir()  # 真实 rename 发生，内容随目录迁移
        assert (dest_dir / "marker.txt").is_file()
        assert not tmp_dir.exists()

    def test_repair_skips_missing_tmp_and_existing_dest(self, tmp_path):
        """tmp 已被清理 / dest 已有同名条目 → 跳过该对，不报错。"""
        gone = tmp_path / ".tmpGONE"
        dest_exist = tmp_path / "destA"
        dest_exist.mkdir()
        stderr = (f"failed to rename file from {gone} to {tmp_path / 'destB'}: x\n"
                  f"failed to rename file from {tmp_path / '.tmpOK'} to {dest_exist}: x")

        assert vm._repair_uv_archive_rename(stderr) == 0
        assert not (tmp_path / "destB").exists()

    def test_repair_parses_word_wrapped_stderr(self, tmp_path):
        """uv 管道输出按固定宽度折行（路径在「from」后断到下一行）→
        折叠空白后仍能解析（2026-10-11 实测踩坑：不折叠则零匹配静默空转）。"""
        tmp_dir = tmp_path / ".tmpWRAPPED"
        tmp_dir.mkdir()
        dest_dir = tmp_path / "archive-v0" / "FtA9Z7Awz3ITXJow"
        # 模拟 uv 80 列 word-wrap：from 后直接换行，路径在下一行
        wrapped = (f"error: Failed to write to the distribution cache\n"
                   f"  cause: failed to rename file from\n"
                   f"{tmp_dir} to\n"
                   f"{dest_dir}: \u62d2\u7edd\u8bbf\u95ee\u3002(os error 5)\n")

        assert vm._repair_uv_archive_rename(wrapped) == 1
        assert dest_dir.is_dir()

    def test_repair_handles_multiple_rename_pairs(self, tmp_path):
        """≥2 个改名错误逐段配对全部修复。

        回归（2026-10-11 review 发现）：全局单正则的贪婪 dest 会把后续
        对整个吞进前一条 dest（实测失配，仅剩 1 条垃圾对，第二对永不
        修复）；改为按关键字切段后各段独立配对。
        """
        tmp_a = tmp_path / ".tmpAAA"
        tmp_a.mkdir()
        (tmp_a / "marker.txt").write_text("a", encoding="utf-8")
        dest_a = tmp_path / "archive-v0" / "hashA"
        tmp_b = tmp_path / ".tmpBBB"
        tmp_b.mkdir()
        (tmp_b / "marker.txt").write_text("b", encoding="utf-8")
        dest_b = tmp_path / "archive-v0" / "hashB"
        stderr = (f"failed to rename file from {tmp_a} to {dest_a}: "
                  f"拒绝访问。(os error 5)\n"
                  f"failed to rename file from {tmp_b} to {dest_b}: "
                  f"拒绝访问。(os error 5)")

        assert vm._repair_uv_archive_rename(stderr) == 2
        assert dest_a.is_dir() and (dest_a / "marker.txt").is_file()
        assert dest_b.is_dir() and (dest_b / "marker.txt").is_file()
        assert not tmp_a.exists() and not tmp_b.exists()

    def test_rename_blocked_falls_back_to_mirror(self, monkeypatch):
        """修复后仍失败 → 第 3 级镜像兑底成功（镜像 + --no-cache 一步到位）。

        独立官方源 --no-cache 层已裁剪：慢网时必 300s 超时（2026-10-11 实测
        三级全超时 15 分钟零成果），成功场景与镜像级重合，信息增量小。
        """
        sleeps: list = []
        cmds, repairs = self._patch_uv(monkeypatch, [False, False, True], sleeps)

        assert vm.preinstall_packages("fake/py") is True
        assert len(cmds) == 3
        mirror_cmd = cmds[2]
        assert "--no-cache" in mirror_cmd
        assert mirror_cmd[mirror_cmd.index("--default-index") + 1] == \
            vm._PYPI_MIRROR
        assert all("--no-cache" not in c for c in cmds[:2])
        assert len(repairs) == 2  # cached-retry 前 + mirror 前各修一次

    def test_all_attempts_fail_returns_false(self, monkeypatch):
        """三级全败 → False（调用方转 retryable，下次调用重新走链路）。"""
        sleeps: list = []
        cmds, repairs = self._patch_uv(monkeypatch, [], sleeps)

        assert vm.preinstall_packages("fake/py") is False
        assert len(cmds) == 3  # cached + cached-retry + mirror
        assert len(repairs) == 2

    def test_pip_branch_has_no_no_cache_fallback(self, monkeypatch):
        """无 uv 走 pip：pip 缓存无 archive 层，无需 --no-cache 绕行（仅 2 次）。"""
        monkeypatch.setattr(vm, "resolve_tool", lambda name: None)
        cmds = []

        def fake_run(cmd):
            cmds.append(cmd)
            return (len(cmds) >= 2, "")  # 重试即成功

        repairs: list = []
        monkeypatch.setattr(
            vm, "_repair_uv_archive_rename",
            lambda err: repairs.append(err) or 0)
        monkeypatch.setattr(vm, "_run_preinstall", fake_run)
        monkeypatch.setattr(vm.time, "sleep", lambda s: None)

        assert vm.preinstall_packages("fake/py") is True
        assert len(cmds) == 2
        assert cmds[0][:5] == ["fake/py", "-m", "pip", "install", "--quiet"]

    def test_pip_branch_falls_back_to_mirror(self, monkeypatch):
        """无 uv 且官方源慢网 → pip 镜像兑底（-i 指向国内镜像）。"""
        monkeypatch.setattr(vm, "resolve_tool", lambda name: None)
        cmds = []

        def fake_run(cmd):
            cmds.append(cmd)
            return (False, "timed out")

        monkeypatch.setattr(vm, "_run_preinstall", fake_run)
        monkeypatch.setattr(vm, "_repair_uv_archive_rename", lambda err: 0)
        monkeypatch.setattr(vm.time, "sleep", lambda s: None)

        assert vm.preinstall_packages("fake/py") is False
        assert len(cmds) == 3  # cached + cached-retry + mirror
        mirror_cmd = cmds[2]
        assert mirror_cmd[mirror_cmd.index("-i") + 1] == vm._PYPI_MIRROR


# ── 3. agent 侧懒 ensure ───────────────────────────────────


class TestAgentEnsureTaskVenv:
    def test_empty_path_passthrough(self, agent):
        """无 venv 任务（外部工作区 / 创建期登记为空）→ 原样返回空。"""
        assert asyncio.run(agent._ensure_task_venv("t1", "")) == ""

    def test_existing_venv_returned_without_creation(self, agent, tmp_path):
        """venv 与预装 marker 均在位 → 直接返回，不触发创建（幂等零开销）。"""
        venv_path = str(tmp_path / ".venv")
        _fake_venv_skeleton(venv_path)
        with open(os.path.join(venv_path, vm._PREINSTALL_MARKER), "w",
                  encoding="utf-8") as f:
            f.write("ok")
        assert asyncio.run(agent._ensure_task_venv("t1", venv_path)) == venv_path

    def test_venv_without_marker_retries_preinstall(self, agent, db, tmp_path, monkeypatch):
        """venv 在位但 marker 缺失（预装中断/跨重启）→ 补跑预装（重试语义）。"""
        task = db.create_task("t", str(tmp_path / "data"), "")
        venv_path = task["venv_path"]
        _fake_venv_skeleton(venv_path)
        installs = []
        monkeypatch.setattr(
            vm, "preinstall_packages", lambda py: installs.append(py) or True)

        assert asyncio.run(agent._ensure_task_venv(task["id"], venv_path)) == venv_path
        _wait_preinstall_done(venv_path)
        assert len(installs) == 1
        assert os.path.isfile(os.path.join(venv_path, vm._PREINSTALL_MARKER))

    def test_deleted_task_cleans_up_orphan_venv(self, agent, tmp_path, monkeypatch):
        """创建窗口内任务被删（delete cancel 超时后 rmtree）→ 回收刚建的 venv。"""
        import agent.task_agent as ta

        task_dir = tmp_path / "tasks" / "t-del"
        venv_path = str(task_dir / ".venv")
        _fake_venv_skeleton(venv_path)  # venv 已在 → create_venv 幂等 no-op
        # 同步 no-op：不真建 venv、不起预装线程，让回收分支确定性可达
        monkeypatch.setattr(ta, "ensure_venv", lambda task_dir: task_dir)

        # t-del 未登记进 db → get_task 返回 None → rmtree 回收
        assert asyncio.run(agent._ensure_task_venv("t-del", venv_path)) == ""
        assert not os.path.isdir(task_dir)

    def test_creation_failure_returns_empty(self, agent, tmp_path, monkeypatch):
        """创建失败 → 返回 ""，后续执行自动回退系统 Python（与旧创建期失败一致）。"""
        import agent.task_agent as ta

        def boom(task_dir):
            raise RuntimeError("cannot create venv")

        monkeypatch.setattr(ta, "ensure_venv", boom)
        venv_path = str(tmp_path / "tasks" / "t1" / ".venv")
        assert asyncio.run(agent._ensure_task_venv("t1", venv_path)) == ""
