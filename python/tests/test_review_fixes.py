"""python/tests/test_review_fixes.py — 全项目 review 第二批（15 项）修复回归测试。

覆盖：
  H2. _LockedConnection 并发写串行化（多线程 execute+commit 无异常、无丢失）
  H3. web_fetch SSRF 防护（协议白名单 + 内网/环回/链路本地/保留地址拦截，
      不可解析域名视为私网拒绝，公网地址放行）
  H4. TaskAgent.try_acquire_task（空闲获取、同任务互斥、并发上限）
  L2. _create_workspace_zip 排除 node_modules/__pycache__/venv/隐藏目录
  L3. git_ops 入口守卫保留（非 Git 仓库拒绝），_run_git 不再重复检查
  L5. _check_upload_size（base64 长度预检 → 413，data-URI 前缀剥离）
"""
import asyncio
import base64
import os
import shutil
import socket
import sys
import threading
import zipfile
from pathlib import Path

import pytest

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── H3. SSRF 防护 ─────────────────────────────────────────


class TestSsrfValidation:
    @pytest.mark.parametrize("url", [
        "file:///etc/passwd",
        "ftp://example.com/file",
        "gopher://example.com/",
        "http:///no-host",
        "http://localhost/admin",
        "http://LOCALHOST:8080/",
        "http://127.0.0.1:8765/",
        "http://10.0.0.1/",
        "http://172.16.0.1/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://0.0.0.0/",
    ])
    def test_blocked_urls(self, url):
        from tools.web_fetch import _validate_url
        assert _validate_url(url) is not None

    def test_public_host_allowed(self, monkeypatch):
        import tools.web_fetch as wf

        # Resolve every hostname to a public IP — validation must pass
        monkeypatch.setattr(
            wf.socket, "getaddrinfo",
            lambda host, port: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))],
        )
        assert wf._validate_url("http://example.com/") is None
        assert wf._validate_url("https://example.com:443/x") is None

    def test_unresolvable_host_treated_as_private(self, monkeypatch):
        import tools.web_fetch as wf

        def raise_gaierror(host, port):
            raise socket.gaierror("name resolution failed")

        monkeypatch.setattr(wf.socket, "getaddrinfo", raise_gaierror)
        assert wf._validate_url("http://nonexistent.invalid/") is not None

    def test_public_hostname_resolving_to_private_blocked(self, monkeypatch):
        import tools.web_fetch as wf

        # DNS rebinding style: public-looking name → private address
        monkeypatch.setattr(
            wf.socket, "getaddrinfo",
            lambda host, port: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.0.10", 0))],
        )
        assert wf._validate_url("http://example.com/") is not None


# ── H4. try_acquire_task 原子预约 ─────────────────────────


class TestTryAcquireTask:
    def _make_agent(self):
        from agent.task_agent import TaskAgent

        agent = TaskAgent.__new__(TaskAgent)
        agent._running_task_ids = set()
        return agent

    def test_acquire_when_idle(self):
        agent = self._make_agent()
        assert agent.try_acquire_task("t1") is True
        assert "t1" in agent._running_task_ids

    def test_same_task_rejected(self):
        agent = self._make_agent()
        assert agent.try_acquire_task("t1") is True
        # Second chat on a running task must be rejected atomically
        assert agent.try_acquire_task("t1") is False

    def test_concurrency_cap_enforced(self):
        agent = self._make_agent()
        for i in range(agent.MAX_CONCURRENT):
            assert agent.try_acquire_task(f"t{i}") is True
        # At the cap → reject even a brand-new task
        assert agent.try_acquire_task("t-extra") is False

    def test_release_allows_reacquire(self):
        agent = self._make_agent()
        agent.try_acquire_task("t1")
        agent._running_task_ids.discard("t1")  # what run() finally does
        assert agent.try_acquire_task("t1") is True


# ── H2. SQLite 连接锁 ─────────────────────────────────────


class TestLockedConnectionConcurrency:
    def test_concurrent_execute_commit_is_serialized(self, tmp_path):
        from db.database import Database

        db = Database(str(tmp_path / "t.db"))
        db.initialize()
        db.conn.executescript("CREATE TABLE t (x TEXT)")

        errors: list[Exception] = []

        def writer(n: int):
            try:
                for i in range(30):
                    db.conn.execute("INSERT INTO t VALUES (?)", (f"{n}-{i}",))
                    db.conn.commit()
            except Exception as e:  # pragma: no cover - failure path
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"concurrent writes raised: {errors}"
        count = db.conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
        assert count == 8 * 30  # no lost writes

    def test_proxy_preserves_row_factory_and_commit(self, tmp_path):
        from db.database import Database

        db = Database(str(tmp_path / "t.db"))
        db.initialize()
        db.conn.executescript("CREATE TABLE t (x TEXT)")
        db.conn.execute("INSERT INTO t VALUES ('a')")
        db.conn.commit()
        rows = db.conn.execute("SELECT x FROM t").fetchall()
        assert len(rows) == 1
        assert db.conn.row_factory is not None  # proxy property passthrough


