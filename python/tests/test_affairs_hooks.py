"""python/tests/test_affairs_hooks.py — 3.0 二期 Task 5:终态声明点直调接线。

服务本体行为已在 test_affairs_service.py 覆盖;此处验「真实链路冒烟 +
接线存在(静态断言,防实现者漏接线)」。
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import Database
from db.playbook_seed import get_all_playbook_templates


@pytest.fixture()
def db():
    with tempfile.TemporaryDirectory() as td:
        db = Database(os.path.join(td, "t.db"))
        db.initialize()
        db.ensure_preset_base_libs()  # create_project 的 auto-link 依赖 preset 基础库
        db.seed_playbook_templates(get_all_playbook_templates())
        yield db
        db.conn.close()


def test_service_completes_task_on_file_parsed(db):
    """真实链路冒烟:铺入(before 期 open)→ 解析完成事件 → material 任务 done。"""
    import affairs_service
    pid = db.create_project(name="P", project_type="recruitment", stage="before")["id"]
    done = affairs_service.on_file_parsed(db, pid, "企业BP最终版.pdf", "f1")
    assert done == 1
    bp = next(t for t in db.list_phase_tasks(pid)
              if t["template_id"] == "playbook_recruitment_before_bp")
    assert bp["status"] == "done" and bp["completed_by"] == "auto"
    assert bp["linked_entity_id"] == "f1"


def test_service_completes_task_on_report_completed(db):
    """真实链路冒烟:general before 报告任务按标题命中自动完成。"""
    import affairs_service
    pid = db.create_project(name="P", project_type="general", stage="before")["id"]
    rid = db._uuid()
    db.conn.execute(
        "INSERT INTO reports (id, project_id, template_id, title, output_format, status, created_at, updated_at) "
        "VALUES (?, ?, 't', '项目分析报告', 'docx', 'done', '2026-10-09', '2026-10-09')",
        (rid, pid))
    db.conn.commit()
    assert affairs_service.on_report_completed(db, rid) == 1
    report_task = next(t for t in db.list_phase_tasks(pid) if t["task_type"] == "report")
    assert report_task["status"] == "done"


def test_hook_wiring_static():
    """静态断言:两个终态声明点文件含直调行(防实现者漏接线)。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pipeline = open(os.path.join(root, "knowledge", "pipeline.py"), encoding="utf-8").read()
    reports = open(os.path.join(root, "routers", "reports.py"), encoding="utf-8").read()
    assert "affairs_service.on_file_parsed" in pipeline
    assert "affairs_service.on_report_completed" in reports
