"""python/tests/test_task_default_mode.py — 任务默认模式回归（2026-10-21）。

chat 任务默认模式 coding → general：后端三层默认（schemas.TaskCreateRequest /
db.create_task 签名 / 迁移列 DEFAULT）需保持一致；PPT 任务显式锁定 coding
（ppt-master 百轮级工作流在 coding 全工具集下实测跑通，不吃新默认）。
前端 useTasks 入参默认（'general'）无法在 pytest 覆盖，由 TS 侧 tsc 把关类型。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.database import Database
from schemas import TaskCreateRequest


@pytest.fixture
def db():
    database = Database(db_path=":memory:")
    database.initialize()
    return database


class TestDefaultMode:
    def test_schema_default_is_general(self):
        """TaskCreateRequest 不传 mode → general（chat 默认，2026-10-21 起）。"""
        assert TaskCreateRequest().mode == "general"

    def test_db_create_task_default_is_general(self, db, tmp_path):
        """db.create_task 不传 mode 落库 general（应用层默认与 schema 一致）。"""
        task = db.create_task("默认模式任务", data_dir=str(tmp_path))
        assert task["mode"] == "general"

    def test_explicit_mode_respected(self, db, tmp_path):
        """显式传参不被默认吞掉（ppt_service 锁 coding / scheduler 传 general 的基础）。"""
        coding = db.create_task("编程任务", data_dir=str(tmp_path), mode="coding")
        general = db.create_task("通用任务", data_dir=str(tmp_path), mode="general")
        assert coding["mode"] == "coding"
        assert general["mode"] == "general"

    def test_migration_column_default_is_general(self, db):
        """新装库 tasks.mode 列 DEFAULT 'general'：绕过 create_task 直插（不指定
        mode），DEFAULT 应填 general——迁移语句 TEXT DEFAULT 'general' 生效证据。"""
        db.conn.execute(
            "INSERT INTO tasks (id, title, status, workspace_path, created_at, updated_at) "
            "VALUES ('raw-row', '裸插行', 'active', '', '2026-01-01', '2026-01-01')"
        )
        db.conn.commit()
        row = db.get_task("raw-row")
        assert row is not None
        assert row["mode"] == "general"