# ── L2. 工作区导出清洗 ────────────────────────────────────


class TestWorkspaceZipExclusions:
    def test_heavy_dirs_excluded(self, tmp_path):
        from routers.tasks import _create_workspace_zip

        ws = tmp_path / "ws"
        (ws / "node_modules" / "pkg").mkdir(parents=True)
        (ws / "node_modules" / "pkg" / "index.js").write_text("x")
        (ws / "__pycache__").mkdir()
        (ws / "__pycache__" / "m.cpython-312.pyc").write_bytes(b"x")
        (ws / "venv").mkdir()
        (ws / "venv" / "lib.py").write_text("x")
        (ws / ".git").mkdir()
        (ws / ".git" / "config").write_text("x")
        (ws / ".hidden").write_text("x")
        (ws / "main.py").write_text("print('hi')")
        (ws / "docs").mkdir()
        (ws / "docs" / "readme.md").write_text("hi")

        result = _create_workspace_zip(ws, "t")
        assert not isinstance(result, dict), result  # no error payload

        with zipfile.ZipFile(result) as zf:
            names = zf.namelist()

        assert "main.py" in names
        assert "docs/readme.md" in names
        assert not any(n.startswith("node_modules") for n in names)
        assert not any(n.startswith("__pycache__") for n in names)
        assert not any(n.startswith("venv") for n in names)
        assert not any(".git" in n for n in names)
        assert not any(n.startswith(".hidden") for n in names)


# ── L3. git_ops 入口守卫 ──────────────────────────────────


class TestGitOpsEntrypointGuard:
    def test_non_repo_rejected_at_entrypoint(self, tmp_path):
        from tools.git_ops import GitOpsTool

        tool = GitOpsTool(workspace_dir=str(tmp_path))
        import json

        result = json.loads(asyncio.run(tool.execute(action="status")))
        assert "不是 Git 仓库" in result["error"]

    @pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
    def test_real_repo_single_guard_suffices(self, tmp_path):
        import subprocess

        def run(*args):
            return subprocess.run(
                ["git"] + list(args), cwd=str(tmp_path),
                capture_output=True, timeout=10,
            )

        if run("init").returncode != 0:
            pytest.skip("git init failed")
        run("config", "user.email", "t@t")
        run("config", "user.name", "t")
        (tmp_path / "a.txt").write_text("hello")
        run("add", "a.txt")
        run("commit", "-m", "init")

        from tools.git_ops import GitOpsTool

        tool = GitOpsTool(workspace_dir=str(tmp_path))
        import json

        result = json.loads(asyncio.run(tool.execute(action="status")))
        assert result.get("exit_code") == 0  # repo check removed from _run_git, entry still guards


# ── L5. 上传大小预检 ──────────────────────────────────────


class TestUploadSizeCap:
    def _upload(self, data: str):
        from routers.knowledge import FileUpload
        return FileUpload(projectId="p", filename="f.bin", fileType=".bin", data=data)

    def test_oversized_rejected_before_decode(self, monkeypatch):
        import routers.knowledge as knowledge
        from fastapi import HTTPException

        monkeypatch.setattr(knowledge, "MAX_UPLOAD_BYTES", 100)
        big = base64.b64encode(b"x" * 200).decode()  # ~268 chars > 100 bytes
        with pytest.raises(HTTPException) as exc:
            knowledge._check_upload_size(self._upload(big))
        assert exc.value.status_code == 413

    def test_data_uri_prefix_stripped(self, monkeypatch):
        import routers.knowledge as knowledge
        from fastapi import HTTPException

        monkeypatch.setattr(knowledge, "MAX_UPLOAD_BYTES", 100)
        # Prefix must not count toward the size estimate
        upload = self._upload("data:application/pdf;base64," + "A" * 50)
        knowledge._check_upload_size(upload)  # ~37 decoded bytes → passes

        with pytest.raises(HTTPException):
            knowledge._check_upload_size(
                self._upload("data:application/pdf;base64," + "A" * 500)
            )

    def test_normal_size_passes(self, monkeypatch):
        import routers.knowledge as knowledge

        monkeypatch.setattr(knowledge, "MAX_UPLOAD_BYTES", 1024)
        knowledge._check_upload_size(self._upload(base64.b64encode(b"x" * 64).decode()))
