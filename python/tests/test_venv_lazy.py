"""python/tests/test_venv_lazy.py — 任务 venv 懒创建回归测试。

回归背景：create_task 此前即在 data_dir/tasks/<id> 下建 .venv 并后台预装
全部常用包——纯聊天任务也占 20~100MB/个（uv 缓存跨盘符时硬链接失效、整包复制）。
改为懒创建后：
  1. create_task 只登记确定性 venv 路径，不落盘；
  2. task_agent 首次执行代码/装包/跑 Skill 时经 venv_manager.ensure_venv 落盘；
  3. 预装以 marker 文件保证每 venv 至多一次，失败可重试；
  4. main.py 将 UV_CACHE_DIR 指到数据目录同盘，同版本包硬链接零增量；
  5. 白名单前置阻断（2.8 §12 F4）：未验证加白前任何安装不发生。
"""
import asyncio
import json
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


@pytest.fixture(autouse=True)
def _verified_exclusion(monkeypatch, tmp_path):
    """默认构造「白名单已验证」环境（前置阻断闸门放行，2.8 §12 F4）。

    不替换闸门函数本身（TestUvExclusionGate 要测真实实现），而是搭好
    已验证环境：UV_CACHE_DIR 指向 tmp、裁决文件在位且匹配；其余用例
    自行 setenv 覆盖时闸门按真实逻辑判定。
    """
    cache_dir = str(tmp_path / "data" / "uv-cache")
    monkeypatch.setenv("UV_CACHE_DIR", cache_dir)
    marker = os.path.join(os.path.dirname(cache_dir), vm._VERIFIED_MARKER_NAME)
    os.makedirs(os.path.dirname(marker), exist_ok=True)
    with open(marker, "w", encoding="utf-8") as f:
        json.dump({"ok": True, "cache_dir": cache_dir}, f)


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


# ── 2c. preinstall_packages 重试编排（两级链）─────────────────


class TestPreinstallFallback:
    """预装重试编排：cached → 镜像 + --no-cache（官方源不可达/极慢时的
    可达性兑底）。

    白名单前置阻断（2.8 §12 F4）上线后，杀软阻断场景在闸门处停机、
    不再进入安装链路，rename 自愈层（cached-retry + 改名修复）随之退役；
    镜像层解决的是网络可达性，与杀软无关，保留。
    """

    def _patch_uv(self, monkeypatch, results):
        """mock uv 存在、_run_preinstall 按序返回 results。

        results 元素可为 bool（默认错误文案）或 (ok, err) 元组。
        """
        monkeypatch.setattr(vm, "resolve_tool",
                            lambda name: "uv.exe" if name == "uv" else None)
        cmds = []

        def fake_run(cmd):
            cmds.append(cmd)
            idx = len(cmds) - 1
            r = results[idx] if idx < len(results) else False
            if isinstance(r, tuple):
                return r
            return (r, "" if r else "exit 1: boom")

        monkeypatch.setattr(vm, "_run_preinstall", fake_run)
        return cmds

    def test_first_attempt_success_no_retry(self, monkeypatch):
        """首次即成功 → 只调 1 次（常态零开销）。"""
        cmds = self._patch_uv(monkeypatch, [True])

        assert vm.preinstall_packages("fake/py") is True
        assert len(cmds) == 1
        assert "--no-cache" not in cmds[0]

    def test_cached_failure_falls_back_to_mirror(self, monkeypatch):
        """cached 失败（网络/源问题）→ 镜像 + --no-cache 一步到位。"""
        cmds = self._patch_uv(monkeypatch, [False, True])

        assert vm.preinstall_packages("fake/py") is True
        assert len(cmds) == 2
        mirror_cmd = cmds[1]
        assert "--no-cache" in mirror_cmd
        assert mirror_cmd[mirror_cmd.index("--default-index") + 1] == \
            vm._PYPI_MIRROR

    def test_all_attempts_fail_returns_false(self, monkeypatch):
        """两级全败 → False（调用方转 retryable，下次调用重新走链路）。"""
        cmds = self._patch_uv(monkeypatch, [])

        assert vm.preinstall_packages("fake/py") is False
        assert len(cmds) == 2  # cached + mirror

    def test_pip_branch_has_no_no_cache_fallback(self, monkeypatch):
        """无 uv 走 pip：pip 缓存无 archive 层，无需 --no-cache 绕行（仅 2 次）。"""
        monkeypatch.setattr(vm, "resolve_tool", lambda name: None)
        cmds = []

        def fake_run(cmd):
            cmds.append(cmd)
            return (len(cmds) >= 2, "")  # 镜像级成功

        monkeypatch.setattr(vm, "_run_preinstall", fake_run)

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

        assert vm.preinstall_packages("fake/py") is False
        assert len(cmds) == 2  # cached + mirror
        mirror_cmd = cmds[1]
        assert mirror_cmd[mirror_cmd.index("-i") + 1] == vm._PYPI_MIRROR


# ── 2d. 白名单前置阻断闸门（2.8 §12 F4）────────────────


