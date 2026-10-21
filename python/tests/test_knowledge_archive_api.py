"""python/tests/test_knowledge_archive_api.py — 项目归档 API 回归。

覆盖：归档/恢复端点（含 kind 守门 404）、进行中活动守门（409 PROJECT_HAS_ACTIVE_WORK，
含图片描述在途口径；归档与删除端点共用，Major-1）、删除准入（未归档 409
PROJECT_NOT_ARCHIVED，归档后可删；归档后有在途活动则拒绝）、unarchive 独立错误码
（PROJECT_ALREADY_ACTIVE，Minor-2：不再与 DELETE 共用）、列表分流
（archived 参数 + archived_count）、首页待办排除归档项目（list_due_phase_tasks）。
"""
import os
import sys
import tempfile
from datetime import date

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
        # 单 router 挂载，避免拉起整个 main app 的 lifespan 依赖（同 test_knowledge_api 口径）
        app = FastAPI()
        app.include_router(knowledge_router.router)
        yield TestClient(app), db
        db.conn.close()


def _create_project(client, name="项目甲", ptype="recruitment"):
    r = client.post("/knowledge/projects", json={"name": name, "type": ptype, "stage": "before"})
    assert r.status_code == 200
    return r.json()


def _seed_generating_report(db, pid):
    db.conn.execute(
        "INSERT INTO reports (id, project_id, template_id, title, output_format, status, created_at, updated_at) "
        "VALUES (?, ?, 'tpl-x', '进行中报告', 'docx', 'generating', ?, ?)",
        ("rep-1", pid, db._now(), db._now()),
    )
    db.conn.commit()


def _seed_filling_form_fill(db, pid):
    db.conn.execute(
        "INSERT INTO form_templates (id, name, source, file_path, status, created_at, updated_at) "
        "VALUES ('tpl-f', '模板', 'upload', 'tpl-f.docx', 'success', ?, ?)",
        (db._now(), db._now()),
    )
    db.create_form_fill(pid, "tpl-f", "进行中填表")
    db.conn.commit()


def _seed_running_compile(db, pid):
    db.conn.execute(
        "INSERT INTO wiki_compile_tasks (id, project_id, trigger_type, status, created_at) "
        "VALUES (?, ?, 'manual', 'queued', ?)",
        ("wct-1", pid, db._now()),
    )
    db.conn.commit()


class TestArchiveRoundtrip:
    def test_archive_moves_between_lists_with_count(self, client_and_db):
        client, db = client_and_db
        pid = _create_project(client)["id"]

        r = client.post(f"/knowledge/projects/{pid}/archive")
        assert r.status_code == 200
        assert db.get_project(pid)["archived_at"]

        active = client.get("/knowledge/projects").json()
        assert [p["id"] for p in active["projects"]] == []
        assert active["archived_count"] == 1

        archived = client.get("/knowledge/projects", params={"archived": "true"}).json()
        assert [p["id"] for p in archived["projects"]] == [pid]
        assert archived["projects"][0]["archived_at"]
        assert archived["archived_count"] == 1

    def test_unarchive_restores_active_list(self, client_and_db):
        client, db = client_and_db
        pid = _create_project(client)["id"]
        client.post(f"/knowledge/projects/{pid}/archive")

        r = client.post(f"/knowledge/projects/{pid}/unarchive")
        assert r.status_code == 200
        assert db.get_project(pid)["archived_at"] is None
        active = client.get("/knowledge/projects").json()
        assert [p["id"] for p in active["projects"]] == [pid]
        assert active["projects"][0]["archived_at"] is None
        assert active["archived_count"] == 0

    def test_unarchive_not_archived_409(self, client_and_db):
        client, _ = client_and_db
        pid = _create_project(client)["id"]
        r = client.post(f"/knowledge/projects/{pid}/unarchive")
        assert r.status_code == 409
        # Minor-2：独立错误码（原与 DELETE 未归档共用 PROJECT_NOT_ARCHIVED，文案误导）
        assert r.json()["code"] == "PROJECT_ALREADY_ACTIVE"

    def test_archive_nonexistent_404(self, client_and_db):
        client, _ = client_and_db
        assert client.post("/knowledge/projects/nope/archive").status_code == 404
        assert client.post("/knowledge/projects/nope/unarchive").status_code == 404

    def test_archive_base_lib_404(self, client_and_db):
        """kind 守门：基础库不可经项目归档端点操作。"""
        client, _ = client_and_db
        assert client.post("/knowledge/projects/bl-recruitment/archive").status_code == 404


