"""python/tests/test_ledger_api.py — 3.0 四期 T5:台账/建议读端点 + 采纳/忽略端点(3.1 §4.3)。

wiki 侧:GET /wiki/projects/{id}/ledger/entries(limit 1-100)、GET /wiki/ledger/latest
(跨项目每项目最新 1 条,JOIN 项目名,首页卡片墙数据源)。
affairs 侧:GET suggestions 列表 / POST accept(create_task 建任务回填、complete_task
打 done、目标失效 404 且建议转 dismissed)/ POST dismiss。
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import Database
from wiki import ledger_service


@pytest.fixture()
def client_and_db(monkeypatch):
    import deps
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import affairs as affairs_router
    from routers import wiki as wiki_router
    with tempfile.TemporaryDirectory() as td:
        db = Database(os.path.join(td, "t.db"))
        db.initialize()
        db.ensure_preset_base_libs()  # create_project 的 auto-link 依赖 preset 基础库
        monkeypatch.setattr(deps, "db", db)
        app = FastAPI()
        app.include_router(wiki_router.router)
        app.include_router(affairs_router.router)
        yield TestClient(app), db
        db.conn.close()


def _seed_entry(db, pid, page_key, date, text, etype):
    # 与实现同源:_fingerprint + 作用域 id(防 helper 与实现漂移,整体 review C1/W3)
    fp = ledger_service._fingerprint(text)
    db.conn.execute(
        "INSERT INTO ledger_entries (id,project_id,page_key,entry_date,entry_type,"
        "content,source_file_id,fingerprint,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,NULL,?,datetime('now'),datetime('now'))",
        (f"le_{pid}_{page_key}_{fp}", pid, page_key, date, etype, text, fp))
    db.conn.commit()
    return fp


def _seed_suggestion(db, pid, kind, title, **kw):
    row = db.create_suggestion(pid, kind=kind, title=title, **kw)
    assert row is not None
    return row


# ── wiki 侧:entries / latest ──

def test_ledger_entries_empty_limit_and_order(client_and_db):
    client, db = client_and_db
    pid = db.create_project(name="P", project_type="general", stage="before")["id"]
    # 空项目
    r = client.get(f"/wiki/projects/{pid}/ledger/entries")
    assert r.status_code == 200 and r.json() == {"entries": [], "has_more": False}
    # limit 越界 422(1-100)
    assert client.get(f"/wiki/projects/{pid}/ledger/entries",
                      params={"limit": 0}).status_code == 422
    assert client.get(f"/wiki/projects/{pid}/ledger/entries",
                      params={"limit": 101}).status_code == 422
    # 两条 → entry_date 倒序
    _seed_entry(db, pid, "milestones", "2026-09", "项目正式投产", "milestone")
    _seed_entry(db, pid, "milestones", "2026-10", "设备全部进场", "milestone")
    r = client.get(f"/wiki/projects/{pid}/ledger/entries")
    dates = [e["entry_date"] for e in r.json()["entries"]]
    assert dates == ["2026-10", "2026-09"]


def test_ledger_entries_filter_by_page_key(client_and_db):
    """V2 E3:page_key 过滤命中指定台账页;缺省/空 = 全量(向后兼容)。"""
    client, db = client_and_db
    pid = db.create_project(name="P", project_type="general", stage="before")["id"]
    _seed_entry(db, pid, "milestones", "2026-10", "设备全部进场", "milestone")
    _seed_entry(db, pid, "milestones", "2026-09", "项目正式投产", "milestone")
    _seed_entry(db, pid, "risk_watchlist", "2026-10", "环保处罚风险", "risk")
    # 过滤命中:只出该页条目,排序/分页结构不变
    r = client.get(f"/wiki/projects/{pid}/ledger/entries",
                   params={"page_key": "milestones"})
    assert r.status_code == 200
    rows = r.json()["entries"]
    assert [e["page_key"] for e in rows] == ["milestones", "milestones"]
    assert [e["entry_date"] for e in rows] == ["2026-10", "2026-09"]
    # 过滤无命中:空列表非 404(空条目集是合法态,Reader 回落原文)
    r = client.get(f"/wiki/projects/{pid}/ledger/entries",
                   params={"page_key": "no_such_page"})
    assert r.status_code == 200
    assert r.json() == {"entries": [], "has_more": False}
    # 缺省 = 全量回归
    assert len(client.get(
        f"/wiki/projects/{pid}/ledger/entries").json()["entries"]) == 3
    # 空串 = 全量(路由层空转 None,向后兼容)
    assert len(client.get(
        f"/wiki/projects/{pid}/ledger/entries",
        params={"page_key": ""}).json()["entries"]) == 3


def test_ledger_latest_one_per_project_with_name(client_and_db):
    client, db = client_and_db
    pa = db.create_project(name="项目A", project_type="general", stage="before")["id"]
    pb = db.create_project(name="项目B", project_type="general", stage="before")["id"]
    _seed_entry(db, pa, "milestones", "2026-08", "A 旧条目", "milestone")
    _seed_entry(db, pa, "milestones", "2026-10", "A 最新", "milestone")
    _seed_entry(db, pb, "risk_watchlist", "2026-09", "B 风险", "risk")
    r = client.get("/wiki/ledger/latest")
    assert r.status_code == 200
    latest = {row["project_id"]: row for row in r.json()["latest"]}
    assert set(latest) == {pa, pb}
    assert latest[pa]["content"] == "A 最新"          # 每项目只出最新 1 条
    assert latest[pa]["project_name"] == "项目A"       # JOIN 出项目名(卡片墙摘要行)
    assert latest[pb]["entry_type"] == "risk"


def test_ledger_latest_empty(client_and_db):
    client, _ = client_and_db
    assert client.get("/wiki/ledger/latest").json() == {"latest": []}


# ── affairs 侧:suggestions 列表 / accept / dismiss ──

def test_list_suggestions_pending_only(client_and_db):
    client, db = client_and_db
    pid = db.create_project(name="P", project_type="general", stage="before")["id"]
    _seed_suggestion(db, pid, "create_task", "跟进风险:xx")
    row = _seed_suggestion(db, pid, "complete_task", "完成设备进场")
    db.decide_suggestion(row["id"], "dismissed")
    r = client.get(f"/projects/{pid}/suggestions")
    assert r.status_code == 200
    rows = r.json()["suggestions"]
    assert len(rows) == 1 and rows[0]["kind"] == "create_task"   # 仅 pending


def test_accept_create_task_suggestion(client_and_db):
    client, db = client_and_db
    pid = db.create_project(name="P", project_type="general", stage="before")["id"]
    sug = _seed_suggestion(db, pid, "create_task", "跟进风险:环保处罚",
                           suggested_phase="during", suggested_task_type="milestone")
    r = client.post(f"/projects/{pid}/suggestions/{sug['id']}/accept")
    assert r.status_code == 200
    task = r.json()["task"]
    assert task["title"] == "跟进风险:环保处罚" and task["phase"] == "during"
    # 建议转 accepted 且回填任务 id
    after = db.get_suggestion(sug["id"])
    assert after["status"] == "accepted"
    assert after["accepted_task_id"] == task["id"]
    assert db.get_phase_task(task["id"])["title"] == "跟进风险:环保处罚"


def test_accept_complete_task_marks_done(client_and_db):
    client, db = client_and_db
    pid = db.create_project(name="P", project_type="general", stage="before")["id"]
    tid = db.create_phase_task(pid, "during", "milestone", "完成设备进场与投产")["id"]
    sug = _seed_suggestion(db, pid, "complete_task", "完成设备进场与投产",
                           target_task_id=tid)
    r = client.post(f"/projects/{pid}/suggestions/{sug['id']}/accept")
    assert r.status_code == 200
    assert r.json()["task"]["status"] == "done"
    assert db.get_suggestion(sug["id"])["accepted_task_id"] == tid


def test_accept_complete_task_target_missing_404_and_dismissed(client_and_db):
    """目标已删 → 404 target_task_missing,且建议转 dismissed(失效永不再提,3.1 §4.4)。"""
    client, db = client_and_db
    pid = db.create_project(name="P", project_type="general", stage="before")["id"]
    sug = _seed_suggestion(db, pid, "complete_task", "完成设备进场",
                           target_task_id="gone-task")
    r = client.post(f"/projects/{pid}/suggestions/{sug['id']}/accept")
    assert r.status_code == 404
    assert r.json()["detail"] == "target_task_missing"
    assert db.get_suggestion(sug["id"])["status"] == "dismissed"


def test_accept_non_pending_or_foreign_404(client_and_db):
    client, db = client_and_db
    pid = db.create_project(name="P", project_type="general", stage="before")["id"]
    sug = _seed_suggestion(db, pid, "create_task", "跟进风险:xx")
    db.decide_suggestion(sug["id"], "dismissed")
    assert client.post(
        f"/projects/{pid}/suggestions/{sug['id']}/accept").status_code == 404
    assert client.post(
        f"/projects/other/suggestions/{sug['id']}/accept").status_code == 404
    assert client.post(
        f"/projects/{pid}/suggestions/nope/accept").status_code == 404


def test_dismiss_suggestion(client_and_db):
    client, db = client_and_db
    pid = db.create_project(name="P", project_type="general", stage="before")["id"]
    sug = _seed_suggestion(db, pid, "create_task", "跟进风险:xx")
    r = client.post(f"/projects/{pid}/suggestions/{sug['id']}/dismiss")
    assert r.status_code == 200
    assert r.json()["suggestion"]["status"] == "dismissed"
    assert db.get_suggestion(sug["id"])["decided_at"]
