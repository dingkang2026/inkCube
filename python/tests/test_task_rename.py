"""python/tests/test_task_rename.py — 任务重命名错误契约回归测试。

回归背景：rename_task 此前直接取 body["title"]（缺键时 KeyError → 500），
且 DB 层 UPDATE 不检查 rowcount（任务不存在时静默返回 ok）。现统一为：
400 INVALID_TITLE（标题缺失/空白）/ 404 TASK_NOT_FOUND（任务不存在）。
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
    monkeypatch.setattr(deps, "db", db)
    monkeypatch.setattr(deps, "task_agent", MagicMock())
    return db


class TestRenameTask:
    def test_rename_success(self, client, mock_deps):
        """存在的任务重命名：200 ok，DB 收到 strip 后的标题。"""
        mock_deps.rename_task.return_value = True

        resp = client.patch(f"/tasks/{TASK_ID}", json={"title": "  新标题  "})

        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        mock_deps.rename_task.assert_called_once_with(TASK_ID, "新标题")

    def test_rename_missing_title_rejected(self, client, mock_deps):
        """body 缺 title 键：400 INVALID_TITLE，不再 KeyError → 500。"""
        resp = client.patch(f"/tasks/{TASK_ID}", json={})

        assert resp.status_code == 400
        assert resp.json()["code"] == "INVALID_TITLE"
        mock_deps.rename_task.assert_not_called()

    def test_rename_blank_title_rejected(self, client, mock_deps):
        """空白标题（纯空格）与缺失同语义：400。"""
        resp = client.patch(f"/tasks/{TASK_ID}", json={"title": "   "})

        assert resp.status_code == 400
        assert resp.json()["code"] == "INVALID_TITLE"
        mock_deps.rename_task.assert_not_called()

    def test_rename_nonexistent_task_404(self, client, mock_deps):
        """任务不存在：404 TASK_NOT_FOUND，不再静默 ok。"""
        mock_deps.rename_task.return_value = False

        resp = client.patch(f"/tasks/{TASK_ID}", json={"title": "x"})

        assert resp.status_code == 404
        assert resp.json()["code"] == "TASK_NOT_FOUND"
