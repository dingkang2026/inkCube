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


def test_service_completes_task_on_output_report(db):
    """产出维度(2026-10-28):material 任务挂 output_completed,报告完成后按
    标题命中闭环——不限 task_type(与 §3.3 report_completed 仅 report 类并存)。"""
    import affairs_service
    pid = db.create_project(name="P", project_type="general", stage="before")["id"]
    task = db.create_phase_task(
        pid, "before", "material", "拿到分析结论",
        auto_complete={"on": "output_completed", "match": "分析报告"})
    guard = db.create_phase_task(
        pid, "before", "material", "无关任务",
        auto_complete={"on": "output_completed", "match": "不会出现的词"})
    rid = db._uuid()
    db.conn.execute(
        "INSERT INTO reports (id, project_id, template_id, title, output_format, status, created_at, updated_at) "
        "VALUES (?, ?, 't', '项目分析报告', 'docx', 'done', '2026-10-28', '2026-10-28')",
        (rid, pid))
    db.conn.commit()
    done = affairs_service.on_report_completed(db, rid)
    assert done >= 1  # 本 material 任务必中;种子 report 任务(report_completed)可能同报命中
    row = db.get_phase_task(task["id"])
    assert row["status"] == "done" and row["completed_by"] == "auto"
    assert row["linked_entity_type"] == "report" and row["linked_entity_id"] == rid
    assert db.get_phase_task(guard["id"])["status"] == "open"  # 未命中不误伤


def test_output_rule_completes_only_earliest_recurring_occurrence(db):
    """H-1 回归(2026-10-28 review):recurring 挂 output_completed,roll 预铺
    下期后当期+下期两期 open,一次产出事件仅闭最早一期(裁决 5,与
    on_file_parsed 同构)——全闭会误掉下期且 occ 占位不会重铺,周期断流。"""
    import affairs_service
    pid = db.create_project(name="P", project_type="general", stage="before")["id"]
    cur = db.create_phase_task(
        pid, "before", "recurring", "月度产出检查",
        occurrence_key="2026-10",
        recurrence_rule={"freq": "monthly", "due_day": 15},
        auto_complete={"on": "output_completed", "match": "分析报告"})
    nxt = db.create_phase_task(
        pid, "before", "recurring", "月度产出检查",
        occurrence_key="2026-11",
        recurrence_rule={"freq": "monthly", "due_day": 15},
        auto_complete={"on": "output_completed", "match": "分析报告"})
    rid = db._uuid()
    db.conn.execute(
        "INSERT INTO reports (id, project_id, template_id, title, output_format, status, created_at, updated_at) "
        "VALUES (?, ?, 't', '项目分析报告', 'docx', 'done', '2026-10-28', '2026-10-28')",
        (rid, pid))
    db.conn.commit()
    affairs_service.on_report_completed(db, rid)
    assert db.get_phase_task(cur["id"])["status"] == "done"    # 最早 open 期闭环
    assert db.get_phase_task(nxt["id"])["status"] == "open"    # 下期不被误闭


def test_service_completes_task_on_ppt_completed(db):
    """产出维度:PPT 交付完成,任务按标题命中 output_completed 闭环
    (种子任务无此规则,done 精确=1)。"""
    import affairs_service
    pid = db.create_project(name="P", project_type="general", stage="before")["id"]
    task = db.create_phase_task(
        pid, "before", "milestone", "路演材料就绪",
        auto_complete={"on": "output_completed", "match": "路演|BP"})
    ppt_id = db._uuid()
    db.conn.execute(
        "INSERT INTO ppts (id, project_id, task_id, title, kind, status, created_at, updated_at) "
        "VALUES (?, ?, 'task_x', '路演PPT最终版', 'deck', 'completed', '2026-10-28', '2026-10-28')",
        (ppt_id, pid))
    db.conn.commit()
    assert affairs_service.on_ppt_completed(db, ppt_id) == 1
    row = db.get_phase_task(task["id"])
    assert row["status"] == "done" and row["completed_by"] == "auto"
    assert row["linked_entity_type"] == "ppt" and row["linked_entity_id"] == ppt_id


def test_service_completes_task_on_form_fill_completed(db):
    """产出维度:填表完成,任务按标题命中 output_completed 闭环。"""
    import affairs_service
    pid = db.create_project(name="P", project_type="general", stage="before")["id"]
    task = db.create_phase_task(
        pid, "before", "material", "尽调清单回收",
        auto_complete={"on": "output_completed", "match": "尽调"})
    fill_id = db._uuid()
    db.conn.execute(
        "INSERT INTO form_fills (id, project_id, template_id, title, status, created_at, updated_at) "
        "VALUES (?, ?, 'tpl1', '尽调清单', 'done', '2026-10-28', '2026-10-28')",
        (fill_id, pid))
    db.conn.commit()
    assert affairs_service.on_form_fill_completed(db, fill_id) == 1
    row = db.get_phase_task(task["id"])
    assert row["status"] == "done" and row["completed_by"] == "auto"
    assert row["linked_entity_type"] == "form_fill" and row["linked_entity_id"] == fill_id


def test_hook_wiring_static():
    """静态断言:各终态声明点文件含直调行(防实现者漏接线)。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pipeline = open(os.path.join(root, "knowledge", "pipeline.py"), encoding="utf-8").read()
    reports = open(os.path.join(root, "routers", "reports.py"), encoding="utf-8").read()
    ppt = open(os.path.join(root, "ppt_service.py"), encoding="utf-8").read()
    forms = open(os.path.join(root, "routers", "forms.py"), encoding="utf-8").read()
    assert "affairs_service.on_file_parsed" in pipeline
    assert "affairs_service.on_report_completed" in reports
    assert "affairs_service.on_ppt_completed" in ppt
    assert "affairs_service.on_form_fill_completed" in forms
    # 产出维度(2026-10-28):completed 终态声明点全部接线（def 1 + 调用 5；
    # 原 enhanced 终态调用点随成品增强 E3 全链下线删除，2026-10-28）
    assert ppt.count("_affairs_on_ppt_completed(db,") == 6