class TestArchiveActiveWorkGuard:
    def test_blocked_by_parsing_file(self, client_and_db):
        client, db = client_and_db
        pid = _create_project(client)["id"]
        db.create_file(pid, "a.pdf", ".pdf", 1, "h1")
        r = client.post(f"/knowledge/projects/{pid}/archive")
        assert r.status_code == 409
        assert r.json()["code"] == "PROJECT_HAS_ACTIVE_WORK"

    def test_blocked_by_generating_report(self, client_and_db):
        client, db = client_and_db
        pid = _create_project(client)["id"]
        _seed_generating_report(db, pid)
        assert client.post(f"/knowledge/projects/{pid}/archive").status_code == 409

    def test_blocked_by_filling_form(self, client_and_db):
        client, db = client_and_db
        pid = _create_project(client)["id"]
        _seed_filling_form_fill(db, pid)
        assert client.post(f"/knowledge/projects/{pid}/archive").status_code == 409

    def test_blocked_by_running_compile(self, client_and_db):
        client, db = client_and_db
        pid = _create_project(client)["id"]
        _seed_running_compile(db, pid)
        assert client.post(f"/knowledge/projects/{pid}/archive").status_code == 409

    def test_blocked_by_pending_image_description(self, client_and_db):
        """图片描述在途（Minor-3 口径补全）：文件落 done 后描述仍异步在途
        （pending=待描述 / processing=worker 已取走），归档不得漏过。"""
        client, db = client_and_db
        pid = _create_project(client)["id"]
        fid = db.create_file(pid, "a.pdf", ".pdf", 1, "h1")["id"]
        db.update_file_status(fid, "done")   # 文件解析已完成，仅描述在途
        db.conn.execute(
            "INSERT INTO images (id, file_id, local_path, status) VALUES ('img-1', ?, 'x.png', 'pending')",
            (fid,),
        )
        db.conn.commit()
        r = client.post(f"/knowledge/projects/{pid}/archive")
        assert r.status_code == 409
        assert r.json()["code"] == "PROJECT_HAS_ACTIVE_WORK"

    def test_archivable_after_work_settles(self, client_and_db):
        """全部活动落终态后可归档（守门只挡在途，不误伤历史数据）。"""
        client, db = client_and_db
        pid = _create_project(client)["id"]
        fid = db.create_file(pid, "a.pdf", ".pdf", 1, "h1")["id"]
        db.update_file_status(fid, "done")
        _seed_generating_report(db, pid)
        _seed_filling_form_fill(db, pid)
        _seed_running_compile(db, pid)
        # 图片描述落终态（Minor-3 口径：终态不误伤）
        fid = db.create_file(pid, "b.pdf", ".pdf", 1, "h2")["id"]
        db.update_file_status(fid, "done")
        db.conn.execute(
            "INSERT INTO images (id, file_id, local_path, status) VALUES ('img-2', ?, 'y.png', 'done')",
            (fid,),
        )
        # 全部置终态
        db.conn.execute("UPDATE reports SET status = 'completed' WHERE id = 'rep-1'")
        db.conn.execute("UPDATE form_fills SET status = 'success' WHERE project_id = ?", (pid,))
        db.conn.execute("UPDATE wiki_compile_tasks SET status = 'completed' WHERE id = 'wct-1'")
        db.conn.commit()
        assert client.post(f"/knowledge/projects/{pid}/archive").status_code == 200

    def test_blocked_by_running_ppt(self, client_and_db):
        """PPT 交付进行中拦归档（2.3 §7.3 口径对齐，复用统一活动 status 字段）。"""
        client, db = client_and_db
        pid = _create_project(client)["id"]
        db.create_ppt(project_id=pid, task_id="t-x", title="演示文稿",
                      workspace_path="nowhere")   # create_ppt 默认 running
        r = client.post(f"/knowledge/projects/{pid}/archive")
        assert r.status_code == 409
        assert r.json()["code"] == "PROJECT_HAS_ACTIVE_WORK"

    def test_blocked_by_awaiting_ppt(self, client_and_db):
        """门禁等待态同样算进行中（等待用户确认期间不可归档/删除）。"""
        client, db = client_and_db
        pid = _create_project(client)["id"]
        ppt = db.create_ppt(project_id=pid, task_id="t-y", title="演示文稿",
                            workspace_path="nowhere")
        db.update_ppt_status(ppt["id"], "awaiting_input")
        assert client.post(f"/knowledge/projects/{pid}/archive").status_code == 409


