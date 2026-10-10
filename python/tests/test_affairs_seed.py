"""python/tests/test_affairs_seed.py — 3.0 二期 Task 1:建表/迁移列/种子幂等。"""
import json
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
        yield db
        db.conn.close()


def test_tables_and_migration_column_created(db):
    cur = db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
        "('playbook_templates','phase_tasks')"
    )
    assert {r["name"] for r in cur.fetchall()} == {"playbook_templates", "phase_tasks"}
    cols = [r["name"] for r in db.conn.execute("PRAGMA table_info(projects)").fetchall()]
    assert "playbook_seeded" in cols


def test_seed_inserts_46_templates_idempotent(db):
    db.seed_playbook_templates(get_all_playbook_templates())
    n1 = db.conn.execute("SELECT COUNT(*) c FROM playbook_templates").fetchone()["c"]
    assert n1 == 46
    db.seed_playbook_templates(get_all_playbook_templates())  # 幂等
    n2 = db.conn.execute("SELECT COUNT(*) c FROM playbook_templates").fetchone()["c"]
    assert n2 == 46


def test_seed_covers_types_phases_and_json_fields(db):
    db.seed_playbook_templates(get_all_playbook_templates())
    rows = [dict(r) for r in db.conn.execute("SELECT * FROM playbook_templates").fetchall()]
    by_type = {}
    for r in rows:
        by_type.setdefault(r["project_type"], []).append(r)
    assert len(by_type["recruitment"]) == 24
    assert len(by_type["investment"]) == 20
    assert len(by_type["general"]) == 2
    # JSON 字段合法且口径正确(§3.3:月报 = 每月 15 日前 + 文件名含月报)
    monthly = next(r for r in rows if r["id"] == "playbook_recruitment_after_monthly_report")
    assert json.loads(monthly["recurrence_rule"]) == {"freq": "monthly", "due_day": 15}
    assert json.loads(monthly["auto_complete"]) == {"on": "file_parsed", "match": "月报|月度"}
    # 报告类 match 带 title~ 前缀(§3.3)
    report_t = next(r for r in rows if r["id"] == "playbook_recruitment_before_initial_report")
    assert json.loads(report_t["auto_complete"])["match"] == "title~初评|评估"


def test_list_by_type_ordered(db):
    db.seed_playbook_templates(get_all_playbook_templates())
    rows = db.list_playbook_templates("recruitment")
    assert len(rows) == 24
    # 排序口径 = 先时期后期内序(计划 Interfaces: phase+position;
    # position 在每时期内从 1 重计,不能断言全局升序)
    keys = [(r["phase"], r["position"]) for r in rows]
    assert keys == sorted(keys)


def test_list_tolerates_corrupt_json(db):
    """corrupt JSON 静默降级为 None,不抛异常(铺入/滚动生成的上游容错)。"""
    db.seed_playbook_templates(get_all_playbook_templates())
    db.conn.execute(
        "UPDATE playbook_templates SET recurrence_rule = 'not-json' "
        "WHERE id = 'playbook_recruitment_after_monthly_report'"
    )
    db.conn.commit()
    rows = {r["id"]: r for r in db.list_playbook_templates("recruitment")}
    assert rows["playbook_recruitment_after_monthly_report"]["recurrence_rule"] is None
