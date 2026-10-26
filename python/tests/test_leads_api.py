"""python/tests/test_leads_api.py — 0.6 §5 V2-E2: /leads 路由契约单测。

单 router 挂载（test_schedules_api 同口径，不拉起 main app lifespan）。
契约（0.6 §5 通道表 leads:list/create/update/delete/extract）：
- GET /leads：limit/offset/has_more/total 分页契约（0.1 §16）+ status 过滤
- POST /leads：name 必填非空；source_type ∈ run|manual；run 来源带快照
- PATCH /leads/{id}：status 值域校验 400；404
- DELETE /leads/{id}：404 / ok
- POST /leads/extract：404 / running 409 / 无报告 404 / LLM 候选 JSON 解析
  （monkeypatch llm.client.LLMClient）/ 解析失败 500 EXTRACT_PARSE_FAILED
- POST /leads/convert-extract：404 lead / 400 无来源 / 404 run 悬空 /
  五字段提取 + 档位路由 / suggestedType 值域外归 general / 解析失败 500
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


@pytest.fixture()
def client_and_db(monkeypatch):
    import deps
    from db.database import Database
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import leads as leads_router
    with tempfile.TemporaryDirectory() as td:
        db = Database(os.path.join(td, "t.db"))
        db.initialize()
        monkeypatch.setattr(deps, "db", db)
        monkeypatch.setattr(leads_router, "get_data_dir", lambda: td)
        app = FastAPI()
        app.include_router(leads_router.router)
        yield TestClient(app), db, td
        db.conn.close()


def _mk_done_run(db, data_dir, report_text="## 摘要\n- 华润启动新园区谈判"):
    """建订阅 + done 期次 + 报告副本（data_dir = monkeypatch 后的 get_data_dir，
    与端点白名单同目录），返回 run 行。"""
    s = db.create_schedule("舆情订阅", "p", "general", "corporate",
                           "daily", "09:00")
    db.insert_schedule_run("run_l1", s["id"], None)
    sched_dir = os.path.join(data_dir, "schedules")
    os.makedirs(sched_dir, exist_ok=True)
    report_path = os.path.join(sched_dir, "run_l1.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_text)
    db.update_schedule_run("run_l1", "done", report_path=report_path,
                           summary=["华润启动新园区谈判"])
    return db.get_run("run_l1")


# ── GET /leads 分页契约 ──────────────────────────────────────


def test_list_pagination_contract(client_and_db):
    client, db, _td = client_and_db
    for i in range(3):
        db.create_lead(f"线索{i}")
    r = client.get("/leads", params={"limit": 2, "offset": 0})
    body = r.json()
    assert len(body["leads"]) == 2
    assert body["has_more"] is True
    assert body["total"] == 3
    r2 = client.get("/leads", params={"limit": 2, "offset": 2})
    body2 = r2.json()
    assert len(body2["leads"]) == 1
    assert body2["has_more"] is False
    assert body2["total"] == 3
    # limit=0 全量（LeadPool 分组视图）
    assert len(client.get("/leads", params={"limit": 0}).json()["leads"]) == 3


def test_list_status_filter(client_and_db):
    client, db, _td = client_and_db
    db.create_lead("甲", status="new")
    db.create_lead("乙", status="converted")
    body = client.get("/leads", params={"status": "new"}).json()
    assert [r["name"] for r in body["leads"]] == ["甲"]
    assert body["total"] == 1


# ── POST /leads ──────────────────────────────────────────────


def test_create_manual_default(client_and_db):
    client, db, _td = client_and_db
    r = client.post("/leads", json={"name": "华润"})
    assert r.status_code == 200
    row = r.json()["lead"]
    assert row["id"].startswith("lead_")
    assert row["source_type"] == "manual" and row["status"] == "new"


def test_create_run_source_snapshot(client_and_db):
    client, db, _td = client_and_db
    r = client.post("/leads", json={
        "name": "华润", "sourceType": "run", "sourceRunId": "run_l1",
        "sourceScheduleName": "舆情订阅", "note": "信号摘要"})
    row = r.json()["lead"]
    assert row["source_type"] == "run"
    assert row["source_run_id"] == "run_l1"
    assert row["source_schedule_name"] == "舆情订阅"
    assert row["note"] == "信号摘要"


def test_create_invalid_source_type_400(client_and_db):
    client, db, _td = client_and_db
    r = client.post("/leads", json={"name": "x", "sourceType": "hacker"})
    assert r.status_code == 400


def test_create_blank_name_400(client_and_db):
    client, db, _td = client_and_db
    assert client.post("/leads", json={"name": "   "}).status_code == 400
    assert client.post("/leads", json={}).status_code == 422


# ── PATCH /leads/{id} ────────────────────────────────────────


def test_patch_status_and_note(client_and_db):
    client, db, _td = client_and_db
    lead = db.create_lead("甲")
    r = client.patch(f"/leads/{lead['id']}",
                     json={"status": "contacting", "note": "已电话"})
    assert r.status_code == 200
    row = r.json()["lead"]
    assert row["status"] == "contacting" and row["note"] == "已电话"
    # 非法状态 400
    r2 = client.patch(f"/leads/{lead['id']}", json={"status": "hacker"})
    assert r2.status_code == 400
    # 不存在 404
    assert client.patch("/leads/lead_nope", json={"note": "x"}).status_code == 404


def test_patch_convert_backfill(client_and_db):
    """立项回填：project_id + converted（0.6 §4.1 读写行为）。"""
    client, db, _td = client_and_db
    lead = db.create_lead("甲")
    r = client.patch(f"/leads/{lead['id']}",
                     json={"projectId": "proj_1", "status": "converted"})
    row = r.json()["lead"]
    assert row["project_id"] == "proj_1" and row["status"] == "converted"


# ── DELETE /leads/{id} ───────────────────────────────────────


def test_delete_lead(client_and_db):
    client, db, _td = client_and_db
    lead = db.create_lead("甲")
    assert client.delete(f"/leads/{lead['id']}").json() == {"ok": True}
    assert client.delete(f"/leads/{lead['id']}").status_code == 404


# ── POST /leads/extract ──────────────────────────────────────


def test_extract_run_not_found(client_and_db):
    client, db, _td = client_and_db
    r = client.post("/leads/extract", json={"runId": "run_nope"})
    assert r.status_code == 404


def test_extract_running_rejected(client_and_db):
    client, db, _td = client_and_db
    s = db.create_schedule("a", "p", "general", "general", "daily", "09:00")
    db.insert_schedule_run("run_r", s["id"], None)   # running 恒 NULL 报告
    assert client.post("/leads/extract",
                       json={"runId": "run_r"}).status_code == 409


def test_extract_done_without_report_404(client_and_db):
    client, db, _td = client_and_db
    s = db.create_schedule("a", "p", "general", "general", "daily", "09:00")
    db.insert_schedule_run("run_d", s["id"], None)
    db.update_schedule_run("run_d", "done")          # 无副本
    assert client.post("/leads/extract",
                       json={"runId": "run_d"}).status_code == 404


class _FakeLLM:
    """返回固定 JSON 候选清单（monkeypatch llm.client.LLMClient）；
    记录构造 settings——档位路由注入回归锚点（CodeReview Blocker 2026-10-24：
    漏 resolve_model 时 model 为空串，真实请求必被网关拒，Fake 不发真实
    请求故必须显式断言，否则 mock 掩盖缺陷）。"""

    received_settings: dict = {}

    def __init__(self, settings):
        _FakeLLM.received_settings = dict(settings or {})

    async def chat_full(self, messages, tools=None):
        return {"content": '[{"name": "华润", "signal": "启动园区谈判",'
                           ' "evidence": "本期报告提及"}]',
                "finish_reason": "stop", "usage": {}}


class _BadLLM:
    def __init__(self, settings):
        pass

    async def chat_full(self, messages, tools=None):
        return {"content": "这不是 JSON", "finish_reason": "stop", "usage": {}}


def test_extract_candidates(client_and_db, monkeypatch):
    client, db, td = client_and_db
    _mk_done_run(db, td)
    monkeypatch.setattr("llm.client.LLMClient", _FakeLLM)
    r = client.post("/leads/extract", json={"runId": "run_l1"})
    assert r.status_code == 200
    cands = r.json()["candidates"]
    assert cands == [{"name": "华润", "signal": "启动园区谈判",
                      "evidence": "本期报告提及"}]
    # 档位路由回归：settings["model"] 必须已由 resolve_model 解析非空
    # （空串 = 漏路由，真实调用会被网关拒——单测 Fake 拦截不到）
    assert (_FakeLLM.received_settings.get("model") or "") != ""


def test_extract_dedups_same_name(client_and_db, monkeypatch):
    """同名候选去重保留首条（CodeReview Minor 2026-10-24）：前端勾选态与
    入池循环以 name 为键，重名会串染勾选状态并创建重复线索。"""
    client, db, td = client_and_db
    _mk_done_run(db, td)

    class _DupLLM:
        def __init__(self, settings):
            pass

        async def chat_full(self, messages, tools=None):
            return {"content": '[{"name": "华润", "signal": "a", "evidence": "e1"},'
                               '{"name": "华润", "signal": "b", "evidence": "e2"}]',
                    "finish_reason": "stop", "usage": {}}

    monkeypatch.setattr("llm.client.LLMClient", _DupLLM)
    r = client.post("/leads/extract", json={"runId": "run_l1"})
    assert r.status_code == 200
    assert r.json()["candidates"] == [
        {"name": "华润", "signal": "a", "evidence": "e1"}]


def test_extract_parse_failure_500(client_and_db, monkeypatch):
    client, db, td = client_and_db
    _mk_done_run(db, td)
    monkeypatch.setattr("llm.client.LLMClient", _BadLLM)
    r = client.post("/leads/extract", json={"runId": "run_l1"})
    assert r.status_code == 500
    assert r.json()["code"] == "EXTRACT_PARSE_FAILED"


# ── POST /leads/convert-extract ──────────────────────


def _obj_llm_class(content):
    """返回固定 JSON 对象的 LLM Fake 类工厂；记录构造 settings——
    档位路由注入回归锚点（同 _FakeLLM，漏 resolve_model 时 model 为空串）."""

    class _FakeObjLLM:
        received_settings: dict = {}

        def __init__(self, settings):
            _FakeObjLLM.received_settings = dict(settings or {})

        async def chat_full(self, messages, tools=None):
            return {"content": content, "finish_reason": "stop", "usage": {}}

    return _FakeObjLLM


def test_convert_extract_lead_not_found(client_and_db):
    client, db, _td = client_and_db
    r = client.post("/leads/convert-extract", json={"leadId": "lead_nope"})
    assert r.status_code == 404


def test_convert_extract_manual_no_source_400(client_and_db):
    """手动录入线索无来源报告 → 400（0.6 §3.3：无来源空白手填不阻断）。"""
    client, db, _td = client_and_db
    lead = db.create_lead("甲", source_type="manual")
    r = client.post("/leads/convert-extract", json={"leadId": lead["id"]})
    assert r.status_code == 400
    assert r.json()["code"] == "LEAD_NO_SOURCE"


def test_convert_extract_run_gone_404(client_and_db):
    """source_run_id 悬空防御校验（正常由删除联动置 NULL 不经此分支）。"""
    client, db, _td = client_and_db
    lead = db.create_lead("甲", source_type="run", source_run_id="run_gone")
    r = client.post("/leads/convert-extract", json={"leadId": lead["id"]})
    assert r.status_code == 404
    assert r.json()["code"] == "RUN_NOT_FOUND"


def test_convert_extract_fields(client_and_db, monkeypatch):
    client, db, td = client_and_db
    _mk_done_run(db, td)
    lead = db.create_lead("华润", source_type="run", source_run_id="run_l1",
                          source_schedule_name="舆情订阅")
    fake = _obj_llm_class(
        '{"name": "华润置地", "description": "物业开发与园区运营企业",'
        ' "companyName": "华润置地有限公司",'
        ' "creditCode": "91440300192231808N", "suggestedType": "recruitment"}')
    monkeypatch.setattr("llm.client.LLMClient", fake)
    r = client.post("/leads/convert-extract", json={"leadId": lead["id"]})
    assert r.status_code == 200
    assert r.json()["fields"] == {
        "name": "华润置地",
        "description": "物业开发与园区运营企业",
        "companyName": "华润置地有限公司",
        "creditCode": "91440300192231808N",
        "suggestedType": "recruitment",
    }
    # 档位路由回归（同 test_extract_candidates：空串 = 漏路由，真实调用被网关拒）
    assert (fake.received_settings.get("model") or "") != ""


def test_convert_extract_type_fallback_general(client_and_db, monkeypatch):
    """suggestedType 值域外（LLM 输出漂移）归 general，不阻断立项动线。"""
    client, db, td = client_and_db
    _mk_done_run(db, td)
    lead = db.create_lead("甲", source_type="run", source_run_id="run_l1")
    monkeypatch.setattr("llm.client.LLMClient", _obj_llm_class(
        '{"name": "甲", "description": "d", "companyName": "",'
        ' "creditCode": "", "suggestedType": "产业园"}'))
    r = client.post("/leads/convert-extract", json={"leadId": lead["id"]})
    assert r.status_code == 200
    assert r.json()["fields"]["suggestedType"] == "general"


def test_convert_extract_parse_failure_500(client_and_db, monkeypatch):
    client, db, td = client_and_db
    _mk_done_run(db, td)
    lead = db.create_lead("甲", source_type="run", source_run_id="run_l1")
    monkeypatch.setattr("llm.client.LLMClient", _BadLLM)
    r = client.post("/leads/convert-extract", json={"leadId": lead["id"]})
    assert r.status_code == 500
    assert r.json()["code"] == "EXTRACT_PARSE_FAILED"


# ── 删除链端到端（schedules router 挂接点，0.6 §4.1）──────────


def _mount_schedules(monkeypatch, td):
    import deps
    from db.database import Database
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import schedules as schedules_router
    db = Database(os.path.join(td, "t.db"))
    db.initialize()
    monkeypatch.setattr(deps, "db", db)
    monkeypatch.setattr(deps, "scheduler", None)
    monkeypatch.setattr(schedules_router, "get_data_dir", lambda: td)
    app = FastAPI()
    app.include_router(schedules_router.router)
    return TestClient(app), db


def test_delete_run_detaches_lead(monkeypatch):
    """DELETE /schedules/runs/{id} → 关联线索 source_run_id 置 NULL。"""
    with tempfile.TemporaryDirectory() as td:
        client, db = _mount_schedules(monkeypatch, td)
        s = db.create_schedule("舆情订阅", "p", "general", "corporate",
                               "daily", "09:00")
        db.insert_schedule_run("run_l1", s["id"], None)
        db.update_schedule_run("run_l1", "done")
        lead = db.create_lead("华润", source_type="run",
                              source_run_id="run_l1",
                              source_schedule_name="舆情订阅")
        assert client.delete("/schedules/runs/run_l1").json() == {"ok": True}
        got = db.get_lead(lead["id"])
        assert got["source_run_id"] is None
        assert got["source_schedule_name"] == "舆情订阅"
        db.conn.close()


def test_delete_schedule_detaches_lead(monkeypatch):
    """DELETE /schedules/{id}（runs 级联删）→ 关联线索全部置 NULL。"""
    with tempfile.TemporaryDirectory() as td:
        client, db = _mount_schedules(monkeypatch, td)
        s = db.create_schedule("舆情订阅", "p", "general", "corporate",
                               "daily", "09:00")
        db.insert_schedule_run("run_l1", s["id"], None)
        db.update_schedule_run("run_l1", "done")
        lead = db.create_lead("华润", source_type="run",
                              source_run_id="run_l1",
                              source_schedule_name="舆情订阅")
        assert client.delete(f"/schedules/{s['id']}").json() == {"ok": True}
        assert db.get_lead(lead["id"])["source_run_id"] is None
        db.conn.close()
