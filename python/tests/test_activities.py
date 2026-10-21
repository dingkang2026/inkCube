"""python/tests/test_activities.py — Activity Hub + Readiness 后端单测（0.4lifecycle-design.md §3.2 / §7）。

覆盖：
  A. DB 通知层（§3.2）：add_notification 未读查重 / unread 正序 / recent 倒序 / mark_read / cleanup
  B. model_downloads 层（§3.3）：状态记录 upsert / 崩溃恢复 downloading→failed
  C. /activities/status 聚合（§3.6）：五类活跃项 + summary 计数 + notifications 段 + mark-read 端点
  C2. 通知中心（2.11 §3.4）：全量分页（含已读）/ limit 钳制 / read-all 幂等 / 单条删除幂等
  D. Onboarding 标记（§7.3 第一层）：get_flag/set_flag + GET /onboarding + POST /onboarding/done
  E. Readiness 矩阵与入口校验（§7.2/§7.3 第三层）：compute_features + ensure_ready 错误映射

async 端点统一用 asyncio.run 驱动（不引入 pytest-asyncio 依赖）。
"""
import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import deps
from db.database import Database
from errors import EmbeddingModelMissing, LLMNotConfigured, ParsingModelMissing
import readiness
import activity_events
import scheduler
from routers import activities as activities_router
from routers import settings as settings_router


@pytest.fixture
def db():
    database = Database(db_path=":memory:")
    database.initialize()
    return database


@pytest.fixture
def project_id(db):
    return db.create_project(name="Activity Test", project_type="general")["id"]


@pytest.fixture(autouse=True)
def isolate_deps(db, monkeypatch):
    """每个用例用独立 in-memory DB / 无外部服务依赖。"""
    monkeypatch.setattr(deps, "db", db)
    monkeypatch.setattr(deps, "task_agent", None, raising=False)
    monkeypatch.setattr(deps, "model_manager", None, raising=False)
    # readiness 模块级缓存重置，避免用例间污染
    monkeypatch.setattr(readiness, "_features_cache", None)


# ── A. DB 通知层（§3.2）──────────────────────────────────────


class TestNotifications:
    def test_add_and_list_unread_ascending(self, db):
        first = db.add_notification("file", "f1", "p1", "completed", "a.pdf")
        second = db.add_notification("file", "f2", "p1", "failed", "b.pdf", detail="boom")

        unread = db.list_unread_notifications()
        assert [n["id"] for n in unread] == [first, second]  # 正序（逐条弹出）
        assert unread[0]["entity_title"] == "a.pdf"
        assert unread[1]["detail"] == "boom"
        assert unread[0]["read_at"] is None

    def test_unread_dedup_same_entity_kind(self, db):
        """未读查重：同 (entity_type, entity_id, kind) 未读存在时跳过。"""
        assert db.add_notification("wiki_compile", "wc1", "p1", "completed", "Wiki") is not None
        assert db.add_notification("wiki_compile", "wc1", "p1", "completed", "Wiki") is None
        # 不同 kind 不查重
        assert db.add_notification("wiki_compile", "wc1", "p1", "failed", "Wiki") is not None
        assert len(db.list_unread_notifications()) == 2

    def test_dedup_released_after_read(self, db):
        nid = db.add_notification("task", "t1", None, "completed", "任务A")
        assert db.mark_notifications_read([nid]) == 1
        # 已读后同一实体可再写（新一轮终态）
        assert db.add_notification("task", "t1", None, "completed", "任务A") is not None

    def test_list_recent_descending_with_limit(self, db):
        ids = [db.add_notification("report", f"r{i}", "p1", "completed", f"报告{i}") for i in range(5)]
        recent = db.list_recent_notifications(3)
        recent_ids = [n["id"] for n in recent]
        assert len(recent_ids) == 3
        assert set(recent_ids) == set(ids[-3:])  # 最新 3 条
        assert recent_ids[0] == max(ids)  # 首条为最新（同秒时按 id DESC 兑现）

    def test_mark_read_counts_only_unread(self, db):
        n1 = db.add_notification("file", "f1", "p1", "completed", "a.pdf")
        n2 = db.add_notification("file", "f2", "p1", "completed", "b.pdf")
        assert db.mark_notifications_read([n1]) == 1
        # n1 已读 → 二次标记不计入
        assert db.mark_notifications_read([n1, n2]) == 1
        assert db.list_unread_notifications() == []
        assert db.mark_notifications_read([]) == 0

    def test_cleanup_keeps_latest(self, db):
        for i in range(6):
            db.add_notification("file", f"f{i}", "p1", "completed", f"file{i}")
        removed = db.cleanup_old_notifications(keep=3)
        assert removed == 3
        recent = db.list_recent_notifications(10)
        assert len(recent) == 3

    def test_delete_notification_idempotent(self, db):
        """单条删除（2.11 §3.4）：存在删除返回 1，不存在幂等返回 0，列表同步收缩。"""
        nid = db.add_notification("file", "f1", "p1", "completed", "a.pdf")
        assert db.delete_notification(nid) == 1
        assert db.delete_notification(nid) == 0  # 已删，幂等
        _, total = db.list_notifications()
        assert total == 0


