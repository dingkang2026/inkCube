# -*- coding: utf-8 -*-
"""sticky_notes CRUD 与归档联动测试（2.12 §2）。

要点回归：
- inbox/archived 二分流按 archived_at 判定，updated_at 倒序；
- color 白名单五色枚举，非法值 ValueError；
- 归档幂等：已归档再归档返回 None；
- 项目删除联动 detach：project_id/archived_at 置 NULL 回收件箱。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.database import Database, STICKY_UNSET


@pytest.fixture
def db():
    d = Database(db_path=":memory:")
    d.initialize()
    yield d
    d.conn.close()


def _project(db) -> str:
    return db.create_project(name="P", seed_playbook=False)["id"]


def test_create_and_list_inbox(db):
    db.create_sticky_note("第一笔", "yellow")
    db.create_sticky_note("第二笔", None)
    rows = db.list_sticky_notes("inbox", limit=10, offset=0)
    assert len(rows) == 2
    assert rows[0]["content"] == "第二笔"  # updated_at 倒序
    assert rows[0]["archived_at"] is None
    assert rows[0]["project_id"] is None


def test_color_whitelist(db):
    for ok in ("yellow", "red", "blue", "green", "purple", None):
        db.create_sticky_note("x", ok)  # 合法不抛
    with pytest.raises(ValueError):
        db.create_sticky_note("x", "pink")  # 非法色值拒绝


def test_get_update_delete(db):
    n = db.create_sticky_note("原文", None)
    got = db.get_sticky_note(n["id"])
    assert got["content"] == "原文"
    upd = db.update_sticky_note(n["id"], "改文", "blue")
    assert upd["content"] == "改文" and upd["color"] == "blue"
    assert db.update_sticky_note("missing", "x", None) is None
    assert db.delete_sticky_note(n["id"]) is True
    assert db.delete_sticky_note(n["id"]) is False
    assert db.get_sticky_note(n["id"]) is None


def test_update_explicit_null_clears_color(db):
    """null = 显式清除（编辑态选「无色」）、STICKY_UNSET = 不改（Code Review #1 回归）：
    旧实现 None 语义是「不改」，色板清除永不生效。"""
    n = db.create_sticky_note("x", "yellow")
    upd = db.update_sticky_note(n["id"], None, None)  # 显式 None → 清除
    assert upd["color"] is None
    db.update_sticky_note(n["id"], "改文", STICKY_UNSET)  # 哨兵 → 不改色
    row = db.get_sticky_note(n["id"])
    assert row["content"] == "改文" and row["color"] is None
    noop = db.update_sticky_note(n["id"], None, STICKY_UNSET)  # 两字段均不改 → 空转
    assert noop["content"] == "改文"


def test_archive_and_views(db):
    n = db.create_sticky_note("待归档", None)
    pid = _project(db)
    ok = db.archive_sticky_note(n["id"], pid)
    assert ok["project_id"] == pid and ok["archived_at"]
    assert db.list_sticky_notes("inbox", limit=0, offset=0) == []
    archived = db.list_sticky_notes("archived", limit=0, offset=0)
    assert len(archived) == 1
    assert db.archive_sticky_note(n["id"], pid) is None  # 幂等：已归档返回 None


def test_detach_on_project_delete(db):
    n = db.create_sticky_note("x", None)
    pid = _project(db)
    db.archive_sticky_note(n["id"], pid)
    assert db.detach_sticky_notes_for_project(pid) == 1
    rows = db.list_sticky_notes("inbox", limit=0, offset=0)
    assert len(rows) == 1
    assert rows[0]["project_id"] is None and rows[0]["archived_at"] is None


# ── 路由层（2.12 §3 端点）──

import asyncio

import deps
import routers.sticky_notes as sn_router
from fastapi import HTTPException


@pytest.fixture
def patched(db, monkeypatch):
    """路由依赖注入：deps.db → 内存 DB。"""
    monkeypatch.setattr(deps, "db", db)
    return db


def test_route_create_and_list_contract(patched):
    for i in range(51):
        asyncio.run(sn_router.create_note(sn_router.StickyCreate(content=f"n{i}")))
    page = asyncio.run(sn_router.list_notes(view="inbox", limit=50, offset=0))
    assert len(page["notes"]) == 50 and page["has_more"] is True
    assert page["total"] == 51
    full = asyncio.run(sn_router.list_notes(view="inbox", limit=0, offset=0))
    assert len(full["notes"]) == 51 and full["has_more"] is False


def test_route_create_rejects_blank_and_bad_color(patched):
    with pytest.raises(HTTPException) as ei:
        asyncio.run(sn_router.create_note(sn_router.StickyCreate(content="   ")))
    assert ei.value.status_code == 422
    with pytest.raises(HTTPException) as ei:
        asyncio.run(sn_router.create_note(sn_router.StickyCreate(content="x", color="pink")))
    assert ei.value.status_code == 422  # ValueError 转结构化 422 而非 500


def test_route_update_delete_404(patched):
    for coro in (sn_router.update_note("missing", sn_router.StickyUpdate(content="x")),
                 sn_router.delete_note("missing")):
        with pytest.raises(HTTPException) as ei:
            asyncio.run(coro)
        assert ei.value.status_code == 404


def test_route_update_null_semantics_and_blank_422(patched):
    """路由层 null/缺席语义分流（Code Review #1）与空白正文 422（#5）。"""
    note = asyncio.run(sn_router.create_note(sn_router.StickyCreate(content="x", color="red")))
    nid = note["note"]["id"]
    # 缺席 color：不改色（只改文）
    r1 = asyncio.run(sn_router.update_note(nid, sn_router.StickyUpdate(content="y")))
    assert r1["note"]["color"] == "red" and r1["note"]["content"] == "y"
    # 显式 null：清除
    r2 = asyncio.run(sn_router.update_note(nid, sn_router.StickyUpdate(color=None)))
    assert r2["note"]["color"] is None
    # 空白正文 422（旧实现会清空正文，并为 to-affair 埋 IndexError 500）
    with pytest.raises(HTTPException) as ei:
        asyncio.run(sn_router.update_note(nid, sn_router.StickyUpdate(content="   ")))
    assert ei.value.status_code == 422


