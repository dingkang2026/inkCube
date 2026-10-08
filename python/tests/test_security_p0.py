"""python/tests/test_security_p0.py — 全项目 review P0 批修复回归测试。

覆盖：
  C1. db.delete_task 只清理应用自管目录（data_dir/tasks/{id}），
      外部工作区及其父目录永不删除
  C2. CORS 白名单收敛：外部 Origin 的 preflight 被拒（400），
      Vite dev Origin 放行
  C3. knowledge 上传 filename 路径遍历防护（_safe_filename +
      _base_lib_upload_core 落盘路径）
  H2. pause_task 标记 _paused_tasks（error 退出时保留 state 的依据）
"""
import asyncio
import base64
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.database import Database


# ── C1. delete_task 作用域 ─────────────────────────────────


class TestDeleteTaskScopedCleanup:
    def _make_db(self, tmp_path, monkeypatch):
        # Patch venv creation — create_task would otherwise build a real venv
        import tools.venv_manager as vm
        monkeypatch.setattr(vm, "create_venv", lambda task_dir: "")
        monkeypatch.setattr(vm, "get_venv_python", lambda p: p)
        monkeypatch.setattr(vm, "preinstall_packages", lambda p: None)
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        db = Database(str(data_dir / "inkcube.db"))
        db.initialize()
        return db, str(data_dir)

    def test_auto_task_dir_removed(self, tmp_path, monkeypatch):
        db, data_dir = self._make_db(tmp_path, monkeypatch)
        task = db.create_task("t", data_dir, workspace_path="")
        task_dir = Path(data_dir) / "tasks" / task["id"]
        assert task_dir.is_dir()

        db.delete_task(task["id"])
        assert not task_dir.exists()

    def test_external_workspace_parent_preserved(self, tmp_path, monkeypatch):
        # Regression: user-provided workspace → dirname(workspace) is the
        # user's parent dir; delete_task must NOT rmtree it.
        db, data_dir = self._make_db(tmp_path, monkeypatch)
        ext_parent = tmp_path / "proj"
        ext_ws = ext_parent / "ws"
        ext_ws.mkdir(parents=True)
        (ext_ws / "code.py").write_text("x=1")

        task = db.create_task("t", data_dir, workspace_path=str(ext_ws))
        db.delete_task(task["id"])

        assert (ext_ws / "code.py").exists()  # user files untouched
        assert ext_parent.is_dir()


# ── C2. CORS 白名单 ────────────────────────────────────────


class TestCorsPolicy:
    def test_foreign_origin_rejected_dev_allowed(self):
        import main as main_module
        from fastapi.testclient import TestClient

        # main 在模块导入时给 root logger 挂生产 app.log 的 file handler；
        # 不立即摘除的话，本测试体内的 httpx 请求日志（TestClient）会写进
        # 生产日志。conftest 的 autouse fixture 只在测试前后摘，覆盖不到
        # 同一测试体内的这个窗口，此处必须就地摘除。
        for h in [h for h in logging.root.handlers
                  if isinstance(h, RotatingFileHandler)]:
            logging.root.handlers.remove(h)

        client = TestClient(main_module.app)  # no `with` → lifespan not started
        res = client.options("/settings", headers={
            "Origin": "http://evil.example.com",
            "Access-Control-Request-Method": "GET",
        })
        assert res.status_code == 400  # preflight disallowed

        res2 = client.options("/settings", headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        })
        assert res2.status_code == 200
        assert res2.headers.get("access-control-allow-origin") == "http://localhost:5173"


# ── C3. 上传 filename 路径遍历防护 ─────────────────────────


class TestSafeFilename:
    @pytest.mark.parametrize("raw,expected", [
        ("../../evil.txt", "evil.txt"),
        ("..\\..\\evil.txt", "evil.txt"),
        ("normal.pdf", "normal.pdf"),
        ("  spaced.md  ", "spaced.md"),
        ("../../..", "unnamed"),
        ("", "unnamed"),
    ])
    def test_safe_filename(self, raw, expected):
        from routers.knowledge import _safe_filename
        assert _safe_filename(raw, "unnamed") == expected


class TestBaseLibUploadTraversal:
    def test_traversal_filename_lands_inside_file_dir(self, tmp_path):
        from routers.knowledge import FileUpload, _base_lib_upload_core

        calls = {}

        class StubDB:
            def find_duplicate_file(self, lib_id, h):
                return None

            def create_file(self, **kw):
                calls["create_file"] = kw

        upload = FileUpload(
            projectId="lib1", filename="../../evil.txt",
            fileType=".txt", data=base64.b64encode(b"data").decode(),
        )
        result = asyncio.run(
            _base_lib_upload_core("lib1", upload, StubDB(), str(tmp_path), None)
        )
        assert result["success"] is True

        # File must land inside files/{lib}/{file_id}/, never escape it
        file_id = result["file_id"]
        expected = tmp_path / "files" / "lib1" / file_id / "evil.txt"
        assert expected.is_file()
        assert not (tmp_path / "evil.txt").exists()
        # DB record stores the sanitized name
        assert calls["create_file"]["filename"] == "evil.txt"


# ── H2. pause 标记（error 退出保留 state 的依据）──────────


class TestPauseStateSurvival:
    def _make_agent(self):
        from agent.task_agent import TaskAgent

        agent = TaskAgent.__new__(TaskAgent)
        agent._cancel_events = {}
        agent._paused_tasks = set()
        saved = {}

        class StubDB:
            def save_agent_state(self, tid, state):
                saved[tid] = state
                return True

        agent.db = StubDB()
        agent._saved = saved
        return agent

    def test_pause_marks_task_and_cancels(self):
        agent = self._make_agent()
        agent._cancel_events["t1"] = asyncio.Event()

        ok = agent.pause_task("t1", [{"role": "user", "content": "hi"}], 3, [])
        assert ok is True
        assert "t1" in agent._paused_tasks        # error exit keeps state
        assert agent._cancel_events["t1"].is_set()
        assert agent._saved["t1"]["iteration"] == 3
        assert agent._saved["t1"]["auto_saved"] is True


