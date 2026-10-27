"""python/tests/test_schedules_api.py — 2.11 E3 T4/T10: /schedules 路由契约单测。

单 router 挂载（test_task_templates_api 同口径，不拉起 main app lifespan）。
契约（2.11 §4.1 / E3 计划 Task 4 + T10 端点 11）：
- POST /schedules：name 缺省取 prompt 首行截断；prompt 存库前拼接 OUTPUT_CONTRACT；
  next_run_at 服务端计算；interval 非法 400
- PATCH /schedules/{id}：enabled=0 → next_run_at NULL；周期变化或启用 → 重算
- GET /schedules/digest、GET /schedules/runs/inbox、POST /schedules/runs/{id}/read
- GET /schedules/runs/{id}/report：读 D5 副本；report_path 越界 403
- POST /schedules/{id}/run-now：R2 忙 {deferred: true}；正常 {ok: true} 且不动周期
- POST /schedules/{id}/rebuild-archive（T10）：404 / 503 / 200 委派调度器
- DELETE /schedules/{id}：档案工作副本随删（§6）
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
    from routers import schedules as schedules_router
    with tempfile.TemporaryDirectory() as td:
        db = Database(os.path.join(td, "t.db"))
        db.initialize()
        monkeypatch.setattr(deps, "db", db)
        monkeypatch.setattr(deps, "scheduler", None)
        monkeypatch.setattr(schedules_router, "get_data_dir", lambda: td)
        app = FastAPI()
        app.include_router(schedules_router.router)
        yield TestClient(app), db, td
        db.conn.close()


_CREATE_BODY = {
    "prompt": "扫描华润舆情并输出报告",
    "category": "corporate",
    "intervalType": "daily",
    "intervalValue": "09:00",
}


def test_create_returns_schedule_with_contract(client_and_db):
    client, db, _td = client_and_db
    from scheduler import OUTPUT_CONTRACT
    r = client.post("/schedules", json=_CREATE_BODY)
    assert r.status_code == 200
    s = r.json()["schedule"]
    assert s["name"] == "扫描华润舆情并输出报告"[:20]      # name 缺省取首行截断
    assert s["prompt"].endswith(OUTPUT_CONTRACT)           # 存库前拼接产出约定
    assert s["next_run_at"] and s["enabled"] == 1
    assert s["category"] == "corporate" and s["mode"] == "general"


def test_create_with_explicit_name_and_target(client_and_db):
    client, db, _td = client_and_db
    # body 保留 autoImport: False：锁定版本倾斜兼容契约——旧版本前端仍发送
    # 该字段时 Pydantic 忽略未知字段不 422；且新库无列、白名单不认，不入行
    body = {**_CREATE_BODY, "name": "华润舆情周报",
            "targetType": "project", "targetId": "p1", "autoImport": False}
    s = client.post("/schedules", json=body).json()["schedule"]
    assert s["name"] == "华润舆情周报"
    assert s["target_type"] == "project" and s["target_id"] == "p1"
    assert "auto_import" not in s


def test_create_invalid_interval_400(client_and_db):
    client, db, _td = client_and_db
    r = client.post("/schedules", json={**_CREATE_BODY, "intervalType": "hourly"})
    assert r.status_code == 400
    r2 = client.post("/schedules", json={**_CREATE_BODY, "intervalValue": "abc"})
    assert r2.status_code == 400


def test_list_filters(client_and_db):
    client, db, _td = client_and_db
    client.post("/schedules", json={**_CREATE_BODY, "name": "甲", "category": "corporate"})
    client.post("/schedules", json={**_CREATE_BODY, "name": "乙", "category": "industry"})
    assert [s["name"] for s in client.get("/schedules", params={"category": "corporate"}).json()["schedules"]] == ["甲"]
    assert len(client.get("/schedules", params={"q": "乙"}).json()["schedules"]) == 1
    assert len(client.get("/schedules").json()["schedules"]) == 2


def test_patch_enabled_and_period(client_and_db):
    client, db, _td = client_and_db
    s = client.post("/schedules", json=_CREATE_BODY).json()["schedule"]
    client.patch(f"/schedules/{s['id']}", json={"enabled": False})
    row = client.get("/schedules").json()["schedules"][0]
    assert row["enabled"] == 0 and row["next_run_at"] is None   # 停用 → NULL
    client.patch(f"/schedules/{s['id']}",
                 json={"enabled": True, "intervalValue": "MON 08:30",
                       "intervalType": "weekly", "name": "新名"})
    row2 = client.get("/schedules").json()["schedules"][0]
    assert row2["enabled"] == 1 and row2["name"] == "新名"
    assert row2["next_run_at"] and "T08:30:00" in row2["next_run_at"]  # 启用重算
    assert client.patch("/schedules/sch_nope", json={"name": "x"}).status_code == 404


def test_patch_clears_target_with_null(client_and_db):
    """R2-P1-3：PATCH 显式 null 语义——targetType/targetId null = 清空去向
    （前端「不沉淀」恒发 null，exclude_none 旧实现丢弃 → 清不掉且无任何
    API 路径可清除）；其余字段 null = 未提供（防 name: null 误清名字）。"""
    client, db, _td = client_and_db
    s = client.post("/schedules", json={
        **_CREATE_BODY, "targetType": "project", "targetId": "p1"}).json()["schedule"]
    r = client.patch(f"/schedules/{s['id']}",
                     json={"targetType": None, "targetId": None})
    assert r.status_code == 200
    row = r.json()["schedule"]
    assert row["target_type"] is None and row["target_id"] is None
    # null 对非去向字段无清空语义：name=null 保持现值
    r2 = client.patch(f"/schedules/{s['id']}", json={"name": None})
    assert r2.status_code == 200
    assert r2.json()["schedule"]["name"] == s["name"]


def test_create_invalid_weekday_400(client_and_db):
    """R2-P2-1：weekly 非法星期名使 compute_next_run 报 KeyError——create
    端点校验须与 PATCH 同口径（400 结构化报错而非 500）。"""
    client, db, _td = client_and_db
    r = client.post("/schedules", json={
        **_CREATE_BODY, "intervalType": "weekly", "intervalValue": "ABC 09:00"})
    assert r.status_code == 400
    assert r.json()["code"] == "INVALID_INTERVAL_VALUE"


def test_patch_prompt_reappends_contract(client_and_db):
    """R2-P2-3：PATCH prompt 与 create 同口径——存库前拼接 OUTPUT_CONTRACT
    （D4「存最终 prompt」原则，执行侧不重复拼接）。"""
    client, db, _td = client_and_db
    from scheduler import OUTPUT_CONTRACT
    s = client.post("/schedules", json=_CREATE_BODY).json()["schedule"]
    r = client.patch(f"/schedules/{s['id']}", json={"prompt": "  新指令文本  \n"})
    assert r.status_code == 200
    # 仅 rstrip（与 create 同口径，不 strip 首部）
    assert r.json()["schedule"]["prompt"] == "  新指令文本\n\n" + OUTPUT_CONTRACT
    # 回存含 contract 的完整 prompt 先剥再拼，不双拼
    full = r.json()["schedule"]["prompt"]
    r2 = client.patch(f"/schedules/{s['id']}", json={"prompt": full})
    assert r2.json()["schedule"]["prompt"] == full


def test_resume_resets_consecutive_failures(client_and_db):
    """R2-P2-4：熔断（连续 3 失败）后手动恢复（enabled 0→1）必须归零
    consecutive_failures——否则恢复后首次失败立即再熔断，恢复形同虚设。
    toggle 批量恢复同走 PATCH {enabled}，同路径覆盖。"""
    client, db, _td = client_and_db
    s = client.post("/schedules", json=_CREATE_BODY).json()["schedule"]
    db.set_schedule_state(s["id"], consecutive_failures=3, enabled=0)  # 熔断态
    r = client.patch(f"/schedules/{s['id']}", json={"enabled": True})
    assert r.status_code == 200
    row = r.json()["schedule"]
    assert row["enabled"] == 1 and row["consecutive_failures"] == 0


def test_summary_counts(client_and_db):
    """摘要行计数：n=订阅总数 / total=未读 done run / abnormal=熔断订阅
    （enabled=0 且 consecutive_failures>0；手动暂停不计入——用户主动决策
    非告警，2026-10-23 口径收紧）。
    T1 schedule_unread_count 的消费端点（§2.9：db 方法必须上链）。"""
    client, db, _td = client_and_db
    s1 = client.post("/schedules", json=_CREATE_BODY).json()["schedule"]
    client.post("/schedules", json={**_CREATE_BODY, "name": "乙"})
    s3 = client.post("/schedules", json={**_CREATE_BODY, "name": "丙"}).json()["schedule"]
    client.patch(f"/schedules/{s1['id']}", json={"enabled": False})      # 手动暂停 → 不计
    db.set_schedule_state(s3["id"], enabled=0, consecutive_failures=3)   # 熔断 → 计 1
    for i in ("run_1", "run_2"):
        db.insert_schedule_run(i, s1["id"], None)
        db.update_schedule_run(i, "done")
    db.mark_run_read("run_1")
    r = client.get("/schedules/summary")
    assert r.status_code == 200
    assert r.json() == {"totals": {"n": 3, "total": 1, "abnormal": 1}}


def test_digest_inbox_and_read(client_and_db):
    client, db, _td = client_and_db
    s = client.post("/schedules", json=_CREATE_BODY).json()["schedule"]
    db.insert_schedule_run("run_1", s["id"], None)
    db.update_schedule_run("run_1", "done", summary=["要点"])
    digest = client.get("/schedules/digest").json()["digest"]
    assert [r["id"] for r in digest] == ["run_1"]
    inbox = client.get("/schedules/runs/inbox").json()["runs"]
    assert [r["id"] for r in inbox] == ["run_1"]
    assert inbox[0]["schedule_name"] == s["name"]
    assert client.post("/schedules/runs/run_1/read").json() == {"ok": True}
    # 已读不逐出收件箱（sort=unread 为未读前置非过滤；unread 纯过滤参数
    # 已随两 Tab 收敛退役删除链，2026-10-25）
    assert [r["id"] for r in
            client.get("/schedules/runs/inbox").json()["runs"]] == ["run_1"]
    assert client.post("/schedules/runs/run_x/read").status_code == 404


def test_runs_history(client_and_db):
    client, db, _td = client_and_db
    s = client.post("/schedules", json=_CREATE_BODY).json()["schedule"]
    db.insert_schedule_run("run_1", s["id"], None)
    r = client.get(f"/schedules/{s['id']}/runs")
    assert [x["id"] for x in r.json()["runs"]] == ["run_1"]
    assert client.get("/schedules/sch_nope/runs").status_code == 404
    assert client.delete(f"/schedules/{s['id']}").json() == {"ok": True}
    assert client.get("/schedules").json()["schedules"] == []


def test_run_report_reads_copy_and_rejects_outside(client_and_db):
    client, db, td = client_and_db
    s = client.post("/schedules", json=_CREATE_BODY).json()["schedule"]
    copy_path = os.path.join(td, "schedules", "run_1.md")
    os.makedirs(os.path.dirname(copy_path), exist_ok=True)
    with open(copy_path, "w", encoding="utf-8") as f:
        f.write("## 摘要\n- 要点\n")
    db.insert_schedule_run("run_1", s["id"], None)
    db.update_schedule_run("run_1", "done", report_path=copy_path)
    r = client.get("/schedules/runs/run_1/report")
    assert r.status_code == 200 and "要点" in r.json()["content"]
    # T8 ReadView 契约：响应携带 run 元数据（标题行/失败态/沉淀态数据）与订阅行
    body = r.json()
    assert body["run"]["id"] == "run_1" and body["run"]["status"] == "done"
    assert body["run"]["schedule_name"] == s["name"]
    assert body["schedule"]["id"] == s["id"]
    assert body["schedule"]["target_type"] is None
    # 副本缺失 404
    db.insert_schedule_run("run_2", s["id"], None)
    db.update_schedule_run("run_2", "done", report_path=os.path.join(td, "schedules", "gone.md"))
    assert client.get("/schedules/runs/run_2/report").status_code == 404
    # report_path 越界（指向 schedules 目录外）403
    outside = os.path.join(td, "evil.md")
    with open(outside, "w", encoding="utf-8") as f:
        f.write("x")
    db.insert_schedule_run("run_3", s["id"], None)
    db.update_schedule_run("run_3", "done", report_path=outside)
    r3 = client.get("/schedules/runs/run_3/report")
    assert r3.status_code == 403 and r3.json()["code"] == "REPORT_PATH_OUTSIDE"


def test_run_report_failed_without_report(client_and_db):
    """T9 修订②：failed run 无副本 → 200 元数据（content 空，ReadView 失败卡
    可达 §8.4）；done 无副本仍 404（REPORT_NOT_READY）。"""
    client, db, _td = client_and_db
    s = client.post("/schedules", json=_CREATE_BODY).json()["schedule"]
    db.insert_schedule_run("run_f", s["id"], None)
    db.update_schedule_run("run_f", "failed", error="report.md 未产出或为空")
    r = client.get("/schedules/runs/run_f/report")
    assert r.status_code == 200
    body = r.json()
    assert body["run"]["status"] == "failed"
    assert body["run"]["error"] == "report.md 未产出或为空"
    assert body["content"] == ""
    assert body["schedule"]["id"] == s["id"]
    db.insert_schedule_run("run_d", s["id"], None)
    db.update_schedule_run("run_d", "done")                # done 无副本 → 仍 404
    assert client.get("/schedules/runs/run_d/report").status_code == 404


def test_run_report_running_returns_metadata(client_and_db):
    """2026-10-23 running 扩展：running run 无副本 → 200 元数据（content 空），
    报告页执行中态可达、头部「执行过程」→ 实时回放（原 404 使期次历史行
    点击死胡同）；markRead 契约：仅 done 调用由前端保证，后端不变。"""
    client, db, _td = client_and_db
    s = client.post("/schedules", json=_CREATE_BODY).json()["schedule"]
    db.insert_schedule_run("run_r", s["id"], None)         # 默认 running，无副本
    r = client.get("/schedules/runs/run_r/report")
    assert r.status_code == 200
    body = r.json()
    assert body["run"]["status"] == "running"
    assert body["content"] == ""
    assert body["schedule"]["id"] == s["id"]


def test_run_now_busy_defers(client_and_db, monkeypatch):
    client, db, td = client_and_db
    import deps
    from scheduler import ScheduleScheduler
    from tests.test_scheduler import _FakeAgent
    s = client.post("/schedules", json=_CREATE_BODY).json()["schedule"]
    human = db.create_task("人工", data_dir=td)
    fake = _FakeAgent(db, td, human_busy_id=human["id"])
    monkeypatch.setattr(deps, "scheduler",
                        ScheduleScheduler(db, td, agent=fake))
    r = client.post(f"/schedules/{s['id']}/run-now")
    assert r.status_code == 200
    assert r.json() == {"ok": False, "deferred": True}
    assert db.list_schedule_runs(s["id"]) == []            # 未建行，周期不动


def test_run_now_accepted_async(client_and_db, monkeypatch):
    """run-now 异步受理（review P2-6）：端点透传调度器受理结果，受理即返回
    {ok: true}（不等待本期完成，终态经通知中心反馈）；不动周期语义由
    dispatch_now 单测覆盖；忙时 deferred 语义不变（见 test_run_now_busy_defers）。"""
    client, db, td = client_and_db
    import deps
    calls = []

    class _StubSched:
        async def dispatch_now(self, s):
            calls.append(s["id"])
            return {"ok": True}

    monkeypatch.setattr(deps, "scheduler", _StubSched())
    s = client.post("/schedules", json=_CREATE_BODY).json()["schedule"]
    before = s["next_run_at"]
    r = client.post(f"/schedules/{s['id']}/run-now")
    assert r.status_code == 200
    assert r.json() == {"ok": True}                        # 受理即返回
    assert calls == [s["id"]]
    assert client.get("/schedules").json()["schedules"][0]["next_run_at"] == before
    assert client.post("/schedules/sch_nope/run-now").status_code == 404


def test_fixed_routes_not_shadowed_by_dynamic(client_and_db):
    """固定路径（digest/inbox）注册先于 /{schedule_id} 动态段。"""
    client, db, _td = client_and_db
    r = client.get("/schedules/digest")
    assert r.status_code == 200 and "digest" in r.json()
    r2 = client.get("/schedules/runs/inbox")
    assert r2.status_code == 200 and "runs" in r2.json()


def test_rebuild_archive_endpoint(client_and_db, monkeypatch):
    """T10 端点 11（§6）：404 不存在 / 503 调度器未就绪 / 200 委派调度器。"""
    client, db, td = client_and_db
    import deps
    from scheduler import ScheduleScheduler
    s = client.post("/schedules", json=_CREATE_BODY).json()["schedule"]
    assert client.post("/schedules/sch_nope/rebuild-archive").status_code == 404
    assert client.post(
        f"/schedules/{s['id']}/rebuild-archive").status_code == 503  # scheduler None
    calls = []

    async def fake_rebuild(schedule_id):
        calls.append(schedule_id)
        return {"ok": True, "batches": 0}   # P2-3：契约演进，真实态含成功批数

    sched = ScheduleScheduler(db, td, agent=object())
    monkeypatch.setattr(sched, "rebuild_archive", fake_rebuild)
    monkeypatch.setattr(deps, "scheduler", sched)
    r = client.post(f"/schedules/{s['id']}/rebuild-archive")
    assert r.status_code == 200 and r.json() == {"ok": True, "batches": 0}
    assert calls == [s["id"]]


def test_delete_cleans_archive_workfile(client_and_db):
    """§6：删订阅时档案工作副本随删（入库档案归目标容器资产不随删）。"""
    client, db, td = client_and_db
    s = client.post("/schedules", json=_CREATE_BODY).json()["schedule"]
    workfile = os.path.join(td, "schedules", f"archive_{s['id']}.md")
    os.makedirs(os.path.dirname(workfile), exist_ok=True)
    with open(workfile, "w", encoding="utf-8") as f:
        f.write("# 档案")
    assert client.delete(f"/schedules/{s['id']}").json() == {"ok": True}
    assert not os.path.exists(workfile)
    assert client.delete(f"/schedules/{s['id']}").status_code == 404


def test_delete_run_cleans_report_copy(client_and_db):
    """单条删除期次（2026-10-20 审计补齐）：终态 run 可删，报告副本随行
    unlink（B5 同源：行删后副本成孤儿）；非锚点 run 锚点不动。"""
    client, db, td = client_and_db
    s = client.post("/schedules", json=_CREATE_BODY).json()["schedule"]
    copy_path = os.path.join(td, "schedules", "run_old.md")
    os.makedirs(os.path.dirname(copy_path), exist_ok=True)
    with open(copy_path, "w", encoding="utf-8") as f:
        f.write("# 旧报告副本")
    db.insert_schedule_run("run_old", s["id"], None)
    db.update_schedule_run("run_old", "done", report_path=copy_path)
    db.insert_schedule_run("run_keep", s["id"], None)
    db.update_schedule_run("run_keep", "done")
    db.update_schedule(s["id"], {"archive_last_run_id": "run_keep"})

    assert client.delete("/schedules/runs/run_old").json() == {"ok": True}
    assert not os.path.exists(copy_path)              # 孤儿副本随删
    assert db.get_run("run_old") is None
    assert db.get_schedule(s["id"])["archive_last_run_id"] == "run_keep"  # 非锚点不动


def test_delete_run_running_409(client_and_db):
    """running 在途拒删（单 run 无法安全取消——cancel_running 是订阅级，
    等终态后删）；固定路径不被 /schedules/{schedule_id} 动态段遮蔽
    （code=RUN_NOT_FOUND 而非 SCHEDULE_NOT_FOUND 即证明路由命中）。"""
    client, db, _td = client_and_db
    s = client.post("/schedules", json=_CREATE_BODY).json()["schedule"]
    db.insert_schedule_run("run_live", s["id"], None)   # status=running
    r = client.delete("/schedules/runs/run_live")
    assert r.status_code == 409 and r.json()["code"] == "RUN_RUNNING"
    assert db.get_run("run_live")["status"] == "running"   # 行保留
    r2 = client.delete("/schedules/runs/run_nope")
    assert r2.status_code == 404 and r2.json()["code"] == "RUN_NOT_FOUND"


def test_delete_run_anchor_resets_to_null(client_and_db):
    """锚点悬空防护：删的恰是 archive_last_run_id 指向的 run → 锚点置
    NULL（悬空锚点使 pending_archive_runs 永远空集阻断后续增量合并；
    置空后下次合并全量重建，幂等）。"""
    client, db, _td = client_and_db
    s = client.post("/schedules", json=_CREATE_BODY).json()["schedule"]
    db.insert_schedule_run("run_a", s["id"], None)
    db.update_schedule_run("run_a", "done")
    db.update_schedule(s["id"], {"archive_last_run_id": "run_a"})
    assert client.delete("/schedules/runs/run_a").json() == {"ok": True}
    assert db.get_schedule(s["id"])["archive_last_run_id"] is None


def test_delete_run_cancelled_cleans_workspace(client_and_db):
    """cancelled run 的半成品 workspace 随删（对齐 delete_schedule 对被
    取消 run 的清理）；done/failed workspace 不在此入口删（保留期治理）。"""
    client, db, td = client_and_db
    s = client.post("/schedules", json=_CREATE_BODY).json()["schedule"]
    task = db.create_task("影子", data_dir=td)
    os.makedirs(task["workspace_path"], exist_ok=True)
    db.insert_schedule_run("run_cxl", s["id"], task["id"])
    db.update_schedule_run("run_cxl", "cancelled")
    assert client.delete("/schedules/runs/run_cxl").json() == {"ok": True}
    assert not os.path.exists(task["workspace_path"])
    assert db.get_run("run_cxl") is None


def test_patch_target_change_triggers_retarget(client_and_db, monkeypatch):
    """review P1-2：PATCH 更换去向 → 旧容器清旧 + 置空锚点 + 新容器重建
    （「绑定即重建」）；旧值（旧 target_id/archive_file_id）作为清理依据
    传入；target 未变不触发。"""
    client, db, td = client_and_db
    import deps
    from scheduler import ScheduleScheduler
    s = client.post("/schedules", json={**_CREATE_BODY,
                    "targetType": "project", "targetId": "p1"}).json()["schedule"]
    sched = ScheduleScheduler(db, td, agent=object())
    calls: list = []

    async def fake_retarget(schedule_id, old):
        calls.append((schedule_id, old["target_id"], old["archive_file_id"]))
        return {"ok": True}

    monkeypatch.setattr(sched, "retarget_archive", fake_retarget)
    monkeypatch.setattr(deps, "scheduler", sched)
    db.update_schedule(s["id"], {"archive_file_id": "file_old"})
    r = client.patch(f"/schedules/{s['id']}", json={"targetId": "p2"})
    assert r.status_code == 200
    assert calls == [(s["id"], "p1", "file_old")]     # 旧容器值传入
    assert r.json()["schedule"]["target_id"] == "p2"
    r2 = client.patch(f"/schedules/{s['id']}", json={"name": "改名"})
    assert r2.status_code == 200 and len(calls) == 1   # target 未变不触发


def test_delete_cancels_inflight_and_cleans_orphans(client_and_db, monkeypatch):
    """review P2-4：删除订阅 → 先取消在途执行（_on_finish 不再产生孤儿行/
    通知/档案写）+ 孤儿文件清理：per-run 报告副本（行级联删后成孤儿）与
    被取消执行的 workspace 半成品目录随删；档案工作副本照旧（见
    test_delete_cleans_archive_workfile）。"""
    client, db, td = client_and_db
    import deps
    s = client.post("/schedules", json=_CREATE_BODY).json()["schedule"]
    cancelled_calls = []

    class _StubSched:
        async def cancel_archive_bg(self, schedule_id):   # R4-P2-1 新接口
            pass

        async def cancel_running(self, schedule_id):
            cancelled_calls.append(schedule_id)
            return ["run_live"]

    monkeypatch.setattr(deps, "scheduler", _StubSched())
    # 被取消在途 run：关联 task 的 workspace 目录（半成品）
    live_task = db.create_task("影子live", data_dir=td)
    os.makedirs(live_task["workspace_path"], exist_ok=True)
    db.insert_schedule_run("run_live", s["id"], live_task["id"])
    # 历史 done run：副本文件在行级联删后成孤儿
    copy_path = os.path.join(td, "schedules", "run_old.md")
    os.makedirs(os.path.dirname(copy_path), exist_ok=True)
    with open(copy_path, "w", encoding="utf-8") as f:
        f.write("# 旧报告副本")
    db.insert_schedule_run("run_old", s["id"], None)
    db.update_schedule_run("run_old", "done", report_path=copy_path)

    assert client.delete(f"/schedules/{s['id']}").json() == {"ok": True}

    assert cancelled_calls == [s["id"]]                # 删行前取消在途（唯一 cancel；复扫系死防线已移除 R3-P2-3）
    assert not os.path.exists(copy_path)               # 孤儿副本清理
    assert not os.path.exists(live_task["workspace_path"])  # 半成品清理
    assert db.conn.execute(
        "SELECT COUNT(*) AS c FROM task_schedule_runs"
        " WHERE schedule_id = ?", (s["id"],)).fetchone()["c"] == 0


def test_delete_cancels_inflight_archive_rebuild(client_and_db, monkeypatch):
    """R4-P2-1：删除订阅联动取消在途换向重建——旧任务每批重读实时行，删除
    后跑完当前批会向容器孤儿上传档案 + 复活已删工作副本 + 白烧 LLM tokens。
    取消置于 cancel_running 之前：保持「cancel 返回到落库零挂起点」论证
    （本 await 期间若有在途执行，run 行仍 running → tick 防重入挡住）。"""
    client, db, td = client_and_db
    import deps
    s = client.post("/schedules", json=_CREATE_BODY).json()["schedule"]

    class _StubSched:
        def __init__(self):
            self.calls = []

        async def cancel_archive_bg(self, schedule_id):
            self.calls.append(("archive_bg", schedule_id))

        async def cancel_running(self, schedule_id):
            self.calls.append(("running", schedule_id))
            return []

    stub = _StubSched()
    monkeypatch.setattr(deps, "scheduler", stub)

    assert client.delete(f"/schedules/{s['id']}").json() == {"ok": True}

    assert stub.calls == [("archive_bg", s["id"]), ("running", s["id"])]


def test_patch_invalid_interval_400_without_partial_write(client_and_db):
    """P2-2：PATCH 先验证后落库——非法 interval 返 400 且同请求内其他字段
    （name）不落库（半提交会让 tick 反复炸 + 拒不推进，饿死整批）。"""
    client, db, _td = client_and_db
    s = client.post("/schedules", json=_CREATE_BODY).json()["schedule"]
    r = client.patch(f"/schedules/{s['id']}",
                     json={"name": "新名", "intervalValue": "25:00"})
    assert r.status_code == 400
    assert r.json()["code"] == "INVALID_INTERVAL_VALUE"
    row = client.get("/schedules").json()["schedules"][0]
    assert row["interval_value"] == "09:00"           # 原值保留
    assert row["name"] != "新名"                       # 同请求字段不落库
