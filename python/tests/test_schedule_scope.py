"""python/tests/test_schedule_scope.py — 2.11 §12 V2.1-T2: 订阅 scope 三型。

迁移回填（category→scope CASE，幂等只回填 NULL、用户显式值不覆盖）/
db 出参归一（scope 零判空 + digest_schedule_ids TEXT(JSON) 读侧解析）/
API create·update rollup 约束（不沉淀 400、{sections} 占位符校验、
汇总范围仅 rollup 可圈选、scope 切入 rollup 清残留去向与档案锚点）。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


@pytest.fixture()
def tmp_db():
    from db.database import Database
    with tempfile.TemporaryDirectory() as td:
        db = Database(os.path.join(td, "t.db"))
        db.initialize()
        yield db
        db.conn.close()


# ── 存量回填迁移（2.11 §12.2）─────────────────────────────────


def _reopen(db):
    """模拟应用重启升级：关旧连接 → 新实例 initialize（迁移重入）。

    注：initialize() 每次调用都新建 _LockedConnection，同实例二次调用会
    丢失旧连接引用（Windows 下文件占用）；真实重启路径是新实例，测试同构。"""
    from db.database import Database
    path = db.db_path
    db.conn.close()
    db2 = Database(path)
    db2.initialize()
    return db2


def test_scope_backfill_by_category(tmp_db):
    db = tmp_db
    for cat in ("corporate", "industry", "region", "general"):
        db.create_schedule(f"s-{cat}", "p", "general", cat, "daily", "09:00")
    # 模拟存量行：scope 全 NULL（列迁移刚补上、尚未回填的形态）
    db.conn.execute("UPDATE task_schedules SET scope = NULL")
    db.conn.commit()
    db2 = _reopen(db)   # 重启升级：backfill 回填全部 NULL 行
    rows = {r["category"]: r["scope"] for r in db2.list_schedules()}
    assert rows == {"corporate": "focus", "industry": "sweep",
                    "region": "sweep", "general": "focus"}
    db2.conn.close()


def test_scope_backfill_idempotent_and_keeps_user_value(tmp_db):
    db = tmp_db
    s = db.create_schedule("s-ind", "p", "general", "industry", "daily", "09:00")
    db.conn.execute("UPDATE task_schedules SET scope = NULL")
    db.conn.commit()
    db = _reopen(db)
    assert db.get_schedule(s["id"])["scope"] == "sweep"
    # 用户显式改为 rollup 后再次重启迁移不覆盖（只回填 NULL 行）
    db.update_schedule(s["id"], {"scope": "rollup"})
    db2 = _reopen(db)
    assert db2.get_schedule(s["id"])["scope"] == "rollup"
    db2.conn.close()


# ── db 出参归一（2.11 §12.2 读取归一化）───────────────────────


def test_default_scope_normalized(tmp_db):
    db = tmp_db
    s = db.create_schedule("a", "p", "general", "general", "daily", "09:00")
    row = db.get_schedule(s["id"])
    assert row["scope"] == "focus" and row["digest_schedule_ids"] is None
    valid = ("focus", "sweep", "rollup")
    assert all(r["scope"] in valid for r in db.list_schedules())
    assert all(r["scope"] in valid
               for r in db.list_due_schedules("2999-01-01T00:00:00"))


def test_rollup_ids_roundtrip(tmp_db):
    db = tmp_db
    s = db.create_schedule("w", "骨架 {sections}", "general", "general",
                           "weekly", "MON 09:00", scope="rollup",
                           digest_schedule_ids='["a","b"]')
    row = db.get_schedule(s["id"])
    assert row["scope"] == "rollup"
    assert row["digest_schedule_ids"] == ["a", "b"]   # TEXT(JSON) 读侧解析
    # update 白名单 list 形态入参落库前归一为 TEXT(JSON)
    assert db.update_schedule(s["id"], {"digest_schedule_ids": ["c"]})
    assert db.get_schedule(s["id"])["digest_schedule_ids"] == ["c"]


# ── API create rollup 约束 ────────────────────────────────────


@pytest.fixture()
def client_and_db(monkeypatch):
    import deps
    from db.database import Database
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import schedules as schedules_router
    with tempfile.TemporaryDirectory() as td:
        db = Database(os.path.join(td, "t.db"))
        db.initialize()
        monkeypatch.setattr(deps, "db", db)
        app = FastAPI()
        app.include_router(schedules_router.router)
        yield TestClient(app), db
        db.conn.close()


_ROLLUP_BODY = {
    "name": "组合周报",
    "prompt": "汇总本期要点\n{sections}",
    "intervalType": "weekly", "intervalValue": "MON 09:00",
    "scope": "rollup",
}


def test_create_rollup_ok(client_and_db):
    client, db = client_and_db
    # V2.1-T3 引用校验：digestScheduleIds 须为真实存在且非 rollup 的订阅 id
    s1 = db.create_schedule("源一", "p", "general", "corporate", "daily", "09:00")
    s2 = db.create_schedule("源二", "p", "general", "industry", "daily", "09:00")
    r = client.post("/schedules", json={
        **_ROLLUP_BODY, "digestScheduleIds": [s1["id"], s2["id"]]})
    assert r.status_code == 200
    row = r.json()["schedule"]
    assert row["scope"] == "rollup"
    assert row["digest_schedule_ids"] == [s1["id"], s2["id"]]
    assert row["target_type"] is None and row["target_id"] is None


def test_create_rollup_target_forbidden(client_and_db):
    client, _db = client_and_db
    r = client.post("/schedules", json={
        **_ROLLUP_BODY, "targetType": "project", "targetId": "proj_1"})
    assert r.status_code == 400
    assert r.json()["code"] == "ROLLUP_TARGET_FORBIDDEN"


def test_create_rollup_prompt_needs_section(client_and_db):
    client, _db = client_and_db
    r = client.post("/schedules", json={
        "prompt": "无占位符", "intervalType": "weekly",
        "intervalValue": "MON 09:00", "scope": "rollup"})
    assert r.status_code == 400
    assert r.json()["code"] == "ROLLUP_PROMPT_NEEDS_SECTION"


def test_create_scope_invalid(client_and_db):
    client, _db = client_and_db
    r = client.post("/schedules", json={
        "prompt": "p", "intervalType": "daily",
        "intervalValue": "09:00", "scope": "hacker"})
    assert r.status_code == 400
    assert r.json()["code"] == "INVALID_SCOPE"


def test_create_focus_with_digest_ids_forbidden(client_and_db):
    client, _db = client_and_db
    r = client.post("/schedules", json={
        "prompt": "p", "intervalType": "daily", "intervalValue": "09:00",
        "scope": "focus", "digestScheduleIds": ["s1"]})
    assert r.status_code == 400
    assert r.json()["code"] == "ROLLUP_DIGEST_IDS_FORBIDDEN"


# ── API update scope 切换与 rollup 约束 ───────────────────────


def test_update_switch_to_rollup_clears_target(client_and_db):
    client, db = client_and_db
    s = db.create_schedule("盯梢", "p", "general", "corporate", "daily",
                           "09:00", target_type="project", target_id="proj_1")
    db.update_schedule(s["id"], {"archive_file_id": "file_1"})
    r = client.patch(f"/schedules/{s['id']}", json={"scope": "rollup"})
    assert r.status_code == 200
    row = r.json()["schedule"]
    assert row["scope"] == "rollup"
    assert row["target_type"] is None and row["target_id"] is None
    # §12 档案合并对 rollup 不适用：置空锚点（deps.scheduler=None 分支）
    assert row["archive_file_id"] is None


def test_update_rollup_digest_ids_roundtrip(client_and_db):
    client, db = client_and_db
    s = db.create_schedule("w", "骨架 {sections}", "general", "general",
                           "weekly", "MON 09:00", scope="rollup")
    # V2.1-T3 引用校验：先建两个非 rollup 源订阅再引用
    a = db.create_schedule("源一", "p", "general", "corporate", "daily", "09:00")
    b = db.create_schedule("源二", "p", "general", "industry", "daily", "09:00")
    sid = s["id"]
    r = client.patch(f"/schedules/{sid}",
                     json={"digestScheduleIds": [a["id"], b["id"]]})
    assert r.status_code == 200
    assert r.json()["schedule"]["digest_schedule_ids"] == [a["id"], b["id"]]
    # 显式 null = 清空圈选回全量（NULL 语义）
    r2 = client.patch(f"/schedules/{sid}", json={"digestScheduleIds": None})
    assert r2.status_code == 200
    assert r2.json()["schedule"]["digest_schedule_ids"] is None


def test_update_focus_digest_ids_forbidden(client_and_db):
    client, db = client_and_db
    s = db.create_schedule("a", "p", "general", "general", "daily", "09:00")
    r = client.patch(f"/schedules/{s['id']}",
                     json={"digestScheduleIds": ["a"]})
    assert r.status_code == 400
    assert r.json()["code"] == "ROLLUP_DIGEST_IDS_FORBIDDEN"


def test_update_rollup_prompt_needs_section(client_and_db):
    client, db = client_and_db
    s = db.create_schedule("w", "骨架 {sections}", "general", "general",
                           "weekly", "MON 09:00", scope="rollup")
    r = client.patch(f"/schedules/{s['id']}", json={"prompt": "改成无占位符"})
    assert r.status_code == 400
    assert r.json()["code"] == "ROLLUP_PROMPT_NEEDS_SECTION"
    # 先验证后落库：现值未被半提交
    assert "{sections}" in db.get_schedule(s["id"])["prompt"]


def test_update_scope_invalid(client_and_db):
    client, db = client_and_db
    s = db.create_schedule("a", "p", "general", "general", "daily", "09:00")
    r = client.patch(f"/schedules/{s['id']}", json={"scope": "hacker"})
    assert r.status_code == 400
    assert r.json()["code"] == "INVALID_SCOPE"