# ── B. model_downloads 层（§3.3）─────────────────────────────


class TestModelDownloads:
    def test_mark_state_upsert(self, db):
        db.mark_model_download_state("bge-m3", "downloading")
        db.mark_model_download_state("bge-m3", "completed")

        stale = db.recover_stale_model_downloads()
        assert stale == []  # completed 不算崩溃遗留

    def test_recover_stale_downloading(self, db):
        db.mark_model_download_state("bge-m3", "downloading")
        db.mark_model_download_state("docling-layout-heron", "downloading")
        db.mark_model_download_state("paddle-ocr", "failed", error="network")

        stale = db.recover_stale_model_downloads()
        assert {row["name"] for row in stale} == {"bge-m3", "docling-layout-heron"}

        row = db.conn.execute(
            "SELECT status, error FROM model_downloads WHERE name = 'bge-m3'"
        ).fetchone()
        assert row["status"] == "failed"
        assert row["error"]  # 崩溃说明非空
        # 非 downloading 不受影响
        row2 = db.conn.execute(
            "SELECT status FROM model_downloads WHERE name = 'paddle-ocr'"
        ).fetchone()
        assert row2["status"] == "failed"


# ── C. /activities/status 聚合（§3.6）────────────────────────


def _insert_compile_task(db, project_id, task_id, status, pages_total=10, pages_completed=3):
    db.conn.execute(
        "INSERT INTO wiki_compile_tasks (id, project_id, trigger_type, status, pages_total, "
        "pages_completed, created_at) VALUES (?, ?, 'manual', ?, ?, ?, datetime('now'))",
        (task_id, project_id, status, pages_total, pages_completed),
    )
    db.conn.commit()


def _insert_report(db, project_id, report_id, status, progress=40):
    db.conn.execute(
        "INSERT INTO reports (id, project_id, template_id, title, output_format, status, "
        "progress, content_path, created_at, updated_at) "
        "VALUES (?, ?, 'tpl', ?, 'Word', ?, ?, '/tmp/x', datetime('now'), datetime('now'))",
        (report_id, project_id, f"报告-{report_id}", status, progress),
    )
    db.conn.commit()


def _insert_base_lib(db, lib_id: str, name: str = "共享资料库",
                     lib_type: str = "preset_test") -> str:
    """直插 base_lib 行（原 create_base_lib 方法的测试替身，2.9 D4 后方法已删）。"""
    db.conn.execute(
        "INSERT INTO projects (id, name, description, kind, lib_type, created_at, updated_at) "
        "VALUES (?, ?, '', 'base_lib', ?, datetime('now'), datetime('now'))",
        (lib_id, name, lib_type),
    )
    db.conn.commit()
    return lib_id


