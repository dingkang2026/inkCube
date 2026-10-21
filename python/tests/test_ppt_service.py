"""PPT 交付物服务测试（2.3 §3.4 门禁 / §5 DDL / §6 数据流 / 2.3.1 §5 文件契约）。

覆盖：_gate_state 两阶段判定、_page_count_from_result、confirm_ui_port、
list_pages 排序沉底、sync_progress 阶段映射与门禁置位、ppts CRUD（progress
夹取 / error 截断 / 活跃过滤）、recover_orphans 落位、fetch_active 形状、
_build_first_message 门禁纪律契约、向导模板选择解析与 explicit root 契约
（2.3 §4.1）、ORIGIN_PRESETS 应用语义、自动续跑与 answer 路由、
finalize 回执竞态与终态防护（review 修复回归）。
"""
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import Database
import ppt_service
from agent.task_agent import ORIGIN_PRESETS, TaskAgent
from ppt_service import (
    PptService,
    TemplateRefError,
    _build_first_message,
    _gate_state,
    _page_count_from_result,
    _resolve_template_refs,
    confirm_ui_port,
    list_pages,
    sync_progress,
)


@pytest.fixture()
def db():
    database = Database(db_path=":memory:")
    database.initialize()
    return database


@pytest.fixture()
def svc():
    return PptService()


# ── 工具 ──

def _mk_ppt(db, tmp_path, *, status=None, project_id=None):
    """建 project + ppt 行（workspace_path 直接存 ppt_project 项目目录，
    同 prepare() 写入端契约——读取端直读行内路径；曾存工作区本身
    掩盖读取端二次套用 _project_dir 的 P0），返回 (ppt, project_dir)。"""
    proj_id = project_id or db.create_project("P", seed_playbook=False)["id"]
    ws = tmp_path / f"ws_{uuid.uuid4().hex[:8]}"
    (ws / "ppt_project").mkdir(parents=True, exist_ok=True)
    ppt = db.create_ppt(
        project_id=proj_id, task_id=f"t_{uuid.uuid4().hex[:8]}", title="演示文稿",
        workspace_path=str(ws / "ppt_project"),
    )
    if status:
        db.update_ppt_status(ppt["id"], status)
        ppt = db.get_ppt(ppt["id"])
    return ppt, ws / "ppt_project"


