"""python/tests/test_task_templates_db.py — 0.5 E2 T1: task_templates 表与 db 方法单测（0.5 §5）。

内置模板不落库（始终以 i18n 为准）；表只存自定义行 + 内置置顶态记录行
（builtin=1，id='tpl_builtin_<name>'，取消置顶即删行）。前端内置 id 约定
'builtin_<name>'，本层负责 'builtin_<name>' → 'tpl_builtin_<name>' 映射。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from db.database import Database


@pytest.fixture
def tmp_db():
    database = Database(db_path=":memory:")
    database.initialize()
    yield database
    database.conn.close()


def test_upsert_and_list_custom_template(tmp_db):
    t = tmp_db.upsert_task_template(None, "global", None, "monitor", "华润舆情", "扫描{企业名}")
    assert t["id"].startswith("tpl_custom_")
    rows = tmp_db.list_task_templates(scope="global")
    assert len(rows) == 1 and rows[0]["title"] == "华润舆情"


def test_update_via_upsert_keeps_id(tmp_db):
    t = tmp_db.upsert_task_template(None, "global", None, "monitor", "A", "p1")
    t2 = tmp_db.upsert_task_template(t["id"], "global", None, "monitor", "B", "p2")
    assert t2["id"] == t["id"]
    assert len(tmp_db.list_task_templates()) == 1 and t2["title"] == "B"


def test_builtin_pin_upserts_record_row(tmp_db):
    assert tmp_db.set_task_template_pinned("builtin_sentiment", True)
    rows = tmp_db.list_task_templates()
    assert any(r["id"] == "tpl_builtin_sentiment" and r["builtin"] == 1 and r["pinned"] == 1
               for r in rows)
    # 取消置顶 = 删记录行（同 DELETE /task-templates/{builtin 记录行} 语义）
    tmp_db.set_task_template_pinned("builtin_sentiment", False)
    assert not any(r["id"] == "tpl_builtin_sentiment" and r["pinned"] == 1
                   for r in tmp_db.list_task_templates())


def test_custom_pin_updates_row(tmp_db):
    t = tmp_db.upsert_task_template(None, "global", None, "monitor", "A", "p1")
    assert tmp_db.set_task_template_pinned(t["id"], True)
    rows = tmp_db.list_task_templates()
    assert rows[0]["id"] == t["id"] and rows[0]["pinned"] == 1   # 置顶排最前
    assert tmp_db.set_task_template_pinned(t["id"], False)
    assert tmp_db.list_task_templates()[0]["pinned"] == 0


def test_project_scope_filter(tmp_db):
    tmp_db.upsert_task_template(None, "project", "p1", "monitor", "x", "y")
    assert len(tmp_db.list_task_templates(scope="project", project_id="p1")) == 1
    assert tmp_db.list_task_templates(scope="project", project_id="p2") == []


def test_delete_task_template(tmp_db):
    t = tmp_db.upsert_task_template(None, "global", None, "monitor", "A", "p1")
    assert tmp_db.delete_task_template(t["id"])
    assert tmp_db.list_task_templates() == []
    assert not tmp_db.delete_task_template(t["id"])   # 幂等：不存在返回 False