class TestActivitiesStatus:
    def test_active_files_grouped_with_image_describing(self, db, project_id):
        f1 = db.create_file(project_id, "a.pdf", ".pdf", 100, "h1")["id"]
        f2 = db.create_file(project_id, "b.pdf", ".pdf", 100, "h2")["id"]
        f3 = db.create_file(project_id, "c.pdf", ".pdf", 100, "h3")["id"]
        # f1/f2 解析中；f3 解析完成但图片描述中（仍算活跃，§3.4）
        db.update_file_status(f1, "parsing")
        db.update_file_status(f2, "pending")
        db.update_file_status(f3, "done")
        db.conn.execute("UPDATE files SET image_status = 'describing' WHERE id = ?", (f3,))
        db.conn.commit()

        rows = activities_router._active_files(db)
        assert len(rows) == 1  # 同项目聚合
        row = rows[0]
        assert row["entity_type"] == "file"
        assert row["count"] == 3
        assert row["image_describing"] == 1

    def test_active_files_stopped_queues_excluded(self, db, project_id):
        """停止态口径（0.4 §队列暂停修订）：排队中（pending）不算活跃，
        只有在飞（parsing / describing）保留；恢复后心跳重新点亮。"""
        f_pend = db.create_file(project_id, "queued.pdf", ".pdf", 100, "h1")["id"]
        f_parsing = db.create_file(project_id, "parsing.pdf", ".pdf", 100, "h2")["id"]
        f_img_pend = db.create_file(project_id, "imgpend.pdf", ".pdf", 100, "h3")["id"]
        f_img_desc = db.create_file(project_id, "imgdesc.pdf", ".pdf", 100, "h4")["id"]
        db.update_file_status(f_pend, "pending")
        db.update_file_status(f_parsing, "parsing")
        db.update_file_status(f_img_pend, "done")
        db.update_file_status(f_img_desc, "done")
        db.conn.execute("UPDATE files SET image_status = 'pending' WHERE id = ?", (f_img_pend,))
        db.conn.execute("UPDATE files SET image_status = 'describing' WHERE id = ?", (f_img_desc,))
        db.set_flag("parsing_paused", True)
        db.set_flag("describe_paused", True)
        db.conn.commit()

        rows = activities_router._active_files(db)
        assert len(rows) == 1
        row = rows[0]
        assert row["count"] == 2  # 仅在飞：parsing 文件 + describing 图片所在文件
        assert row["image_describing"] == 1  # describing 保留；pending 图片不计

        # 恢复（flag 翻转）→ 排队中重新点亮，心跳下一拍回到全量口径
        db.set_flag("parsing_paused", False)
        db.set_flag("describe_paused", False)
        rows = activities_router._active_files(db)
        assert rows[0]["count"] == 4
        assert rows[0]["image_describing"] == 2

    def test_active_files_excludes_terminal(self, db, project_id):
        f1 = db.create_file(project_id, "done.pdf", ".pdf", 100, "h1")["id"]
        db.update_file_status(f1, "done")
        assert activities_router._active_files(db) == []

    def test_active_compiles_only_active_statuses(self, db, project_id):
        _insert_compile_task(db, project_id, "wc_active", "ingesting", pages_total=5, pages_completed=2)
        _insert_compile_task(db, project_id, "wc_storing", "storing")
        _insert_compile_task(db, project_id, "wc_done", "completed")
        _insert_compile_task(db, project_id, "wc_failed", "failed")

        rows = activities_router._active_compiles(db)
        assert {r["entity_id"] for r in rows} == {"wc_active", "wc_storing"}
        active = next(r for r in rows if r["entity_id"] == "wc_active")
        assert active["progress_text"] == "2/5"

    def test_active_reports_generating_only(self, db, project_id):
        _insert_report(db, project_id, "r_gen", "generating", progress=55)
        _insert_report(db, project_id, "r_done", "done")

        rows = activities_router._active_reports(db)
        assert len(rows) == 1
        assert rows[0]["entity_id"] == "r_gen"
        assert rows[0]["progress"] == 55

    def test_active_tasks_and_models_empty_without_deps(self, db):
        """task_agent / model_manager 未初始化 → 空列表（启动早期不崩）。"""
        assert activities_router._active_tasks(db) == []
        assert activities_router._active_model_downloads() == []

    def test_active_tasks_exclude_ppt_origin(self, db, monkeypatch, tmp_path):
        """PPT 借道任务（origin='ppt'）不入 task 源（2.3 审核项 #2）：
        PPT 活动已由 ppt 源（fetch_active）呈现，双源上报造成首页双条目
        与 running_tasks 假角标；chat 任务照常上报。
        data_dir 必须传 tmp_path：空串会被 create_task 拼成相对路径
        `tasks/<uuid>`，在 pytest cwd（项目根）下建目录污染仓库。"""
        chat_id = db.create_task("对话任务", data_dir=str(tmp_path), workspace_path="")['id']
        ppt_id = db.create_task("PPT 生成", data_dir=str(tmp_path), workspace_path="", origin="ppt")['id']
        agent = SimpleNamespace(_running_task_ids={chat_id, ppt_id})
        monkeypatch.setattr(deps, "task_agent", agent, raising=False)

        rows = activities_router._active_tasks(db)
        assert [r["entity_id"] for r in rows] == [chat_id]
        assert rows[0]["entity_type"] == "task"
        assert rows[0]["entity_title"] == "对话任务"

    def test_active_tasks_exclude_shadow_schedule(self, db, monkeypatch, tmp_path):
        """影子任务（schedule_id 非空，定时订阅执行）不入 task 源（2.11 D2 复核）：
        订阅执行由 schedule 源（scheduler.fetch_active）呈现；影子任务不进
        人工任务列表（服务端排除），混入 task 源会造成任务假角标与点击落
        任务页扑空。chat 任务照常。
        data_dir 必须传 tmp_path（同 test_active_tasks_exclude_ppt_origin）。"""
        sch = db.create_schedule("周报订阅", "p", "general", "general",
                                 "weekly", "MON 09:00")
        chat_id = db.create_task("对话任务", data_dir=str(tmp_path), workspace_path="")['id']
        shadow_id = db.create_task("订阅期次", data_dir=str(tmp_path), workspace_path="",
                                   schedule_id=sch["id"])['id']
        agent = SimpleNamespace(_running_task_ids={chat_id, shadow_id})
        monkeypatch.setattr(deps, "task_agent", agent, raising=False)

        rows = activities_router._active_tasks(db)
        assert [r["entity_id"] for r in rows] == [chat_id]

    def test_active_schedules_running_only(self, db):
        """schedule 源（scheduler.fetch_active）：仅 running 期次呈现，
        done/failed/skipped/missed 终态不入；entity_title 取订阅名。"""
        sch = db.create_schedule("周报订阅", "p", "general", "general",
                                 "weekly", "MON 09:00")
        db.insert_schedule_run("run_live", sch["id"], None, status="running")
        db.insert_schedule_run("run_done", sch["id"], None, status="done")
        db.insert_schedule_run("run_fail", sch["id"], None, status="failed")

        rows = scheduler.fetch_active(db)
        assert [r["entity_id"] for r in rows] == ["run_live"]
        assert rows[0]["entity_type"] == "schedule"
        assert rows[0]["entity_title"] == "周报订阅"

    def test_status_summary_counts_running_schedules(self, db):
        """summary.running_schedules 计数 + schedule 条目进 active 快照
        （0.4 §8.1 台账 schedule kind；影子任务不再计入 running_tasks）。"""
        sch = db.create_schedule("日报", "p", "general", "general",
                                 "daily", "09:00")
        db.insert_schedule_run("run_s1", sch["id"], None, status="running")

        result = asyncio.run(activities_router.activities_status())

        assert result["summary"]["running_schedules"] == 1
        assert result["summary"]["running_tasks"] == 0
        assert any(a["entity_type"] == "schedule" for a in result["active"])

    def test_status_summary_and_notifications(self, db, project_id):
        db.create_file(project_id, "a.pdf", ".pdf", 100, "h1")
        _insert_compile_task(db, project_id, "wc1", "ingesting", pages_total=3, pages_completed=1)
        _insert_compile_task(db, project_id, "wc2", "completed", pages_total=2, pages_completed=2)
        _insert_report(db, project_id, "r1", "generating")
        db.add_notification("file", "fx", project_id, "completed", "a.pdf")

        result = asyncio.run(activities_router.activities_status())

        assert result["summary"]["active_files"] == 1
        assert result["summary"]["active_compiles"] == 1  # 终态不计
        assert result["summary"]["generating_reports"] == 1
        assert result["summary"]["running_tasks"] == 0
        assert result["summary"]["downloading_models"] == 0

        assert len(result["notifications"]["unread"]) == 1
        assert result["notifications"]["recent"][0]["entity_title"] == "a.pdf"

    def test_aggregation_failure_degrades_to_empty(self, db, monkeypatch):
        """聚合异常降级：active 为空 + summary 全 0，不抛 500（§6.1）。"""
        def boom(_db):
            raise RuntimeError("db exploded")

        monkeypatch.setattr(activities_router, "_active_files", boom)
        result = asyncio.run(activities_router.activities_status())
        assert result["active"] == []
        assert result["summary"]["active_files"] == 0

    def test_mark_read_endpoint(self, db):
        nid = db.add_notification("file", "f1", "p1", "completed", "a.pdf")
        req = activities_router.MarkReadRequest(ids=[nid])
        result = asyncio.run(activities_router.mark_notifications_read(req))
        assert result == {"ok": True, "marked": 1}
        assert db.list_unread_notifications() == []


