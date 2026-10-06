"""python/tests/test_task_export.py — 任务工作区导出回归测试。

回归背景：export_task 曾把中文任务标题拼进 Content-Disposition header，
starlette 以 latin-1 编码 header 值时抛 UnicodeEncodeError（中文标题任务
导出必 500）。修复后响应不携带该 header——文件名由 Electron 保存对话框
决定（tasks:export 用 task.title 拼 defaultPath，不读此 header）。

覆盖：
  - 中文标题任务导出 → 200 + application/zip + zip 内容完整（本 bug 回归）；
  - 响应无 Content-Disposition；
  - 任务不存在 / 工作区目录不存在 → error JSON。
"""
import io
import os
import sys
import zipfile
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
def mock_db(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(deps, "db", db)
    return db


def _workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "report.md").write_text("# 产出", encoding="utf-8")
    return ws


class TestExportTask:
    def test_chinese_title_exports_zip(self, client, mock_db, tmp_path):
        """回归核心用例：中文标题任务导出不再 500，zip 内容完整。"""
        mock_db.get_task.return_value = {
            "id": TASK_ID,
            "title": "宁庆空天装备调研报告",
            "workspace_path": str(_workspace(tmp_path)),
        }
        resp = client.get(f"/tasks/{TASK_ID}/export")

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/zip")
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            assert zf.namelist() == ["report.md"]
            assert zf.read("report.md").decode("utf-8") == "# 产出"

    def test_no_content_disposition_header(self, client, mock_db, tmp_path):
        """header 缺席是修复本体：任何非 ASCII 标题都无法再进入 header。"""
        mock_db.get_task.return_value = {
            "id": TASK_ID,
            "title": "中文标题 🚀 emoji",
            "workspace_path": str(_workspace(tmp_path)),
        }
        resp = client.get(f"/tasks/{TASK_ID}/export")

        assert resp.status_code == 200
        assert "content-disposition" not in resp.headers

    def test_task_not_found(self, client, mock_db):
        mock_db.get_task.return_value = None
        resp = client.get(f"/tasks/{TASK_ID}/export")

        assert resp.status_code == 200
        assert resp.json() == {"error": "任务不存在"}

    def test_workspace_missing(self, client, mock_db, tmp_path):
        mock_db.get_task.return_value = {
            "id": TASK_ID,
            "title": "任意标题",
            "workspace_path": str(tmp_path / "nope"),
        }
        resp = client.get(f"/tasks/{TASK_ID}/export")

        assert resp.status_code == 200
        assert resp.json() == {"error": "工作区目录不存在"}