def test_route_archive_uploads_material(patched, monkeypatch):
    monkeypatch.setattr(sn_router, "ensure_ready", lambda _f: None)  # 测试环境模型未就绪，绕过入口检查
    async def fake_upload(project_id, upload, db, data_dir, worker):
        assert upload.filename.startswith("便签-") and upload.filename.endswith(".md")
        return {"success": True, "file_id": "file_x"}
    monkeypatch.setattr(sn_router, "_upload_core", fake_upload)
    note = asyncio.run(sn_router.create_note(sn_router.StickyCreate(content="归档我")))
    pid = _project(patched)
    result = asyncio.run(sn_router.archive_note(note["note"]["id"], sn_router.StickyArchive(project_id=pid)))
    assert result["note"]["archived_at"] and result["file"]["file_id"] == "file_x"
    # 已归档重放 409（幂等拒绝）
    with pytest.raises(HTTPException) as ei:
        asyncio.run(sn_router.archive_note(note["note"]["id"], sn_router.StickyArchive(project_id=pid)))
    assert ei.value.status_code == 409


def test_route_archive_unready_keeps_inbox(patched, monkeypatch):
    def boom(_feature):
        raise HTTPException(status_code=503, detail="parsing not ready")
    monkeypatch.setattr(sn_router, "ensure_ready", boom)
    note = asyncio.run(sn_router.create_note(sn_router.StickyCreate(content="x")))
    pid = _project(patched)
    with pytest.raises(HTTPException):
        asyncio.run(sn_router.archive_note(note["note"]["id"], sn_router.StickyArchive(project_id=pid)))
    assert patched.get_sticky_note(note["note"]["id"])["archived_at"] is None  # 未落盘不置归档


def test_route_to_affair_creates_material_task(patched):
    note = asyncio.run(sn_router.create_note(sn_router.StickyCreate(content="跟进寄样\n第二行")))
    pid = _project(patched)
    result = asyncio.run(sn_router.to_affair(note["note"]["id"], sn_router.StickyToAffair(project_id=pid)))
    assert result["task"]["task_type"] == "material"
    assert result["task"]["title"] == "跟进寄样"  # 首行未超 40 字不截断
    assert result["task"]["description"] == "跟进寄样\n第二行"
    # 便签原地留存（转事务不消耗便签，2.12 §3）
    assert patched.get_sticky_note(note["note"]["id"]) is not None


def test_route_archive_in_progress_conflicts_409(patched, monkeypatch):
    """archive 复合操作占坑（await 窗口真存在）：进行中 DELETE/archive 重入均 409——
    防「行删后素材 md 照常落盘成孤儿」竞态；占坑放行后归档正常完成。"""
    monkeypatch.setattr(sn_router, "ensure_ready", lambda _f: None)

    async def slow_upload(project_id, upload, db_, data_dir, worker):
        await asyncio.sleep(0.05)  # 制造 await 窗口供并发请求撞坑
        return {"success": True, "file_id": "file_slow"}

    monkeypatch.setattr(sn_router, "_upload_core", slow_upload)
    note = asyncio.run(sn_router.create_note(sn_router.StickyCreate(content="x")))
    nid = note["note"]["id"]
    pid = _project(patched)

    async def scenario():
        arch = asyncio.create_task(
            sn_router.archive_note(nid, sn_router.StickyArchive(project_id=pid)))
        await asyncio.sleep(0.02)  # archive 已过 pre-check、挂在 upload await
        with pytest.raises(HTTPException) as e1:
            await sn_router.delete_note(nid)
        assert e1.value.status_code == 409
        with pytest.raises(HTTPException) as e2:
            await sn_router.archive_note(nid, sn_router.StickyArchive(project_id=pid))
        assert e2.value.status_code == 409
        return await arch

    result = asyncio.run(scenario())
    assert result["note"]["archived_at"]  # 占坑串行化不影响正常完成
    assert patched.get_sticky_note(nid) is not None  # 行未被并发 delete 误删