class TestNotificationCenter:
    """通知中心历史面（2.11 §3.4）：全量分页（含已读）+ 一键全部已读。"""

    def test_list_notifications_pagination_and_total(self, db):
        ids = [db.add_notification("file", f"f{i}", "p1", "completed", f"a{i}.pdf") for i in range(5)]
        items, total = db.list_notifications(0, 2)
        assert total == 5
        assert [n["id"] for n in items] == [ids[4], ids[3]]  # 最新优先（created_at DESC, id DESC）
        items2, _ = db.list_notifications(2, 2)
        assert [n["id"] for n in items2] == [ids[2], ids[1]]

    def test_list_notifications_includes_read(self, db):
        """历史面含已读（与 unread 流的核心差异，§3.4）。"""
        nid = db.add_notification("file", "f1", "p1", "completed", "a.pdf")
        db.mark_notifications_read([nid])
        items, total = db.list_notifications(0, 50)
        assert total == 1
        assert items[0]["read_at"] is not None

    def test_mark_all_notifications_read_idempotent(self, db):
        db.add_notification("file", "f1", "p1", "completed", "a.pdf")
        db.add_notification("file", "f2", "p1", "completed", "b.pdf")
        nid = db.add_notification("file", "f3", "p1", "completed", "c.pdf")
        db.mark_notifications_read([nid])
        assert db.mark_all_notifications_read() == 2  # 只清未读
        assert db.list_unread_notifications() == []
        assert db.mark_all_notifications_read() == 0  # 幂等

    def test_list_endpoint_offset_and_limit_clamp(self, db):
        for i in range(3):
            db.add_notification("file", f"f{i}", "p1", "completed", f"a{i}.pdf")
        result = asyncio.run(activities_router.list_notifications(offset=0, limit=2))
        assert result["total"] == 3
        assert len(result["items"]) == 2
        # limit 钳制：>200 取 200（此处 3 条全量）；<1 取 1（防误用拉爆）
        result_all = asyncio.run(activities_router.list_notifications(offset=0, limit=9999))
        assert len(result_all["items"]) == 3
        result_min = asyncio.run(activities_router.list_notifications(offset=0, limit=0))
        assert len(result_min["items"]) == 1

    def test_read_all_endpoint(self, db):
        db.add_notification("file", "f1", "p1", "completed", "a.pdf")
        db.add_notification("task", "t1", None, "failed", "任务A")
        result = asyncio.run(activities_router.read_all_notifications())
        assert result == {"ok": True, "marked": 2}
        assert db.list_unread_notifications() == []

    def test_delete_endpoint_idempotent(self, db):
        """单条删除端点（2.11 §3.4）：首次 deleted=1，重删幂等 deleted=0，total 同步收缩。"""
        nid = db.add_notification("file", "f1", "p1", "completed", "a.pdf")
        db.add_notification("task", "t1", None, "failed", "任务A")
        result = asyncio.run(activities_router.delete_notification(nid))
        assert result == {"ok": True, "deleted": 1}
        result_again = asyncio.run(activities_router.delete_notification(nid))
        assert result_again == {"ok": True, "deleted": 0}
        _, total = db.list_notifications()
        assert total == 1


