"""python/tests/test_auto_wiki_compile.py — 事件驱动自动汇编回归（2.6 §4.1 路径 A）.

覆盖五组：
  1. TestGuardMatrix  — 六重守卫条件矩阵（开关/项目门/待编译集/活跃任务/冷却/就绪）
  2. TestCooldown     — 冷却写入（mark_user_stop）、过期懒清理、仅抑制自动路径
  3. TestStopEndpoint — stop 端点成功写冷却；404（无任务）/409（非活跃）不写；
                        冷却不拦手动触发
  4. TestParseEdge    — _process_file 解析完成边沿端到端（点火入队、冷却抑制、
                        触发失败不打断解析、磁盘缺失不点火）
  5. TestSettingsKey  — auto_wiki_compile 键：kv 缺省即真（存量无迁移）、回读、
                        SettingsModel 同构默认
"""
import asyncio
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wiki.auto_compile as auto_compile
from db.database import Database


@pytest.fixture(autouse=True)
def _reset_cooldown():
    """冷却为模块级进程内存态，用例间必须清零防串扰。"""
    auto_compile._COOLDOWN_UNTIL.clear()
    yield
    auto_compile._COOLDOWN_UNTIL.clear()


@pytest.fixture()
def db(tmp_path):
    database = Database(str(tmp_path / "auto_compile.db"))
    database.initialize()
    database.ensure_preset_base_libs()  # create_project 的 auto-link 依赖 preset 基础库
    yield database
    database.conn.close()


def _ready(monkeypatch, ok=True):
    """就绪判定旁路：守卫⑥与被测逻辑正交，按用例意图显式声明。"""
    monkeypatch.setattr(auto_compile, "_readiness_ready", lambda feature: ok)


def _project_with_done_file(db, name="项目甲"):
    pid = db.create_project(name)["id"]
    fid = db.create_file(pid, "a.pdf", ".pdf", 1, "h1")["id"]
    db.update_file_status(fid, "done")
    return pid


class TestGuardMatrix:
    """六重守卫：全过点火（trigger_type='auto'），任一不过静默跳过。"""

    def test_all_guards_pass_fires_task(self, db, monkeypatch):
        _ready(monkeypatch)
        pid = _project_with_done_file(db)
        task = auto_compile.try_auto_compile(db, pid)
        assert task is not None
        assert task["trigger_type"] == "auto"
        assert task["status"] == "queued"

    def test_active_task_guard_absorbs(self, db, monkeypatch):
        """守卫④：运行中编译逐轮重查待编译集合，新文件由当前轮吸收，不重复点火。"""
        _ready(monkeypatch)
        pid = _project_with_done_file(db)
        assert auto_compile.try_auto_compile(db, pid) is not None
        assert auto_compile.try_auto_compile(db, pid) is None
        cnt = db.conn.execute(
            "SELECT COUNT(*) AS cnt FROM wiki_compile_tasks WHERE project_id = ?",
            (pid,),
        ).fetchone()["cnt"]
        assert cnt == 1

    def test_switch_off_skips(self, db, monkeypatch):
        """守卫①：全局开关关闭（设置页可改），静默跳过。"""
        _ready(monkeypatch)
        pid = _project_with_done_file(db)
        db.save_settings({"auto_wiki_compile": False})
        assert auto_compile.try_auto_compile(db, pid) is None

    def test_base_lib_skipped(self, db, monkeypatch):
        """守卫②：基础库不编译（§1.3，kind 门）。"""
        _ready(monkeypatch)
        bl_id = db.conn.execute(
            "SELECT id FROM projects WHERE kind = 'base_lib' LIMIT 1"
        ).fetchone()["id"]
        fid = db.create_file(bl_id, "x.pdf", ".pdf", 1, "hx")["id"]
        db.update_file_status(fid, "done")
        assert auto_compile.try_auto_compile(db, bl_id) is None

    def test_archived_project_skipped(self, db, monkeypatch):
        """守卫②：归档项目不点火。"""
        _ready(monkeypatch)
        pid = _project_with_done_file(db)
        assert db.archive_project(pid)
        assert auto_compile.try_auto_compile(db, pid) is None

    def test_no_pending_files_skipped(self, db, monkeypatch):
        """守卫③：待编译集合为空（文件仍 pending，未入集合）不点火。"""
        _ready(monkeypatch)
        pid = db.create_project("空项目")["id"]
        db.create_file(pid, "a.pdf", ".pdf", 1, "h1")
        assert auto_compile.try_auto_compile(db, pid) is None

    def test_not_ready_skips(self, db, monkeypatch):
        """守卫⑥：LLM/嵌入未就绪静默跳过（配置引导由人工入口 guard 承担）。"""
        _ready(monkeypatch, ok=False)
        pid = _project_with_done_file(db)
        assert auto_compile.try_auto_compile(db, pid) is None

    def test_readiness_error_treated_as_not_ready(self, db, monkeypatch):
        """守卫⑥纵深：就绪判定自身异常视为未就绪，绝不打断解析链路。"""
        import readiness

        def _boom():
            raise RuntimeError("readiness exploded")

        monkeypatch.setattr(readiness, "get_features_cached", _boom)
        pid = _project_with_done_file(db)
        assert auto_compile.try_auto_compile(db, pid) is None


