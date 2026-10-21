"""python/tests/test_task_file_b64.py — 0.5 E2 T5:files/content 端点 b64 参数。

?b64=1 时任意类型统一 base64 返回（导入项目/基础库链路，与 upload 同款 50MB 上限）；
不带 b64 行为与旧版完全一致（FilePreview 不受影响）。
"""
import base64
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import Database


@pytest.fixture()
def client_and_db(monkeypatch):
    import deps
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import tasks as tasks_router
    with tempfile.TemporaryDirectory() as td:
        db = Database(os.path.join(td, "t.db"))
        db.initialize()
        monkeypatch.setattr(deps, "db", db)
        workspace = os.path.join(td, "ws")
        os.makedirs(workspace)
        task = db.create_task("T", data_dir=td, workspace_path=workspace)
        app = FastAPI()
        app.include_router(tasks_router.router)
        yield TestClient(app), workspace, task["id"]
        db.conn.close()


def test_b64_text_file(client_and_db):
    """?b64=1 文本文件 → {type:'file', data:<base64>, filename}。"""
    client, ws, tid = client_and_db
    with open(os.path.join(ws, "report.md"), "w", encoding="utf-8", newline="") as f:
        f.write("# 周报\n正文 {占位}")
    r = client.get(f"/tasks/{tid}/files/content", params={"path": "report.md", "b64": "1"})
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "file"
    assert base64.b64decode(body["data"]).decode("utf-8") == "# 周报\n正文 {占位}"
    assert body["filename"] == "report.md"
    assert body["path"] == "report.md"
    assert body["size"] == len("# 周报\n正文 {占位}".encode("utf-8"))


def test_b64_binary_ext(client_and_db):
    """?b64=1 对二进制扩展名（.pdf 假字节）→ type='file' 且 data 可解码回原字节。"""
    client, ws, tid = client_and_db
    raw = bytes(range(256))
    with open(os.path.join(ws, "doc.pdf"), "wb") as f:
        f.write(raw)
    r = client.get(f"/tasks/{tid}/files/content", params={"path": "doc.pdf", "b64": "1"})
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "file"
    assert base64.b64decode(body["data"]) == raw
    assert body["filename"] == "doc.pdf"


def test_without_b64_unchanged(client_and_db):
    """不带 b64 行为与旧版一致：文本 type='text'，二进制扩展名 type='binary'。"""
    client, ws, tid = client_and_db
    with open(os.path.join(ws, "note.txt"), "w", encoding="utf-8") as f:
        f.write("hello")
    body = client.get(f"/tasks/{tid}/files/content", params={"path": "note.txt"}).json()
    assert body["type"] == "text" and body["content"] == "hello"
    with open(os.path.join(ws, "x.pdf"), "wb") as f:
        f.write(b"\x00\x01")
    body = client.get(f"/tasks/{tid}/files/content", params={"path": "x.pdf"}).json()
    assert body["type"] == "binary"


def test_b64_path_guard_unchanged(client_and_db):
    """b64=1 不放松路径校验：越界仍 PATH_OUTSIDE_WORKSPACE。"""
    client, ws, tid = client_and_db
    r = client.get(f"/tasks/{tid}/files/content",
                   params={"path": os.path.join("..", "escape.md"), "b64": "1"})
    assert r.json()["code"] == "PATH_OUTSIDE_WORKSPACE"