class TestContainerRouting:
    """基础库容器闭环（0.4 §3.6）：活跃项/终态通知携带 container，前端导航分流取数。"""

    def test_active_files_carry_container(self, db, project_id):
        lib_id = _insert_base_lib(db, "lib-test-1")
        f_proj = db.create_file(project_id, "a.pdf", ".pdf", 100, "h1")["id"]
        f_lib = db.create_file(lib_id, "b.pdf", ".pdf", 100, "h2")["id"]
        db.update_file_status(f_proj, "parsing")
        db.update_file_status(f_lib, "parsing")

        by_project = {r["project_id"]: r["container"] for r in activities_router._active_files(db)}
        assert by_project[project_id] == "project"
        assert by_project[lib_id] == "baselib"

    def test_file_terminal_notification_carries_container(self, db, project_id):
        """file 终态通知经 activity_events 显式声明（E2，0.4 §6.4），JOIN projects.kind 取 container。"""
        lib_id = _insert_base_lib(db, "lib-test-1")
        f_lib = db.create_file(lib_id, "b.pdf", ".pdf", 100, "h2")["id"]
        db.update_file_status(f_lib, "done")
        activity_events.complete_file(db, f_lib)

        notifications = db.list_unread_notifications()
        assert len(notifications) == 1
        assert notifications[0]["container"] == "baselib"

    def test_report_terminal_notification_carries_container(self, db, project_id):
        """报告终态通知经 activity_events 显式声明（E2，顺序 4），JOIN projects.kind 取 container。"""
        lib_id = _insert_base_lib(db, "lib-test-1")
        _insert_report(db, lib_id, "r_lib", "generating")
        activity_events.complete_report(db, "r_lib")

        notifications = db.list_unread_notifications()
        assert len(notifications) == 1
        assert notifications[0]["container"] == "baselib"

    def test_container_defaults_to_project(self, db):
        """无项目实体（task / model_download）不传 container → 落默认值 'project'。"""
        db.add_notification("task", "t1", None, "completed", "任务A")
        assert db.list_unread_notifications()[0]["container"] == "project"


