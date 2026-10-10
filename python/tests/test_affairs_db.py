"""python/tests/test_affairs_db.py — 3.0 二期 Task 2:铺入/回填/CRUD/创建同事务。"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import Database
from db.playbook_seed import get_all_playbook_templates


def _templated_with_due(phase: str) -> set[str]:
    """有明确截止规则的模板 id(§3.3 注:milestone/部分 material 无截止规则,
    铺入 due=NULL 属设计本意,用户抽屉自定日)。"""
    return {
        t["id"] for t in get_all_playbook_templates()
        if t["phase"] == phase
        and (t.get("due_offset_days") or t.get("due_date_fixed") or t.get("recurrence_rule"))
    }


@pytest.fixture()
def db():
    with tempfile.TemporaryDirectory() as td:
        db = Database(os.path.join(td, "t.db"))
        db.initialize()
        db.ensure_preset_base_libs()  # create_project 的 auto-link 依赖 preset 基础库
        db.seed_playbook_templates(get_all_playbook_templates())
        yield db
        db.conn.close()


def test_insert_playbook_tasks_current_open_others_pending(db):
    pid = db.create_project(name="P", project_type="recruitment", stage="during")["id"]
    tasks = db.list_phase_tasks(pid)
    assert len(tasks) == 24
    by = {}
    for t in tasks:
        by.setdefault(t["phase"], []).append(t)
    assert all(t["status"] == "open" and t["due_date"] for t in by["during"]
               if t["template_id"] in _templated_with_due("during"))
    assert all(t["status"] == "pending_activation" and not t["due_date"]
               for p in ("before", "after") for t in by[p])
    # recurring 只铺当前期一期(recruitment recurring 全在 after,不进 during)
    assert not any(t["task_type"] == "recurring" for t in by["during"])


def test_insert_playbook_tasks_seeds_current_occurrence_for_recurring(db):
    pid = db.create_project(name="P", project_type="recruitment", stage="after")["id"]
    monthly = next(t for t in db.list_phase_tasks(pid)
                   if t["template_id"] == "playbook_recruitment_after_monthly_report")
    assert monthly["status"] == "open"
    assert monthly["occurrence_key"]  # 非空,形如 2026-10
    assert monthly["due_date"]


def test_create_project_rolls_back_on_seed_failure(db):
    # 铺入失败 → 项目不残留(同事务,§5.3)
    db.conn.execute("DROP TABLE playbook_templates")
    with pytest.raises(Exception):
        db.create_project(name="X", project_type="recruitment", stage="before")
    row = db.conn.execute(
        "SELECT COUNT(*) c FROM projects WHERE name='X'").fetchone()
    assert row["c"] == 0


def test_migrate_backfill_idempotent(db):
    # 模拟存量:直接 SQL 建项目(绕过 create_project 的铺入)
    pid = db._uuid()
    db.conn.execute(
        "INSERT INTO projects (id, name, type, stage, kind, playbook_seeded) "
        "VALUES (?, 'old', 'general', 'before', 'project', 0)", (pid,))
    db.conn.commit()
    assert db.migrate_backfill_playbooks() == 1
    assert len(db.list_phase_tasks(pid)) == 2
    assert db.migrate_backfill_playbooks() == 0  # 标记防重入
    # 用户删光任务也不重铺(§4.3)
    for t in db.list_phase_tasks(pid):
        db.delete_phase_task(t["id"])
    assert db.migrate_backfill_playbooks() == 0


def test_update_phase_task_status_transitions(db):
    pid = db.create_project(name="P", project_type="general", stage="before")["id"]
    task = db.list_phase_tasks(pid)[0]
    up = db.update_phase_task(task["id"], {"status": "done"})
    assert up["status"] == "done" and up["completed_by"] == "manual" and up["completed_at"]
    up = db.update_phase_task(task["id"], {"status": "open"})
    assert up["completed_at"] is None and up["completed_by"] is None


def test_service_report_seeds_yearly_occurrence(db):
    """report+recurrence_rule 组合(§3.3 服务报告/投后报告)同样铺当期 occurrence。"""
    import datetime as _dt
    pid = db.create_project(name="P", project_type="recruitment", stage="after")["id"]
    sr = next(t for t in db.list_phase_tasks(pid)
              if t["template_id"] == "playbook_recruitment_after_service_report")
    assert sr["status"] == "open"
    assert sr["occurrence_key"] == str(_dt.date.today().year)
    assert sr["due_date"] == f"{_dt.date.today().year}-12-31"


def test_occurrence_for_now_boundaries(db):
    import calendar as _cal
    import datetime as _dt
    today = _dt.date.today()
    # monthly:due_day 超当月天数时 clamp 到月末
    expected_day = min(31, _cal.monthrange(today.year, today.month)[1])
    key, due = db._occurrence_for_now({"freq": "monthly", "due_day": 31})
    assert key == today.strftime("%Y-%m")
    assert due == f"{today.year:04d}-{today.month:02d}-{expected_day:02d}"
    # quarterly:当季末月最后一日
    q = (today.month - 1) // 3 + 1
    last_month = q * 3
    expected_due = _dt.date(
        today.year, last_month,
        _cal.monthrange(today.year, last_month)[1]).isoformat()
    key, due = db._occurrence_for_now({"freq": "quarterly", "due_day": "end"})
    assert key == f"{today.year}-Q{q}"
    assert due == expected_due
    # yearly:MM-DD 直拼;due_day 缺省 12-31
    key, due = db._occurrence_for_now({"freq": "yearly", "due_day": "04-30"})
    assert key == str(today.year) and due == f"{today.year}-04-30"
    key, due = db._occurrence_for_now({"freq": "yearly"})
    assert due == f"{today.year}-12-31"


def test_update_phase_task_whitelist_and_skipped(db):
    pid = db.create_project(name="P", project_type="general", stage="before")["id"]
    task = db.list_phase_tasks(pid)[0]
    up = db.update_phase_task(task["id"], {"status": "done"})
    assert up["status"] == "done" and up["completed_by"] == "manual" and up["completed_at"]
    # done 分支不接受白名单外 completed_*:防 PATCH 注入伪造完成者(评审 C-6)
    up = db.update_phase_task(task["id"], {"status": "done", "completed_by": "ghost"})
    assert up["completed_by"] == "manual" and up["completed_at"]
    # skipped → completed_at/completed_by 清空;白名单外字段被忽略
    up = db.update_phase_task(task["id"], {
        "status": "skipped", "id": "hacked", "project_id": "elsewhere",
        "completed_by": "ghost",
    })
    assert up["status"] == "skipped"
    assert up["completed_at"] is None and up["completed_by"] is None
    assert up["id"] != "hacked" and up["project_id"] != "elsewhere"


def test_create_project_without_seed(db):
    r = db.create_project(name="NS", project_type="general", stage="before",
                          seed_playbook=False)
    assert r["seeded_tasks_count"] == 0
    assert db.list_phase_tasks(r["id"]) == []
