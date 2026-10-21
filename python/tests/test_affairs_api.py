"""python/tests/test_affairs_api.py — 3.0 二期 Task 4:路由 + 创建/激活/心跳挂钩。"""
import asyncio
import os
import sys
import tempfile
from datetime import date, timedelta

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import affairs_service
from db.database import Database
from db.playbook_seed import get_all_playbook_templates


@pytest.fixture()
def client_and_db(monkeypatch):
    import deps
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import affairs as affairs_router
    with tempfile.TemporaryDirectory() as td:
        db = Database(os.path.join(td, "t.db"))
        db.initialize()
        db.ensure_preset_base_libs()  # create_project 的 auto-link 依赖 preset 基础库
        db.seed_playbook_templates(get_all_playbook_templates())
        monkeypatch.setattr(deps, "db", db)
        # 单 router 挂载,避免拉起整个 main app 的 lifespan 依赖
        app = FastAPI()
        app.include_router(affairs_router.router)
        yield TestClient(app), db
        db.conn.close()


def test_list_and_crud(client_and_db):
    client, db = client_and_db
    pid = db.create_project(name="P", project_type="general", stage="before")["id"]
    r = client.get(f"/projects/{pid}/tasks")
    assert r.status_code == 200
    assert len(r.json()["tasks"]) == 2  # general before 模板数
    # create
    r = client.post(f"/projects/{pid}/tasks", json={
        "title": "拜访客户", "task_type": "milestone", "due_date": "2026-10-20"})
    assert r.status_code == 200
    tid = r.json()["task"]["id"]
    assert r.json()["task"]["phase"] == "before"  # 归当前时期(§6.1.7)
    # update(手动完成)
    r = client.patch(f"/projects/{pid}/tasks/{tid}", json={"status": "done"})
    assert r.json()["task"]["status"] == "done"
    assert r.json()["task"]["completed_by"] == "manual"
    # delete
    r = client.delete(f"/projects/{pid}/tasks/{tid}")
    assert r.json()["ok"] is True
    # 404
    assert client.patch(f"/projects/{pid}/tasks/{tid}", json={"status": "open"}).status_code == 404
    assert client.delete(f"/projects/{pid}/tasks/{tid}").status_code == 404


def test_create_recurring_task_seeds_current_occurrence(client_and_db):
    """自建 recurring:校验 rule 并铺当期实例(occ+due 由 rule 推导)。"""
    client, db = client_and_db
    pid = db.create_project(name="P", project_type="general", stage="before")["id"]
    r = client.post(f"/projects/{pid}/tasks", json={
        "title": "周报", "task_type": "recurring",
        "recurrence_rule": {"freq": "monthly", "due_day": 20},
        "auto_complete": {"on": "file_parsed", "match": "周报"}})
    task = r.json()["task"]
    assert task["occurrence_key"] and task["due_date"]
    assert task["template_id"].startswith("user_")  # 裁决 2:自建节奏共享 id


def test_create_recurring_invalid_rule_rejected(client_and_db):
    """非法 rule → 422 不落库:freq 白名单 + due_day 类型/值域(parse_rule 同口径)。"""
    client, db = client_and_db
    pid = db.create_project(name="P", project_type="general", stage="before")["id"]
    bad_rules = [
        {"freq": "weekly", "due_day": 1},       # freq 白名单外
        {"freq": "monthly", "due_day": "end"},   # "end" 仅 quarterly 契约,int("end") 炸
        {"freq": "monthly"},                      # 缺 due_day → int(None) 炸
        {"freq": "monthly", "due_day": 0},        # 产 "2026-10-00" 非法日期
        {"freq": "monthly", "due_day": 32},
        {"freq": "monthly", "due_day": "15"},     # 类型非法
        {"freq": "yearly", "due_day": "13-01"},   # 月份非法
        {"freq": "yearly", "due_day": "02-30"},   # 伪日期(current_occurrence 直拼不 clamp)
        {"freq": "yearly", "due_day": 15},        # 类型非法 → 产 "2026-15"
    ]
    for rule in bad_rules:
        r = client.post(f"/projects/{pid}/tasks", json={
            "title": "周报", "task_type": "recurring", "recurrence_rule": rule})
        assert r.status_code == 422, f"rule={rule}"
    assert len(db.list_phase_tasks(pid)) == 2  # 均未落库


