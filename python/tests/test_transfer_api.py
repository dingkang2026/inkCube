"""python/tests/test_transfer_api.py — 导出/导入端点单测（2.9 §6/§10）。

直接调用路由函数（test_activities.py 同模式，不引 TestClient）。
导入端点的后台任务用 fake run_import 立即完成，await sleep 让事件循环跑完。
"""
import asyncio
import json
import os
import sys
import time
import uuid
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import deps
from db.database import Database
from knowledge.fts_store import FTSStore
from routers import knowledge as knowledge_router
from transfer import service


@pytest.fixture
def db():
    database = Database(db_path=":memory:")
    database.initialize()
    database.ensure_preset_base_libs()
    return database


@pytest.fixture(autouse=True)
def isolate(db, monkeypatch, tmp_path):
    monkeypatch.setattr(deps, "db", db)
    monkeypatch.setenv("INKCUBE_DATA_DIR", str(tmp_path / "data"))
    os.makedirs(tmp_path / "data" / "chroma", exist_ok=True)
    os.makedirs(tmp_path / "data" / "files", exist_ok=True)
    service._RUNS.clear()
    yield
    service._RUNS.clear()


def _make_pkg(tmp_path, name="p.inkproj"):
    pkg = tmp_path / name
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("data/manifest.json", json.dumps(
            {"format_version": 1, "embedder": {"model": "bge-m3", "dim": 1024},
             "project": {"name": "Imp"}, "counts": {"files": 0}}))
    return str(pkg)


def test_export_endpoint_returns_counts(db, tmp_path):
    pid = db.create_project(name="Alpha")["id"]
    target = str(tmp_path / "out.inkproj")
    res = knowledge_router.export_project_endpoint(
        pid, knowledge_router.ExportProjectBody(target_path=target))
    assert res["path"] == target
    assert res["counts"]["files"] == 0
    assert os.path.isfile(target)
    # 终态通知已写（导出成功）
    unread = db.list_unread_notifications()
    assert any(n["entity_type"] == "project_export" and n["kind"] == "completed"
               for n in unread)


def test_export_endpoint_409_when_running(db, tmp_path):
    pid = db.create_project(name="Alpha")["id"]
    run_id = service.try_start_run("project_export", pid, "Alpha")
    try:
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            knowledge_router.export_project_endpoint(
                pid, knowledge_router.ExportProjectBody(target_path=str(tmp_path / "x.inkproj")))
        assert exc.value.status_code == 409
    finally:
        service.finish_run(run_id)


def test_export_endpoint_404_when_missing(db, tmp_path):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        knowledge_router.export_project_endpoint(
            "nope", knowledge_router.ExportProjectBody(target_path=str(tmp_path / "x.inkproj")))
    assert exc.value.status_code == 404   # 2.9 §5.1：项目不存在 404


def test_import_endpoint_starts_background_task(db, tmp_path, monkeypatch):
    from transfer import importer as importer_mod
    pkg = _make_pkg(tmp_path)
    calls = {}

    def fake_run_import(db_, data_dir_, source, new_pid, on_progress=None):
        calls["new_pid"] = new_pid
        return {"project_id": new_pid, "counts": {}}

    monkeypatch.setattr(importer_mod, "run_import", fake_run_import)

    async def scenario():
        res = await knowledge_router.import_project_endpoint(
            knowledge_router.ImportProjectBody(source_path=pkg))
        await asyncio.sleep(0.1)   # 让 create_task 的后台协程跑完
        return res

    res = asyncio.run(scenario())
    assert res["project_id"] == calls["new_pid"]
    assert res["activity_id"]
    assert service.fetch_running("project_import") == []   # fake 完成后已 finish
    unread = db.list_unread_notifications()
    assert any(n["entity_type"] == "project_import" and n["kind"] == "completed"
               for n in unread)


def test_import_endpoint_409_when_running(db, tmp_path):
    from fastapi import HTTPException
    pkg = _make_pkg(tmp_path)
    run_id = service.try_start_run("project_import", "p9", "Beta", dedupe="kind")
    try:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(knowledge_router.import_project_endpoint(
                knowledge_router.ImportProjectBody(source_path=pkg)))
        assert exc.value.status_code == 409
    finally:
        service.finish_run(run_id)


def test_import_endpoint_400_on_invalid_package(db, tmp_path):
    from fastapi import HTTPException
    bad = tmp_path / "bad.inkproj"
    bad.write_bytes(b"not a zip")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(knowledge_router.import_project_endpoint(
            knowledge_router.ImportProjectBody(source_path=str(bad))))
    assert exc.value.status_code == 400
