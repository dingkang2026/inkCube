"""python/tests/test_task_archive.py — 任务归档运行中拦截回归测试。

回归背景：任务生命周期调整后，删除入口仅存在于归档 Tab（进行中任务前端
不渲染删除项、运行中任务不渲染归档项）；后端 archive_task 同步拦截运行中
任务（409 TASK_RUNNING），保证 API 层与前端规则一致（纵深防御）。
"""
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI
from fastapi.testclient import TestClient

import deps
from routers.tasks import router

TASK_ID = "ce255c24-64e3-40d3-8285-60e85d3fb8f8"


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture()
def mock_deps(monkeypatch):
    db = MagicMock()
    agent = MagicMock()
    monkeypatch.setattr(deps, "db", db)
    monkeypatch.setattr(deps, "task_agent", agent)
    return db, agent


class TestArchiveRunningGuard:
    def test_running_task_archive_rejected(self, client, mock_deps):
        """运行中任务归档被拦截：409 + TASK_RUNNING，不触碰 DB。"""
        db, agent = mock_deps
        agent.is_task_running.return_value = True

        resp = client.post(f"/tasks/{TASK_ID}/archive")

        assert resp.status_code == 409
        body = resp.json()
        assert body["code"] == "TASK_RUNNING"
        assert "正在执行" in body["error"]
        db.archive_task.assert_not_called()

    def test_idle_task_archives(self, client, mock_deps):
        """空闲任务正常归档。"""
        db, agent = mock_deps
        agent.is_task_running.return_value = False
        db.archive_task.return_value = True

        resp = client.post(f"/tasks/{TASK_ID}/archive")

        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        db.archive_task.assert_called_once_with(TASK_ID)

    def test_archive_nonexistent_task(self, client, mock_deps):
        """任务不存在时维持原错误语义。"""
        db, agent = mock_deps
        agent.is_task_running.return_value = False
        db.archive_task.return_value = False

        resp = client.post(f"/tasks/{TASK_ID}/archive")

        assert resp.status_code == 200
        assert resp.json() == {"error": "任务不存在或已删除"}
