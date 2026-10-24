"""python/tests/test_leads.py — 0.6 §4.1 V2-E2: leads 表与 db 方法单测。

覆盖：建表幂等（重复 initialize 不炸，失败即抛阻断启动由 initialize 链
天然保证）、线索 CRUD（create 默认值 / run 来源快照 / list 状态过滤与
分页契约 / update 白名单 / delete）、溯源快照删除链（期次删除与订阅删除
均置 source_run_id=NULL、订阅名快照保留可读——0.6 §4.1 读写行为）。
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


def _mk_run(db, schedule_name="舆情订阅"):
    """建订阅 + done 期次，返回 (schedule_id, run_id)——溯源快照挂靠点。"""
    s = db.create_schedule(schedule_name, "p", "general", "corporate",
                           "daily", "09:00")
    db.insert_schedule_run("run_l1", s["id"], None)
    db.update_schedule_run("run_l1", "done", summary=["要点"])
    return s["id"], "run_l1"


# ── 建表幂等 ─────────────────────────────────────────────────


def test_initialize_idempotent(tmp_db):
    """重复 initialize 不炸（CREATE TABLE IF NOT EXISTS 幂等；0.6 §4.1
    「新表启动幂等建表」——存量库升级安装路径）。"""
    tmp_db.initialize()
    row = tmp_db.create_lead("华润")
    assert row["id"].startswith("lead_")


def test_leads_table_schema_defaults(tmp_db):
    """DDL 默认值：source_type=manual / status=new / note 空串。"""
    row = tmp_db.create_lead("华润")
    assert row["source_type"] == "manual"
    assert row["status"] == "new"
    assert row["note"] == ""
    assert row["source_run_id"] is None
    assert row["source_schedule_name"] == ""
    assert row["project_id"] is None
    assert row["created_at"] and row["updated_at"]


# ── CRUD ─────────────────────────────────────────────────────


def test_create_lead_run_source_snapshot(tmp_db):
    """run 来源：source_run_id + 订阅名快照落库（期次删除后仍可读溯源）。"""
    _mk_run(tmp_db)
    row = tmp_db.create_lead("华润", source_type="run",
                             source_run_id="run_l1",
                             source_schedule_name="舆情订阅")
    assert row["source_type"] == "run"
    assert row["source_run_id"] == "run_l1"
    assert row["source_schedule_name"] == "舆情订阅"


def test_get_lead_missing_returns_none(tmp_db):
    assert tmp_db.get_lead("lead_nope") is None


def _backdate_lead(db, lead_id, updated_at):
    """显式控制 updated_at（同微秒连建排序不稳定，test_schedules_db._backdate 同源）。"""
    db.conn.execute(
        "UPDATE leads SET updated_at = ? WHERE id = ?", (updated_at, lead_id))
    db.conn.commit()


def test_list_leads_status_filter_and_pagination(tmp_db):
    a = tmp_db.create_lead("甲", status="new")
    b = tmp_db.create_lead("乙", status="contacting")
    c = tmp_db.create_lead("丙", status="new")
    _backdate_lead(tmp_db, a["id"], "2026-10-01T09:00:00")
    _backdate_lead(tmp_db, b["id"], "2026-10-02T09:00:00")
    _backdate_lead(tmp_db, c["id"], "2026-10-03T09:00:00")
    assert len(tmp_db.list_leads()) == 3
    assert len(tmp_db.list_leads(status="new")) == 2
    # updated_at DESC：后建在前
    assert [r["name"] for r in tmp_db.list_leads()] == ["丙", "乙", "甲"]
    page = tmp_db.list_leads(limit=2, offset=0)
    assert len(page) == 2
    assert tmp_db.count_leads() == 3
    assert tmp_db.count_leads(status="new") == 2


def test_update_lead_whitelist(tmp_db):
    row = tmp_db.create_lead("甲")
    assert tmp_db.update_lead(row["id"], {
        "status": "contacting", "note": "已电话联系",
        "hacker_column": "x",                # 白名单外忽略
    })
    got = tmp_db.get_lead(row["id"])
    assert got["status"] == "contacting"
    assert got["note"] == "已电话联系"
    assert "hacker_column" not in got.keys()


def test_update_lead_convert_backfill(tmp_db):
    """立项回填：project_id + status=converted 单行 PATCH（0.6 §4.1）。"""
    row = tmp_db.create_lead("甲")
    assert tmp_db.update_lead(row["id"],
                              {"project_id": "proj_1", "status": "converted"})
    got = tmp_db.get_lead(row["id"])
    assert got["project_id"] == "proj_1"
    assert got["status"] == "converted"


def test_update_lead_missing_returns_false(tmp_db):
    assert not tmp_db.update_lead("lead_nope", {"note": "x"})


def test_delete_lead(tmp_db):
    row = tmp_db.create_lead("甲")
    assert tmp_db.delete_lead(row["id"])
    assert tmp_db.get_lead(row["id"]) is None
    assert not tmp_db.delete_lead(row["id"])   # 再删 False


# ── 溯源快照删除链（0.6 §4.1 读写行为）──────────────────────


def test_detach_leads_for_run(tmp_db):
    """期次删除：线索不删，source_run_id 置 NULL，订阅名快照保留可读。"""
    _mk_run(tmp_db)
    row = tmp_db.create_lead("华润", source_type="run",
                             source_run_id="run_l1",
                             source_schedule_name="舆情订阅")
    assert tmp_db.detach_leads_for_run("run_l1") == 1
    got = tmp_db.get_lead(row["id"])
    assert got["source_run_id"] is None
    assert got["source_schedule_name"] == "舆情订阅"   # 快照兜底可读


def test_detach_leads_for_schedule(tmp_db):
    """订阅删除（runs 级联删前调用）：按 schedule 关联全部置 NULL。"""
    schedule_id, _ = _mk_run(tmp_db)
    row = tmp_db.create_lead("华润", source_type="run",
                             source_run_id="run_l1",
                             source_schedule_name="舆情订阅")
    assert tmp_db.detach_leads_for_schedule(schedule_id) == 1
    assert tmp_db.get_lead(row["id"])["source_run_id"] is None


def test_detach_leads_for_project(tmp_db):
    """项目删除联动（CodeReview Major 2026-10-24）：立项引用置 NULL，
    线索与 converted 状态保留（前端 project_id 空即不渲染「查看项目」）。"""
    row = tmp_db.create_lead("甲")
    tmp_db.update_lead(row["id"],
                       {"project_id": "proj_1", "status": "converted"})
    assert tmp_db.detach_leads_for_project("proj_1") == 1
    got = tmp_db.get_lead(row["id"])
    assert got["project_id"] is None
    assert got["status"] == "converted"
    assert tmp_db.detach_leads_for_project("proj_1") == 0   # 幂等


def test_detach_leads_manual_untouched(tmp_db):
    """manual 线索无 source_run_id，删除链不触碰。"""
    row = tmp_db.create_lead("手动线索")
    assert tmp_db.detach_leads_for_run("run_l1") == 0
    assert tmp_db.get_lead(row["id"])["name"] == "手动线索"