class TestUvExclusionGate:
    """前置阻断：未验证加白前任何安装不允许发生。

    裁决文件 = 提权会话内回读成功后 Electron 落盘的
    {UV_CACHE_DIR 同级}/uv-exclusion-verified.json。
    """

    def _set_cache(self, monkeypatch, tmp_path):
        cache_dir = str(tmp_path / "uv-cache")
        monkeypatch.setenv("UV_CACHE_DIR", cache_dir)
        return cache_dir

    def _write_verified(self, cache_dir, ok=True, cache_dir_field=None):
        import json
        marker = os.path.join(os.path.dirname(cache_dir),
                              vm._VERIFIED_MARKER_NAME)
        payload = {"ok": ok, "ts": "2026-10-12T00:00:00Z"}
        if cache_dir_field is not None:
            payload["cache_dir"] = cache_dir_field
        with open(marker, "w", encoding="utf-8") as f:
            json.dump(payload, f)

    def test_valid_marker_verified(self, tmp_path, monkeypatch):
        """ok=true 且 cache_dir 匹配（含相对/绝对路径归一）→ 已验证。"""
        cache_dir = self._set_cache(monkeypatch, tmp_path)
        self._write_verified(cache_dir, ok=True, cache_dir_field=cache_dir)
        assert vm.is_uv_exclusion_verified() is True

    def test_relative_path_normalized(self, tmp_path, monkeypatch):
        """Python 侧 UV_CACHE_DIR 可能是相对路径（main.py setdefault 相对
        cwd），Electron 写入的是绝对路径——归一后必须匹配。"""
        real = str(tmp_path / "data" / "uv-cache")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("UV_CACHE_DIR", os.path.join("data", "uv-cache"))
        self._write_verified(real, ok=True, cache_dir_field=real)
        assert vm.is_uv_exclusion_verified() is True

    def test_missing_marker_not_verified(self, tmp_path, monkeypatch):
        cache_dir = self._set_cache(monkeypatch, tmp_path)
        assert vm.is_uv_exclusion_verified() is False

    def test_ok_false_not_verified(self, tmp_path, monkeypatch):
        cache_dir = self._set_cache(monkeypatch, tmp_path)
        self._write_verified(cache_dir, ok=False, cache_dir_field=cache_dir)
        assert vm.is_uv_exclusion_verified() is False

    def test_cache_dir_mismatch_not_verified(self, tmp_path, monkeypatch):
        """data 目录迁移后旧裁决文件不误放行。"""
        cache_dir = self._set_cache(monkeypatch, tmp_path)
        self._write_verified(cache_dir, ok=True, cache_dir_field="D:\\other\\uv-cache")
        assert vm.is_uv_exclusion_verified() is False

    def test_corrupt_marker_not_verified(self, tmp_path, monkeypatch):
        import json
        cache_dir = self._set_cache(monkeypatch, tmp_path)
        marker = os.path.join(os.path.dirname(cache_dir),
                              vm._VERIFIED_MARKER_NAME)
        with open(marker, "w", encoding="utf-8") as f:
            f.write("{not json")
        assert vm.is_uv_exclusion_verified() is False

    def test_no_uv_cache_dir_treated_verified(self, monkeypatch):
        """UV_CACHE_DIR 未设置（理论不发生）：无路径可阻，放行。"""
        monkeypatch.delenv("UV_CACHE_DIR", raising=False)
        assert vm.is_uv_exclusion_verified() is True

    def test_blocking_path_raises_when_not_verified(self, tmp_path, monkeypatch):
        """首用同步路径：未验证 → 抛 UvExclusionRequired（不跑安装）。"""
        cache_dir = self._set_cache(monkeypatch, tmp_path)
        venv_path = str(tmp_path / ".venv")
        _fake_venv_skeleton(venv_path)
        called = []
        monkeypatch.setattr(
            vm, "preinstall_packages", lambda py: called.append(py) or True)

        with pytest.raises(vm.UvExclusionRequired):
            vm.ensure_venv_blocking(venv_path)
        assert called == []  # 安装命令零执行（前置阻断语义）

    def test_background_preinstall_skipped_when_not_verified(self, tmp_path, monkeypatch):
        """后台线程路径：未验证 → 跳过预装（marker 缺失，首用时同步路径给出指引）。"""
        cache_dir = self._set_cache(monkeypatch, tmp_path)
        monkeypatch.setattr(vm, "is_uv_exclusion_verified", lambda: False)
        venv_path = str(tmp_path / ".venv")
        _fake_venv_skeleton(venv_path)
        called = []
        monkeypatch.setattr(
            vm, "preinstall_packages", lambda py: called.append(py) or True)

        assert vm.ensure_venv(str(tmp_path)) == venv_path
        assert called == []
        assert not os.path.isfile(os.path.join(venv_path, vm._PREINSTALL_MARKER))


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

    def test_not_verified_raises_uv_exclusion_required(self, agent, db, tmp_path, monkeypatch):
        """白名单未验证 → marker 缺失不交付空 venv，抛 UvExclusionRequired
        给调用方向 LLM 转授权指引（前置阻断，2.8 §12 F4）。

        ensure_venv 置为 no-op：其内部跳过预装行为已由
        TestUvExclusionGate.test_background_preinstall_skipped_when_not_verified
        覆盖，此处聚焦 _ensure_task_venv 的 marker 检查与抛出。
        """
        import agent.task_agent as ta

        monkeypatch.setattr(vm, "is_uv_exclusion_verified", lambda: False)
        monkeypatch.setattr(ta, "ensure_venv", lambda task_dir: None)
        task = db.create_task("t", str(tmp_path / "data"), "")
        venv_path = task["venv_path"]

        with pytest.raises(ta.UvExclusionRequired):
            asyncio.run(agent._ensure_task_venv(task["id"], venv_path))