# ── D. Onboarding 标记（§7.3 第一层）────────────────────────


class TestOnboarding:
    def test_flag_roundtrip_and_default(self, db):
        assert db.get_flag("onboarding_done") is False  # 默认未完成
        db.set_flag("onboarding_done", True)
        assert db.get_flag("onboarding_done") is True
        db.set_flag("onboarding_done", False)
        assert db.get_flag("onboarding_done") is False

    def test_flag_invalid_json_falls_back_to_default(self, db):
        db.conn.execute(
            "INSERT INTO settings (key, value) VALUES ('onboarding_done', 'not-json{')"
        )
        db.conn.commit()
        assert db.get_flag("onboarding_done") is False
        assert db.get_flag("onboarding_done", default=True) is True

    def test_flag_does_not_leak_into_get_settings(self, db):
        """onboarding_done 不在 get_settings 白名单内，不污染设置模型。"""
        db.set_flag("onboarding_done", True)
        assert "onboarding_done" not in db.get_settings()

    def test_onboarding_endpoints(self, db):
        assert asyncio.run(settings_router.get_onboarding()) == {"done": False}
        asyncio.run(settings_router.set_onboarding_done())
        assert asyncio.run(settings_router.get_onboarding()) == {"done": True}


# ── E. Readiness 矩阵与入口校验（§7.2/§7.3 第三层）─────────


ALL_OK = {
    "llm": {"status": "ok"},
    "vlm": {"status": "ok"},
    "web_search": {"status": "ok"},
    "embedding": {"status": "ok"},
    "parsing": {"status": "ok"},
}


