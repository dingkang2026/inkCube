"""python/tests/test_system_data.py — 数据管理 usage/clean 端点单测（2026-10-20 存储审计扩展）。

覆盖 system.py 数据管理面的新口径：
- usage 9 分类完整返回 + cleanable 标记（reports/extras 不可整体清理）
- uv_cache/fetched 目录缺失时键不返回（前端不渲染零值行）
- clean tasks：归档工作区清盘、活跃任务不动（共享 maintenance 核心）
- clean uv_cache：整体 rmtree（uv 韧性语义）
- clean models：下载中 409 门（MODEL_DOWNLOAD_IN_PROGRESS）+ 成功后
  model_downloads 行联动清空
- clean reports/extras：不可整体清理契约
- clean fetched：整体 rmtree（产物可再生，下次抓取自建目录）
- reset-all：真·全清——在飞作业 409 门（任务/模型下载）、全部用户表
  FK 依赖序清零、磁盘产物目录删净、settings 清空后 get_settings 回默认、
  目录锁死时 partial 上报

get_data_dir 双点位 patch：system.py 模块级 from-import 绑定 + maintenance
函数内 import（delete_task 同）——统一锚 tmp_path。
"""
import os
import sys
from datetime import datetime
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI
from fastapi.testclient import TestClient

import deps
from db.database import Database
from routers.system import router


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr("paths.get_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr("routers.system.get_data_dir", lambda: str(tmp_path))
    database = Database(str(tmp_path / "inkcube.db"))
    database.initialize()
    monkeypatch.setattr(deps, "db", database)
    app = FastAPI()
    app.include_router(router)
    yield TestClient(app), database, tmp_path
    database.conn.close()


def _mkdir(base, *parts):
    d = os.path.join(str(base), *parts)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "x.txt"), "w", encoding="utf-8") as f:
        f.write("x")
    return d


def _insert_task(db, task_id, workspace, status="active"):
    now = datetime.now().isoformat()
    db.conn.execute(
        "INSERT INTO tasks (id, title, status, workspace_path, venv_path,"
        " mode, origin, created_at, updated_at)"
        " VALUES (?, 't', ?, ?, '', 'coding', 'chat', ?, ?)",
        (task_id, status, workspace, now, now),
    )
    db.conn.commit()


class TestUsage:
    def test_nine_categories_with_cleanable_flags(self, env):
        client, db, data_dir = env
        for parts in (("logs",), ("files", "p1"), ("chroma",), ("models",),
                      ("tasks", "t1"), ("reports", "r1"),
                      ("skills", "s1"), ("schedules",),
                      ("form_templates",), ("tiktoken-cache",),
                      ("uv-cache",), ("fetched",)):
            _mkdir(data_dir, *parts)
        db.conn.execute(
            "INSERT INTO projects (id, name) VALUES ('p1', 'P')")
        db.conn.commit()

        resp = client.get("/system/data/usage")

        assert resp.status_code == 200
        cats = resp.json()["categories"]
        assert set(cats) == {"logs", "knowledge", "settings", "models",
                             "tasks", "reports", "extras", "uv_cache",
                             "fetched"}
        assert all(cats[k]["cleanable"] for k in
                   ("logs", "knowledge", "settings", "models", "tasks",
                    "uv_cache", "fetched"))
        assert cats["reports"]["cleanable"] is False
        assert cats["extras"]["cleanable"] is False
        assert cats["knowledge"]["count"] == 1
        total = resp.json()["total_size"]
        assert total == sum(c["size"] for c in cats.values())

    def test_uv_cache_and_fetched_keys_absent_when_dirs_missing(self, env):
        client, _, _ = env
        cats = client.get("/system/data/usage").json()["categories"]
        assert "uv_cache" not in cats
        assert "fetched" not in cats


class TestCleanFetched:
    def test_removes_whole_dir(self, env):
        client, _, data_dir = env
        fetched = _mkdir(data_dir, "fetched")

        resp = client.delete("/system/data/fetched")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["cleaned_bytes"] > 0
        assert not os.path.isdir(fetched)

    def test_missing_dir_is_noop(self, env):
        client, _, _ = env
        resp = client.delete("/system/data/fetched")

        assert resp.status_code == 200
        assert resp.json() == {"success": True, "cleaned_bytes": 0}