def test_create_recurring_valid_rules_accepted(client_and_db):
    """合法边界不误伤:quarterly 的 "end"、yearly 的 "MM-DD"、monthly 31 号。"""
    client, db = client_and_db
    pid = db.create_project(name="P", project_type="general", stage="before")["id"]
    for rule in ({"freq": "quarterly", "due_day": "end"},
                 {"freq": "yearly", "due_day": "04-30"},
                 {"freq": "monthly", "due_day": 31}):
        r = client.post(f"/projects/{pid}/tasks", json={
            "title": "节奏任务", "task_type": "recurring", "recurrence_rule": rule})
        assert r.status_code == 200, f"rule={rule}"
    assert len(db.list_phase_tasks(pid)) == 5


def test_create_task_unknown_project_404(client_and_db):
    client, _ = client_and_db
    r = client.post("/projects/nope/tasks",
                    json={"title": "x", "task_type": "milestone"})
    assert r.status_code == 404


def test_patch_status_domain_and_system_fields(client_and_db):
    """PATCH:status 值域校验(系统态不可手动设);linked 系统字段透传被忽略。"""
    client, db = client_and_db
    pid = db.create_project(name="P", project_type="general", stage="before")["id"]
    tid = client.post(f"/projects/{pid}/tasks", json={
        "title": "拜访客户", "task_type": "milestone"}).json()["task"]["id"]
    for bad in ("banana", "pending_activation", ""):
        r = client.patch(f"/projects/{pid}/tasks/{tid}", json={"status": bad})
        assert r.status_code == 422, f"status={bad}"
    # linked 系统字段不在更新白名单:静默忽略,不改动
    r = client.patch(f"/projects/{pid}/tasks/{tid}", json={
        "linked_entity_id": "hack", "status": "skipped"})
    assert r.status_code == 200
    task = r.json()["task"]
    assert task["linked_entity_id"] is None
    assert task["status"] == "skipped"


def test_patch_phase_edit(client_and_db):
    """Task 10 开放时期编辑:合法值落库;非法值 422 不落库(值域校验在路由层)。"""
    client, db = client_and_db
    pid = db.create_project(name="P", project_type="general", stage="before")["id"]
    tid = client.post(f"/projects/{pid}/tasks", json={
        "title": "拜访客户", "task_type": "milestone"}).json()["task"]["id"]
    # 'none' 是项目 stage 值,不在任务 phase 值域内
    for bad in ("banana", "none", ""):
        r = client.patch(f"/projects/{pid}/tasks/{tid}", json={"phase": bad})
        assert r.status_code == 422, f"phase={bad}"
    r = client.patch(f"/projects/{pid}/tasks/{tid}", json={"phase": "after"})
    assert r.status_code == 200
    assert r.json()["task"]["phase"] == "after"
    assert db.get_phase_task(tid)["phase"] == "after"  # 确认真正落库


def test_patch_recurrence_rule_validation(client_and_db):
    """Task 10 复审:PATCH rule 与 POST 同口径——伪日期(02-30)等若落库,
    roll 时整组静默停滚,故路由层拒绝;显式 null(清除规则)保留。"""
    client, db = client_and_db
    pid = db.create_project(name="P", project_type="general", stage="before")["id"]
    tid = client.post(f"/projects/{pid}/tasks", json={
        "title": "年报", "task_type": "recurring",
        "recurrence_rule": {"freq": "yearly", "due_day": "12-31"}}).json()["task"]["id"]
    # 伪日期前端放行(mo 1-12/d 1-31),后端权威拒绝
    for bad in ({"freq": "yearly", "due_day": "02-30"},
                {"freq": "monthly", "due_day": "13-01"},
                {"freq": "weekly", "due_day": 1}):
        r = client.patch(f"/projects/{pid}/tasks/{tid}", json={"recurrence_rule": bad})
        assert r.status_code == 422, f"rule={bad}"
    assert db.get_phase_task(tid)["recurrence_rule"] == {"freq": "yearly", "due_day": "12-31"}
    # 合法变更 200
    r = client.patch(f"/projects/{pid}/tasks/{tid}",
                     json={"recurrence_rule": {"freq": "yearly", "due_day": "04-30"}})
    assert r.status_code == 200
    assert db.get_phase_task(tid)["recurrence_rule"]["due_day"] == "04-30"