class TestComputeFeatures:
    def test_all_ready(self):
        features = readiness.compute_features(ALL_OK)
        assert all(f["ready"] for f in features.values())
        assert all(f["missing"] == [] for f in features.values())

    def test_missing_llm_blocks_task(self):
        services = {**ALL_OK, "llm": {"status": "error"}}
        features = readiness.compute_features(services)
        assert features["task"]["ready"] is False
        assert features["task"]["missing"][0]["id"] == "llm"
        assert features["task"]["missing"][0]["action"] == "settings:external"

    def test_missing_embedding_blocks_search_only_vlm_optional(self):
        """VLM 缺失只影响 file_parsing 可选段（§7.2 矩阵）。"""
        services = {**ALL_OK, "vlm": {"status": "error"}, "embedding": {"status": "error"}}
        features = readiness.compute_features(services)

        assert features["file_parsing"]["ready"] is False
        miss_ids = {m["id"] for m in features["file_parsing"]["missing"]}
        assert miss_ids == {"bge-m3"}  # VLM 不在必需里
        opt = features["file_parsing"]["optional_missing"]
        assert len(opt) == 1 and opt[0]["id"] == "vlm" and opt[0]["impact"] == "image_description"

        # task 只依赖 llm → embedding 缺失不阻断
        assert features["task"]["ready"] is True
        # knowledge_search 只依赖 embedding → 阻断
        assert features["knowledge_search"]["ready"] is False

    def test_parsing_missing_blocks_file_parsing_only(self):
        services = {**ALL_OK, "parsing": {"status": "error"}}
        features = readiness.compute_features(services)
        assert features["file_parsing"]["ready"] is False
        assert features["wiki_compile"]["ready"] is True  # 编译不依赖解析模型
        assert features["knowledge_search"]["ready"] is True


class TestEnsureReady:
    def _patch_features(self, monkeypatch, services):
        # 先用原函数算好结果再闭包（避免 monkeypatch 后自递归）
        features = readiness.compute_features(services)
        monkeypatch.setattr(readiness, "compute_features", lambda: features)

    def test_ready_passes(self, monkeypatch):
        self._patch_features(monkeypatch, ALL_OK)
        readiness.ensure_ready("report")  # 不抛

    def test_unknown_feature_skips_check(self, monkeypatch):
        self._patch_features(monkeypatch, ALL_OK)
        readiness.ensure_ready("nonexistent_feature")  # 未知功能不拦截（记 warning 放行）

    def test_llm_missing_raises_config(self, monkeypatch):
        self._patch_features(monkeypatch, {**ALL_OK, "llm": {"status": "error"}})
        with pytest.raises(LLMNotConfigured):
            readiness.ensure_ready("task")

    def test_embedding_missing_raises_resource(self, monkeypatch):
        self._patch_features(monkeypatch, {**ALL_OK, "embedding": {"status": "error"}})
        with pytest.raises(EmbeddingModelMissing):
            readiness.ensure_ready("wiki_compile")

    def test_parsing_missing_raises_resource(self, monkeypatch):
        self._patch_features(monkeypatch, {**ALL_OK, "parsing": {"status": "error"}})
        with pytest.raises(ParsingModelMissing):
            readiness.ensure_ready("file_parsing")

    def test_optional_vlm_never_blocks(self, monkeypatch):
        """功能面不变：vlm 仍是 file_parsing 可选依赖，ensure_ready 仅缺可选 → 放行
        （必配升级仅影响提示面红条/状态点，不改入口拦截）。"""
        self._patch_features(monkeypatch, {**ALL_OK, "vlm": {"status": "error"}})
        readiness.ensure_ready("file_parsing")  # 仅缺可选 → 放行