class TestCleanTasks:
    def test_purges_archived_keeps_active(self, env):
        client, db, data_dir = env
        arch = _mkdir(data_dir, "tasks", "task-arch")
        act = _mkdir(data_dir, "tasks", "task-act")
        _insert_task(db, "task-arch", os.path.join(arch, "workspace"), status="archived")
        _insert_task(db, "task-act", os.path.join(act, "workspace"))

        resp = client.delete("/system/data/tasks")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["removed"] == 1
        assert not os.path.isdir(arch)
        assert os.path.isdir(act)
        # DB 行保留
        assert db.get_task("task-arch")["status"] == "archived"

    def test_active_tasks_untouched_when_no_archived(self, env):
        client, db, data_dir = env
        act = _mkdir(data_dir, "tasks", "task-act")
        _insert_task(db, "task-act", os.path.join(act, "workspace"))

        resp = client.delete("/system/data/tasks")

        assert resp.json() == {"success": True, "removed": 0}
        assert os.path.isdir(act)


class TestCleanUvCache:
    def test_removes_whole_dir(self, env):
        client, _, data_dir = env
        cache = _mkdir(data_dir, "uv-cache")

        resp = client.delete("/system/data/uv_cache")

        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert not os.path.isdir(cache)


class TestCleanModels:
    def test_gate_when_downloading(self, env, monkeypatch):
        client, _, data_dir = env
        _mkdir(data_dir, "models", "bge-m3")
        mm = MagicMock()
        mm.get_active_downloads.return_value = [{"name": "bge-m3"}]
        monkeypatch.setattr(deps, "model_manager", mm)

        resp = client.delete("/system/data/models")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert body["code"] == "MODEL_DOWNLOAD_IN_PROGRESS"
        assert os.path.isdir(os.path.join(str(data_dir), "models", "bge-m3"))

    def test_success_clears_dir_and_download_rows(self, env, monkeypatch):
        client, db, data_dir = env
        _mkdir(data_dir, "models", "bge-m3")
        db.conn.execute(
            "INSERT INTO model_downloads (name, status, started_at, updated_at)"
            " VALUES ('bge-m3', 'completed', 't', 't')")
        db.conn.commit()
        mm = MagicMock()
        mm.get_active_downloads.return_value = []
        monkeypatch.setattr(deps, "model_manager", mm)

        resp = client.delete("/system/data/models")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["cleaned_bytes"] > 0
        assert not os.path.exists(os.path.join(str(data_dir), "models", "bge-m3"))
        remain = db.conn.execute(
            "SELECT COUNT(*) AS c FROM model_downloads").fetchone()["c"]
        assert remain == 0


class TestCleanNotCleanable:
    def test_reports_rejected(self, env):
        client, _, _ = env
        resp = client.delete("/system/data/reports")
        assert resp.status_code == 200
        assert resp.json()["success"] is False

    def test_extras_rejected(self, env):
        client, _, _ = env
        resp = client.delete("/system/data/extras")
        assert resp.status_code == 200
        assert resp.json()["success"] is False


