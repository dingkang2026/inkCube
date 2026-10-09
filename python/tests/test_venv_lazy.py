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


# ── 3. agent 侧懒 ensure ───────────────────────────────────


class TestAgentEnsureTaskVenv:
    def test_empty_path_passthrough(self, agent):
        """无 venv 任务（外部工作区 / 创建期登记为空）→ 原样返回空。"""
        assert asyncio.run(agent._ensure_task_venv("")) == ""

    def test_existing_venv_returned_without_creation(self, agent, tmp_path):
        """venv 已在位 → 直接返回，不触发创建（幂等零开销）。"""
        venv_path = str(tmp_path / ".venv")
        _fake_venv_skeleton(venv_path)
        assert asyncio.run(agent._ensure_task_venv(venv_path)) == venv_path

    def test_creation_failure_returns_empty(self, agent, tmp_path, monkeypatch):
        """创建失败 → 返回 ""，后续执行自动回退系统 Python（与旧创建期失败一致）。"""
        import agent.task_agent as ta

        def boom(task_dir):
            raise RuntimeError("cannot create venv")

        monkeypatch.setattr(ta, "ensure_venv", boom)
        venv_path = str(tmp_path / "tasks" / "t1" / ".venv")
        assert asyncio.run(agent._ensure_task_venv(venv_path)) == ""
