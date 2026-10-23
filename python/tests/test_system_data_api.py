"""python/tests/test_system_data_api.py — 数据清理端点回归：预置基础库必须存活。

2026-10-20 复盘：_clean_knowledge / reset_all_data 清空 projects 表时无差别
删掉 kind='base_lib' 系统内置行（招商/投资），且 ensure_preset_base_libs 只在
启动播种——不重启则侧栏基础库消失。修复后两入口必须在清空后补种预置库。
"""
import os
import sys
import tempfile
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import Database

PRESET_IDS = ("bl-recruitment", "bl-investment")


@pytest.fixture()
def client_and_db(monkeypatch):
    import deps
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import system as system_router
    with tempfile.TemporaryDirectory() as td:
        db = Database(os.path.join(td, "t.db"))
        db.initialize()
        db.ensure_preset_base_libs()
        # get_data_dir 双点位 patch（paths 模块绑定 + routers.system 模块级
        # from-import 绑定）：漏 patch 时 _clean_knowledge / reset_all_data
        # 的 rmtree 会落在真实 python/data——2026-10-22 复盘根因，每次全量
        # 回归清一次用户磁盘产物（DB 因 deps.db 已隔离而幸存）。
        monkeypatch.setattr("paths.get_data_dir", lambda: td)
        monkeypatch.setattr("routers.system.get_data_dir", lambda: td)
        monkeypatch.setattr(deps, "db", db)
        # 清理门禁依赖（_reset_gates）：无在飞任务/下载 = 放行
        monkeypatch.setattr(deps, "task_agent",
                            SimpleNamespace(running_count=lambda: 0))
        monkeypatch.setattr(deps, "model_manager",
                            SimpleNamespace(get_active_downloads=lambda: []))
        # 单 router 挂载，避免拉起 main lifespan（同 test_knowledge_api 口径）
        app = FastAPI()
        app.include_router(system_router.router)
        yield TestClient(app), db
        db.conn.close()


def _seed_user_project(db: Database) -> str:
    return db.create_project("用户项目", seed_playbook=False)["id"]


def test_clean_knowledge_restores_preset_base_libs(client_and_db):
    """清理知识库删光用户项目后，两个预置基础库必须自动补种回位。"""
    client, db = client_and_db
    user_pid = _seed_user_project(db)
    r = client.delete("/system/data/knowledge")
    assert r.status_code == 200
    assert r.json()["success"] is True
    # 用户数据清干净，预置库补种还原
    assert db.get_base_lib(user_pid) is None
    remaining = {row["id"] for row in db.list_base_libs()}
    assert remaining == set(PRESET_IDS)


def test_reset_all_restores_preset_base_libs(client_and_db):
    """真·全清「恢复初始状态」的初始态含两个预置基础库，必须补种。"""
    client, db = client_and_db
    user_pid = _seed_user_project(db)
    r = client.post("/system/data/reset-all")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert db.get_base_lib(user_pid) is None
    remaining = {row["id"] for row in db.list_base_libs()}
    assert remaining == set(PRESET_IDS)


def test_reset_all_idempotent_presets_not_duplicated(client_and_db):
    """重复清理不产生重复预置库行（播种幂等，PRIMARY KEY 兜底断言）。"""
    client, db = client_and_db
    for _ in range(2):
        r = client.delete("/system/data/knowledge")
        assert r.status_code == 200
    libs = db.list_base_libs()
    assert sorted(row["id"] for row in libs) == sorted(PRESET_IDS)