class TestCleanKnowledge:
    """B1（chroma 句柄关闭 + 目录删净验证）与 B4（reports 产物 /
    schedules target / ppts 行联动）回归。"""

    def test_removes_dirs_rows_and_unbinds_targets(self, env, monkeypatch):
        client, db, data_dir = env
        _mkdir(data_dir, "files", "p1")
        chroma = _mkdir(data_dir, "chroma")
        rep_dir = _mkdir(data_dir, "reports", "r1")
        db.conn.execute("INSERT INTO projects (id, name) VALUES ('p1', 'P')")
        db.conn.execute(
            "INSERT INTO reports (id, project_id, template_id, title,"
            " content_path, created_at, updated_at)"
            " VALUES ('r1', 'p1', 'tpl', 'R', ?, 't', 't')", (rep_dir,))
        db.conn.execute(
            "INSERT INTO ppts (id, project_id, task_id, title,"
            " created_at, updated_at)"
            " VALUES ('ppt1', 'p1', 'task1', 'D', 't', 't')")
        # 全局模板工坊行（project_id=''）：知识库清理不动
        db.conn.execute(
            "INSERT INTO ppts (id, project_id, task_id, title, kind,"
            " created_at, updated_at)"
            " VALUES ('ppt-tpl', '', 'task2', 'T', 'template', 't', 't')")
        now = "t"
        for sch_id, ttype, tid in (("sch1", "project", "p1"),
                                   ("sch2", "baselib", "bl1")):
            db.conn.execute(
                "INSERT INTO task_schedules (id, name, prompt, interval_type,"
                " interval_value, target_type, target_id, created_at, updated_at)"
                " VALUES (?, 's', 'p', 'daily', '09:00', ?, ?, ?, ?)",
                (sch_id, ttype, tid, now, now))
        db.conn.commit()
        dropped = []
        monkeypatch.setattr("knowledge.vector_store.drop_chroma_client",
                            lambda p: dropped.append(p))
        worker = MagicMock()
        monkeypatch.setattr(deps, "worker", worker)

        resp = client.delete("/system/data/knowledge")

        body = resp.json()
        assert resp.status_code == 200
        assert body["success"] is True
        assert "partial" not in body
        # 磁盘：三目录全删（含 reports 产物——B4 缺口回归）
        assert not os.path.exists(os.path.join(str(data_dir), "files"))
        assert not os.path.isdir(chroma)
        assert not os.path.isdir(rep_dir)
        # drop_chroma_client 被调用且参数为 chroma 目录（B1）
        assert dropped == [os.path.join(str(data_dir), "chroma")]
        # worker 单例缓存失效（High#1：不失效则后续解析打在已 stop 的
        # client 上，确定性失败直至重启）
        worker.reset_vector_store.assert_called_once_with()
        # 行：报告/文件/图片随清；projects 用户行清零后补种 2 个预置基础库
        # （目标态 = 初始状态：预置空容器在位，2026-10-20 复盘）
        for table in ("reports", "files", "images"):
            assert db.conn.execute(
                f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"] == 0
        remain_projects = db.conn.execute(
            "SELECT id FROM projects ORDER BY id").fetchall()
        assert [r["id"] for r in remain_projects] == ["bl-investment", "bl-recruitment"]
        remain_ppts = db.conn.execute(
            "SELECT id FROM ppts").fetchall()
        assert [r["id"] for r in remain_ppts] == ["ppt-tpl"]
        # 订阅 target：project 引用解绑、baselib 引用不动
        sch1 = db.get_schedule("sch1")
        sch2 = db.get_schedule("sch2")
        assert sch1["target_type"] is None and sch1["target_id"] is None
        assert sch2["target_type"] == "baselib"

    def test_partial_reported_when_dirs_locked(self, env, monkeypatch):
        client, _, data_dir = env
        chroma = _mkdir(data_dir, "chroma")
        # 模拟 Windows 句柄锁死：rmtree 静默失败（ignore_errors 吞掉），
        # 端点必须如实上报 partial 而非打假「已删除」日志（B1 缺陷 C）
        monkeypatch.setattr("routers.system.shutil.rmtree",
                            lambda *a, **k: None)

        resp = client.delete("/system/data/knowledge")

        body = resp.json()
        assert resp.status_code == 200
        assert body["success"] is True
        assert body["partial"] is True
        assert "chroma" in body["message"]
        assert os.path.isdir(chroma)


def _allow_reset(monkeypatch, dropped=None):
    """放行 reset-all 前置门（task_agent / model_manager 全闲）并 mock
    chroma 句柄关闭——真 drop 会 import chromadb 拖慢全量套件。

    env fixture 不预设这几个 deps（其余端点不触），reset-all 用例内 patch。
    返回 worker mock 供调用方断言向量库缓存失效（审查 High#1）。"""
    agent = MagicMock()
    agent.running_count.return_value = 0
    monkeypatch.setattr(deps, "task_agent", agent)
    mm = MagicMock()
    mm.get_active_downloads.return_value = []
    monkeypatch.setattr(deps, "model_manager", mm)
    worker = MagicMock()
    monkeypatch.setattr(deps, "worker", worker)
    monkeypatch.setattr(
        "knowledge.vector_store.drop_chroma_client",
        lambda p: dropped.append(p) if dropped is not None else None)
    return worker


class TestResetAll:
    """B3+B6（真·全清）：409 门 ×2 / 全表+磁盘清空+settings 回默认 /
    磁盘 partial 上报；worker 缓存失效（High#1）、task_templates 漏表
    回归、logs truncate 语义（M3）、schema 枚举比对防清单漂移（L6）。"""

    def test_gate_blocks_when_task_running(self, env, monkeypatch):
        client, _, data_dir = env
        _mkdir(data_dir, "files", "p1")
        agent = MagicMock()
        agent.running_count.return_value = 1
        monkeypatch.setattr(deps, "task_agent", agent)

        resp = client.post("/system/data/reset-all")

        body = resp.json()
        assert resp.status_code == 200
        assert body["success"] is False
        assert body["code"] == "RESET_BLOCKED_ACTIVE_WORK"
        assert "任务" in body["message"]
        assert os.path.isdir(os.path.join(str(data_dir), "files"))

    def test_gate_blocks_when_model_downloading(self, env, monkeypatch):
        client, _, _ = env
        agent = MagicMock()
        agent.running_count.return_value = 0
        monkeypatch.setattr(deps, "task_agent", agent)
        mm = MagicMock()
        mm.get_active_downloads.return_value = [{"name": "bge-m3"}]
        monkeypatch.setattr(deps, "model_manager", mm)

        resp = client.post("/system/data/reset-all")

        body = resp.json()
        assert resp.status_code == 200
        assert body["success"] is False
        assert body["code"] == "MODEL_DOWNLOAD_IN_PROGRESS"
        assert "bge-m3" in body["message"]

    def test_resets_tables_dirs_and_settings_defaults(self, env, monkeypatch):
        from routers.system import _RESET_TABLES

        client, db, data_dir = env
        # DB 行覆盖各 FK 层：父 projects/tasks + 子 reports + 独立表
        # settings/web_logins/llm_usage
        db.conn.execute("INSERT INTO projects (id, name) VALUES ('p1', 'P')")
        rep_dir = _mkdir(data_dir, "reports", "r1")
        # status 非 generating（DDL 默认即 generating），避开在飞门
        db.conn.execute(
            "INSERT INTO reports (id, project_id, template_id, title,"
            " content_path, status, created_at, updated_at)"
            " VALUES ('r1', 'p1', 'tpl', 'R', ?, 'completed', 't', 't')",
            (rep_dir,))
        act = _mkdir(data_dir, "tasks", "task-a")
        _insert_task(db, "task-a", os.path.join(act, "workspace"))
        db.conn.execute(
            "INSERT INTO web_logins (domain, note, created_at)"
            " VALUES ('example.com', 'n', 't')")
        db.conn.execute(
            "INSERT INTO llm_usage (ts, source, model, mode)"
            " VALUES ('t', 'task', 'm', 'full')")
        # 无 FK 的用户资产表（M2 漏表回归：漏列则重置后残留）
        db.conn.execute(
            "INSERT INTO task_templates (id, scope, category, title, prompt,"
            " created_at, updated_at)"
            " VALUES ('tpl_custom_x', 'global', 'info_gather', 'T', 'p',"
            " 't', 't')")
        db.conn.execute(
            "INSERT INTO settings (key, value) VALUES ('api_key', 'sk-test')")
        db.conn.commit()
        for parts in (("chroma",), ("files", "p1"), ("schedules",),
                      ("models", "bge-m3"), ("logs",), ("fetched",),
                      ("tiktoken-cache",), ("uv-cache",),
                      ("skills", "s1"), ("form_templates",)):
            _mkdir(data_dir, *parts)
        with open(os.path.join(str(data_dir), "logs", "app.log"), "w",
                  encoding="utf-8") as f:
            f.write("log line\n")
        dropped = []
        worker = _allow_reset(monkeypatch, dropped)

        resp = client.post("/system/data/reset-all")

        body = resp.json()
        assert resp.status_code == 200
        assert body["success"] is True
        assert "partial" not in body
        # chroma 句柄先关且参数为 chroma 目录（同 _clean_knowledge 语义）
        assert dropped == [os.path.join(str(data_dir), "chroma")]
        # worker 缓存失效（High#1：不失效则后续解析/编译确定性失败）
        worker.reset_vector_store.assert_called_once_with()
        # 清单防护：关键表在列（防后续维护误删导致悬挂行）
        for table in ("projects", "reports", "tasks", "settings",
                      "web_logins", "llm_usage", "task_schedules",
                      "task_templates"):
            assert table in _RESET_TABLES
        # 全部用户表清零：sqlite_master 枚举比对（L6——只遍历 _RESET_TABLES
        # 时清单漏表会盲过）。豁免：启动 seed 表、FTS5 影子表（chunks_fts_*，
        # 主表单独断言）
        all_tables = {r["name"] for r in db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
            " AND name NOT LIKE 'sqlite_%'").fetchall()}
        exempt = ({"wiki_schemas", "playbook_templates"}
                  | {n for n in all_tables if n.startswith("chunks_fts")})
        for table in all_tables - exempt:
            remain = db.conn.execute(
                f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
            # projects 例外：全清后补种 2 个预置基础库（目标态 = 初始状态，
            # 2026-10-20 复盘）——断言精确到这两行，防用户项目残留混过
            if table == "projects":
                rows = db.conn.execute(
                    "SELECT id FROM projects ORDER BY id").fetchall()
                assert [r["id"] for r in rows] == [
                    "bl-investment", "bl-recruitment"], table
                continue
            assert remain == 0, table
        # 清单与 schema 全集一致（漏表/多表都暴露）
        assert all_tables - exempt == set(_RESET_TABLES)
        # FTS 虚表清空（对齐 _clean_knowledge 口径）
        assert db.conn.execute(
            "SELECT COUNT(*) AS c FROM chunks_fts").fetchone()["c"] == 0
        # 磁盘产物目录全删净，DB 文件保留（schema 即结构）——logs 除外：
        # RotatingFileHandler 句柄被本进程持有，走 truncate 语义（M3）
        for name in ("chroma", "files", "reports", "schedules", "tasks",
                     "models", "skills", "form_templates", "tiktoken-cache",
                     "uv-cache", "fetched"):
            assert not os.path.exists(os.path.join(str(data_dir), name)), name
        assert os.path.exists(str(data_dir / "inkcube.db"))
        logs_dir = os.path.join(str(data_dir), "logs")
        assert os.path.isdir(logs_dir)
        assert not os.path.exists(os.path.join(logs_dir, "app.log"))
        # settings 清空 → get_settings 回默认值（B2 前端回灌的后端契约）
        assert db.get_settings()["api_key"] == ""

    def test_partial_when_dir_locked(self, env, monkeypatch):
        client, db, data_dir = env
        chroma = _mkdir(data_dir, "chroma")
        # 模拟 Windows 句柄锁死：rmtree 静默失败，端点如实上报 partial
        monkeypatch.setattr("routers.system.shutil.rmtree",
                            lambda *a, **k: None)
        _allow_reset(monkeypatch)

        resp = client.post("/system/data/reset-all")

        body = resp.json()
        assert resp.status_code == 200
        assert body["success"] is True
        assert body["partial"] is True
        assert "chroma" in body["message"]
        assert os.path.isdir(chroma)
        # DB 清理不受磁盘 partial 影响（照常清空）
        assert db.conn.execute(
            "SELECT COUNT(*) AS c FROM settings").fetchone()["c"] == 0
