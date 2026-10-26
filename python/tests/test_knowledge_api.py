"""python/tests/test_knowledge_api.py — 3.0 二期 Task 13 review C1:创建项目契约回传 seeded_tasks_count。

裁决 8 确认态「带入 N 项任务」的数据源是 POST /knowledge/projects 的响应体;
DB 层 create_project 已返回该字段,路由层重建响应字典时不得丢弃。
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import Database
from db.playbook_seed import get_all_playbook_templates


@pytest.fixture()
def client_and_db(monkeypatch):
    import deps
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import knowledge as knowledge_router
    with tempfile.TemporaryDirectory() as td:
        db = Database(os.path.join(td, "t.db"))
        db.initialize()
        db.ensure_preset_base_libs()  # create_project 的 auto-link 依赖 preset 基础库
        db.seed_playbook_templates(get_all_playbook_templates())
        monkeypatch.setattr(deps, "db", db)
        # 单 router 挂载,避免拉起整个 main app 的 lifespan 依赖(同 test_affairs_api 口径)
        app = FastAPI()
        app.include_router(knowledge_router.router)
        yield TestClient(app), db
        db.conn.close()


def test_create_project_returns_seeded_tasks_count(client_and_db):
    """回传值必须与 DB 实际铺入数一致(缺键 = 前端恒显示 0,契约断裂)。"""
    client, db = client_and_db
    r = client.post("/knowledge/projects", json={
        "name": "招聘项目", "type": "recruitment", "stage": "before"})
    assert r.status_code == 200
    body = r.json()
    assert body["seeded_tasks_count"] > 0
    assert body["seeded_tasks_count"] == len(db.list_phase_tasks(body["id"]))


def test_update_project_company_fields(client_and_db):
    """todo「项目创建后不能修改」回归:PATCH 企业字段全链(不传不改语义)。

    企业字段创建后原无任何修改通道(ProjectUpdate 不含 → 前端无入口);
    现补全后:只传企业字段时名称/描述/类型/阶段不得被牵连,空体 400。
    """
    client, db = client_and_db
    r = client.post("/knowledge/projects", json={
        "name": "企业项目", "type": "company",
        "company_name": "旧公司", "credit_code": "91110000OLDCODE"})
    assert r.status_code == 200
    pid = r.json()["id"]
    # 只传企业字段:其余字段不动
    r2 = client.patch(f"/knowledge/projects/{pid}", json={
        "company_name": "新公司名", "credit_code": "91110000NEWCODE"})
    assert r2.status_code == 200
    proj = db.get_project(pid)
    assert proj["company_name"] == "新公司名"
    assert proj["credit_code"] == "91110000NEWCODE"
    assert proj["name"] == "企业项目"
    assert proj["type"] == "company"
    # 空体 PATCH → 400(No fields to update)
    r3 = client.patch(f"/knowledge/projects/{pid}", json={})
    assert r3.status_code == 400


def test_profile_extract_executor_submit_failure_releases_slot(
        client_and_db, monkeypatch):
    """占坑防泄漏回归（review Info 2026-10-25）：executor 提交失败（如
    get_running_loop 抿不到事件循环）→ 占坑归还，不残留致死锁
    （泄漏则该项目恒 409 直至重启）；且归还后可再次受理。"""
    import types

    from wiki import profile_service as ps

    client, db = client_and_db
    proj = db.create_project("Acme", seed_playbook=False)

    def _boom():
        raise RuntimeError("no running event loop")

    # 精准替换模块内 asyncio 引用（不 patch 全局 asyncio，避免波及
    # TestClient 自身的事件循环设施）；ensure_ready 摘除 LLM 准入依赖
    import routers.knowledge as knowledge_router
    monkeypatch.setattr(
        knowledge_router, "asyncio",
        types.SimpleNamespace(get_running_loop=_boom))
    monkeypatch.setattr(knowledge_router, "ensure_ready", lambda kind: None)

    r = client.post(f"/knowledge/projects/{proj['id']}/profile/extract")

    assert r.status_code == 500
    assert ps.fetch_running() == []            # 占坑已归还：活动不残留
    assert ps.try_acquire(proj["id"]) is True  # 可再次受理（未死锁 409）
    ps.release(proj["id"])                     # 收尾释放（review Info：_RUNNING 是
    # 进程级共享态，不把占坑残留带给后续用例；顺带覆盖 release 幂等路径）
    assert ps.fetch_running() == []