def _w(path: Path, obj) -> Path:
    """写文件（dict/list 走 JSON，其余原样文本），自动建父目录。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(obj, (dict, list)):
        path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    else:
        path.write_text(str(obj), encoding="utf-8")
    return path


# ── 门禁两阶段判定（2.3.1 §5.1 文件契约）──

class TestGateState:
    def test_empty(self, tmp_path):
        assert _gate_state(tmp_path) == {
            "pending": None, "confirmed_final": False, "receipt_stage1": False,
        }

    def test_stage1_pending(self, tmp_path):
        _w(tmp_path / "confirm_ui" / "recommendations.stage1.json", {"a": 1})
        state = _gate_state(tmp_path)
        assert state["pending"] == "stage1"
        assert not state["receipt_stage1"]

    def test_stage2_pending(self, tmp_path):
        cui = tmp_path / "confirm_ui"
        _w(cui / "recommendations.stage1.json", {})
        _w(cui / "result.json", {"stage": "stage1"})
        _w(cui / "template_handoff.json", {})
        _w(cui / "recommendations.stage2.json", {})
        state = _gate_state(tmp_path)
        assert state["pending"] == "stage2"
        assert state["receipt_stage1"]

    def test_final(self, tmp_path):
        _w(tmp_path / "confirm_ui" / "result.json", {"stage": "final"})
        state = _gate_state(tmp_path)
        assert state["pending"] is None
        assert state["confirmed_final"]

    def test_receipt_gap(self, tmp_path):
        """Stage 1 回执已写但 handoff/rec2 未齐 → 无 pending（回执间隙，等心跳续跑）。"""
        cui = tmp_path / "confirm_ui"
        _w(cui / "recommendations.stage1.json", {})
        _w(cui / "result.json", {"stage": "stage1"})
        state = _gate_state(tmp_path)
        assert state["pending"] is None
        assert state["receipt_stage1"]

    def test_rec2_without_handoff(self, tmp_path):
        cui = tmp_path / "confirm_ui"
        _w(cui / "recommendations.stage1.json", {})
        _w(cui / "result.json", {"stage": "stage1"})
        _w(cui / "recommendations.stage2.json", {})
        assert _gate_state(tmp_path)["pending"] is None

    def test_corrupt_result_ignored(self, tmp_path):
        """result.json 损坏视同不存在，rec1 仍判 stage1 pending。"""
        cui = tmp_path / "confirm_ui"
        _w(cui / "recommendations.stage1.json", {})
        _w(cui / "result.json", "{not json")
        assert _gate_state(tmp_path)["pending"] == "stage1"


# ── 页数解析与端口锁 ──

class TestPageCountAndPort:
    def test_page_count_int(self, tmp_path):
        _w(tmp_path / "confirm_ui" / "result.json", {"page_count": 12})
        assert _page_count_from_result(tmp_path) == 12

    def test_page_count_range_string(self, tmp_path):
        _w(tmp_path / "confirm_ui" / "result.json", {"page_count": "12-15"})
        assert _page_count_from_result(tmp_path) == 15

    def test_page_count_missing(self, tmp_path):
        assert _page_count_from_result(tmp_path) is None

    def test_page_count_invalid(self, tmp_path):
        _w(tmp_path / "confirm_ui" / "result.json", {"page_count": "abc"})
        assert _page_count_from_result(tmp_path) is None

    def test_port(self, tmp_path):
        _w(tmp_path / ".confirm_ui.lock", {"port": 5051})
        assert confirm_ui_port(tmp_path) == 5051

    def test_port_no_lock(self, tmp_path):
        assert confirm_ui_port(tmp_path) is None

    def test_port_bad_type(self, tmp_path):
        _w(tmp_path / ".confirm_ui.lock", {"port": "5051"})
        assert confirm_ui_port(tmp_path) is None


# ── 创作页清单（预览网格数据源）──

class TestListPages:
    def test_numeric_order(self, tmp_path):
        svg = tmp_path / "svg_output"
        svg.mkdir(parents=True)
        for n in (1, 10, 2):
            (svg / f"page_{n}.svg").write_text("<svg/>", encoding="utf-8")
        assert list_pages(tmp_path) == [
            "svg_output/page_1.svg", "svg_output/page_2.svg", "svg_output/page_10.svg",
        ]

    def test_nonstandard_sinks(self, tmp_path):
        svg = tmp_path / "svg_output"
        svg.mkdir(parents=True)
        (svg / "page_1.svg").write_text("s", encoding="utf-8")
        (svg / "page_broken.svg").write_text("s", encoding="utf-8")
        pages = list_pages(tmp_path)
        assert pages[0] == "svg_output/page_1.svg"
        assert pages[-1] == "svg_output/page_broken.svg"

    def test_missing_dir(self, tmp_path):
        assert list_pages(tmp_path) == []


# ── 进度聚合（§9.1 信号映射 + 门禁置位）──

@pytest.fixture()
def _agent_idle(monkeypatch):
    """任务侧 agent 空闲（未在跑）→ 门禁置位可达。"""
    monkeypatch.setattr(ppt_service, "_task_running", lambda tid: False)


@pytest.fixture()
def _agent_busy(monkeypatch):
    """任务侧 agent 回合仍在跑 → 门禁不打扰。"""
    monkeypatch.setattr(ppt_service, "_task_running", lambda tid: True)


class TestSyncProgress:
    def test_stage_intake_default(self, db, tmp_path, _agent_busy):
        ppt, _ = _mk_ppt(db, tmp_path)
        sync_progress(db, db.get_ppt(ppt["id"]))
        assert db.get_ppt(ppt["id"])["stage"] == "intake"

    def test_stage_strategy_on_gate(self, db, tmp_path, _agent_busy):
        ppt, proj = _mk_ppt(db, tmp_path)
        _w(proj / "confirm_ui" / "recommendations.stage1.json", {})
        sync_progress(db, db.get_ppt(ppt["id"]))
        assert db.get_ppt(ppt["id"])["stage"] == "strategy"

    def test_stage_authoring_on_pages(self, db, tmp_path, _agent_busy):
        ppt, proj = _mk_ppt(db, tmp_path)
        _w(proj / "svg_output" / "page_1.svg", "s")
        _w(proj / "svg_output" / "page_2.svg", "s")
        sync_progress(db, db.get_ppt(ppt["id"]))
        cur = db.get_ppt(ppt["id"])
        assert cur["stage"] == "authoring"
        assert cur["page_done"] == 2

    def test_stage_export_wins(self, db, tmp_path, _agent_busy):
        ppt, proj = _mk_ppt(db, tmp_path)
        _w(proj / "svg_output" / "page_1.svg", "s")
        _w(proj / "exports" / "deck.pptx", "PK")
        sync_progress(db, db.get_ppt(ppt["id"]))
        assert db.get_ppt(ppt["id"])["stage"] == "export"

    def test_progress_ratio(self, db, tmp_path, _agent_busy):
        ppt, proj = _mk_ppt(db, tmp_path)
        _w(proj / "confirm_ui" / "result.json", {"stage": "final", "page_count": 10})
        for i in range(1, 4):
            _w(proj / "svg_output" / f"page_{i}.svg", "s")
        sync_progress(db, db.get_ppt(ppt["id"]))
        cur = db.get_ppt(ppt["id"])
        assert cur["page_count"] == 10
        assert cur["page_done"] == 3
        assert cur["progress"] == pytest.approx(0.3)

    def test_gate_sets_awaiting_and_notifies(self, db, tmp_path, _agent_idle):
        ppt, proj = _mk_ppt(db, tmp_path)  # create_ppt 初始 running
        _w(proj / "confirm_ui" / "recommendations.stage1.json", {})
        sync_progress(db, db.get_ppt(ppt["id"]))
        cur = db.get_ppt(ppt["id"])
        assert cur["status"] == "awaiting_input"
        assert any(
            n["entity_type"] == "ppt" and n["kind"] == "awaiting_input"
            and n["entity_id"] == ppt["id"]
            for n in db.list_unread_notifications()
        )

    def test_gate_skipped_while_task_running(self, db, tmp_path, _agent_busy):
        ppt, proj = _mk_ppt(db, tmp_path)
        _w(proj / "confirm_ui" / "recommendations.stage1.json", {})
        sync_progress(db, db.get_ppt(ppt["id"]))
        assert db.get_ppt(ppt["id"])["status"] == "running"

    def test_gate_notifies_once(self, db, tmp_path, _agent_idle):
        """状态已置 awaiting 后重复 sync 不再堆叠通知。"""
        ppt, proj = _mk_ppt(db, tmp_path)
        _w(proj / "confirm_ui" / "recommendations.stage1.json", {})
        sync_progress(db, db.get_ppt(ppt["id"]))
        sync_progress(db, db.get_ppt(ppt["id"]))
        gate_notes = [
            n for n in db.list_unread_notifications()
            if n["entity_type"] == "ppt" and n["kind"] == "awaiting_input"
        ]
        assert len(gate_notes) == 1

    def test_no_project_dir_noop(self, db, tmp_path, _agent_busy):
        import shutil
        ppt, proj = _mk_ppt(db, tmp_path)
        shutil.rmtree(proj)
        sync_progress(db, db.get_ppt(ppt["id"]))  # 不抛
        assert db.get_ppt(ppt["id"])["stage"] is None


# ── ppts CRUD ──

class TestPptCrud:
    def test_create_defaults(self, db, tmp_path):
        ppt, _ = _mk_ppt(db, tmp_path)
        assert ppt["status"] == "running"
        assert ppt["kind"] == "deck"
        assert ppt["source_images"] == []
        assert ppt["stage"] is None

    def test_source_images_roundtrip(self, db):
        proj = db.create_project("P", seed_playbook=False)
        ppt = db.create_ppt(project_id=proj["id"], task_id="t1", title="T",
                            source_images=["staging/images/a.png"])
        assert db.get_ppt(ppt["id"])["source_images"] == ["staging/images/a.png"]

    def test_progress_clamped(self, db, tmp_path):
        ppt, _ = _mk_ppt(db, tmp_path)
        db.update_ppt_progress(ppt["id"], page_count=10, page_done=15)
        assert db.get_ppt(ppt["id"])["progress"] == 1.0
        db.update_ppt_progress(ppt["id"], page_done=2)
        assert db.get_ppt(ppt["id"])["progress"] == pytest.approx(0.2)

    def test_status_error_truncated(self, db, tmp_path):
        ppt, _ = _mk_ppt(db, tmp_path)
        db.update_ppt_status(ppt["id"], "failed", error="x" * 3000,
                             export_path="exports/a.pptx")
        cur = db.get_ppt(ppt["id"])
        assert cur["status"] == "failed"
        assert len(cur["error"]) == 2000
        assert cur["export_path"] == "exports/a.pptx"

    def test_delete_returns_row(self, db, tmp_path):
        ppt, _ = _mk_ppt(db, tmp_path)
        row = db.delete_ppt(ppt["id"])
        assert row["id"] == ppt["id"]
        assert db.get_ppt(ppt["id"]) is None
        assert db.delete_ppt(ppt["id"]) is None

    def test_list_active_filter(self, db, tmp_path):
        p1, _ = _mk_ppt(db, tmp_path)  # running
        p2, _ = _mk_ppt(db, tmp_path, status="awaiting_input")
        _mk_ppt(db, tmp_path, status="completed")
        _mk_ppt(db, tmp_path, status="failed")
        assert {r["id"] for r in db.list_active_ppts()} == {p1["id"], p2["id"]}

    def test_list_ppts_scoped_to_project(self, db, tmp_path):
        proj_id = db.create_project("P", seed_playbook=False)["id"]
        a, _ = _mk_ppt(db, tmp_path, project_id=proj_id)
        b, _ = _mk_ppt(db, tmp_path, project_id=proj_id)
        other, _ = _mk_ppt(db, tmp_path)
        rows = db.list_ppts(proj_id)
        assert len(rows) == 2
        assert {r["id"] for r in rows} == {a["id"], b["id"]}
        assert other["id"] not in {r["id"] for r in rows}


# ── 启动恢复落位（0.4 §2.4 同语义）──

class TestRecoverOrphans:
    def test_exports_complete(self, db, tmp_path):
        ppt, proj = _mk_ppt(db, tmp_path)  # running
        _w(proj / "exports" / "deck.pptx", "PK")
        PptService().recover_orphans(db)
        cur = db.get_ppt(ppt["id"])
        assert cur["status"] == "completed"
        assert cur["export_path"] == "exports/deck.pptx"
        assert any(
            n["entity_id"] == ppt["id"] and n["kind"] == "completed"
            for n in db.list_unread_notifications()
        )

    def test_pending_gate_awaiting(self, db, tmp_path):
        ppt, proj = _mk_ppt(db, tmp_path)
        _w(proj / "confirm_ui" / "recommendations.stage1.json", {})
        PptService().recover_orphans(db)
        assert db.get_ppt(ppt["id"])["status"] == "awaiting_input"

    def test_running_without_artifacts_paused(self, db, tmp_path):
        ppt, _ = _mk_ppt(db, tmp_path)
        PptService().recover_orphans(db)
        assert db.get_ppt(ppt["id"])["status"] == "paused"

    def test_terminal_untouched(self, db, tmp_path):
        ppt, proj = _mk_ppt(db, tmp_path, status="cancelled")
        _w(proj / "confirm_ui" / "recommendations.stage1.json", {})
        PptService().recover_orphans(db)
        assert db.get_ppt(ppt["id"])["status"] == "cancelled"


# ── 活动注册表 source（0.4 §2.2）──

class TestFetchActive:
    def test_shape_and_filter(self, db, tmp_path):
        run, _ = _mk_ppt(db, tmp_path)
        _mk_ppt(db, tmp_path, status="completed")
        db.update_ppt_progress(run["id"], page_count=10, page_done=4)
        items = PptService.fetch_active(db)
        assert len(items) == 1
        item = items[0]
        assert item["entity_type"] == "ppt"
        assert item["entity_id"] == run["id"]
        assert item["status"] == "running"
        assert item["container"] == "project"
        assert item["project_name"] == "P"
        assert item["progress"] == 40
        assert item["progress_text"] == "4/10"


# ── 首条工作流指令契约（2.3 §4.1 / §6.2）──

class TestFirstMessage:
    def test_gate_discipline_keywords(self, tmp_path):
        msg = _build_first_message(
            str(tmp_path), {"report": None, "images": [], "image_count": 0}, None, None)
        # 门禁纪律：daemon 启动、写完即收工（禁 --wait-only）、final 后关停、导出命令
        assert "--daemon --no-browser --timeout 0" in msg
        assert "不要用 --wait-only" in msg
        assert "--shutdown" in msg
        assert "svg_to_pptx" in msg
        # Default 全质量管线纪律
        assert "Generate PPTX" in msg
        assert "quick" in msg
        # 无报告 → 自由选题，不出现 import-sources
        assert "自由主题" in msg
        assert "import-sources" not in msg
        # 项目目录以正斜杠形式写入
        assert str(tmp_path).replace("\\", "/") in msg

    def test_with_report_and_extras(self, tmp_path):
        staging = {
            "report": "staging/report.md",
            "images": ["staging/images/a.png"],
            "image_count": 1,
        }
        msg = _build_first_message(str(tmp_path), staging, "商务深蓝", "每页不超过 5 条要点")
        assert "import-sources" in msg
        assert "staging/report.md" in msg
        assert "staging/images/*" in msg
        assert "商务深蓝" in msg
        assert "每页不超过 5 条要点" in msg

    def test_with_template_roots(self, tmp_path):
        """显式模板选择：explicit root 契约指令 + 每项路径行 + 源标注
        （builtin 方向回归：True=内置源）。"""
        staging = {"report": None, "images": [], "image_count": 0}
        roots = [
            {"kind": "style", "id": "custom_b", "builtin": False,
             "root": "D:/lib/ppt-library/templates/styles/custom_b"},
            {"kind": "brand", "id": "b1", "builtin": True,
             "root": "D:/res/skills/ppt-master/templates/brands/b1"},
        ]
        msg = _build_first_message(str(tmp_path), staging, "商务蓝", None,
                                   template_roots=roots)
        # explicit root 契约（2.3.1 §6 显式根表述）：写 template_options.json 的指令
        assert "explicit_workspace_roots" in msg
        assert "default_mode 固定 templates" in msg
        # 每项路径行 + 源随条目标注（正斜杠路径，agent 可安全手写进 JSON）
        assert "style:custom_b → D:/lib/ppt-library/templates/styles/custom_b（用户库模板）" in msg
        assert "brand:b1 → D:/res/skills/ppt-master/templates/brands/b1（内置模板）" in msg
        # 风格意向仍在（与模板选择叠加语义）
        assert "商务蓝" in msg

    def test_without_template_roots_default(self, tmp_path):
        """不选模板：显式模板段不出现，默认行为（Stage 1 内置候选自主决定）不变。"""
        staging = {"report": None, "images": [], "image_count": 0}
        msg = _build_first_message(str(tmp_path), staging, None, None)
        assert "explicit_workspace_roots" not in msg
        assert "显式模板选择" not in msg


# ── 向导模板选择解析（2.3 §4.1 explicit root）──

@pytest.fixture()
def _tpl_env(tmp_path, monkeypatch):
    """双源隔离：用户库 styles/custom_b + 随包源 brands/builtin_a。"""
    monkeypatch.setenv("INKCUBE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("skills.seed.builtin_skill_source",
                        lambda name: str(tmp_path / "res" / "skills" / "ppt-master"))
    (tmp_path / "data" / "ppt-library" / "templates" / "styles"
     / "custom_b" / "templates").mkdir(parents=True)
    (tmp_path / "res" / "skills" / "ppt-master" / "templates" / "brands"
     / "builtin_a" / "templates").mkdir(parents=True)


class TestTemplateRefs:
    def test_user_library_resolved(self, _tpl_env, tmp_path):
        """用户库命中：root 指向库内模板包根，builtin=False，路径统一正斜杠。"""
        roots = _resolve_template_refs("style:custom_b")
        expected = str(tmp_path / "data" / "ppt-library" / "templates"
                       / "styles" / "custom_b").replace("\\", "/")
        assert roots == [{"kind": "style", "id": "custom_b",
                          "root": expected, "builtin": False}]

    def test_builtin_resolved(self, _tpl_env, tmp_path):
        """内置源命中：builtin=True，root 指向随包源（server 与 library 注册根
        重合时自动归并候选，显式选择即预选高亮）。"""
        roots = _resolve_template_refs("brand:builtin_a")
        assert roots[0]["builtin"] is True
        assert roots[0]["root"] == str(
            tmp_path / "res" / "skills" / "ppt-master" / "templates"
            / "brands" / "builtin_a").replace("\\", "/")

    def test_user_library_shadows_builtin(self, _tpl_env, tmp_path):
        """同名同 kind：用户库优先（定位顺序 user → builtin）。"""
        (tmp_path / "data" / "ppt-library" / "templates" / "brands"
         / "builtin_a" / "templates").mkdir(parents=True)
        assert _resolve_template_refs("brand:builtin_a")[0]["builtin"] is False

    def test_invalid_format(self, _tpl_env):
        """格式非法（缺 kind/id、kind 不在四类、冒号/空白歧义）→ TemplateRefError。"""
        for bad in ("style", "style:", ":x", "style:a:b", "style:a b", "poster:x"):
            with pytest.raises(TemplateRefError):
                _resolve_template_refs(bad)

    def test_missing_template(self, _tpl_env):
        """双源均无此 id → TemplateRefError（创建前拦截，不跑到 Stage 1 才发现）。"""
        with pytest.raises(TemplateRefError, match="不存在或已删除"):
            _resolve_template_refs("style:ghost")

    def test_duplicate_kind_rejected(self, _tpl_env):
        """同 kind 重复（ppt-master 每类至多一个约束）→ TemplateRefError。"""
        with pytest.raises(TemplateRefError, match="重复"):
            _resolve_template_refs("style:custom_b,style:custom_b")

    def test_empty_yields_empty(self, _tpl_env):
        assert _resolve_template_refs("") == []


# ── ORIGIN_PRESETS 应用语义（2.3 §3.2）──

class TestOriginPresets:
    def test_constant(self):
        assert ORIGIN_PRESETS["ppt"] == {
            "task_ppt_max_iterations": 240, "task_ppt_timeout": 600}
        assert set(ORIGIN_PRESETS) == {"ppt"}  # chat 等无预设

    def test_apply_fills_missing(self):
        s = TaskAgent._apply_origin_presets({}, "ppt")
        assert s == {
            "task_ppt_max_iterations": 240, "task_ppt_timeout": 600,
            "task_max_iterations": 240, "task_timeout": 600,  # 落地通用键
        }

    def test_ppt_decoupled_from_chat_keys(self):
        """chat 通用设置（设置页 task_max_iterations 字段）不渗透 ppt 任务：
        解耦语义——ppt 参数只由专用键决定，通用键被预设落地值覆盖。"""
        s = TaskAgent._apply_origin_presets({"task_max_iterations": 100}, "ppt")
        assert s["task_ppt_max_iterations"] == 240
        assert s["task_max_iterations"] == 240
        assert s["task_timeout"] == 600

    def test_explicit_ppt_key_wins(self):
        s = TaskAgent._apply_origin_presets(
            {"task_ppt_max_iterations": 100, "task_ppt_timeout": 120}, "ppt")
        assert s["task_max_iterations"] == 100
        assert s["task_timeout"] == 120

    def test_none_counts_as_unset(self):
        s = TaskAgent._apply_origin_presets({"task_ppt_timeout": None}, "ppt")
        assert s["task_timeout"] == 600

    def test_chat_untouched(self):
        assert TaskAgent._apply_origin_presets({"a": 1}, "chat") == {"a": 1}


class TestOriginPresetsIntegration:
    """真实 get_settings() 形状下的预设生效回归（本 bug 直接回归）。

    defaults 白名单恒含通用键（60/30），旧实现以「get 为 None」判定未设置，
    对通用键永不成立——预设自 E1 起整体失效（ppt 实际以 60 轮/300s 运行）。
    旧用例全传手工 dict，绕过了真实数据形状，故未拦截；本类补上该缺口。
    """

    def test_ppt_presets_apply_over_real_settings(self, db):
        settings = TaskAgent._apply_origin_presets(db.get_settings(), "ppt")
        assert settings["task_max_iterations"] == 240
        assert settings["task_timeout"] == 600

    def test_explicit_ppt_key_survives_real_settings(self, db):
        db.save_settings({"task_ppt_max_iterations": 100})
        settings = TaskAgent._apply_origin_presets(db.get_settings(), "ppt")
        assert settings["task_max_iterations"] == 100
        assert settings["task_timeout"] == 600

    def test_chat_keeps_defaults(self, db):
        settings = TaskAgent._apply_origin_presets(db.get_settings(), "chat")
        assert settings["task_max_iterations"] == 60
        assert settings["task_timeout"] == 30


# ── 自动续跑（2.3 §3.4 不挂 wait）──

class TestAutoResume:
    def _prepare(self, db, tmp_path, monkeypatch, *, status, gate_files):
        ppt, proj = _mk_ppt(db, tmp_path)
        db.update_ppt_status(ppt["id"], status)
        for rel, obj in gate_files:
            _w(proj / rel, obj)
        calls = []
        monkeypatch.setattr(
            PptService, "_start_drive", lambda self_, d, p, m: calls.append(m))
        return ppt, calls

    def test_resumes_on_stage1_receipt(self, db, tmp_path, monkeypatch):
        ppt, calls = self._prepare(
            db, tmp_path, monkeypatch, status="awaiting_input",
            gate_files=[("confirm_ui/result.json", {"stage": "stage1"})])
        PptService().auto_resume_if_receipt(db, db.get_ppt(ppt["id"]))
        assert len(calls) == 1
        assert "Stage 1" in calls[0]

    def test_resumes_on_final(self, db, tmp_path, monkeypatch):
        ppt, calls = self._prepare(
            db, tmp_path, monkeypatch, status="awaiting_input",
            gate_files=[("confirm_ui/result.json", {"stage": "final"})])
        PptService().auto_resume_if_receipt(db, db.get_ppt(ppt["id"]))
        assert len(calls) == 1
        assert "Stage 2 最终确认" in calls[0]

    def test_no_receipt_no_resume(self, db, tmp_path, monkeypatch):
        ppt, calls = self._prepare(db, tmp_path, monkeypatch, status="awaiting_input",
                                   gate_files=[])
        PptService().auto_resume_if_receipt(db, db.get_ppt(ppt["id"]))
        assert calls == []

    def test_pending_stage2_blocks(self, db, tmp_path, monkeypatch):
        """已进入 stage2 等待（pending 非空）→ 不续跑，等用户确认。"""
        cui_files = [
            ("confirm_ui/recommendations.stage1.json", {}),
            ("confirm_ui/result.json", {"stage": "stage1"}),
            ("confirm_ui/template_handoff.json", {}),
            ("confirm_ui/recommendations.stage2.json", {}),
        ]
        ppt, calls = self._prepare(db, tmp_path, monkeypatch, status="awaiting_input",
                                   gate_files=cui_files)
        PptService().auto_resume_if_receipt(db, db.get_ppt(ppt["id"]))
        assert calls == []

    def test_running_untouched(self, db, tmp_path, monkeypatch):
        ppt, calls = self._prepare(
            db, tmp_path, monkeypatch, status="running",
            gate_files=[("confirm_ui/result.json", {"stage": "stage1"})])
        PptService().auto_resume_if_receipt(db, db.get_ppt(ppt["id"]))
        assert calls == []


# ── finalize 竞态与终态防护（review 修复回归）──

class TestFinalize:
    def test_receipt_before_round_end_resumes(self, db, tmp_path, monkeypatch):
        """回执早于回合收尾落盘（M1）：pending 已清空，sync_progress 不置位——
        finalize 补置 awaiting + 通知后立即续跑（原实现在此直接 return，
        状态卡 running 且泵已死，详情页连确认页都不渲染）。"""
        ppt, proj = _mk_ppt(db, tmp_path)
        _w(proj / "confirm_ui" / "result.json", {"stage": "stage1"})
        calls = []
        monkeypatch.setattr(
            PptService, "_start_drive", lambda self_, d, p, m: calls.append(m))
        PptService()._finalize(db, ppt["id"])
        assert db.get_ppt(ppt["id"])["status"] == "awaiting_input"
        assert len(calls) == 1
        assert "Stage 1" in calls[0]

    def test_finalize_skips_paused(self, db, tmp_path, monkeypatch):
        """用户主动暂停后回合收尾（M5）：不重复置位与声明（弹出即读后
        重复声明会成为第二条文案失真的通知）。"""
        ppt, _ = _mk_ppt(db, tmp_path, status="paused")
        fired = []
        monkeypatch.setattr(ppt_service.activity_events, "pause_ppt",
                            lambda d, pid, detail=None: fired.append(pid))
        PptService()._finalize(db, ppt["id"])
        assert fired == []
        assert db.get_ppt(ppt["id"])["status"] == "paused"


# ── answer / cancel / delete 路由 ──

class TestInteractions:
    def test_answer_not_found(self, db):
        assert PptService().answer(db, "nope")["code"] == "PPT_NOT_FOUND"

    def test_answer_rejected_while_running(self, db, tmp_path, monkeypatch):
        ppt, _ = _mk_ppt(db, tmp_path)
        monkeypatch.setattr(ppt_service, "_task_running", lambda tid: True)
        assert PptService().answer(db, ppt["id"], "意见")["code"] == "TASK_RUNNING"

    def test_answer_carries_user_note(self, db, tmp_path, monkeypatch):
        ppt, _ = _mk_ppt(db, tmp_path)
        monkeypatch.setattr(ppt_service, "_task_running", lambda tid: False)
        calls = []
        monkeypatch.setattr(
            PptService, "_start_drive", lambda self_, d, p, m: calls.append(m))
        assert PptService().answer(db, ppt["id"], "重点突出成本") == {"ok": True}
        assert "用户补充意见：重点突出成本" in calls[0]

    def test_answer_limit_reached_mapped(self, db, tmp_path, monkeypatch):
        ppt, _ = _mk_ppt(db, tmp_path)
        monkeypatch.setattr(ppt_service, "_task_running", lambda tid: False)

        def _raise(self_, d, p, m):
            raise RuntimeError("TASK_LIMIT_REACHED")

        monkeypatch.setattr(PptService, "_start_drive", _raise)
        assert PptService().answer(db, ppt["id"])["code"] == "TASK_LIMIT_REACHED"

    def test_answer_rejected_when_finished(self, db, tmp_path):
        """终态（completed/failed/cancelled）拒绝 answer/resume（m1 回归）。"""
        for status in ("completed", "failed", "cancelled"):
            ppt, _ = _mk_ppt(db, tmp_path)
            db.update_ppt_status(ppt["id"], status)
            assert PptService().answer(db, ppt["id"])["code"] == "PPT_FINISHED"

    def test_cancel_terminal_idempotent(self, db, tmp_path):
        """终态重复 cancel 幂等成功，不触碰任务系统。"""
        ppt, _ = _mk_ppt(db, tmp_path, status="completed")
        assert asyncio.run(PptService().cancel(db, ppt["id"])) == {"ok": True}

    def test_delete_cascades_task(self, db, tmp_path, monkeypatch):
        import deps as deps_mod
        ppt, _ = _mk_ppt(db, tmp_path)
        monkeypatch.setattr(deps_mod, "task_agent", SimpleNamespace(
            is_task_running=lambda tid: False,
            request_cancel=lambda tid: None,
        ))
        deleted = []
        monkeypatch.setattr(deps_mod, "db", SimpleNamespace(
            delete_task=lambda tid: deleted.append(tid)))
        assert asyncio.run(PptService().delete(db, ppt["id"])) == {"ok": True}
        assert db.get_ppt(ppt["id"]) is None
        assert deleted == [ppt["task_id"]]  # 先删任务（级联工作区）再删 ppt 行

    def test_delete_waits_for_agent_exit(self, db, tmp_path, monkeypatch):
        """运行中删除：request_cancel 后 bounded 等待 agent 退出再删（M4 回归）。

        曾直接 request_cancel 后立即级联删除工作区，与 agent 在飞写盘竞态。
        """
        import deps as deps_mod
        ppt, _ = _mk_ppt(db, tmp_path)
        state = {"running": True, "cancels": 0}

        def _is_running(tid):
            return state["running"]

        def _request_cancel(tid):
            state["cancels"] += 1
            state["running"] = False  # 取消后 agent 退出

        monkeypatch.setattr(deps_mod, "task_agent", SimpleNamespace(
            is_task_running=_is_running, request_cancel=_request_cancel))
        deleted = []
        monkeypatch.setattr(deps_mod, "db", SimpleNamespace(
            delete_task=lambda tid: deleted.append(tid)))
        assert asyncio.run(PptService().delete(db, ppt["id"])) == {"ok": True}
        assert state["cancels"] == 1
        assert deleted == [ppt["task_id"]]
        assert db.get_ppt(ppt["id"]) is None

    def test_delete_not_found(self, db, tmp_path, monkeypatch):
        import deps as deps_mod
        monkeypatch.setattr(deps_mod, "task_agent", SimpleNamespace(
            is_task_running=lambda tid: False))
        monkeypatch.setattr(deps_mod, "db", SimpleNamespace(delete_task=lambda tid: None))
        assert asyncio.run(PptService().delete(db, "nope"))["code"] == "PPT_NOT_FOUND"


# ── 创建回滚（review 二轮回归：create 两段式异常路径不留僵尸行）──

class TestCreateRollback:
    """create 两段式（prepare 线程池 + launch 事件循环）异常路径的原子性。

    僵尸行后果链：ppts 行 running 落库后泵未启动 → sync_progress 不触达
    （无门禁产物）、auto_resume 只认 awaiting_input、前端 running 态无
    续跑入口 → 永久轮询；重试创建再双份资源。
    """

    def test_launch_failure_marks_ppt_failed(self, db, tmp_path, monkeypatch):
        """launch 抛 RuntimeError（并发满 429 等）：ppts 行回滚为 failed。"""
        import deps as deps_mod
        from routers import ppts as ppts_router
        monkeypatch.setattr(ppts_router, "ensure_ready", lambda *a, **k: None)
        monkeypatch.setattr(deps_mod, "db", db, raising=False)
        monkeypatch.setenv("INKCUBE_DATA_DIR", str(tmp_path / "data"))

        def _boom(*a, **k):
            raise RuntimeError("TASK_LIMIT_REACHED")

        monkeypatch.setattr(ppt_service.service, "launch", _boom)
        pid = db.create_project("P", seed_playbook=False)["id"]
        res = asyncio.run(ppts_router.create_ppt(
            pid, ppts_router.PptCreateRequest(title="T")))
        assert res.status_code == 429
        rows = db.list_ppts(pid)
        assert len(rows) == 1
        assert rows[0]["status"] == "failed"
        assert "并发" in rows[0]["error"]

    def test_prepare_failure_cleans_orphan_task(self, db, tmp_path, monkeypatch):
        """prepare 中断（素材就位 OSError 等）：孤儿 task 一并回滚不留不可见泄漏。"""
        import deps as deps_mod
        from routers import ppts as ppts_router
        monkeypatch.setattr(ppts_router, "ensure_ready", lambda *a, **k: None)
        monkeypatch.setattr(deps_mod, "db", db, raising=False)
        monkeypatch.setenv("INKCUBE_DATA_DIR", str(tmp_path / "data"))

        def _boom(db_, ws, rid, iids):
            raise OSError("disk full")

        monkeypatch.setattr(ppt_service, "_stage_materials", _boom)
        pid = db.create_project("P", seed_playbook=False)["id"]
        res = asyncio.run(ppts_router.create_ppt(
            pid, ppts_router.PptCreateRequest(title="T")))
        assert res.status_code == 500
        assert db.list_ppts(pid) == []
        task_rows, _ = db.list_tasks(origin="ppt")
        assert task_rows == []


# ── 图片来源复合选择物化（2.3 §4.1 E2：钉选全收 + 范围圈池 + 向量缩池）──

class TestImagePool:
    def _mk_imgs(self, db, project_id, specs):
        """specs: [(category, width, status)] 按序建图，返回 id 列表。"""
        f = db.create_file(project_id, "a.pdf", "pdf", 1, "h")
        ids = []
        for cat, w, status in specs:
            img = db.create_image(f["id"], f"/tmp/{len(ids)}.png",
                                  width_px=w, category=cat)
            if status:
                db.update_image_status(img["id"], status)
            ids.append(img["id"])
        return ids

    @staticmethod
    def _sel(pinned=None, cats=None, min_w=None):
        return SimpleNamespace(pinned_ids=pinned or [],
                               categories=cats or [], min_width=min_w)

    def test_none_selection_empty(self, db, tmp_path):
        pid = db.create_project("P", seed_playbook=False)["id"]
        assert ppt_service._resolve_image_pool(db, pid, None, None) == []

    def test_pinned_all_in_regardless_of_scope(self, db, tmp_path):
        """钉选全收：不合范围（未 done/类目不符）也必入池，且去重保序。"""
        pid = db.create_project("P", seed_playbook=False)["id"]
        ids = self._mk_imgs(db, pid, [("chart", 100, "done"),
                                      ("photo", 100, "pending")])
        pool = ppt_service._resolve_image_pool(
            db, pid, self._sel(pinned=[ids[1], ids[0], ids[1]]), None)
        assert pool == [ids[1], ids[0]]

    def test_category_and_width_filter(self, db, tmp_path):
        pid = db.create_project("P", seed_playbook=False)["id"]
        ids = self._mk_imgs(db, pid, [
            ("chart", 1920, "done"), ("photo", 1920, "done"),
            ("chart", 500, "done"), ("chart", 1920, "error"),
        ])
        pool = ppt_service._resolve_image_pool(
            db, pid, self._sel(cats=["chart"], min_w=800), None)
        assert pool == [ids[0]]

    def test_under_cap_returns_all(self, db, tmp_path, monkeypatch):
        pid = db.create_project("P", seed_playbook=False)["id"]
        ids = self._mk_imgs(db, pid, [("chart", 800, "done")] * 3)
        monkeypatch.setattr(ppt_service, "_IMAGE_POOL_CAP", 10)
        pool = ppt_service._resolve_image_pool(db, pid, self._sel(), None)
        assert set(pool) == set(ids)

    def test_over_cap_recency_fallback(self, db, tmp_path, monkeypatch):
        """无报告/向量不可用 → 范围池按 created_at 序截到剩余额度。"""
        pid = db.create_project("P", seed_playbook=False)["id"]
        ids = self._mk_imgs(db, pid, [("chart", 800, "done")] * 4)
        monkeypatch.setattr(ppt_service, "_IMAGE_POOL_CAP", 3)
        pool = ppt_service._resolve_image_pool(db, pid, self._sel(), None)
        assert len(pool) == 3
        assert set(pool) <= set(ids)

    def test_over_cap_vector_ranking(self, db, tmp_path, monkeypatch):
        """有报告 + 向量排序可用 → head 按相关性名次取，钉选优先占位。"""
        pid = db.create_project("P", seed_playbook=False)["id"]
        ids = self._mk_imgs(db, pid, [("chart", 800, "done")] * 4)
        monkeypatch.setattr(ppt_service, "_IMAGE_POOL_CAP", 3)
        # 最旧一张钉选占位后，范围池额度剩 2；相关性名次取 [3, 1]，
        # 剩余额度满后不再补尾
        monkeypatch.setattr(
            ppt_service, "_rank_by_report_figure_similarity",
            lambda pid_, pool, text: [ids[3], ids[1], ids[2], ids[0]])
        pool = ppt_service._resolve_image_pool(
            db, pid, self._sel(pinned=[ids[0]]), "报告正文")
        assert pool == [ids[0], ids[3], ids[1]]

    def test_ranking_partial_coverage_fills_rest(self, db, tmp_path, monkeypatch):
        """向量只覆盖部分池内图：head 取命中，剩余额度按 created_at 序补尾。"""
        pid = db.create_project("P", seed_playbook=False)["id"]
        ids = self._mk_imgs(db, pid, [("chart", 800, "done")] * 3)
        monkeypatch.setattr(ppt_service, "_IMAGE_POOL_CAP", 2)  # 3 图超限才走排序分支
        monkeypatch.setattr(
            ppt_service, "_rank_by_report_figure_similarity",
            lambda pid_, pool, text: [ids[2]])  # 只命中一张（其余无向量）
        pool = ppt_service._resolve_image_pool(
            db, pid, self._sel(), "报告正文")
        assert pool[0] == ids[2]
        assert len(pool) == 2

    def test_report_text_reads_content(self, db, tmp_path):
        pid = db.create_project("P", seed_playbook=False)["id"]
        md = tmp_path / "r.md"
        md.write_text("X" * 5000, encoding="utf-8")
        rep = db.create_report(pid, "t", "T", content_path=str(md))
        text = ppt_service._report_text(db, rep["id"])
        assert len(text) == 4000
        assert ppt_service._report_text(db, None) is None
        assert ppt_service._report_text(db, "gone") is None


# ── prepare 端到端契约（防写入端/读取端路径契约再漂移）──

class TestPrepareContract:
    def test_prepare_row_readable_by_sync(self, db, tmp_path, svc, monkeypatch,
                                          _agent_busy):
        """prepare() 真实建行 → 行内 workspace_path 即项目目录本身 →
        产物写进该目录后 sync_progress 必须能读到（曾因夹具存工作区本身
        掩盖读取端二次套用 _project_dir 的 P0）。"""
        monkeypatch.setenv("INKCUBE_DATA_DIR", str(tmp_path / "data"))
        proj_id = db.create_project("P", seed_playbook=False)["id"]
        ppt, _message = svc.prepare(db, proj_id, "契约演示")
        project = Path(ppt["workspace_path"])
        assert project.name == "ppt_project"   # 行内存的是项目目录，非任务工作区
        assert project.is_dir()                # prepare 已建目录
        (project / "svg_output").mkdir(parents=True)
        (project / "svg_output" / "page_1.svg").write_text("s", encoding="utf-8")
        sync_progress(db, db.get_ppt(ppt["id"]))
        cur = db.get_ppt(ppt["id"])
        assert cur["stage"] == "authoring"
        assert cur["page_done"] == 1

    def test_prepare_template_refs_row_and_message(self, db, tmp_path, svc,
                                                    monkeypatch, _agent_busy):
        """template_refs → style_ref 列存选择标识（拍板口径：kind:id 串或
        legacy 文本）+ 首条消息带显式根契约与用户库正斜杠路径。"""
        monkeypatch.setenv("INKCUBE_DATA_DIR", str(tmp_path / "data"))
        ws = (tmp_path / "data" / "ppt-library" / "templates"
              / "styles" / "my_s" / "templates")
        ws.mkdir(parents=True)
        proj_id = db.create_project("P", seed_playbook=False)["id"]
        ppt, message = svc.prepare(db, proj_id, "T", template_refs="style:my_s")
        assert ppt["style_ref"] == "style:my_s"
        assert "explicit_workspace_roots" in message
        assert "style:my_s → " in message
        assert str(ws.parent).replace("\\", "/") in message
