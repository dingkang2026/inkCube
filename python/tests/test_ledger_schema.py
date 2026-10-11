"""python/tests/test_ledger_schema.py — 3.0 四期 T1:schema 就位(3.1 §3)。

两新表(ledger_entries/task_suggestions)+ 三新列(projects.pulse_ai_status*
两列 / wiki_schemas.is_ledger)+ seed 台账页标注。
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import Database
from db.wiki_seed import get_all_wiki_schemas


@pytest.fixture()
def db():
    with tempfile.TemporaryDirectory() as td:
        db = Database(os.path.join(td, "t.db"))
        db.initialize()
        db.ensure_preset_base_libs()  # create_project 的 auto-link 依赖 preset 基础库(同 affairs fixture)
        db.seed_wiki_schemas(get_all_wiki_schemas())
        yield db
        db.conn.close()


def test_tables_and_columns_exist(db):
    cur = db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
        " AND name IN ('ledger_entries','task_suggestions')")
    assert {r["name"] for r in cur.fetchall()} == {"ledger_entries", "task_suggestions"}
    for table, col in [("projects", "pulse_ai_status"), ("projects", "pulse_ai_status_at"),
                       ("wiki_schemas", "is_ledger")]:
        cols = {r["name"] for r in db.conn.execute(f"PRAGMA table_info({table})").fetchall()}
        assert col in cols, f"{table}.{col} missing"


def test_unique_and_index(db):
    idx = {r["name"] for r in db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    assert "idx_ledger_entries_project" in idx
    assert "idx_task_suggestions_project" in idx


def test_seed_marks_ledger_pages(db):
    rows = db.conn.execute(
        "SELECT page_key, is_ledger FROM wiki_schemas WHERE project_type='recruitment'").fetchall()
    marked = {r["page_key"] for r in rows if r["is_ledger"]}
    assert {"milestones", "risk_watchlist", "policy_ledger",
            "operation_monitor", "service_log"} <= marked
    assert "enterprise_profile" not in marked          # 画像页不标


def test_seed_marks_investment_and_general(db):
    """投资线 6 个监控页同标(3.1 §3.4);general 无监控页全 0。"""
    rows = db.conn.execute(
        "SELECT page_key, is_ledger FROM wiki_schemas WHERE project_type='investment'").fetchall()
    marked = {r["page_key"] for r in rows if r["is_ledger"]}
    assert {"financial_monitor", "legal_risk", "milestone_tracker",
            "risk_summary", "exit_path", "deal_terms"} <= marked
    assert {"company_overview", "core_team", "valuation_ref"} & marked == set()
    rows = db.conn.execute(
        "SELECT page_key, is_ledger FROM wiki_schemas WHERE project_type='general'").fetchall()
    assert all(not r["is_ledger"] for r in rows)


def test_fingerprint_unique_constraint(db):
    """同 (project, page, fingerprint) 二次插入撞 UNIQUE(3.1 不变量 3 的落库锚)。"""
    import sqlite3
    pid = db.create_project(name="P", project_type="recruitment", stage="after")["id"]
    row = (db._uuid(), pid, "milestones", "2026-09", "milestone",
           "项目正式投产", None, "fp-1", "2026-10-11T00:00:00", "2026-10-11T00:00:00")
    db.conn.execute(
        "INSERT INTO ledger_entries (id, project_id, page_key, entry_date, entry_type,"
        " content, source_file_id, fingerprint, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", row)
    db.conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute(
            "INSERT INTO ledger_entries (id, project_id, page_key, entry_date, entry_type,"
            " content, source_file_id, fingerprint, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (db._uuid(), pid, "milestones", "2026-09", "milestone",
             "项目正式投产", None, "fp-1", "2026-10-11T00:00:00", "2026-10-11T00:00:00"))


def test_create_and_update_schema_is_ledger(db):
    """T10:create/update_wiki_schema 透传 is_ledger(3.1 §3 Schema 管理开关 DB 层锚)。

    缺省 False(存量调用向后兼容);update 可双向切换。"""
    row = db.create_wiki_schema(
        project_type="recruitment", page_key="custom_ledger",
        title="自定义台账", is_ledger=True)
    assert row["id"] == "wiki_schema_recruitment_custom_ledger"
    schema = db.get_wiki_schema("recruitment", "custom_ledger")
    assert schema["is_ledger"] == 1

    db.update_wiki_schema(schema["id"], is_ledger=False)
    assert db.get_wiki_schema("recruitment", "custom_ledger")["is_ledger"] == 0
    db.update_wiki_schema(schema["id"], is_ledger=True)
    assert db.get_wiki_schema("recruitment", "custom_ledger")["is_ledger"] == 1

    # 缺省为 0(不传 is_ledger 的既有调用不受影响)
    db.create_wiki_schema(project_type="recruitment", page_key="plain", title="普通页")
    assert db.get_wiki_schema("recruitment", "plain")["is_ledger"] == 0
