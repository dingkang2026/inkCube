"""python/tests/test_usage_api.py — 用量读端点与聚合（2.10 §4.6/§8）。

覆盖：/usage/summary 的 today/7d/30d/all 边界与 by_day/by_source/by_model/
by_project 聚合正确性（含 project_name 补齐、空表全零）；project_id 筛选下
by_source 不含 task 行（任务不归属项目，维度口径不变量）；/usage/task 轮次
聚合；get_usage_by_round 的 models 去重串与空轮 {}；get_usage_by_tasks/
projects/entities 行聚合；attach_usage_to_last_assistant 收尾附着。
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import deps
from db.database import Database


def _ts(days_ago: int, hour: int = 12, minute: int = 0) -> str:
    """相对当前的行时间戳（本地 ISO，date(ts) 按天分组口径）。"""
    t = datetime.now() - timedelta(days=days_ago)
    return t.replace(hour=hour, minute=minute, second=0, microsecond=0).isoformat()


def _row(db, ts, source="chat", model="m1", project_id=None, task_id=None,
         round_id=None, entity_id=None, purpose=None, detail=None,
         prompt=10, completion=5):
    db.insert_llm_usage({
        "ts": ts, "source": source, "purpose": purpose, "task_id": task_id,
        "round_id": round_id, "project_id": project_id, "entity_id": entity_id,
        "model": model, "mode": "full", "prompt_tokens": prompt,
        "completion_tokens": completion, "total_tokens": prompt + completion,
        "detail": detail,
    })


@pytest.fixture()
def db(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        db = Database(os.path.join(td, "t.db"))
        db.initialize()
        monkeypatch.setattr(deps, "db", db)
        yield db
        db.conn.close()


@pytest.fixture()
def client(monkeypatch, db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.usage import router as usage_router
    app = FastAPI()
    app.include_router(usage_router)
    return TestClient(app), db


# ── /usage/summary：时间窗口边界 ──


def test_summary_today_boundary(db):
    """today = 本地自然日：今天 0 点后 1 行计入，昨天行不计；total 恒全量。"""
    now = datetime.now()
    _row(db, now.replace(hour=0, minute=0, second=0, microsecond=1).isoformat())
    _row(db, _ts(1), model="m2")  # 昨天
    s = db.get_usage_summary(range_key="today")
    assert s["today"]["calls"] == 1
    assert s["today"]["total_tokens"] == 15
    assert s["total"]["calls"] == 2 and s["total"]["total_tokens"] == 30
    assert s["range"]["calls"] == 1 and s["range_key"] == "today"


def test_summary_range_windows(db):
    """7d 含 6 天前不含 8 天前；30d 含 29 天前不含 31 天前；all 全量。"""
    _row(db, _ts(6), model="m6")
    _row(db, _ts(8), model="m8")
    _row(db, _ts(29), model="m29")
    _row(db, _ts(31), model="m31")
    assert db.get_usage_summary(range_key="7d")["range"]["calls"] == 1
    assert db.get_usage_summary(range_key="30d")["range"]["calls"] == 3
    assert db.get_usage_summary(range_key="all")["range"]["calls"] == 4
    # 非法 range_key 视作 all（统计页兜底口径，与 _usage_range_start 一致）
    assert db.get_usage_summary(range_key="bogus")["range"]["calls"] == 4


def test_summary_empty_all_zero(db):
    """空表：today/total/range 全零 + models_count=0，by_* 空列表。"""
    s = db.get_usage_summary()
    empty = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}
    assert s["today"] == empty and s["range"] == empty
    assert s["total"]["models_count"] == 0
    assert s["by_day"] == [] and s["by_source"] == [] and s["by_model"] == []
    assert s["by_project"] == []


# ── by_* 聚合与维度口径 ──


def test_summary_by_groups(db):
    p1 = db.create_project(name="甲项目")["id"]
    p2 = db.create_project(name="乙项目")["id"]
    t = _ts(0)
    _row(db, t, source="chat", model="m1", project_id=p1)
    _row(db, t, source="chat", model="m2", project_id=p1, prompt=20, completion=10)
    _row(db, t, source="vlm", model="m1", project_id=p2)
    _row(db, t, source="task", model="m1", task_id="t1", round_id="r1")
    s = db.get_usage_summary(range_key="all")
    by_source = {r["grp"]: r for r in s["by_source"]}
    assert set(by_source) == {"chat", "vlm", "task"}
    assert by_source["chat"]["total_tokens"] == 45
    assert by_source["vlm"]["calls"] == 1
    by_model = {r["grp"]: r for r in s["by_model"]}
    assert by_model["m1"]["calls"] == 3
    assert by_model["m2"]["total_tokens"] == 30
    by_project = {r["grp"]: r for r in s["by_project"]}
    assert by_project[p1]["project_name"] == "甲项目"
    assert by_project[p2]["project_name"] == "乙项目"
    assert s["total"]["models_count"] == 2
    # by_day：同日多行聚合为一行
    assert len(s["by_day"]) == 1 and s["by_day"][0]["calls"] == 4


def test_project_filter_excludes_task_source(db):
    """维度口径不变量：任务不归属项目 → project_id 筛选下 by_source 无 task 行。"""
    p1 = db.create_project(name="P")["id"]
    t = _ts(0)
    _row(db, t, source="chat", model="m1", project_id=p1)
    _row(db, t, source="task", model="m1", task_id="t1", round_id="r1")
    s = db.get_usage_summary(range_key="all", project_id=p1)
    assert [r["grp"] for r in s["by_source"]] == ["chat"]
    assert s["total"]["calls"] == 1


# ── 轮次聚合与行级 group_by ──


def test_task_rounds_and_by_round(db):
    t = _ts(0)
    _row(db, t, source="task", model="mA", task_id="T", round_id="r1")
    _row(db, t, source="task", model="mB", task_id="T", round_id="r1")
    _row(db, t, source="task", model="mA", task_id="T", round_id="r2", prompt=7)
    rounds = db.get_usage_task_rounds("T")
    assert [r["round_id"] for r in rounds] == ["r1", "r2"]
    assert rounds[0]["calls"] == 2 and rounds[0]["total_tokens"] == 30
    assert set(rounds[0]["models"].split(",")) == {"mA", "mB"}
    # 单轮：去重 models 串；无记录轮返回空 dict（调用方跳过）
    one = db.get_usage_by_round("r1")
    assert one["calls"] == 2 and set(one["models"].split(",")) == {"mA", "mB"}
    assert db.get_usage_by_round("nope") == {}
    # 未知任务的轮次列表为空
    assert db.get_usage_task_rounds("nope") == []


def test_group_by_helpers(db):
    t = _ts(0)
    _row(db, t, source="task", model="m1", task_id="T1", round_id="r1")
    p1 = db.create_project(name="P")["id"]
    _row(db, t, source="chat", model="m1", project_id=p1)
    _row(db, t, source="report", model="m2", entity_id="R1")
    tasks = db.get_usage_by_tasks(["T1", "ghost"])
    assert set(tasks) == {"T1"} and tasks["T1"]["calls"] == 1
    projects = db.get_usage_by_projects([p1])
    assert projects[p1]["total_tokens"] == 15
    entities = db.get_usage_by_entities(["R1"])
    assert entities["R1"]["completion_tokens"] == 5
    assert db.get_usage_by_entities([]) == {}


# ── 路由层 ──


def test_summary_route_shape(client):
    c, db = client
    p1 = db.create_project(name="P")["id"]
    _row(db, _ts(0), source="chat", model="m1", project_id=p1)
    r = c.get("/usage/summary", params={"range_key": "7d"})
    assert r.status_code == 200
    body = r.json()
    assert body["range_key"] == "7d"
    assert body["total"]["models_count"] == 1
    assert body["range"]["total_tokens"] == 15
    assert body["by_project"][0]["project_name"] == "P"
    assert {"today", "total", "range", "by_day", "by_source", "by_model",
            "by_project"} <= set(body)
    # project_id 筛选经查询参数透传
    r2 = c.get("/usage/summary", params={"range_key": "all", "project_id": p1})
    assert r2.json()["total"]["calls"] == 1


def test_task_rounds_route(client):
    c, db = client
    _row(db, _ts(0), source="task", model="m1", task_id="T", round_id="r1")
    body = c.get("/usage/task/T").json()
    assert [r["round_id"] for r in body["rounds"]] == ["r1"]
    assert c.get("/usage/task/nope").json() == {
        "rounds": [], "has_more": False, "total": 0}


# ── 轮次收尾：assistant metadata.usage 附着 ──


def test_attach_usage_to_last_assistant(db, tmp_path):
    task_id = db.create_task("attach 用例", str(tmp_path))["id"]
    db.save_task_message(task_id, "user", "问题")
    db.save_task_message(task_id, "assistant", "回答", metadata={"key": "v"})
    db.attach_usage_to_last_assistant(task_id, {
        "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
        "calls": 1, "models": "m1",
    })
    rows = db.conn.execute(
        "SELECT metadata FROM task_messages WHERE task_id = ? AND role = 'assistant'",
        (task_id,)).fetchall()
    meta = rows[0][0]
    import json
    parsed = json.loads(meta)
    assert parsed["key"] == "v"  # 既有 metadata 保留
    assert parsed["usage"]["total_tokens"] == 15

    # 无 assistant 消息：静默返回不炸
    db.attach_usage_to_last_assistant("ghost", {"total_tokens": 1})

    # 损坏 metadata：重建为 {} 后写入（不抛异常）
    db.save_task_message(task_id, "assistant", "第二条", metadata=None)
    db.conn.execute(
        "UPDATE task_messages SET metadata = 'not-json' WHERE task_id = ? "
        "AND role = 'assistant'", (task_id,))
    db.conn.commit()
    db.attach_usage_to_last_assistant(task_id, {"total_tokens": 2})
    row = db.conn.execute(
        "SELECT metadata FROM task_messages WHERE task_id = ? "
        "ORDER BY created_at DESC, rowid DESC LIMIT 1", (task_id,)).fetchone()
    import json
    assert json.loads(row[0])["usage"]["total_tokens"] == 2