class TestCooldown:
    """冷却语义：进程内存态、懒清理、仅抑制自动路径。"""

    def test_mark_user_stop_blocks_auto(self, db, monkeypatch):
        _ready(monkeypatch)
        pid = _project_with_done_file(db)
        auto_compile.mark_user_stop(pid)
        assert auto_compile.in_cooldown(pid)
        assert auto_compile.try_auto_compile(db, pid) is None

    def test_cooldown_expires_with_lazy_cleanup(self, db, monkeypatch):
        """过期冷却放行点火，且顺带懒清理字典项（无定时器依赖）。"""
        _ready(monkeypatch)
        pid = _project_with_done_file(db)
        auto_compile._COOLDOWN_UNTIL[pid] = time.time() - 1
        assert not auto_compile.in_cooldown(pid)
        assert pid not in auto_compile._COOLDOWN_UNTIL
        assert auto_compile.try_auto_compile(db, pid) is not None


@pytest.fixture()
def wiki_client_and_db(monkeypatch, tmp_path):
    """单 router 挂载 + deps.db 注入（test_knowledge_archive_api 同款口径，
    不拉起 main lifespan；get_db() 经 import deps 读注入实例）。"""
    import deps
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import wiki as wiki_router

    database = Database(str(tmp_path / "wiki_stop.db"))
    database.initialize()
    database.ensure_preset_base_libs()
    monkeypatch.setattr(deps, "db", database)
    app = FastAPI()
    app.include_router(wiki_router.router)
    yield TestClient(app), database
    database.conn.close()


class TestStopEndpoint:
    """stop 端点 × 冷却联动：成功写冷却，404/409 不写；手动触发不受冷却限制。"""

    def test_stop_writes_cooldown(self, wiki_client_and_db):
        client, db = wiki_client_and_db
        pid = db.create_project("项目乙")["id"]
        db.create_wiki_compile_task(pid, trigger_type="manual")  # queued，活跃
        r = client.post(f"/wiki/projects/{pid}/compile/stop")
        assert r.status_code == 200
        assert r.json()["success"] is True
        assert auto_compile.in_cooldown(pid)

    def test_stop_without_task_404_no_cooldown(self, wiki_client_and_db):
        """无任务 404 早退：不写冷却（用户未停止任何东西）。"""
        client, db = wiki_client_and_db
        pid = db.create_project("项目丙")["id"]
        assert client.post(f"/wiki/projects/{pid}/compile/stop").status_code == 404
        assert not auto_compile.in_cooldown(pid)

    def test_stop_completed_task_409_no_cooldown(self, wiki_client_and_db):
        """非活跃任务 409 早退：不写冷却。"""
        client, db = wiki_client_and_db
        pid = db.create_project("项目丁")["id"]
        tid = db.create_wiki_compile_task(pid, trigger_type="manual")["id"]
        db.update_compile_task_status(tid, "completed")
        assert client.post(f"/wiki/projects/{pid}/compile/stop").status_code == 409
        assert not auto_compile.in_cooldown(pid)

    def test_cooldown_does_not_block_manual_trigger(self, wiki_client_and_db, monkeypatch):
        """冷却仅抑制自动点火；人工触发端点即刻可用（§4.1 停止语义另一半）。
        端点级证据：trigger_compile 全链无冷却判定（结构性保证，非约定）——
        guard 只验就绪（与冷却正交，测试旁路），任务照常创建。"""
        client, db = wiki_client_and_db
        pid = db.create_project("手动项目")["id"]
        fid = db.create_file(pid, "a.pdf", ".pdf", 1, "h1")["id"]
        db.update_file_status(fid, "done")
        auto_compile.mark_user_stop(pid)
        assert auto_compile.in_cooldown(pid)  # 冷却已激活——请求在冷却期内发出
        import routers.wiki as wiki_router
        monkeypatch.setattr(wiki_router, "ensure_ready", lambda feature: None)
        r = client.post(
            f"/wiki/projects/{pid}/compile", json={"trigger_type": "manual"})
        assert r.status_code == 200
        task = r.json()["task"]
        assert task["trigger_type"] == "manual"
        assert task["status"] == "queued"


class _StubPipeline:
    """替身 pipeline：落 done 状态 + 返回成功摘要（真实 pipeline 行为的最小面，
    规避模型/嵌入依赖；_process_file 仅消费 success/chunks_count/images_count）。"""

    def __init__(self, db_conn=None, chroma_persist_dir=None, models_dir=None):
        self.db_conn = db_conn

    async def process_file(self, file_path, project_id, file_id, filename):
        self.db_conn.update_file_status(file_id, "done", chunk_count=1)
        return {"success": True, "chunks_count": 1, "images_count": 0}


