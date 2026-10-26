"""python/tests/test_schedule_scope.py — 2.11 §12 收敛（2026-10-25）：订阅类型标记。

存量迁移（focus/sweep 退役置 NULL，rollup 保留，幂等重入）/
db 出参归一（非 rollup 值兜底归一 NULL + digest_schedule_ids TEXT(JSON) 读侧解析）/
db intel/rollup 过滤（list_schedules/count_schedules 两值口径）/
API create scope 收窄（仅 rollup 或缺省；focus/sweep/hacker 均 400）/
PATCH scope 白名单移除（类型由创建入口给定，PATCH 静默忽略 scope 字段）/
rollup 约束保留（不沉淀 400、{sections} 占位符、汇总范围仅 rollup 可圈选）。
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


# ── 存量迁移：focus/sweep 退役置 NULL（2.11 §12.2 收敛）──────────


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


def test_scope_retire_focus_sweep(tmp_db):
    db = tmp_db
    s_focus = db.create_schedule("a", "p", "general", "corporate", "daily", "09:00",
                                 scope="focus")
    s_sweep = db.create_schedule("b", "p", "general", "industry", "daily", "09:00",
                                 scope="sweep")
    s_rollup = db.create_schedule("w", "骨架 {sections}", "general", "general",
                                  "weekly", "MON 09:00", scope="rollup")
    db2 = _reopen(db)   # 重启升级：focus/sweep → NULL，rollup 保留
    assert db2.get_schedule(s_focus["id"])["scope"] is None
    assert db2.get_schedule(s_sweep["id"])["scope"] is None
    assert db2.get_schedule(s_rollup["id"])["scope"] == "rollup"
    db2.conn.close()


def test_scope_retire_idempotent(tmp_db):
    db = tmp_db
    s = db.create_schedule("a", "p", "general", "corporate", "daily", "09:00",
                           scope="focus")
    db2 = _reopen(db)
    assert db2.get_schedule(s["id"])["scope"] is None
    # 再次重启迁移重入：已是 NULL 无事发生；rollup 值二次确认不被误清
    s2 = db2.create_schedule("w", "骨架 {sections}", "general", "general",
                             "weekly", "MON 09:00", scope="rollup")
    db3 = _reopen(db2)
    assert db3.get_schedule(s["id"])["scope"] is None
    assert db3.get_schedule(s2["id"])["scope"] == "rollup"
    db3.conn.close()


# ── db 出参归一（2.11 §12.2 收敛后）──────────────────────────


def test_default_scope_normalized(tmp_db):
    db = tmp_db
    s = db.create_schedule("a", "p", "general", "general", "daily", "09:00")
    row = db.get_schedule(s["id"])
    assert row["scope"] is None and row["digest_schedule_ids"] is None
    # 出参恒为两值：None（情报订阅）/ 'rollup'（聚合简报）
    assert all(r["scope"] in (None, "rollup") for r in db.list_schedules())
    assert all(r["scope"] in (None, "rollup")
               for r in db.list_due_schedules("2999-01-01T00:00:00"))


def test_legacy_dirty_value_normalized_to_none(tmp_db):
    """脏值兜底：迁移遗漏的残留 focus/sweep 读侧归一 NULL（零判空消费）。"""
    db = tmp_db
    s = db.create_schedule("a", "p", "general", "general", "daily", "09:00")
    db.conn.execute("UPDATE task_schedules SET scope = 'focus' WHERE id = ?",
                    (s["id"],))
    db.conn.commit()
    assert db.get_schedule(s["id"])["scope"] is None


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


# ── db intel/rollup 过滤（2.11 §12 收敛后两值口径）───────────────


def test_list_count_scope_filter(tmp_db):
    db = tmp_db
    db.create_schedule("情报一", "p", "general", "corporate", "daily", "09:00")
    db.create_schedule("情报二", "p", "general", "industry", "daily", "09:00")
    db.create_schedule("周报", "骨架 {sections}", "general", "general",
                       "weekly", "MON 09:00", scope="rollup")
    intel = db.list_schedules(scope="intel")
    # created_at DESC；同秒创建顺序不定，按集合比较
    assert {r["name"] for r in intel} == {"情报一", "情报二"}
    rollup = db.list_schedules(scope="rollup")
    assert [r["name"] for r in rollup] == ["周报"]
    assert db.count_schedules(scope="intel") == 2
    assert db.count_schedules(scope="rollup") == 1
    assert db.count_schedules() == 3
    # 与其他筛选叠加
    assert db.count_schedules(scope="intel", category="industry") == 1


# ── API create scope 收窄 ────────────────────────────────────


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
    "name": "聚合简报",
    "prompt": "汇总本期要点\n{sections}",
    "intervalType": "weekly", "intervalValue": "MON 09:00",
    "scope": "rollup",
}


def test_create_intel_default_scope_none(client_and_db):
    client, _db = client_and_db
    r = client.post("/schedules", json={
        "prompt": "盯一个企业", "intervalType": "daily",
        "intervalValue": "09:00"})
    assert r.status_code == 200
    row = r.json()["schedule"]
    assert row["scope"] is None and row["target_type"] is None


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


def test_create_scope_retired_values_rejected(client_and_db):
    """focus/sweep 已退役：创建传入即 400（存量行由迁移置 NULL）。"""
    client, _db = client_and_db
    for legacy in ("focus", "sweep"):
        r = client.post("/schedules", json={
            "prompt": "p", "intervalType": "daily",
            "intervalValue": "09:00", "scope": legacy})
        assert r.status_code == 400
        assert r.json()["code"] == "INVALID_SCOPE"


def test_create_scope_invalid(client_and_db):
    client, _db = client_and_db
    r = client.post("/schedules", json={
        "prompt": "p", "intervalType": "daily",
        "intervalValue": "09:00", "scope": "hacker"})
    assert r.status_code == 400
    assert r.json()["code"] == "INVALID_SCOPE"


def test_create_intel_with_digest_ids_forbidden(client_and_db):
    """非 rollup（缺省 scope）带 digestScheduleIds → 400（仅聚合简报可圈选）。"""
    client, _db = client_and_db
    r = client.post("/schedules", json={
        "prompt": "p", "intervalType": "daily", "intervalValue": "09:00",
        "digestScheduleIds": ["s1"]})
    assert r.status_code == 400
    assert r.json()["code"] == "ROLLUP_DIGEST_IDS_FORBIDDEN"


# ── API PATCH：scope 白名单移除 + rollup 约束保留 ─────────────────


def test_update_scope_field_ignored(client_and_db):
    """类型由创建入口给定不可 PATCH 切换：scope 字段被白名单静默忽略。"""
    client, db = client_and_db
    s = db.create_schedule("盯梢", "p", "general", "corporate", "daily", "09:00",
                           target_type="project", target_id="proj_1")
    r = client.patch(f"/schedules/{s['id']}",
                     json={"scope": "rollup", "name": "改名"})
    assert r.status_code == 200
    row = r.json()["schedule"]
    assert row["scope"] is None            # 类型未变
    assert row["name"] == "改名"            # 其余字段正常更新
    assert row["target_type"] == "project"  # 去向不受 rollup 切换影响（未切换）
    # 非法值同样被忽略（白名单外键不校验）
    r2 = client.patch(f"/schedules/{s['id']}", json={"scope": "hacker"})
    assert r2.status_code == 200
    assert r2.json()["schedule"]["scope"] is None


def test_update_rollup_target_forbidden(client_and_db):
    client, db = client_and_db
    s = db.create_schedule("w", "骨架 {sections}", "general", "general",
                           "weekly", "MON 09:00", scope="rollup")
    r = client.patch(f"/schedules/{s['id']}",
                     json={"targetType": "project", "targetId": "proj_1"})
    assert r.status_code == 400
    assert r.json()["code"] == "ROLLUP_TARGET_FORBIDDEN"


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


def test_update_intel_digest_ids_forbidden(client_and_db):
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
