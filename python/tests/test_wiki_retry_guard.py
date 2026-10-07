"""python/tests/test_wiki_retry_guard.py — retry_page 同项目防重回归测试。

背景（2026-10-07）：编译并发默认 1→2 后，单页重试（POST
/projects/{id}/pages/{key}/retry）此前绕过 trigger_compile 的活跃任务检查
——并发 2 下两个 worker 会同时 ingest 同一项目的同一 pending 文件：同一
page_key 双写、overview/change_log 交错滚动。防重与 trigger_compile 对齐：
已有 queued/ingesting/storing 任务时 409，终态（completed/failed/stopped）
放行（retry 本就是失败页重试入口，不得被终态任务挡住）。
"""
import asyncio
import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import deps
import routers.wiki as wiki_router
from db.database import Database


@pytest.fixture
def db():
    d = Database(db_path=":memory:")
    d.initialize()
    yield d
    d.conn.close()


@pytest.fixture
def patched(db, monkeypatch):
    """router 依赖注入：deps.db → 内存 DB；LLM 准入检查跳过（非本测试主题）。"""
    monkeypatch.setattr(deps, "db", db)
    monkeypatch.setattr(wiki_router, "ensure_ready", lambda *_: None)
    return db


def _seed(patched) -> tuple[str, str]:
    """建项目 + 一页 Wiki，返回 (project_id, page_key)。"""
    project = patched.create_project("P")
    patched.create_or_update_wiki_page(project["id"], "overview", "总览", "...")
    return project["id"], "overview"


def _make_task(patched, project_id: str, status: str) -> str:
    task = patched.create_wiki_compile_task(project_id, trigger_type="manual")
    patched.update_compile_task_status(task["id"], status)
    return task["id"]


@pytest.mark.parametrize("status", ["queued", "ingesting", "storing"])
def test_retry_rejected_while_compile_active(patched, status):
    """活跃编译任务存在 → retry 409（并发 2 下防同项目双 worker ingest）。"""
    project_id, page_key = _seed(patched)
    _make_task(patched, project_id, status)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(wiki_router.retry_page(project_id, page_key))
    assert exc.value.status_code == 409


@pytest.mark.parametrize("status", ["completed", "failed", "stopped"])
def test_retry_allowed_after_terminal_status(patched, status):
    """终态任务 → retry 放行：新建 queued 编译任务成为 latest。"""
    project_id, page_key = _seed(patched)
    old_id = _make_task(patched, project_id, status)

    result = asyncio.run(wiki_router.retry_page(project_id, page_key))

    latest = patched.get_latest_compile_task(project_id)
    assert result["task"]["id"] == latest["id"]
    assert latest["id"] != old_id
    assert latest["status"] == "queued"


def test_retry_allowed_without_any_task(patched):
    """无历史任务 → retry 正常创建 queued 任务。"""
    project_id, page_key = _seed(patched)

    result = asyncio.run(wiki_router.retry_page(project_id, page_key))
    assert result["task"]["status"] == "queued"


def test_retry_missing_page_404(patched):
    """页面不存在 → 404（防重检查不得遮蔽原有语义）。"""
    project_id, _ = _seed(patched)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(wiki_router.retry_page(project_id, "no-such-page"))
    assert exc.value.status_code == 404