@pytest.fixture()
def worker_and_db(tmp_path, monkeypatch):
    """手工构造 KnowledgeWorker（绕过 __init__ 的模型/LLM 依赖，
    test_wiki_continuous_compile 同款），仅注入 _process_file 触达的属性。"""
    import knowledge.task_queue as tq
    from knowledge.task_queue import KnowledgeWorker

    database = Database(str(tmp_path / "edge.db"))
    database.initialize()
    database.ensure_preset_base_libs()
    worker = KnowledgeWorker.__new__(KnowledgeWorker)
    worker.db = database
    worker.data_dir = str(tmp_path)
    worker._compile_queue = asyncio.Queue()
    worker._compile_enqueued = set()
    monkeypatch.setattr(tq, "ProcessingPipeline", _StubPipeline)
    yield worker, database
    database.conn.close()


def _seed_pending_file_on_disk(db, data_dir):
    """_process_file 前置守卫要求：文件 status='pending' 且磁盘文件存在
    （done 状态由 StubPipeline 内部写入，模拟真实 pipeline 行为）。"""
    pid = db.create_project("边沿项目")["id"]
    fid = db.create_file(pid, "a.pdf", ".pdf", 1, "h1")["id"]
    disk = Path(data_dir) / "files" / pid / fid / "a.pdf"
    disk.parent.mkdir(parents=True)
    disk.write_bytes(b"%PDF-1.4 stub")
    return pid, fid


class TestParseEdge:
    """解析完成边沿端到端：_process_file 成功分支自动点火并入队。"""

    def test_parse_done_fires_and_enqueues(self, worker_and_db, monkeypatch):
        worker, db = worker_and_db
        _ready(monkeypatch)
        pid, fid = _seed_pending_file_on_disk(db, worker.data_dir)
        asyncio.run(worker._process_file(fid))
        assert db.get_file(fid)["status"] == "done"
        task = db.get_latest_compile_task(pid)
        assert task is not None
        assert task["trigger_type"] == "auto"
        assert worker._compile_queue.qsize() == 1

    def test_cooldown_suppresses_edge_fire(self, worker_and_db, monkeypatch):
        """冷却抑制边沿点火：解析照常落 done，但不创建任务不入队。"""
        worker, db = worker_and_db
        _ready(monkeypatch)
        pid, fid = _seed_pending_file_on_disk(db, worker.data_dir)
        auto_compile.mark_user_stop(pid)
        asyncio.run(worker._process_file(fid))
        assert db.get_file(fid)["status"] == "done"
        assert db.get_latest_compile_task(pid) is None
        assert worker._compile_queue.qsize() == 0

    def test_trigger_failure_does_not_break_parse(self, worker_and_db, monkeypatch):
        """触发链路异常仅告警：解析主流程不受影响（旁路隔离不变量）。"""
        worker, db = worker_and_db
        _ready(monkeypatch)
        pid, fid = _seed_pending_file_on_disk(db, worker.data_dir)

        def _boom(db_, project_id):
            raise RuntimeError("trigger exploded")

        monkeypatch.setattr(auto_compile, "try_auto_compile", _boom)
        asyncio.run(worker._process_file(fid))
        assert db.get_file(fid)["status"] == "done"
        assert db.get_latest_compile_task(pid) is None

    def test_disk_missing_marks_error_no_trigger(self, worker_and_db, monkeypatch):
        """磁盘缺失前置守卫：文件标 error，无点火（失败边沿不在触发范围）。"""
        worker, db = worker_and_db
        _ready(monkeypatch)
        pid = db.create_project("缺盘项目")["id"]
        fid = db.create_file(pid, "a.pdf", ".pdf", 1, "h1")["id"]
        asyncio.run(worker._process_file(fid))
        assert db.get_file(fid)["status"] == "error"
        assert db.get_latest_compile_task(pid) is None


class TestSettingsKey:
    """auto_wiki_compile 键：kv 缺省即真（存量升级零迁移）、回读、Model 同构。"""

    def test_default_on_in_fresh_db(self, tmp_path):
        fresh = Database(str(tmp_path / "fresh.db"))
        fresh.initialize()
        try:
            assert fresh.get_settings()["auto_wiki_compile"] is True
        finally:
            fresh.conn.close()

    def test_save_and_read_roundtrip(self, db):
        db.save_settings({"auto_wiki_compile": False})
        assert db.get_settings()["auto_wiki_compile"] is False
        db.save_settings({"auto_wiki_compile": True})
        assert db.get_settings()["auto_wiki_compile"] is True

    def test_settings_model_default_true(self):
        from schemas import SettingsModel

        assert SettingsModel().auto_wiki_compile is True
