"""python/tests/test_task_templates_api.py — 0.5 E2 T2: /task-templates 路由契约单测。

单 router 挂载（test_knowledge_api 同口径，不拉起 main app lifespan）。
契约（E2 计划 Task 2 / 0.5 §5）：
- GET  /task-templates?scope=&projectId= → {templates, builtinPinned}
        templates=非 builtin 行；builtinPinned=置顶内置名（'tpl_builtin_<name>' 行提取）
- POST /task-templates → {template}；scope='project' 且无 projectId → 400 INVALID_SCOPE
- DELETE /task-templates/{id} → {ok: true}（builtin 记录行可删 = 取消置顶）
- POST /task-templates/{id}/pin → {ok: true}
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


@pytest.fixture()
def client_and_db(monkeypatch):
    import deps
    from db.database import Database
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import task_templates as task_templates_router
    with tempfile.TemporaryDirectory() as td:
        db = Database(os.path.join(td, "t.db"))
        db.initialize()
        monkeypatch.setattr(deps, "db", db)
        app = FastAPI()
        app.include_router(task_templates_router.router)
        yield TestClient(app), db
        db.conn.close()


def test_get_empty(client_and_db):
    client, _ = client_and_db
    r = client.get("/task-templates")
    assert r.status_code == 200
    assert r.json() == {"templates": [], "builtinPinned": []}


def test_post_then_get_visible(client_and_db):
    client, _ = client_and_db
    r = client.post("/task-templates", json={
        "scope": "global", "category": "monitor",
        "title": "华润舆情", "prompt": "扫描{企业名}"})
    assert r.status_code == 200
    tpl = r.json()["template"]
    assert tpl["id"].startswith("tpl_custom_")
    body = client.get("/task-templates").json()
    assert [t["id"] for t in body["templates"]] == [tpl["id"]]
    assert body["builtinPinned"] == []


def test_post_project_scope_requires_project_id(client_and_db):
    client, _ = client_and_db
    r = client.post("/task-templates", json={
        "scope": "project", "category": "monitor", "title": "x", "prompt": "y"})
    assert r.status_code == 400
    assert r.json()["code"] == "INVALID_SCOPE"


def test_delete_then_get_invisible(client_and_db):
    client, _ = client_and_db
    tpl = client.post("/task-templates", json={
        "scope": "global", "category": "monitor",
        "title": "A", "prompt": "p"}).json()["template"]
    r = client.delete(f"/task-templates/{tpl['id']}")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert client.get("/task-templates").json()["templates"] == []


def test_pin_builtin_appears_in_pinned_list(client_and_db):
    client, _ = client_and_db
    r = client.post("/task-templates/builtin_sentiment/pin", json={"pinned": True})
    assert r.status_code == 200 and r.json()["ok"] is True
    body = client.get("/task-templates").json()
    assert body["builtinPinned"] == ["tpl_builtin_sentiment"]
    assert body["templates"] == []   # builtin 记录行不入 templates
    client.post("/task-templates/builtin_sentiment/pin", json={"pinned": False})
    assert client.get("/task-templates").json()["builtinPinned"] == []


def test_delete_builtin_record_unpins(client_and_db):
    client, _ = client_and_db
    client.post("/task-templates/builtin_sentiment/pin", json={"pinned": True})
    r = client.delete("/task-templates/tpl_builtin_sentiment")
    assert r.status_code == 200
    assert client.get("/task-templates").json()["builtinPinned"] == []
