"""python/tests/test_knowledge_delete.py — 项目删除 reports 目录补清单测（2026-10-20 存储审计）。

锚定 delete_project 端点内联的报告产物补清块（m3）：reports 行随项目级联删，
磁盘目录原先只在单报告删除端点清理——级联后变孤儿。补清块在级联删行前按
project_id 收集 content_path 逐个 rmtree。

测试口径：
- 补清块为端点内联代码，唯一单测入口是完整端点；VectorStore（真实 chroma
  初始化）是唯一重依赖 → fake 注入并记录 delete_project 调用；FTSStore
  （SQLite FTS5）与全部 DB 链路真实走。
- 项目行直接 INSERT（绕开 create_project 的 playbook 铺入与 auto-link），
  archived_at 置非空满足删除准入（409 守门在空表上自然通过）。
- 反向验证：未归档 409 后数据零变动（目录与行原样保留）。
"""
import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI
from fastapi.testclient import TestClient

import deps
from db.database import Database
from routers import knowledge as knowledge_router


def _mkdir_with_file(base, *parts):
    d = os.path.join(str(base), *parts)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "out.md"), "w", encoding="utf-8") as f:
        f.write("report body")
    return d


@pytest.fixture
def client_and_db(tmp_path, monkeypatch):
    # 端点模块级 from-import 绑定，patch routers.knowledge 命名空间
    monkeypatch.setattr("paths.get_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(knowledge_router, "get_data_dir", lambda: str(tmp_path))
    deleted_projects: list[str] = []

    class FakeVectorStore:
        def __init__(self, chroma_dir):
            self.chroma_dir = chroma_dir

        def delete_project(self, project_id):
            deleted_projects.append(project_id)

    monkeypatch.setattr(knowledge_router, "VectorStore", FakeVectorStore)

    database = Database(str(tmp_path / "inkcube.db"))
    database.initialize()
    monkeypatch.setattr(deps, "db", database)
    app = FastAPI()
    app.include_router(knowledge_router.router)
    yield TestClient(app), database, tmp_path, deleted_projects
    database.conn.close()


def _insert_project(db, project_id, archived):
    now = datetime.now().isoformat()
    db.conn.execute(
        "INSERT INTO projects (id, name, kind, archived_at, created_at, updated_at)"
        " VALUES (?, 'P', 'project', ?, ?, ?)",
        (project_id, now if archived else None, now, now),
    )
    db.conn.commit()


def _insert_report(db, project_id, report_id, content_path):
    db.conn.execute(
        "INSERT INTO reports (id, project_id, template_id, title, status,"
        " content_path, created_at, updated_at)"
        " VALUES (?, ?, 'tpl', 'R', 'completed', ?, 't', 't')",
        (report_id, project_id, content_path),
    )
    db.conn.commit()


def _report_rows(db, project_id):
    return db.conn.execute(
        "SELECT * FROM reports WHERE project_id = ?", (project_id,)).fetchall()


class TestProjectDelete:
    def test_report_dirs_purged_and_rows_cascaded(self, client_and_db):
        client, db, data_dir, deleted = client_and_db
        _insert_project(db, "p1", archived=True)
        dir_a = _mkdir_with_file(data_dir, "reports", "2026-10-19_a")
        dir_b = _mkdir_with_file(data_dir, "reports", "2026-10-19_b")
        _insert_report(db, "p1", "r1", dir_a)
        _insert_report(db, "p1", "r2", dir_b)
        # NULL/空 content_path 行（旧数据形态）：跳过但不阻断删除
        _insert_report(db, "p1", "r3", None)

        resp = client.delete("/knowledge/projects/p1")

        assert resp.status_code == 200
        assert resp.json() == {"success": True}
        assert not os.path.isdir(dir_a)
        assert not os.path.isdir(dir_b)
        assert len(_report_rows(db, "p1")) == 0  # 级联删行
        assert db.get_project("p1") is None
        assert deleted == ["p1"]  # fake 收到调用 = 端点走满全链

    def test_unarchived_rejected_keeps_data(self, client_and_db):
        client, db, data_dir, deleted = client_and_db
        _insert_project(db, "p1", archived=False)
        report_dir = _mkdir_with_file(data_dir, "reports", "2026-10-19_a")
        _insert_report(db, "p1", "r1", report_dir)

        resp = client.delete("/knowledge/projects/p1")

        assert resp.status_code == 409
        assert resp.json()["code"] == "PROJECT_NOT_ARCHIVED"
        # 409 后数据未动：目录与行原样保留
        assert os.path.isdir(report_dir)
        assert len(_report_rows(db, "p1")) == 1
        assert db.get_project("p1") is not None
        assert deleted == []

    def test_unknown_project_404(self, client_and_db):
        client, _, _, deleted = client_and_db
        assert client.delete("/knowledge/projects/no-such").status_code == 404
        assert deleted == []
