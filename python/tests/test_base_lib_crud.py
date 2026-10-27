"""python/tests/test_base_lib_crud.py — 自定义基础库 CRUD API 回归。

覆盖（2.1 §7 / 2.7 §7 重建，2.9 D3 曾全链移除）：
- 创建：lib_type='custom' + kind='base_lib'，空名 400，入列 GET /base-libs；
- 更新：改名/改描述，预置 403（BASE_LIB_PRESET_PROTECTED），404 / 空名 / 无字段 400；
- 删除守门：预置 403、在途活动 409（BASE_LIB_HAS_ACTIVE_WORK，含图片描述在途
  口径；基础库仅文件解析/图片描述两类活动），活动落终态后可删；含清理链前
  复查（M-1 防线，守门与破坏链间隙内新活动不放行）；
- 删除清理链（对齐删项目 2.1 §11.4）：files/project_base_libs 外键级联、
  磁盘 files/{id}/ 目录、指向该库的订阅去向置空 + 合并通知（对照：指向
  预置库的订阅不动）；
- PATCH /projects kind 守门：基础库（预置/自定义）不可经项目端点改写（404）；
- DB 层直测：is_preset_base_lib 判定、update/delete 预置硬闸、kind 防误删项目行。
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
        db.ensure_preset_base_libs()
        db.seed_playbook_templates(get_all_playbook_templates())
        monkeypatch.setattr(deps, "db", db)
        # 单 router 挂载，避免拉起整个 main app 的 lifespan 依赖（同口径）
        app = FastAPI()
        app.include_router(knowledge_router.router)
        yield TestClient(app), db
        db.conn.close()


def _create_lib(client, name="半导体产业情报", description=""):
    r = client.post("/knowledge/base-libs", json={"name": name, "description": description})
    assert r.status_code == 200
    return r.json()


def _seed_parsing_file(db, lib_id):
    """在途文件解析（create_file 默认 pending）。"""
    return db.create_file(lib_id, "a.pdf", ".pdf", 1, "h1")


class TestCreateBaseLib:
    def test_create_returns_custom_lib(self, client_and_db):
        client, db = client_and_db
        lib = _create_lib(client, "半导体产业情报", "承载产业情报沉淀")
        assert lib["id"].startswith("bl-")
        assert lib["lib_type"] == "custom"
        row = db.get_base_lib(lib["id"])
        assert row["kind"] == "base_lib"
        assert row["lib_type"] == "custom"
        assert row["name"] == "半导体产业情报"
        assert row["description"] == "承载产业情报沉淀"

    def test_create_empty_name_400(self, client_and_db):
        client, _ = client_and_db
        assert client.post(
            "/knowledge/base-libs", json={"name": "   "}).status_code == 400

    def test_create_shows_in_list(self, client_and_db):
        client, _ = client_and_db
        lib = _create_lib(client)
        libs = client.get("/knowledge/base-libs").json()["base_libs"]
        ids = [b["id"] for b in libs]
        assert lib["id"] in ids
        assert "bl-recruitment" in ids   # 预置库仍在列


class TestUpdateBaseLib:
    def test_rename_and_describe(self, client_and_db):
        client, db = client_and_db
        lib = _create_lib(client, "旧名", "旧描述")
        r = client.patch(
            f"/knowledge/base-libs/{lib['id']}",
            json={"name": "新名", "description": "新描述"})
        assert r.status_code == 200
        row = db.get_base_lib(lib["id"])
        assert row["name"] == "新名"
        assert row["description"] == "新描述"

    def test_update_preset_403(self, client_and_db):
        """预置库不可改名（kind 守门外的业务保护，2.9 收敛期不变量延续）。"""
        client, db = client_and_db
        r = client.patch(
            "/knowledge/base-libs/bl-recruitment", json={"name": "X"})
        assert r.status_code == 403
        assert r.json()["code"] == "BASE_LIB_PRESET_PROTECTED"
        assert db.get_base_lib("bl-recruitment")["name"] != "X"   # 无写入

    def test_update_nonexistent_404(self, client_and_db):
        client, _ = client_and_db
        assert client.patch(
            "/knowledge/base-libs/bl-nope", json={"name": "X"}).status_code == 404

    def test_update_empty_name_400(self, client_and_db):
        client, _ = client_and_db
        lib = _create_lib(client)
        assert client.patch(
            f"/knowledge/base-libs/{lib['id']}", json={"name": "  "}).status_code == 400

    def test_update_no_fields_400(self, client_and_db):
        client, _ = client_and_db
        lib = _create_lib(client)
        assert client.patch(
            f"/knowledge/base-libs/{lib['id']}", json={}).status_code == 400


class TestDeleteGuard:
    def test_delete_ok(self, client_and_db):
        client, db = client_and_db
        lib = _create_lib(client)
        r = client.delete(f"/knowledge/base-libs/{lib['id']}")
        assert r.status_code == 200
        assert db.get_base_lib(lib["id"]) is None

    def test_delete_preset_403(self, client_and_db):
        client, db = client_and_db
        r = client.delete("/knowledge/base-libs/bl-investment")
        assert r.status_code == 403
        assert r.json()["code"] == "BASE_LIB_PRESET_PROTECTED"
        assert db.get_base_lib("bl-investment") is not None

    def test_delete_nonexistent_404(self, client_and_db):
        client, _ = client_and_db
        assert client.delete("/knowledge/base-libs/bl-nope").status_code == 404

    def test_delete_blocked_by_parsing_file(self, client_and_db):
        """文件解析在途拦删除（409；解析完成的 done 不误伤）。"""
        client, db = client_and_db
        lib = _create_lib(client)
        _seed_parsing_file(db, lib["id"])
        r = client.delete(f"/knowledge/base-libs/{lib['id']}")
        assert r.status_code == 409
        assert r.json()["code"] == "BASE_LIB_HAS_ACTIVE_WORK"
        assert db.get_base_lib(lib["id"]) is not None

    def test_delete_blocked_by_pending_image_description(self, client_and_db):
        """图片描述在途（pending/processing）拦删除：文件已 done 但描述异步在途，
        与项目侧 Minor-3 口径对齐，防删库后描述 worker 写孤儿产物。"""
        client, db = client_and_db
        lib = _create_lib(client)
        fid = _seed_parsing_file(db, lib["id"])["id"]
        db.update_file_status(fid, "done")   # 仅描述在途
        db.conn.execute(
            "INSERT INTO images (id, file_id, local_path, status) "
            "VALUES ('img-1', ?, 'x.png', 'pending')", (fid,))
        db.conn.commit()
        r = client.delete(f"/knowledge/base-libs/{lib['id']}")
        assert r.status_code == 409
        assert r.json()["code"] == "BASE_LIB_HAS_ACTIVE_WORK"

    def test_deletable_after_work_settles(self, client_and_db):
        """活动落终态后可删（守门只挡在途，不误伤历史数据）。"""
        client, db = client_and_db
        lib = _create_lib(client)
        fid = _seed_parsing_file(db, lib["id"])["id"]
        db.update_file_status(fid, "done")
        db.conn.execute(
            "INSERT INTO images (id, file_id, local_path, status) "
            "VALUES ('img-2', ?, 'y.png', 'done')", (fid,))
        db.conn.commit()
        assert client.delete(f"/knowledge/base-libs/{lib['id']}").status_code == 200

    def test_delete_recheck_blocks_new_activity(self, client_and_db, monkeypatch):
        """清理前复查（M-1 回归，对齐删项目防线）：入口守门通过后、破坏链执行
        前间隙内新落入的在途活动被复查拦截（409），数据未动可重试。"""
        from routers import knowledge as knowledge_router
        client, db = client_and_db
        lib = _create_lib(client)
        real_check = knowledge_router._base_lib_active_work
        calls = {"n": 0}

        def racing_check(db_, lib_id_):
            calls["n"] += 1
            # 第 1 次 = 入口守门（放行）；第 2 次 = 清理前复查（间隙内新活动）
            return real_check(db_, lib_id_) if calls["n"] < 2 else ["文件解析"]

        monkeypatch.setattr(knowledge_router, "_base_lib_active_work", racing_check)
        r = client.delete(f"/knowledge/base-libs/{lib['id']}")
        assert r.status_code == 409
        assert r.json()["code"] == "BASE_LIB_HAS_ACTIVE_WORK"
        assert calls["n"] == 2   # 复查确已执行
        assert db.get_base_lib(lib["id"]) is not None   # 数据未动


class TestDeleteCleanupChain:
    def test_delete_cascades_files_and_associations(self, client_and_db):
        """外键级联：files 与 project_base_libs 关联行随库行删除（ON DELETE CASCADE）。"""
        client, db = client_and_db
        pid = client.post("/knowledge/projects", json={
            "name": "项目甲", "type": "recruitment", "stage": "before"}).json()["id"]
        lib = _create_lib(client)
        fid = _seed_parsing_file(db, lib["id"])["id"]
        db.update_file_status(fid, "done")   # 落终态，不被在途守门拦
        db.link_project_to_base_lib(pid, lib["id"])
        assert db.get_project_base_libs(pid)   # 前置：关联存在

        r = client.delete(f"/knowledge/base-libs/{lib['id']}")
        assert r.status_code == 200
        rows = db.conn.execute(
            "SELECT COUNT(*) AS c FROM files WHERE project_id = ?", (lib["id"],)
        ).fetchone()["c"]
        assert rows == 0                       # files 级联清除
        # 项目仍持有 auto-link 的预置库，但自建库关联行已级联清除
        assert all(b["id"] != lib["id"] for b in db.get_project_base_libs(pid))

    def test_delete_removes_disk_dir(self, client_and_db, tmp_path, monkeypatch):
        """磁盘清理：data/files/{lib_id}/ 目录删除（patch get_data_dir 到 tmp）。"""
        import paths
        client, db = client_and_db
        lib = _create_lib(client)
        files_dir = tmp_path / "files" / lib["id"]
        files_dir.mkdir(parents=True)
        (files_dir / "a.txt").write_text("x", encoding="utf-8")
        monkeypatch.setattr("routers.knowledge.get_data_dir", lambda: str(tmp_path))
        monkeypatch.setattr(paths, "get_data_dir", lambda: str(tmp_path))

        r = client.delete(f"/knowledge/base-libs/{lib['id']}")
        assert r.status_code == 200
        assert not files_dir.exists()

    def test_delete_clears_schedule_targets_and_notifies(self, client_and_db,
                                                         monkeypatch):
        """订阅去向联动（与删项目同构）：指向该库的订阅 target 置空 + 合并通知；
        指向预置库的订阅对照不动。通知 container='project'（点击不导航到已删库）。"""
        from routers import knowledge as knowledge_router
        client, db = client_and_db
        lib = _create_lib(client)
        s1 = db.create_schedule(
            name="半导体周报", prompt="p", mode="general",
            category="industry", interval_type="daily",
            interval_value="09:00", next_run_at=None,
            target_type="baselib", target_id=lib["id"])
        s2 = db.create_schedule(
            name="招商参考", prompt="p", mode="general",
            category="industry", interval_type="daily",
            interval_value="09:00", next_run_at=None,
            target_type="baselib", target_id="bl-recruitment")
        notified: list = []
        monkeypatch.setattr(knowledge_router.activity_events, "notify",
                            lambda db, **kw: notified.append(kw))

        r = client.delete(f"/knowledge/base-libs/{lib['id']}")

        assert r.status_code == 200
        row = db.get_schedule(s1["id"])
        assert row["target_type"] is None and row["target_id"] is None   # 置空
        row2 = db.get_schedule(s2["id"])
        assert row2["target_type"] == "baselib"                          # 对照不动
        assert notified and notified[0]["entity_type"] == "schedule"
        assert notified[0]["container"] == "project"
        assert "已重置" in notified[0]["detail"]
        assert lib["name"] in notified[0]["detail"]
        assert s1["name"] in notified[0]["detail"]


class TestProjectEndpointKindGuard:
    def test_patch_preset_lib_via_project_endpoint_404(self, client_and_db):
        """存量漏洞收口：预置库不可经 PATCH /projects 改名（原无 kind 守门）。"""
        client, db = client_and_db
        r = client.patch("/knowledge/projects/bl-recruitment", json={"name": "X"})
        assert r.status_code == 404
        assert db.get_base_lib("bl-recruitment")["name"] != "X"

    def test_patch_custom_lib_via_project_endpoint_404(self, client_and_db):
        """自定义库改写唯一入口为 PATCH /base-libs/{id}（经项目端点一律 404）。"""
        client, db = client_and_db
        lib = _create_lib(client)
        r = client.patch(f"/knowledge/projects/{lib['id']}", json={"name": "X"})
        assert r.status_code == 404
        assert db.get_base_lib(lib["id"])["name"] == lib["name"]

    def test_patch_real_project_still_ok(self, client_and_db):
        """对照：真项目改名不受守门影响。"""
        client, _ = client_and_db
        pid = client.post("/knowledge/projects", json={
            "name": "项目甲", "type": "recruitment", "stage": "before"}).json()["id"]
        assert client.patch(
            f"/knowledge/projects/{pid}", json={"name": "新项目甲"}).status_code == 200


class TestDbLayerDirect:
    """DB 层直测：路由层守门被绕过时的硬闸（防未来新端点漏检）。"""

    def test_is_preset_detection(self, client_and_db):
        client, db = client_and_db
        assert db.is_preset_base_lib(db.get_base_lib("bl-recruitment"))
        assert db.is_preset_base_lib(db.get_base_lib("bl-investment"))
        lib = _create_lib(client)
        assert not db.is_preset_base_lib(db.get_base_lib(lib["id"]))

    def test_update_preset_returns_false(self, client_and_db):
        client, db = client_and_db
        assert db.update_base_lib("bl-recruitment", name="X") is False
        assert db.get_base_lib("bl-recruitment")["name"] != "X"

    def test_delete_preset_returns_false(self, client_and_db):
        client, db = client_and_db
        assert db.delete_base_lib("bl-recruitment") is False
        assert db.get_base_lib("bl-recruitment") is not None

    def test_delete_never_touches_project_rows(self, client_and_db):
        """kind='base_lib' 谓词防误删：kind='project' 的行传给 delete_base_lib 不动。"""
        client, db = client_and_db
        pid = client.post("/knowledge/projects", json={
            "name": "项目甲", "type": "recruitment", "stage": "before"}).json()["id"]
        assert db.delete_base_lib(pid) is False
        assert db.get_project(pid) is not None
