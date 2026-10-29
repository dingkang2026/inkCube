"""meeting_notes DB 层测试（2.13 §2 表结构与状态机）。

要点回归：
- 建行默认 draft；status 流转经 update 白名单（非法值 ValueError、白名单外键忽略）；
- reset_interrupted 只动 structuring 行且幂等（二次调用 0）；
- 项目级联删行（纪要草稿是项目内资产，与便签 detach 回收件箱语义相反）；
- image_file_ids JSON 编解码对称 + 坏数据 decay []；
- system.py _RESET_TABLES 覆盖 meeting_notes（同类必清项，便签 T2 同款缺陷防回归）。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.database import Database


@pytest.fixture
def db():
    d = Database(db_path=":memory:")
    d.initialize()
    yield d
    d.conn.close()


def _project(db) -> str:
    return db.create_project(name="P", seed_playbook=False)["id"]


def test_create_defaults_draft_and_json_roundtrip(db):
    pid = _project(db)
    n = db.create_meeting_note(pid, "拜访-20261027", "2026-10-27",
                               "和王总聊了扩产计划…", ["f1", "f2"])
    assert n["status"] == "draft"
    assert n["image_file_ids"] == ["f1", "f2"]  # 编解码对称
    assert n["raw_text"] == "和王总聊了扩产计划…"
    assert n["saved_refs"] is None and n["draft_md"] is None
    # image_file_ids 缺省（None）读回为空 list
    bare = db.create_meeting_note(pid, "b", None, None, None)
    assert bare["image_file_ids"] == []


def test_update_whitelist_and_status_flow(db):
    pid = _project(db)
    n = db.create_meeting_note(pid, "t", None, "x", [])
    upd = db.update_meeting_note(n["id"], {"status": "structuring", "bogus": "x"})
    assert upd["status"] == "structuring"
    assert "bogus" not in upd  # 白名单外键忽略（路由层已过滤，db 层双保险）
    db.update_meeting_note(n["id"], {"status": "structured", "draft_md": "{}"})
    row = db.get_meeting_note(n["id"])
    assert row["status"] == "structured" and row["draft_md"] == "{}"
    with pytest.raises(ValueError):
        db.update_meeting_note(n["id"], {"status": "bogus-status"})


def test_list_pending_and_by_status(db):
    p1, p2 = _project(db), _project(db)
    db.create_meeting_note(p1, "a", None, None, [])
    b = db.create_meeting_note(p1, "b", None, None, [])
    db.create_meeting_note(p2, "c", None, None, [])
    db.update_meeting_note(b["id"], {"status": "structuring"})
    s = db.create_meeting_note(p1, "s", None, None, [])
    db.update_meeting_note(s["id"], {"status": "structured"})
    # pending = draft + structuring + structured 待确认（structured 必须在列——
    # 否则生成成功后行脱离前端唯一数据源；终态 confirm 全量成功后删行）
    assert [r["title"] for r in db.list_pending_meeting_notes(p1)] == ["s", "b", "a"]
    assert db.list_pending_meeting_notes(p2)[0]["title"] == "c"
    # 跨项目按状态查（Activity FunctionSource 数据源）
    assert len(db.list_meeting_notes_by_status("structuring")) == 1
    assert [r["title"] for r in db.list_meeting_notes_by_status("structured")] == ["s"]


def test_reset_interrupted_idempotent(db):
    pid = _project(db)
    s = db.create_meeting_note(pid, "s", None, None, [])
    d = db.create_meeting_note(pid, "d", None, None, [])
    x = db.create_meeting_note(pid, "x", None, None, [])
    db.update_meeting_note(s["id"], {"status": "structuring"})
    db.update_meeting_note(d["id"], {"status": "structured"})
    db.update_meeting_note(x["id"], {"status": "structuring"})
    assert db.reset_interrupted_meeting_notes() == 2  # 只动 structuring 行
    s_row, d_row = db.get_meeting_note(s["id"]), db.get_meeting_note(d["id"])
    assert s_row["status"] == "draft" and s_row["last_error"] == "interrupted"
    assert d_row["status"] == "structured"  # 不误伤
    assert db.reset_interrupted_meeting_notes() == 0  # 幂等：二次调用 0


def test_project_cascade_delete(db):
    pid = _project(db)
    db.create_meeting_note(pid, "a", None, None, [])
    db.create_meeting_note(pid, "b", None, None, [])
    assert db.delete_meeting_notes_for_project(pid) == 2
    assert db.list_pending_meeting_notes(pid) == []
    assert db.delete_meeting_notes_for_project(pid) == 0


def test_bad_image_ids_decays_empty(db):
    pid = _project(db)
    n = db.create_meeting_note(pid, "t", None, None, ["f1"])
    db.conn.execute(
        "UPDATE meeting_notes SET image_file_ids = 'not-json' WHERE id = ?", (n["id"],))
    db.conn.commit()
    assert db.get_meeting_note(n["id"])["image_file_ids"] == []


def test_get_delete_missing(db):
    pid = _project(db)
    n = db.create_meeting_note(pid, "t", None, None, [])
    assert db.get_meeting_note("missing") is None
    assert db.delete_meeting_note(n["id"]) is True
    assert db.delete_meeting_note(n["id"]) is False


# ── 2.14 纪要中心：archived 终态 + 归档分页 ──


def test_archived_status_in_whitelist(db):
    pid = _project(db)
    n = db.create_meeting_note(pid, "t", None, None, [])
    row = db.update_meeting_note(n["id"], {"status": "archived"})
    assert row["status"] == "archived"  # 终态合法值
    with pytest.raises(ValueError):
        db.update_meeting_note(n["id"], {"status": "bogus-status"})


def test_list_archived_pagination_and_order(db):
    """分页契约（0.1 §16）：拜访日期主序倒序、null occurred_at 回落 created_at；
    limit=0 全量且 has_more 恒 False；非 archived 不在列。"""
    pid = _project(db)
    a = db.create_meeting_note(pid, "早拜访", "2026-10-20", None, [])
    b = db.create_meeting_note(pid, "晚拜访", "2026-10-26", None, [])
    c = db.create_meeting_note(pid, "无日期", None, None, [])  # 回落 created_at（晚于 10-20）
    d = db.create_meeting_note(pid, "还是草稿", None, None, [])
    for n in (a, b, c):
        db.update_meeting_note(n["id"], {"status": "archived"})
    page1 = db.list_meeting_notes_archived(pid)
    assert page1["total"] == 3 and page1["limit"] == 50 and page1["offset"] == 0
    # 无日期回落 created_at（今天）> 晚拜访(10-26) > 早拜访(10-20)
    assert [n["title"] for n in page1["notes"]] == ["无日期", "晚拜访", "早拜访"]
    assert page1["has_more"] is False
    # 手工翻页：limit=1 步进
    p1 = db.list_meeting_notes_archived(pid, limit=1, offset=0)
    p2 = db.list_meeting_notes_archived(pid, limit=1, offset=1)
    p3 = db.list_meeting_notes_archived(pid, limit=1, offset=2)
    assert [p["notes"][0]["title"] for p in (p1, p2, p3)] == ["无日期", "晚拜访", "早拜访"]
    assert (p1["has_more"], p2["has_more"], p3["has_more"]) == (True, True, False)
    assert db.list_meeting_notes_archived(pid, limit=0)["has_more"] is False
    # draft 不在列
    assert all(n["id"] != d["id"] for n in page1["notes"])


def test_list_pending_excludes_archived(db):
    pid = _project(db)
    n = db.create_meeting_note(pid, "t", None, None, [])
    db.update_meeting_note(n["id"], {"status": "structured"})
    db.update_meeting_note(n["id"], {"status": "archived"})
    assert db.list_pending_meeting_notes(pid) == []  # 终态脱离待处理数据源


def test_reset_and_activity_ignore_archived(db):
    """启动清扫只动 structuring、活动心跳只报 structuring——archived 两路均不误伤。"""
    pid = _project(db)
    n = db.create_meeting_note(pid, "t", None, None, [])
    db.update_meeting_note(n["id"], {"status": "archived"})
    assert db.reset_interrupted_meeting_notes() == 0
    assert db.get_meeting_note(n["id"])["status"] == "archived"
    import meeting_notes_service as svc
    assert svc.fetch_active(db) == []


def test_reset_tables_covers_meeting_notes():
    """ResetAll「真·全清」清单必须含 meeting_notes（便签 T2 同款缺陷防回归）。"""
    from routers.system import _RESET_TABLES
    assert "meeting_notes" in _RESET_TABLES


# ── 路由层（2.13 §3 端点）──

import asyncio
import json
import threading

import deps
import meeting_notes_service as svc
import routers.meeting_notes as mn_router
from fastapi import HTTPException
from fastapi.responses import JSONResponse


@pytest.fixture
def patched(db, monkeypatch):
    """路由依赖注入：deps.db → 内存 DB。"""
    monkeypatch.setattr(deps, "db", db)
    return db


def test_route_create_defaults_title_and_404(patched):
    with pytest.raises(HTTPException) as ei:
        asyncio.run(mn_router.create_note("missing", mn_router.NoteCreate()))
    assert ei.value.status_code == 404
    pid = _project(patched)
    note = asyncio.run(mn_router.create_note(pid, mn_router.NoteCreate()))
    assert note["note"]["title"].startswith("拜访-")  # 默认标题
    assert note["note"]["status"] == "draft"


def test_route_list_pending_and_update(patched):
    pid = _project(patched)
    a = asyncio.run(mn_router.create_note(
        pid, mn_router.NoteCreate(title="A", raw_text="x")))
    b = asyncio.run(mn_router.create_note(
        pid, mn_router.NoteCreate(title="B")))
    patched.update_meeting_note(b["note"]["id"], {"status": "structured"})
    listed = asyncio.run(mn_router.list_pending(pid))
    # structured 待确认仍在列（生成成功后确认步恢复的数据源；终态 confirm 后删行）
    assert [n["title"] for n in listed["notes"]] == ["B", "A"]
    nid = a["note"]["id"]
    upd = asyncio.run(mn_router.update_note(
        pid, nid, mn_router.NoteUpdate(title="A2", raw_text="y")))
    assert upd["note"]["title"] == "A2" and upd["note"]["raw_text"] == "y"
    # title NOT NULL 例外：显式 null/空白按「不改」（NOT NULL 列无清除语义）
    n1 = asyncio.run(mn_router.update_note(
        pid, nid, mn_router.NoteUpdate(title=None)))
    assert n1["note"]["title"] == "A2"
    n2 = asyncio.run(mn_router.update_note(
        pid, nid, mn_router.NoteUpdate(title="   ")))
    assert n2["note"]["title"] == "A2"
    # 显式 null 清除 occurred_at（缺席=不改，同 sticky CR #1 语义；title pop 不影响本列）
    c = asyncio.run(mn_router.create_note(
        pid, mn_router.NoteCreate(title="C", occurred_at="2026-10-27")))
    c1 = asyncio.run(mn_router.update_note(
        pid, c["note"]["id"], mn_router.NoteUpdate(occurred_at=None)))
    assert c1["note"]["occurred_at"] is None
    # 跨项目/不存在 → 404
    with pytest.raises(HTTPException) as ei:
        asyncio.run(mn_router.update_note(
            "missing", nid, mn_router.NoteUpdate(title="x")))
    assert ei.value.status_code == 404


def test_route_delete_and_cross_project_404(patched):
    pid = _project(patched)
    note = asyncio.run(mn_router.create_note(
        pid, mn_router.NoteCreate(title="D")))
    nid = note["note"]["id"]
    assert asyncio.run(mn_router.delete_note(pid, nid)) == {"ok": True}
    with pytest.raises(HTTPException) as ei:
        asyncio.run(mn_router.delete_note(pid, nid))
    assert ei.value.status_code == 404


def test_route_delete_conflict_during_confirm(patched):
    """confirm 进行中 DELETE → 409（与 confirm 共用内存占坑，防「保存进行中放弃」竞态）。"""
    pid = _project(patched)
    note = asyncio.run(mn_router.create_note(
        pid, mn_router.NoteCreate(title="X", raw_text="材料")))
    nid = note["note"]["id"]
    patched.update_meeting_note(nid, {"status": "structured"})
    assert svc.try_acquire_confirm(nid)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(mn_router.delete_note(pid, nid))
    assert ei.value.status_code == 409
    svc.release_confirm(nid)
    assert asyncio.run(mn_router.delete_note(pid, nid)) == {"ok": True}  # 释放后放行


def test_service_sync_collect_failure_returns_draft(patched, monkeypatch):
    """executor 全程在 try 内：拼材料抛异常也回 draft + LLM_FAILED（防 structuring 卡死）。"""
    pid = _project(patched)
    note = asyncio.run(mn_router.create_note(
        pid, mn_router.NoteCreate(title="t", raw_text="材料")))
    nid = note["note"]["id"]
    patched.update_meeting_note(nid, {"status": "structuring"})
    monkeypatch.setattr(svc, "_collect_materials",
                        lambda db, n: (_ for _ in ()).throw(RuntimeError("boom")))
    svc.structure_meeting_note_sync(patched, nid, {})
    row = patched.get_meeting_note(nid)
    assert row["status"] == "draft" and row["last_error"] == "LLM_FAILED"


def test_route_structure_guards_and_202(patched, monkeypatch):
    monkeypatch.setattr(mn_router, "ensure_ready", lambda _f: None)
    pid = _project(patched)
    # 材料空 → 422
    empty = asyncio.run(mn_router.create_note(
        pid, mn_router.NoteCreate(title="e", raw_text="   ")))
    with pytest.raises(HTTPException) as ei:
        asyncio.run(mn_router.structure_note(pid, empty["note"]["id"]))
    assert ei.value.status_code == 422
    # structured 可重发（2.14「上一步」改输入链）：202 回 structuring 重走；
    # archived → 409（不可再生成）
    s = asyncio.run(mn_router.create_note(
        pid, mn_router.NoteCreate(title="s", raw_text="材料")))
    nid_s = s["note"]["id"]
    patched.update_meeting_note(nid_s, {"status": "structured"})
    done_s = threading.Event()
    monkeypatch.setattr(svc, "structure_meeting_note_sync",
                        lambda db_, i, st: done_s.set())
    resp_s = asyncio.run(mn_router.structure_note(pid, nid_s))
    assert isinstance(resp_s, JSONResponse) and resp_s.status_code == 202
    assert patched.get_meeting_note(nid_s)["status"] == "structuring"
    assert done_s.wait(timeout=2)
    patched.update_meeting_note(nid_s, {"status": "archived"})
    with pytest.raises(HTTPException) as ei:
        asyncio.run(mn_router.structure_note(pid, nid_s))
    assert ei.value.status_code == 409
    # 受理：202 + status=structuring + service 被 executor 调用
    d = asyncio.run(mn_router.create_note(
        pid, mn_router.NoteCreate(title="d", raw_text="材料")))
    nid = d["note"]["id"]
    done = threading.Event()

    def fake_sync(db, note_id, settings):
        done.set()

    monkeypatch.setattr(svc, "structure_meeting_note_sync", fake_sync)
    resp = asyncio.run(mn_router.structure_note(pid, nid))
    assert isinstance(resp, JSONResponse) and resp.status_code == 202
    assert patched.get_meeting_note(nid)["status"] == "structuring"
    assert done.wait(timeout=2)  # executor 提交已执行


def test_route_structure_requeues_pending_images(patched, monkeypatch):
    """定稿驱动受理（2026-10-28）：附图此刻才入解析队列；
    空文本 + 有附图不再 422（图描述属受理后产物，前置只看「文本或图非空」）。"""
    monkeypatch.setattr(mn_router, "ensure_ready", lambda _f: None)
    pid = _project(patched)

    class FakeWorker:
        def __init__(self):
            self.files: list[str] = []

        async def enqueue_file(self, fid):
            self.files.append(fid)

    fake = FakeWorker()
    monkeypatch.setattr(deps, "worker", fake)
    f = patched.create_file(pid, "img.png", "image/png", 10, "h")
    note = asyncio.run(mn_router.create_note(
        pid, mn_router.NoteCreate(title="t", raw_text="   ",
                                  image_file_ids=[f["id"]])))
    nid = note["note"]["id"]
    done = threading.Event()
    monkeypatch.setattr(svc, "structure_meeting_note_sync",
                        lambda db_, i, s: done.set())
    resp = asyncio.run(mn_router.structure_note(pid, nid))
    assert isinstance(resp, JSONResponse) and resp.status_code == 202
    assert patched.get_meeting_note(nid)["status"] == "structuring"
    assert done.wait(timeout=2)
    assert fake.files == [f["id"]]  # 附图即时入队（不等扫描器）


def test_route_structure_error_file_self_heals(patched, monkeypatch):
    """error 附图受理时自愈：清残留 → 重置 pending → 重新入队（retry_file 同款姿势）。
    cleanup/VectorStore 为函数内局部 import，patch 源模块属性隔离重依赖。"""
    monkeypatch.setattr(mn_router, "ensure_ready", lambda _f: None)
    pid = _project(patched)

    class FakeWorker:
        def __init__(self):
            self.files: list[str] = []

        async def enqueue_file(self, fid):
            self.files.append(fid)

    fake = FakeWorker()
    monkeypatch.setattr(deps, "worker", fake)
    monkeypatch.setattr("knowledge.vector_store.VectorStore", lambda p: object())
    cleaned: list[str] = []

    async def fake_cleanup(db_, vs, fts, fid, proj, data_dir, remove_file_record):
        cleaned.append(fid)

    monkeypatch.setattr("knowledge.file_cleanup.cleanup_file_data", fake_cleanup)
    f = patched.create_file(pid, "img.png", "image/png", 10, "h")
    patched.conn.execute(
        "UPDATE files SET status = 'error', error_message = 'boom' WHERE id = ?",
        (f["id"],))
    patched.conn.commit()
    note = asyncio.run(mn_router.create_note(
        pid, mn_router.NoteCreate(title="t", raw_text="材料",
                                  image_file_ids=[f["id"]])))
    nid = note["note"]["id"]
    monkeypatch.setattr(svc, "structure_meeting_note_sync",
                        lambda db_, i, s: None)
    resp = asyncio.run(mn_router.structure_note(pid, nid))
    assert resp.status_code == 202
    assert cleaned == [f["id"]]
    row = patched.get_file(f["id"])
    assert row["status"] == "pending" and row["error_message"] is None
    assert fake.files == [f["id"]]


def test_route_confirm_guards_and_idempotent_resume(patched, monkeypatch):
    monkeypatch.setattr(mn_router, "ensure_ready", lambda _f: None)
    pid = _project(patched)
    note = asyncio.run(mn_router.create_note(
        pid, mn_router.NoteCreate(title="纪要X", raw_text="材料")))
    nid = note["note"]["id"]
    body = mn_router.NoteConfirm(content_md="# 纪要", action_items=[
        mn_router.ConfirmItem(title="任务一"),
        mn_router.ConfirmItem(title="任务二"),
    ])
    # 非 structured → 409
    with pytest.raises(HTTPException) as ei:
        asyncio.run(mn_router.confirm_note(pid, nid, body))
    assert ei.value.status_code == 409
    patched.update_meeting_note(nid, {"status": "structured", "draft_md": "{}"})
    # 并发占坑：第二请求 409（多步复合无法事务化，内存坑串行化同 note）
    assert svc.try_acquire_confirm(nid)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(mn_router.confirm_note(pid, nid, body))
    assert ei.value.status_code == 409
    svc.release_confirm(nid)

    calls = {"upload": 0, "tasks": []}

    async def fake_upload(project_id, upload, db_, data_dir, worker):
        calls["upload"] += 1
        return {"success": True, "file_id": f"file_{calls['upload']}"}

    monkeypatch.setattr(mn_router, "_upload_core", fake_upload)
    orig_create = patched.create_phase_task

    fails = {"n": 0}

    def fake_create(**kw):
        if kw["title"] == "任务二" and fails["n"] == 0:
            fails["n"] += 1
            raise RuntimeError("db busy")  # 仅首次抛（瞬时故障），重试应放行
        calls["tasks"].append(kw["title"])
        return orig_create(**kw)

    monkeypatch.setattr(patched, "create_phase_task", fake_create)

    # 首次：md 落盘 + 首事务成功 + 第二事务失败 → failed_tasks 列出且行留存 refs
    r1 = asyncio.run(mn_router.confirm_note(pid, nid, body))
    assert r1["failed_tasks"] == ["任务二"]
    assert calls["upload"] == 1 and calls["tasks"] == ["任务一"]
    row = patched.get_meeting_note(nid)
    assert row is not None
    refs = json.loads(row["saved_refs"])
    assert refs["file_id"] == "file_1" and refs["task_ids"] == ["任务一"]  # 幂等键 = 条目 title（偏差 21）
    # 重试：md 不重写、首事务不重建、只补第二项 → 完成置 archived 终态（2.14 原删行）
    r2 = asyncio.run(mn_router.confirm_note(pid, nid, body))
    assert r2["failed_tasks"] == []
    assert calls["upload"] == 1
    assert calls["tasks"] == ["任务一", "任务二"]
    final_row = patched.get_meeting_note(nid)
    assert final_row["status"] == "archived"  # 终态保留行（归档列表数据源）


def test_service_llm_model_routing_anchor(patched, monkeypatch):
    """档位路由锚点（leads/schedule 同款缺陷防回归，2026-10-28 实证）：
    路由键未登记 _TASK_MODEL_KEYS 时 get_task_settings 原样返回，
    settings["model"] 为空 → 真实请求被网关拒（模型不存在），
    Fake 不发真实请求故必须显式断言。未配置 meeting_note_model
    → 按 standard 档解析到全局默认槽。"""
    pid = _project(patched)
    note = asyncio.run(mn_router.create_note(
        pid, mn_router.NoteCreate(title="t", raw_text="与王总确认扩产计划")))
    nid = note["note"]["id"]
    patched.update_meeting_note(nid, {"status": "structuring"})

    received: dict = {}

    class _FakeLLM:
        def __init__(self, settings):
            received.update(dict(settings or {}))

        async def chat_full(self, messages, tools=None):
            return {"content": json.dumps(
                {"content_md": "# 纪要", "action_items": []},
                ensure_ascii=False),
                "finish_reason": "stop", "usage": {}}

    # service 函数体内 `from llm import LLMClient`（包属性），patch 目标为包
    monkeypatch.setattr("llm.LLMClient", _FakeLLM)
    svc.structure_meeting_note_sync(
        patched, nid, {"standard_model": "glm-standard-x", "high_model": ""})
    row = patched.get_meeting_note(nid)
    assert row["status"] == "structured"
    # 锚点：model 必须已由 resolve_model 解析非空（空串 = 漏路由）
    assert (received.get("model") or "") == "glm-standard-x"


def test_route_confirm_all_unselected_completes(patched, monkeypatch):
    """全部不建事务也保存（spec §4 确认步合法态）：md 落盘即删行。"""
    monkeypatch.setattr(mn_router, "ensure_ready", lambda _f: None)
    pid = _project(patched)
    note = asyncio.run(mn_router.create_note(
        pid, mn_router.NoteCreate(title="t", raw_text="材料")))
    nid = note["note"]["id"]
    patched.update_meeting_note(nid, {"status": "structured", "draft_md": "{}"})

    async def fake_upload(project_id, upload, db_, data_dir, worker):
        return {"success": True, "file_id": "f1"}

    monkeypatch.setattr(mn_router, "_upload_core", fake_upload)
    body = mn_router.NoteConfirm(
        content_md="# 纪要",
        action_items=[mn_router.ConfirmItem(title="任务", selected=False)])
    r = asyncio.run(mn_router.confirm_note(pid, nid, body))
    assert r["created_tasks"] == 0 and r["failed_tasks"] == []
    assert patched.get_meeting_note(nid)["status"] == "archived"  # 全不选也归档（2.14 终态）


def test_route_confirm_without_materials_skips_upload(patched, monkeypatch):
    """2.14 save_to_materials=False：正文仅存纪要列表——upload 零调用、
    refs.file_id 空、完成仍置 archived（事后可 toggle 补入素材）；事务照建。"""
    monkeypatch.setattr(mn_router, "ensure_ready", lambda _f: None)
    pid = _project(patched)
    note = asyncio.run(mn_router.create_note(
        pid, mn_router.NoteCreate(title="t", raw_text="材料")))
    nid = note["note"]["id"]
    patched.update_meeting_note(nid, {"status": "structured", "draft_md": "{}"})

    async def fail_upload(*a, **kw):
        raise AssertionError("upload must not be called")

    monkeypatch.setattr(mn_router, "_upload_core", fail_upload)
    body = mn_router.NoteConfirm(
        content_md="# 纪要", save_to_materials=False,
        action_items=[mn_router.ConfirmItem(title="任务", selected=True)])
    r = asyncio.run(mn_router.confirm_note(pid, nid, body))
    assert r["archived"] is True
    row = patched.get_meeting_note(nid)
    assert row["status"] == "archived"
    refs = json.loads(row["saved_refs"])
    assert refs["file_id"] is None and refs["task_ids"] == ["任务"]  # 幂等键 = 条目 title（偏差 21）


def test_route_patch_archived_409(patched):
    """2.14：归档行不可编辑（历史记录防与素材 md 双源漂移）。"""
    pid = _project(patched)
    note = asyncio.run(mn_router.create_note(
        pid, mn_router.NoteCreate(title="t", raw_text="x")))
    nid = note["note"]["id"]
    patched.update_meeting_note(nid, {"status": "archived"})
    with pytest.raises(HTTPException) as ei:
        asyncio.run(mn_router.update_note(
            pid, nid, mn_router.NoteUpdate(title="y")))
    assert ei.value.status_code == 409


# ── 2.14 纪要中心：materialize / dematerialize ──


def _archived_row(db, pid, title, occurred_at=None,
                  draft_md=None, refs=None) -> str:
    """构造 archived 行：经 structured（draft_md/refs 随步落）再到终态。
    confirm 幂等重试语义要求行真实留存，构造走与生产一致的状态路径。"""
    n = db.create_meeting_note(pid, title, occurred_at, None, [])
    fields: dict = {"status": "structured"}
    if draft_md is not None:
        fields["draft_md"] = draft_md
    if refs is not None:
        fields["saved_refs"] = refs
    db.update_meeting_note(n["id"], fields)
    db.update_meeting_note(n["id"], {"status": "archived"})
    return n["id"]


def test_route_materialize_upload_and_idempotent(patched, monkeypatch):
    """事后补入素材（2.14）：复用 confirm 步骤 1 上传链（解析入队进 RAG），
    文件名规则同 confirm；refs 落库；已入库幂等不重传。"""
    monkeypatch.setattr(mn_router, "ensure_ready", lambda _f: None)
    pid = _project(patched)
    nid = _archived_row(patched, pid, "纪要M", occurred_at="2026-10-20",
                        draft_md=json.dumps({"content_md": "# 正文",
                                             "action_items": []}))
    calls = {"upload": 0, "names": []}

    async def fake_upload(project_id, upload, db_, data_dir, worker):
        calls["upload"] += 1
        calls["names"].append(upload.filename)
        return {"success": True, "file_id": f"file_{calls['upload']}"}

    monkeypatch.setattr(mn_router, "_upload_core", fake_upload)
    r1 = asyncio.run(mn_router.materialize_note(pid, nid))
    assert r1 == {"ok": True, "file_id": "file_1"}
    assert calls["names"] == ["纪要-20261020-纪要M.md"]
    refs = json.loads(patched.get_meeting_note(nid)["saved_refs"])
    assert refs["file_id"] == "file_1"
    # 二调：file_id 已存在 → 幂等返回，不重传
    r2 = asyncio.run(mn_router.materialize_note(pid, nid))
    assert r2 == {"ok": True, "file_id": "file_1"}
    assert calls["upload"] == 1


def test_route_materialize_guards(patched, monkeypatch):
    """非 archived 409；draft_md 缺/content_md 空 422（无正文无从入库）。"""
    monkeypatch.setattr(mn_router, "ensure_ready", lambda _f: None)
    pid = _project(patched)
    d = patched.create_meeting_note(pid, "d", None, None, [])
    with pytest.raises(HTTPException) as ei:
        asyncio.run(mn_router.materialize_note(pid, d["id"]))
    assert ei.value.status_code == 409
    n1 = _archived_row(patched, pid, "n1")  # 无 draft_md
    with pytest.raises(HTTPException) as ei:
        asyncio.run(mn_router.materialize_note(pid, n1))
    assert ei.value.status_code == 422
    n2 = _archived_row(patched, pid, "n2",
                       draft_md=json.dumps({"content_md": "   "}))
    with pytest.raises(HTTPException) as ei:
        asyncio.run(mn_router.materialize_note(pid, n2))
    assert ei.value.status_code == 422


def test_route_dematerialize_cleanup_and_idempotent(patched, monkeypatch):
    """移出素材：完整级联删文件（remove_file_record=True，含 DB file 行+磁盘目录）
    + refs.file_id 置 null，纪要行保留可重新加入；无 file_id 幂等不触发 cleanup。
    cleanup/VectorStore 为局部 import，patch 源模块属性（同 structure 自愈用例）。"""
    pid = _project(patched)
    nid = _archived_row(patched, pid, "m", occurred_at="2026-10-20",
                        refs=json.dumps({"file_id": "f9", "task_ids": [0]}))
    cleaned: list[tuple] = []

    async def fake_cleanup(db_, vs, fts, fid, proj, data_dir,
                           remove_file_record):
        cleaned.append((fid, remove_file_record))

    monkeypatch.setattr("knowledge.file_cleanup.cleanup_file_data", fake_cleanup)
    monkeypatch.setattr("knowledge.vector_store.VectorStore", lambda p: object())
    assert asyncio.run(mn_router.dematerialize_note(pid, nid)) == {"ok": True}
    assert cleaned == [("f9", True)]
    refs = json.loads(patched.get_meeting_note(nid)["saved_refs"])
    assert refs["file_id"] is None  # 行保留，仅断素材关联
    # 幂等：file_id 已 null → 不再触发 cleanup
    assert asyncio.run(mn_router.dematerialize_note(pid, nid)) == {"ok": True}
    assert len(cleaned) == 1


def test_route_material_guards(patched, monkeypatch):
    """archived 守卫：draft 行两端点均 409；confirm 占坑中均 409
    （refs 并发写串行化，同 DELETE/confirm 共坑防线）。"""
    monkeypatch.setattr(mn_router, "ensure_ready", lambda _f: None)
    pid = _project(patched)
    nid = _archived_row(patched, pid, "g")
    assert svc.try_acquire_confirm(nid)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(mn_router.materialize_note(pid, nid))
    assert ei.value.status_code == 409
    with pytest.raises(HTTPException) as ei:
        asyncio.run(mn_router.dematerialize_note(pid, nid))
    assert ei.value.status_code == 409
    svc.release_confirm(nid)
    d = patched.create_meeting_note(pid, "d2", None, None, [])
    with pytest.raises(HTTPException) as ei:
        asyncio.run(mn_router.materialize_note(pid, d["id"]))
    assert ei.value.status_code == 409
    with pytest.raises(HTTPException) as ei:
        asyncio.run(mn_router.dematerialize_note(pid, d["id"]))
    assert ei.value.status_code == 409


def test_route_confirm_content_sync(patched, monkeypatch):
    """confirm 正文联动（2.14 修订）：行内 draft_md 是「已确认正文」事实源——
    载荷与行内不同 → 删旧传新 + 回写 draft_md（含 action_items 全量 titles）；
    一致 → 幂等跳过；重试改 save_to_materials=false → 按不入库语义删旧不传新。"""
    monkeypatch.setattr(mn_router, "ensure_ready", lambda _f: None)
    pid = _project(patched)
    note = asyncio.run(mn_router.create_note(
        pid, mn_router.NoteCreate(title="纪要Y", raw_text="材料")))
    nid = note["note"]["id"]
    patched.update_meeting_note(nid, {
        "status": "structured",
        "draft_md": json.dumps({"content_md": "# 原稿",
                                "action_items": [{"title": "旧项"}]},
                               ensure_ascii=False)})
    uploads: list[str] = []
    cleaned: list[str] = []

    async def fake_upload(project_id_, upload, db_, data_dir, worker):
        uploads.append(upload.filename)
        return {"success": True, "file_id": f"file_u{len(uploads)}"}

    async def fake_cleanup(db_, vs, fts, fid, proj, data_dir, remove_file_record):
        cleaned.append(fid)

    monkeypatch.setattr(mn_router, "_upload_core", fake_upload)
    monkeypatch.setattr("knowledge.file_cleanup.cleanup_file_data", fake_cleanup)
    monkeypatch.setattr("knowledge.vector_store.VectorStore", lambda p: object())

    # 首次：载荷（编辑稿）≠ 行内（LLM 原稿）→ 传新 + 回写（无旧文件不 cleanup）
    body_v2 = mn_router.NoteConfirm(content_md="# 编辑稿", action_items=[
        mn_router.ConfirmItem(title="任务一")])
    r1 = asyncio.run(mn_router.confirm_note(pid, nid, body_v2))
    assert r1["failed_tasks"] == [] and len(uploads) == 1
    row = patched.get_meeting_note(nid)
    assert json.loads(row["saved_refs"])["file_id"] == "file_u1"
    draft = json.loads(row["draft_md"])
    assert draft["content_md"] == "# 编辑稿"
    assert [it["title"] for it in draft["action_items"]] == ["任务一"]
    assert row["status"] == "archived"

    # 重试同载荷（部分失败后重试场景）：行内已回写 = 载荷 → 幂等不重传
    patched.update_meeting_note(nid, {"status": "structured"})
    r2 = asyncio.run(mn_router.confirm_note(pid, nid, body_v2))
    assert r2["archived"] is True and len(uploads) == 1 and cleaned == []

    # 重试改载荷：内容变化 → 删旧（file_u1）传新（file_u2）+ 回写
    patched.update_meeting_note(nid, {"status": "structured"})
    body_v3 = mn_router.NoteConfirm(content_md="# 编辑稿二", action_items=[
        mn_router.ConfirmItem(title="任务一")])
    asyncio.run(mn_router.confirm_note(pid, nid, body_v3))
    assert cleaned == ["file_u1"] and len(uploads) == 2
    row = patched.get_meeting_note(nid)
    assert json.loads(row["saved_refs"])["file_id"] == "file_u2"
    assert json.loads(row["draft_md"])["content_md"] == "# 编辑稿二"

    # 重试改不入库：按不入库语义删旧不传新，refs.file_id 置 null + 回写
    patched.update_meeting_note(nid, {"status": "structured"})
    body_off = mn_router.NoteConfirm(content_md="# 编辑稿三", save_to_materials=False,
                                     action_items=[])
    r4 = asyncio.run(mn_router.confirm_note(pid, nid, body_off))
    assert r4["file_id"] is None and len(uploads) == 2 and cleaned == ["file_u1", "file_u2"]
    row = patched.get_meeting_note(nid)
    assert json.loads(row["saved_refs"])["file_id"] is None
    assert json.loads(row["draft_md"])["content_md"] == "# 编辑稿三"


def test_route_update_content_syncs_material(patched, monkeypatch):
    """归档正文编辑（2.14 补）：内容变化已入素材行删旧传新落新 file_id +
    draft_md 回写（action_items 保留不动）；未入素材仅回写；内容未变幂等；
    非 archived 409。"""
    monkeypatch.setattr(mn_router, "ensure_ready", lambda _f: None)
    pid = _project(patched)
    uploads: list[str] = []
    cleaned: list[str] = []

    async def fake_upload(project_id_, upload, db_, data_dir, worker):
        uploads.append(upload.filename)
        return {"success": True, "file_id": f"file_e{len(uploads)}"}

    async def fake_cleanup(db_, vs, fts, fid, proj, data_dir, remove_file_record):
        cleaned.append(fid)

    monkeypatch.setattr(mn_router, "_upload_core", fake_upload)
    monkeypatch.setattr("knowledge.file_cleanup.cleanup_file_data", fake_cleanup)
    monkeypatch.setattr("knowledge.vector_store.VectorStore", lambda p: object())

    # 已入素材行：删旧传新 + 回写（action_items 保留）
    nid = _archived_row(patched, pid, "纪要E", occurred_at="2026-10-20",
                        draft_md=json.dumps({"content_md": "# 旧正文",
                                             "action_items": [{"title": "跟进"}]},
                                            ensure_ascii=False),
                        refs=json.dumps({"file_id": "f_old", "task_ids": [0]}))
    assert svc.try_acquire_confirm(nid)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(mn_router.update_note_content(
            pid, nid, mn_router.NoteContentUpdate(content_md="# 新正文")))
    assert ei.value.status_code == 409  # 占坑互斥
    svc.release_confirm(nid)
    r = asyncio.run(mn_router.update_note_content(
        pid, nid, mn_router.NoteContentUpdate(content_md="# 新正文")))
    assert r == {"ok": True, "file_id": "file_e1"}
    assert cleaned == ["f_old"] and uploads == ["纪要-20261020-纪要E.md"]
    row = patched.get_meeting_note(nid)
    draft = json.loads(row["draft_md"])
    assert draft["content_md"] == "# 新正文"
    assert [it["title"] for it in draft["action_items"]] == ["跟进"]  # 保留
    assert json.loads(row["saved_refs"])["file_id"] == "file_e1"

    # 未入素材行：仅回写 draft_md，不上传不清理、refs.file_id 保持空
    # （review 修复回归：原实现无条件上传静默入库，违背目标态语义）
    nid2 = _archived_row(patched, pid, "n2",
                         draft_md=json.dumps({"content_md": "# a", "action_items": []}))
    r2 = asyncio.run(mn_router.update_note_content(
        pid, nid2, mn_router.NoteContentUpdate(content_md="# b")))
    assert r2 == {"ok": True, "file_id": None}
    assert uploads == ["纪要-20261020-纪要E.md"] and len(cleaned) == 1
    assert json.loads(patched.get_meeting_note(nid2)["saved_refs"]).get("file_id") is None

    # 内容未变幂等（返回当前 file_id）；非 archived 409；空正文 422
    r3 = asyncio.run(mn_router.update_note_content(
        pid, nid2, mn_router.NoteContentUpdate(content_md=" # b ")))
    assert r3 == {"ok": True, "file_id": None} and len(uploads) == 1
    d = patched.create_meeting_note(pid, "d3", None, None, [])
    with pytest.raises(HTTPException) as ei:
        asyncio.run(mn_router.update_note_content(
            pid, d["id"], mn_router.NoteContentUpdate(content_md="# x")))
    assert ei.value.status_code == 409
    with pytest.raises(HTTPException) as ei:
        asyncio.run(mn_router.update_note_content(
            pid, nid2, mn_router.NoteContentUpdate(content_md="   ")))
    assert ei.value.status_code == 422


# ── 三态模型（2026-10-28 实测裁决）：同步目标态 + 暂存 + 删除级联 ──


def test_route_confirm_sync_target_semantics(patched, monkeypatch):
    """勾选框 = 同步目标态：save=true 目标「在素材」（内容变删旧传新；file 缺失
    补传——上传失败重试不再死循环）；save=false 目标「不在素材」（已有素材一律
    删，内容未变也删——旧实现 content_changed 门控整步跳过漏删）。"""
    monkeypatch.setattr(mn_router, "ensure_ready", lambda _f: None)
    pid = _project(patched)
    uploads: list[str] = []
    cleaned: list[str] = []

    async def fake_upload(project_id_, upload, db_, data_dir, worker):
        uploads.append(upload.filename)
        return {"success": True, "file_id": f"file_t{len(uploads)}"}

    async def fake_cleanup(db_, vs, fts, fid, proj, data_dir, remove_file_record):
        cleaned.append(fid)

    monkeypatch.setattr(mn_router, "_upload_core", fake_upload)
    monkeypatch.setattr("knowledge.file_cleanup.cleanup_file_data", fake_cleanup)
    monkeypatch.setattr("knowledge.vector_store.VectorStore", lambda p: object())

    def structured_row(title, content, file_id=None):
        note = asyncio.run(mn_router.create_note(
            pid, mn_router.NoteCreate(title=title, raw_text="材料")))
        nid = note["note"]["id"]
        patched.update_meeting_note(nid, {
            "status": "structured",
            "draft_md": json.dumps({"content_md": content, "action_items": []},
                                   ensure_ascii=False),
            "saved_refs": json.dumps({"file_id": file_id, "task_ids": []})})
        return nid

    # a) save=false + 内容未变：目标「不在素材」→ 删旧 + refs.file_id 置 null
    a = structured_row("目标态A", "# 正文", file_id="f_a")
    r_a = asyncio.run(mn_router.confirm_note(
        pid, a, mn_router.NoteConfirm(content_md="# 正文",
                                      save_to_materials=False, action_items=[])))
    assert r_a["file_id"] is None and cleaned == ["f_a"] and uploads == []
    assert json.loads(patched.get_meeting_note(a)["saved_refs"])["file_id"] is None

    # b) save=true + 内容未变：幂等，不删不传
    uploads.clear(); cleaned.clear()
    b = structured_row("目标态B", "# 正文", file_id="f_b")
    asyncio.run(mn_router.confirm_note(
        pid, b, mn_router.NoteConfirm(content_md="# 正文", action_items=[])))
    assert uploads == [] and cleaned == []
    assert json.loads(patched.get_meeting_note(b)["saved_refs"])["file_id"] == "f_b"

    # c) save=true + file 缺失 + 内容未变（上传失败后重试）→ 补传
    # （旧实现漏传致 refs 永不完整、「重试保存」死循环）
    c = structured_row("目标态C", "# 正文", file_id=None)
    asyncio.run(mn_router.confirm_note(
        pid, c, mn_router.NoteConfirm(content_md="# 正文", action_items=[])))
    from datetime import date as _date
    assert uploads == [f"纪要-{_date.today():%Y%m%d}-目标态C.md"] and cleaned == []
    assert json.loads(patched.get_meeting_note(c)["saved_refs"])["file_id"] == "file_t1"


def test_route_update_content_stash_structured(patched, monkeypatch):
    """updateContent 放宽 structured（三态「暂存」）：人工修改正文存回行内，
    status 保持 structured（不入素材不建事务不归档）；重试残留 file_id 行照常
    联动删旧传新；draft 行仍 409；暂存后的正文成为 confirm 幂等基准。"""
    monkeypatch.setattr(mn_router, "ensure_ready", lambda _f: None)
    pid = _project(patched)
    uploads: list[str] = []
    cleaned: list[str] = []

    async def fake_upload(project_id_, upload, db_, data_dir, worker):
        uploads.append(upload.filename)
        return {"success": True, "file_id": f"file_s{len(uploads)}"}

    async def fake_cleanup(db_, vs, fts, fid, proj, data_dir, remove_file_record):
        cleaned.append(fid)

    monkeypatch.setattr(mn_router, "_upload_core", fake_upload)
    monkeypatch.setattr("knowledge.file_cleanup.cleanup_file_data", fake_cleanup)
    monkeypatch.setattr("knowledge.vector_store.VectorStore", lambda p: object())

    # structured 无 file_id：暂存回写，status 保持
    n = asyncio.run(mn_router.create_note(
        pid, mn_router.NoteCreate(title="暂存行", raw_text="材料")))
    nid = n["note"]["id"]
    patched.update_meeting_note(nid, {
        "status": "structured",
        "draft_md": json.dumps({"content_md": "# LLM 原稿", "action_items": []},
                               ensure_ascii=False)})
    r = asyncio.run(mn_router.update_note_content(
        pid, nid, mn_router.NoteContentUpdate(content_md="# 人工修改")))
    assert r == {"ok": True, "file_id": None}  # 无 file_id：仅回写不上传
    row = patched.get_meeting_note(nid)
    assert row["status"] == "structured"  # 不归档
    assert json.loads(row["draft_md"])["content_md"] == "# 人工修改"
    # 暂存后的正文成为 confirm 基准：同载荷幂等不重传
    r2 = asyncio.run(mn_router.confirm_note(
        pid, nid, mn_router.NoteConfirm(content_md="# 人工修改", action_items=[])))
    assert r2["archived"] is True and len(uploads) == 1 and cleaned == []

    # structured + 重试残留 file_id：照常联动删旧传新
    n2 = asyncio.run(mn_router.create_note(
        pid, mn_router.NoteCreate(title="残留行", raw_text="材料")))
    nid2 = n2["note"]["id"]
    patched.update_meeting_note(nid2, {
        "status": "structured", "saved_refs": json.dumps({"file_id": "f_stale"}),
        "draft_md": json.dumps({"content_md": "# 旧", "action_items": []},
                               ensure_ascii=False)})
    asyncio.run(mn_router.update_note_content(
        pid, nid2, mn_router.NoteContentUpdate(content_md="# 新")))
    assert cleaned == ["f_stale"] and len(uploads) == 2
    assert patched.get_meeting_note(nid2)["status"] == "structured"

    # 内容未变幂等（返回当前 file_id）；draft 行仍 409
    r3 = asyncio.run(mn_router.update_note_content(
        pid, nid2, mn_router.NoteContentUpdate(content_md=" # 新 ")))
    assert r3 == {"ok": True, "file_id": "file_s2"} and len(uploads) == 2
    d = patched.create_meeting_note(pid, "d4", None, None, [])
    with pytest.raises(HTTPException) as ei:
        asyncio.run(mn_router.update_note_content(
            pid, d["id"], mn_router.NoteContentUpdate(content_md="# x")))
    assert ei.value.status_code == 409


def test_route_delete_cascades_assets(patched, monkeypatch):
    """删除纪要级联（实测裁决推翻 2.14「仅删行」）：素材 md + 未被其他行引用的
    现场图片一并 cleanup；共享图片保护；幽灵 file_id 跳过；已建事务保留。"""
    monkeypatch.setattr(mn_router, "ensure_ready", lambda _f: None)
    pid = _project(patched)
    cleaned: list[str] = []

    async def fake_cleanup(db_, vs, fts, fid, proj, data_dir, remove_file_record):
        cleaned.append(fid)

    monkeypatch.setattr("knowledge.file_cleanup.cleanup_file_data", fake_cleanup)
    monkeypatch.setattr("knowledge.vector_store.VectorStore", lambda p: object())

    img_solo = patched.create_file(pid, "solo.png", "image/png", 10, "h")["id"]
    img_shared = patched.create_file(pid, "shared.png", "image/png", 10, "h")["id"]
    # 共享方：另一行（archived）也引用 shared 图
    sharer = asyncio.run(mn_router.create_note(
        pid, mn_router.NoteCreate(title="sharer", raw_text="材料",
                                  image_file_ids=[img_shared])))
    patched.update_meeting_note(sharer["note"]["id"], {"status": "archived"})
    target = asyncio.run(mn_router.create_note(
        pid, mn_router.NoteCreate(title="目标行", raw_text="材料",
                                  image_file_ids=[img_solo, img_shared, "ghost"])))
    nid = target["note"]["id"]
    patched.update_meeting_note(nid, {
        "status": "structured",
        "saved_refs": json.dumps({"file_id": "f_md", "task_ids": [0, 1]}),
        "draft_md": json.dumps({"content_md": "# 正文", "action_items": []},
                               ensure_ascii=False)})
    task = patched.create_phase_task(project_id=pid, phase="before",
                                     task_type="material", title="待办一")
    assert asyncio.run(mn_router.delete_note(pid, nid)) == {"ok": True}
    # 级联：素材 md + 独立图片删；共享图片保留；幽灵 file_id 跳过
    assert set(cleaned) == {"f_md", img_solo}
    assert patched.get_meeting_note(nid) is None
    assert patched.get_file(img_shared) is not None
    # 已建事务保留（在事务面板管理）
    assert patched.get_phase_task(task["id"])["title"] == "待办一"
    # 确认中占坑：409
    other = asyncio.run(mn_router.create_note(
        pid, mn_router.NoteCreate(title="占坑行", raw_text="材料")))
    svc.try_acquire_confirm(other["note"]["id"])
    with pytest.raises(HTTPException) as ei:
        asyncio.run(mn_router.delete_note(pid, other["note"]["id"]))
    assert ei.value.status_code == 409
    svc.release_confirm(other["note"]["id"])


def test_route_confirm_task_retry_with_edited_list(patched, monkeypatch):
    """事务幂等键 = 条目 title（review Major 修复回归）：部分失败后改清单重试
    不再按下标误跳——items=[甲,乙] 甲成乙败，删甲留乙重试 → 乙正常建成再归档
    （旧逻辑乙落 idx0 被误跳，从未建成却被判全归档）；同载荷重复 confirm 幂等
    不重建；返回 archived 反映实际状态（部分失败 False）。"""
    monkeypatch.setattr(mn_router, "ensure_ready", lambda _f: None)
    pid = _project(patched)
    note = asyncio.run(mn_router.create_note(
        pid, mn_router.NoteCreate(title="重试行", raw_text="材料")))
    nid = note["note"]["id"]
    patched.update_meeting_note(nid, {
        "status": "structured",
        "draft_md": json.dumps({"content_md": "# 正文", "action_items": []},
                               ensure_ascii=False),
        "saved_refs": json.dumps({"file_id": None, "task_ids": []})})

    real_create = patched.create_phase_task
    calls = {"n": 0}

    def flaky_create(**kw):
        calls["n"] += 1
        if calls["n"] == 2:  # 第二条（乙）建失败
            raise RuntimeError("db down")
        return real_create(**kw)

    monkeypatch.setattr(patched, "create_phase_task", flaky_create)
    items = [mn_router.ConfirmItem(title="甲"), mn_router.ConfirmItem(title="乙")]
    r1 = asyncio.run(mn_router.confirm_note(
        pid, nid, mn_router.NoteConfirm(content_md="# 正文",
                                        save_to_materials=False,
                                        action_items=items)))
    assert r1["created_tasks"] == 1 and r1["failed_tasks"] == ["乙"]
    assert r1["archived"] is False  # 实际状态：乙未建成，行留存待重试
    assert patched.get_meeting_note(nid)["status"] == "structured"

    # 改清单重试（删甲留乙）：乙不再被旧下标误跳，正常补建后归档
    # （同载荷幂等跳过由存量重试测试覆盖：已建成项 title 已记不重建；
    #  归档行再 confirm 被守卫 409——「重复 confirm」场景仅存在于成功前重试内）
    monkeypatch.setattr(patched, "create_phase_task", real_create)
    r2 = asyncio.run(mn_router.confirm_note(
        pid, nid, mn_router.NoteConfirm(content_md="# 正文",
                                        save_to_materials=False,
                                        action_items=[mn_router.ConfirmItem(title="乙")])))
    assert r2["created_tasks"] == 1 and r2["failed_tasks"] == []
    assert r2["archived"] is True
    assert patched.get_meeting_note(nid)["status"] == "archived"
    assert calls["n"] == 2  # flaky 段仅 r1 两次（甲、乙败）；r2 补建经 real_create 不入计数