class TestDeleteGuard:
    def test_delete_unarchived_409(self, client_and_db):
        client, db = client_and_db
        pid = _create_project(client)["id"]
        r = client.delete(f"/knowledge/projects/{pid}")
        assert r.status_code == 409
        assert r.json()["code"] == "PROJECT_NOT_ARCHIVED"
        assert db.get_project(pid) is not None  # 未删除

    def test_delete_archived_ok(self, client_and_db):
        client, db = client_and_db
        pid = _create_project(client)["id"]
        client.post(f"/knowledge/projects/{pid}/archive")
        r = client.delete(f"/knowledge/projects/{pid}")
        assert r.status_code == 200
        assert db.get_project(pid) is None
        assert client.get("/knowledge/projects").json()["archived_count"] == 0

    def test_delete_archived_with_active_work_409(self, client_and_db):
        """Major-1：归档后详情页仍可发起活动（归档仅是状态层），删除前必须复查，
        防 runner 写已删行产出孤儿产物（归档瞬间无活动 ≠ 归档后永远无活动）。"""
        client, db = client_and_db
        pid = _create_project(client)["id"]
        client.post(f"/knowledge/projects/{pid}/archive")
        # 归档成功后用户发起报告生成（状态层语义允许）
        _seed_generating_report(db, pid)
        r = client.delete(f"/knowledge/projects/{pid}")
        assert r.status_code == 409
        assert r.json()["code"] == "PROJECT_HAS_ACTIVE_WORK"
        assert db.get_project(pid) is not None  # 未删除

    def test_deletable_after_work_settles(self, client_and_db):
        """Major-1 反向：在途活动落终态后，已归档项目可正常删除（守门只挡在途）。"""
        client, db = client_and_db
        pid = _create_project(client)["id"]
        client.post(f"/knowledge/projects/{pid}/archive")
        _seed_generating_report(db, pid)
        db.conn.execute("UPDATE reports SET status = 'completed' WHERE id = 'rep-1'")
        db.conn.commit()
        assert client.delete(f"/knowledge/projects/{pid}").status_code == 200
        assert db.get_project(pid) is None

    def test_delete_nonexistent_404(self, client_and_db):
        client, _ = client_and_db
        assert client.delete("/knowledge/projects/nope").status_code == 404

    def test_delete_base_lib_404(self, client_and_db):
        """kind 守门：基础库不可经项目删除端点删除（原为静默 success，收口为 404）。"""
        client, _ = client_and_db
        assert client.delete("/knowledge/projects/bl-recruitment").status_code == 404

    def test_delete_blocked_by_active_ppt(self, client_and_db):
        """PPT 级联前置：归档后新发起的 running PPT 拦删除（防幽灵活动）。"""
        client, db = client_and_db
        pid = _create_project(client)["id"]
        client.post(f"/knowledge/projects/{pid}/archive")
        db.create_ppt(project_id=pid, task_id="t-z", title="演示文稿",
                      workspace_path="nowhere")
        r = client.delete(f"/knowledge/projects/{pid}")
        assert r.status_code == 409
        assert r.json()["code"] == "PROJECT_HAS_ACTIVE_WORK"
        assert db.get_project(pid) is not None

    def test_delete_cascades_ppt_rows_and_workspace(self, client_and_db, tmp_path,
                                                    monkeypatch):
        """PPT 级联清理回归（ppts 无外键、tasks 无 project_id，不随
        db.delete_project 级联）：删项目复用 service.delete 链路——ppts 行、
        origin='ppt' 任务行与任务工作区一并清除。"""
        from types import SimpleNamespace
        import deps
        client, db = client_and_db
        pid = _create_project(client)["id"]
        client.post(f"/knowledge/projects/{pid}/archive")   # 先归档（无活动）
        # delete_task 的 rmtree 只对 get_data_dir()/tasks 下的 app 管理目录生效
        # （防误删用户自选目录；2026-10-20 审计起锚点与 create_task 的数据目录
        # 同源 get_data_dir，不再读 db_path）：patch 使 tmp 布局成 managed
        monkeypatch.setattr("paths.get_data_dir", lambda: str(tmp_path))
        task = db.create_task(title="[PPT] T", data_dir=str(tmp_path), origin="ppt")
        ws = task["workspace_path"]
        assert os.path.isdir(ws)
        ppt = db.create_ppt(project_id=pid, task_id=task["id"], title="演示文稿",
                            workspace_path=str(tmp_path / "ppt_project"))
        (tmp_path / "ppt_project").mkdir()
        db.update_ppt_status(ppt["id"], "paused")   # 非在途，守门放行
        # 测试环境无 task_agent：stub 运行侧依赖（is_task_running=False →
        # bounded 等待直通；shutdown 在 venv_python 为空时本就直通）
        monkeypatch.setattr(
            deps, "task_agent",
            SimpleNamespace(is_task_running=lambda t: False,
                            request_cancel=lambda t: None),
            raising=False)
        r = client.delete(f"/knowledge/projects/{pid}")
        assert r.status_code == 200
        assert db.get_project(pid) is None
        assert db.list_ppts(pid) == []
        assert db.get_task(task["id"]) is None
        assert not os.path.isdir(ws)   # 任务工作区（含 ppt_project）已清除

    def test_delete_clears_schedule_targets_and_notifies(self, client_and_db,
                                                         monkeypatch):
        """验收-P2（产品验收发现）：删除项目联动订阅去向——指向该项目的订阅
        target_type/target_id 置空（对称置空，与「删订阅时 tasks.schedule_id
        置 NULL」同构），并合并一条通知；否则订阅每期 archive_failed 幽灵
        警告（产出正常但无处沉淀，根因无处可见）。基础库对照不清理（不可
        经本端点删除，无此场景）。"""
        from routers import knowledge as knowledge_router
        client, db = client_and_db
        pid = _create_project(client)["id"]
        client.post(f"/knowledge/projects/{pid}/archive")
        s1 = db.create_schedule(
            name="演示产业·半导体", prompt="p", mode="general",
            category="industry", interval_type="daily",
            interval_value="09:00", next_run_at=None,
            target_type="project", target_id=pid)
        s2 = db.create_schedule(
            name="演示企业·华为", prompt="p", mode="general",
            category="corporate", interval_type="daily",
            interval_value="09:00", next_run_at=None,
            target_type="baselib", target_id="bl-recruitment")
        notified: list = []
        monkeypatch.setattr(knowledge_router.activity_events, "notify",
                            lambda db, **kw: notified.append(kw))

        r = client.delete(f"/knowledge/projects/{pid}")

        assert r.status_code == 200
        row = db.get_schedule(s1["id"])
        assert row["target_type"] is None and row["target_id"] is None   # 置空
        row2 = db.get_schedule(s2["id"])
        assert row2["target_type"] == "baselib"                          # 对照不动
        assert notified and notified[0]["entity_type"] == "schedule"
        assert "已重置" in notified[0]["detail"]
        assert s1["name"] in notified[0]["detail"]


class TestHomeTodoExcludesArchived:
    def test_list_due_excludes_archived_project(self, client_and_db):
        """归档项目的 open 任务退出首页待办（恢复后自动回来，不动任务数据）。"""
        client, db = client_and_db
        pid = _create_project(client)["id"]
        tasks = db.list_phase_tasks(pid)
        open_tasks = [t for t in tasks if t["status"] == "open"]
        assert open_tasks
        today = date.today().isoformat()
        for t in open_tasks:
            db.conn.execute("UPDATE phase_tasks SET due_date = ? WHERE id = ?", (today, t["id"]))
        db.conn.commit()

        assert any(t["project_id"] == pid for t in db.list_due_phase_tasks(7))
        client.post(f"/knowledge/projects/{pid}/archive")
        assert not any(t["project_id"] == pid for t in db.list_due_phase_tasks(7))
        client.post(f"/knowledge/projects/{pid}/unarchive")
        assert any(t["project_id"] == pid for t in db.list_due_phase_tasks(7))