class TestComputeServiceStatus:
    def test_llm_not_configured_when_no_api_key(self, db, monkeypatch):
        db.save_settings({"api_key": "", "api_url": "https://example.com", "model": "m"})
        services = readiness.compute_service_status()
        assert services["llm"]["status"] == "error"
        assert services["llm"]["code"] == "LLM_NOT_CONFIGURED"

    def test_llm_ok_with_api_key(self, db):
        db.save_settings({"api_key": "sk-x", "api_url": "https://example.com", "model": "glm-4"})
        services = readiness.compute_service_status()
        assert services["llm"]["status"] == "ok"

    def test_web_search_error_when_not_configured(self, db):
        """网络搜索必配（2026-10-19 全必配统一）：未选供应商/Key 空 = error，进 OVERALL_REQUIRED_SERVICES。"""
        db.save_settings({"web_search_provider": "", "tavily_key": "", "zhipu_search_key": ""})
        services = readiness.compute_service_status()
        assert services["web_search"]["status"] == "error"
        assert services["web_search"]["code"] == "WEB_SEARCH_NOT_CONFIGURED"
        assert services["web_search"]["action"] == "settings:webSearch"

    def test_web_search_ok_with_provider_and_key(self, db):
        db.save_settings({"web_search_provider": "tavily", "tavily_key": "tvly-x"})
        services = readiness.compute_service_status()
        assert services["web_search"]["status"] == "ok"

    def test_health_overall_degraded_on_missing_web_search(self, monkeypatch):
        """overall 全必配聚合：缺网络搜索打 degraded（2026-10-19 必配统一）。"""
        from routers import health as health_router

        services = {**ALL_OK, "web_search": {"status": "error", "code": "WEB_SEARCH_NOT_CONFIGURED"}}
        monkeypatch.setattr(health_router, "compute_service_status", lambda: services)
        result = asyncio.run(health_router.health_check())
        assert result["status"] == "degraded"

    def test_model_manager_missing_reports_unavailable(self, monkeypatch):
        monkeypatch.setattr(deps, "model_manager", None, raising=False)
        services = readiness.compute_service_status()
        assert services["embedding"]["status"] == "error"
        assert services["parsing"]["status"] == "error"

    def test_health_endpoint_shape(self, monkeypatch):
        """health 薄壳直接复用 readiness 判定（单一真相）。"""
        from routers import health as health_router

        # health.py 持有 from-import 引用，需 patch health 模块内的名字
        monkeypatch.setattr(health_router, "compute_service_status", lambda: dict(ALL_OK))
        result = asyncio.run(health_router.health_check())
        assert result["status"] == "ok"
        assert set(result["features"].keys()) == {
            "file_parsing", "wiki_compile", "report", "task", "knowledge_search",
            "form_fill", "form_analysis",
        }

    def test_health_overall_degraded_on_missing_vlm(self, monkeypatch):
        """overall 全必配聚合：缺 VLM 打 degraded（2026-10-19 必配统一）。"""
        from routers import health as health_router

        services = {**ALL_OK, "vlm": {"status": "error", "code": "VLM_NOT_CONFIGURED"}}
        monkeypatch.setattr(health_router, "compute_service_status", lambda: services)
        result = asyncio.run(health_router.health_check())
        assert result["status"] == "degraded"

    def test_health_overall_degraded_on_required_missing(self, monkeypatch):
        """必需服务（embedding）缺失仍打 degraded。"""
        from routers import health as health_router

        services = {**ALL_OK, "embedding": {"status": "error"}}
        monkeypatch.setattr(health_router, "compute_service_status", lambda: services)
        result = asyncio.run(health_router.health_check())
        assert result["status"] == "degraded"


# ── F. /activities/stream SSE（0.4 §6.4 E3）──────────────────

class TestActivitiesStream:
    def test_stream_delivers_terminal_event(self, db, project_id):
        """订阅后终态事件以 SSE 帧推送（event: activity + data: 通知行 JSON）。"""
        import json as _json

        async def scenario():
            # 直接 await：subscribe() 需注册在当前事件循环（后续投递同 loop）
            response = await activities_router.activities_stream()
            gen = response.body_iterator
            try:
                fid = db.create_file(project_id, "strm.pdf", ".pdf", 100, "hstrm")["id"]
                activity_events.complete_file(db, fid)
                chunk = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
                assert chunk.startswith("event: activity\ndata: ")
                payload = _json.loads(chunk.split("data: ", 1)[1])
                assert payload["entity_type"] == "file"
                assert payload["kind"] == "completed"
                assert payload["entity_title"] == "strm.pdf"
            finally:
                await gen.aclose()  # 断连语义：finally 反注册

        asyncio.run(scenario())
