"""PPT 模板创建任务列表分页回归（GET /ppt-templates/jobs）。

回归背景（2026-10）：
- jobs 采用分段契约：活跃段（running/awaiting_input）全量（进度轮询/门禁
  不可截断，天然小）+ 终态段 limit/offset；has_more 只看终态段（探测在
  db 层完成，list_template_jobs 返回 (jobs, has_more) 二元组）。
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import deps
import routers.ppt_templates as ppt_templates
from db.database import Database


@pytest.fixture
def db():
    database = Database(db_path=":memory:")
    database.initialize()
    return database


@pytest.fixture
def deps_db(db, monkeypatch):
    monkeypatch.setattr(deps, "db", db, raising=False)
    return db


def _seed_jobs(database, n_active=0, n_terminal=0):
    """模板任务行（kind='template'，project_id 空串=全局资产语义）。"""
    ids = []
    seq = 0
    for _ in range(n_active):
        row = database.create_ppt("", f"task-{seq}", f"活跃-{seq}", kind="template")
        ids.append((row["id"], "running"))
        seq += 1
    for _ in range(n_terminal):
        row = database.create_ppt("", f"task-{seq}", f"完成-{seq}", kind="template")
        database.update_ppt_status(row["id"], "completed")
        ids.append((row["id"], "completed"))
        seq += 1
    database.conn.commit()
    return ids


class TestListTemplateJobsPagination:
    def test_active_segment_never_truncated(self, deps_db):
        """关键契约：limit 截断只作用于终态段，活跃段全量返回。"""
        _seed_jobs(deps_db, n_active=5, n_terminal=30)
        result = asyncio.run(ppt_templates.list_template_jobs(limit=20, offset=0))
        jobs = result["jobs"]
        assert len(jobs) == 25  # 5 活跃 + 20 终态
        assert result["has_more"] is True
        active_in_page = [j for j in jobs if j["status"] == "running"]
        assert len(active_in_page) == 5

    def test_limit_zero_returns_full(self, deps_db):
        _seed_jobs(deps_db, n_active=5, n_terminal=30)
        result = asyncio.run(ppt_templates.list_template_jobs(limit=0, offset=0))
        assert len(result["jobs"]) == 35
        assert result["has_more"] is False

    def test_offset_walks_terminal_segment_only(self, deps_db):
        """offset 只翻终态段：活跃行在每页恒在，终态部分跨页无重叠。"""
        _seed_jobs(deps_db, n_active=2, n_terminal=10)
        page1 = asyncio.run(ppt_templates.list_template_jobs(limit=4, offset=0))
        page2 = asyncio.run(ppt_templates.list_template_jobs(limit=4, offset=4))
        # 活跃段全量在每一页都返回（进度轮询/门禁不截断）
        assert len(page1["jobs"]) == 6  # 2 活跃 + 终态[0:4]
        assert len(page2["jobs"]) == 6  # 2 活跃 + 终态[4:8]
        terminal1 = [j for j in page1["jobs"] if j["status"] == "completed"]
        terminal2 = [j for j in page2["jobs"] if j["status"] == "completed"]
        assert len(terminal1) == 4
        assert len(terminal2) == 4
        ids1 = {j["id"] for j in terminal1}
        ids2 = {j["id"] for j in terminal2}
        assert ids1.isdisjoint(ids2)

    def test_has_more_follows_terminal_segment(self, deps_db):
        """has_more 只看终态段：终态 30 行 limit=30 → False；limit=10 → True。"""
        _seed_jobs(deps_db, n_terminal=30)
        assert asyncio.run(
            ppt_templates.list_template_jobs(limit=30, offset=0))["has_more"] is False
        assert asyncio.run(
            ppt_templates.list_template_jobs(limit=10, offset=0))["has_more"] is True

    def test_deck_rows_excluded(self, deps_db):
        """kind='deck'（交付物）行不混入模板任务列表。"""
        deps_db.create_ppt("p1", "task-deck", "交付物", kind="deck")
        _seed_jobs(deps_db, n_terminal=2)
        result = asyncio.run(ppt_templates.list_template_jobs(limit=0, offset=0))
        assert len(result["jobs"]) == 2