def test_stage_change_hook_calls_activate(client_and_db, monkeypatch):
    """knowledge.update_project 阶段切换挂钩:stage 变更才触发 activate_phase。"""
    client, db = client_and_db
    pid = db.create_project(name="P", project_type="recruitment", stage="before")["id"]
    calls = []
    monkeypatch.setattr(affairs_service, "activate_phase",
                        lambda db_, pid_, stage: calls.append(stage) or 0)
    from routers.knowledge import update_project, ProjectUpdate
    asyncio.run(update_project(pid, ProjectUpdate(stage="during")))
    assert calls == ["during"]
    asyncio.run(update_project(pid, ProjectUpdate(stage="during")))  # 同值不触发
    assert calls == ["during"]
    asyncio.run(update_project(pid, ProjectUpdate(name="P2")))       # 改名不触发
    assert calls == ["during"]


def test_stage_kept_when_activate_fails(client_and_db, monkeypatch):
    """M-2：activate_phase 失败上抛 → 500 且 stage 保持旧值（先激活后写 stage，
    不留「阶段已换、任务未铺出」中间态）。原实现后置调用且吞异常。"""
    client, db = client_and_db
    pid = db.create_project(name="P", project_type="recruitment", stage="before")["id"]

    def boom(db_, pid_, stage):
        raise RuntimeError("activate failed")

    monkeypatch.setattr(affairs_service, "activate_phase", boom)
    from routers.knowledge import update_project, ProjectUpdate
    with pytest.raises(HTTPException) as ei:
        asyncio.run(update_project(pid, ProjectUpdate(stage="during")))
    assert ei.value.status_code == 500
    assert db.get_project(pid)["stage"] == "before"


def test_heartbeat_hook_calls_roll(client_and_db, monkeypatch):
    """activities_status 心跳入口 check-date roll 挂钩(§5.4 复用心跳不新增轮询)。"""
    client, db = client_and_db
    calls = []
    monkeypatch.setattr(affairs_service, "maybe_roll_daily",
                        lambda db_: calls.append(1) or True)
    from routers.activities import activities_status
    asyncio.run(activities_status())
    assert len(calls) == 1


# ── 3.0 三期 Task 1:GET /projects/due(首页今日工作台 + 色点数据源)──

def _d(offset: int) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


def test_list_due_orders_horizon_and_review_counts(client_and_db):
    """跨项目 open+有截止任务;due ASC;horizon 过滤;reviews 计数内嵌(§7.2 防 N+1)。"""
    client, db = client_and_db
    pa = db.create_project(name="A", project_type="general", stage="before")["id"]
    pb = db.create_project(name="B", project_type="general", stage="before")["id"]
    db.create_phase_task(pa, "before", "milestone", "逾期A", due_date=_d(-2))
    db.create_phase_task(pa, "before", "material", "今日A", due_date=_d(0))
    db.create_phase_task(pb, "before", "milestone", "明日B", due_date=_d(1))
    db.create_phase_task(pb, "before", "report", "八天后B", due_date=_d(8))      # 界外,不返回
    db.create_phase_task(pa, "before", "milestone", "已完成A", due_date=_d(-5),
                         status="done")                                          # 终态,不返回
    db.create_phase_task(pa, "before", "milestone", "无截止A")                   # due NULL,不返回
    now = db._now()
    for rid, pid, resolved in (("rv1", pa, 0), ("rv2", pa, 0), ("rv3", pa, 1)):
        db.conn.execute(
            "INSERT INTO wiki_reviews (id, project_id, type, title, resolved, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)", (rid, pid, "gap", rid, resolved, now))
    db.conn.commit()

    r = client.get("/projects/due")
    assert r.status_code == 200
    body = r.json()
    assert [t["title"] for t in body["tasks"]] == ["逾期A", "今日A", "明日B"]     # due ASC,逾期天然在前
    assert all(t["project_name"] for t in body["tasks"])                          # JOIN 出项目名
    assert all(t["status"] == "open" for t in body["tasks"])
    assert body["reviews_counts"] == {pa: 2}                                      # resolved=1 不计;pb 无发现项不出现


def test_list_due_horizon_validation(client_and_db):
    client, _ = client_and_db
    assert client.get("/projects/due", params={"horizon_days": 0}).status_code == 422
    assert client.get("/projects/due", params={"horizon_days": 32}).status_code == 422
    assert client.get("/projects/due", params={"horizon_days": 1}).status_code == 200
