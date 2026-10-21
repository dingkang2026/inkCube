"""python/tests/test_form_fills_fk.py — form_fills.template_id 摘除外键（2.4 §5.4 不变量 11）。

覆盖：新库无 FK、悬空 template_id 可插入、列表 LEFT JOIN 悬空可见、
删除被引用模板成功且实例存活。未上线无存量库：直接改 DDL，无迁移（2026-10-16 裁决）。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from db.database import Database


@pytest.fixture
def db():
    database = Database(db_path=":memory:")
    database.initialize()
    return database


def _insert_fill(db, pid: str, fill_id: str = "ff-1", template_id: str = "gone-template"):
    db.conn.execute(
        "INSERT INTO form_fills (id, project_id, template_id, title, status, created_at, updated_at) "
        "VALUES (?, ?, ?, 'T', 'success', datetime('now'), datetime('now'))",
        (fill_id, pid, template_id),
    )
    db.conn.commit()


def test_new_database_has_no_fk_on_template_id(db):
    refs = db.conn.execute("PRAGMA foreign_key_list(form_fills)").fetchall()
    assert all(r["table"] != "form_templates" for r in refs)


def test_dangling_template_id_insert_allowed(db):
    pid = db.create_project(name="P", project_type="general")["id"]
    _insert_fill(db, pid)   # FK 存在时此处 IntegrityError
    assert db.get_form_fill("ff-1")["template_id"] == "gone-template"


def test_list_form_fills_left_join_dangling(db):
    pid = db.create_project(name="P", project_type="general")["id"]
    _insert_fill(db, pid)
    rows = db.list_form_fills(pid)
    assert len(rows) == 1
    assert rows[0]["template_name"] is None


def test_delete_referenced_template_succeeds(db):
    pid = db.create_project(name="P", project_type="general")["id"]
    db.conn.execute(
        "INSERT INTO form_templates (id, name, source, file_path, status, created_at, updated_at) "
        "VALUES ('t1', 'Tpl', 'upload', 'x.docx', 'success', datetime('now'), datetime('now'))")
    _insert_fill(db, pid, template_id="t1")
    db.conn.commit()
    db.delete_form_template("t1")   # FK RESTRICT 时代此处 IntegrityError
    assert db.get_form_template("t1") is None
    assert db.get_form_fill("ff-1") is not None   # 实例存活（悬空引用）
