"""PPT 成品增强服务测试（2.3 §4.3 Enhance Native，E3）。

覆盖：_stage_enhance_source 拷贝与重名序号、_enhance_notes_coverage、
_enhance_gate 产物状态机（两道确认门 / 无门禁 / 损坏 JSON）、
_build_enhance_message 工作流契约（init 命令 / 禁 SVG 管线 / 门禁纪律）、
prepare_enhance 行字段与失败回滚、_finalize_enhanced 终态三分支、
answer enhanced 门禁应答路由、recover_orphans enhanced 落位、
sync_progress enhanced 阶段映射。
"""
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import Database
import ppt_service
from ppt_service import (
    PptService,
    _build_enhance_message,
    _enhance_exports,
    _enhance_gate,
    _enhance_notes_coverage,
    _stage_enhance_source,
    sync_progress,
)


@pytest.fixture()
def db():
    database = Database(db_path=":memory:")
    database.initialize()
    return database


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    """prepare_enhance 内部经 paths.get_data_dir() 建 task 工作区：环境变量
    优先级重定向到 tmp，杜绝测试污染真实数据目录（python/data/tasks）。"""
    monkeypatch.setenv("INKCUBE_DATA_DIR", str(tmp_path / "data"))


# ── 工具 ──

def _w(path: Path, obj) -> Path:
    """写文件（dict/list 走 JSON，其余原样文本），自动建父目录。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(obj, (dict, list)):
        path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    else:
        path.write_text(str(obj), encoding="utf-8")
    return path


def _plan(status="draft", audio_enabled=True, provider=None):
    """enhancement_plan.json / project.json 的最小形状（_enhance_gate 消费面）。"""
    return {
        "status": status,
        "modules": {"audio": {"enabled": audio_enabled}},
        "_provider": provider,
    }


def _mk_enhanced(db, tmp_path, *, status=None):
    """建 enhanced 行（workspace_path 直接指向 enhance_project，同 prepare_enhance）。"""
    proj_id = db.create_project("P", seed_playbook=False)["id"]
    task = db.create_task(
        title="[PPT增强] 测试", data_dir=str(tmp_path / "data"), origin="ppt")
    project = Path(task["workspace_path"]) / "enhance_project"
    project.mkdir(parents=True, exist_ok=True)
    ppt = db.create_ppt(
        project_id=proj_id, task_id=task["id"], title="测试 · 增强",
        kind="enhanced", workspace_path=str(project),
        enhance_source_path="staging/sources/demo.pptx",
    )
    if status:
        db.update_ppt_status(ppt["id"], status)
        ppt = db.get_ppt(ppt["id"])
    return ppt, project


def _notes_full(project: Path, total: int):
    """写满覆盖的 notes（slide_index + 非空 note 文件）。"""
    _w(project / "analysis" / "slide_index.json", {
        "slides": [
            {"index": i, "note_file": f"notes/{i:03d}.md"} for i in range(1, total + 1)
        ],
    })
    for i in range(1, total + 1):
        _w(project / "notes" / f"{i:03d}.md", f"讲稿 {i}")


# ── 源就位 ──

class TestStageEnhanceSource:
    def test_copies_relative_path(self, tmp_path):
        src = tmp_path / "demo.pptx"
        src.write_text("PK", encoding="utf-8")
        ws = tmp_path / "ws"
        staged = _stage_enhance_source(str(ws), str(src))
        assert staged == "staging/sources/demo.pptx"
        assert (ws / "staging" / "sources" / "demo.pptx").read_text(
            encoding="utf-8") == "PK"

    def test_duplicate_names_numbered(self, tmp_path):
        a = tmp_path / "a" / "demo.pptx"
        b = tmp_path / "b" / "demo.pptx"
        for p in (a, b):
            p.parent.mkdir(parents=True)
            p.write_text("x", encoding="utf-8")
        ws = str(tmp_path / "ws")
        assert _stage_enhance_source(ws, str(a)) == "staging/sources/demo.pptx"
        assert _stage_enhance_source(ws, str(b)) == "staging/sources/demo_1.pptx"


# ── notes 覆盖与门禁状态机（产物即状态）──

class TestEnhanceNotesCoverage:
    def test_missing_index(self, tmp_path):
        assert _enhance_notes_coverage(tmp_path) == (0, 0)

    def test_partial(self, tmp_path):
        _w(tmp_path / "analysis" / "slide_index.json", {"slides": [
            {"index": 1, "note_file": "notes/001.md"},
            {"index": 2, "note_file": "notes/002.md"},
        ]})
        _w(tmp_path / "notes" / "001.md", "讲稿")
        # 002 缺文件
        assert _enhance_notes_coverage(tmp_path) == (1, 2)

    def test_empty_note_not_counted(self, tmp_path):
        _w(tmp_path / "analysis" / "slide_index.json", {"slides": [
            {"index": 1, "note_file": "notes/001.md"},
        ]})
        _w(tmp_path / "notes" / "001.md", "")
        assert _enhance_notes_coverage(tmp_path) == (0, 1)


class TestEnhanceGate:
    def test_no_plan(self, tmp_path):
        assert _enhance_gate(tmp_path) is None

    def test_plan_draft_no_notes(self, tmp_path):
        _w(tmp_path / "analysis" / "enhancement_plan.json", _plan())
        assert _enhance_gate(tmp_path) == "plan"

    def test_plan_confirmed_notes_pending(self, tmp_path):
        """确认后 notes 未产出：无门禁（进行中）。"""
        _w(tmp_path / "analysis" / "enhancement_plan.json",
           _plan(status="confirmed"))
        assert _enhance_gate(tmp_path) is None

    def test_partial_notes_no_gate(self, tmp_path):
        """draft + notes 已开始：越过门①但未满覆盖 → 无门禁（写稿中）。"""
        _w(tmp_path / "analysis" / "enhancement_plan.json", _plan())
        _w(tmp_path / "analysis" / "slide_index.json", {"slides": [
            {"index": 1, "note_file": "notes/001.md"},
            {"index": 2, "note_file": "notes/002.md"},
        ]})
        _w(tmp_path / "notes" / "001.md", "讲稿")
        assert _enhance_gate(tmp_path) is None

    def test_audio_gate(self, tmp_path):
        _w(tmp_path / "analysis" / "enhancement_plan.json", _plan())
        _notes_full(tmp_path, 2)
        _w(tmp_path / "analysis" / "enhancement_plan.json", _plan())
        assert _enhance_gate(tmp_path) == "audio"

    def test_audio_disabled_no_gate(self, tmp_path):
        _w(tmp_path / "analysis" / "enhancement_plan.json",
           _plan(audio_enabled=False))
        _notes_full(tmp_path, 2)
        _w(tmp_path / "analysis" / "enhancement_plan.json",
           _plan(audio_enabled=False))
        assert _enhance_gate(tmp_path) is None

    def test_provider_configured_no_gate(self, tmp_path):
        """门②通过（provider 已写入 project.json）→ 无门禁。"""
        _w(tmp_path / "analysis" / "enhancement_plan.json", _plan())
        _notes_full(tmp_path, 2)
        _w(tmp_path / "analysis" / "enhancement_plan.json", _plan())
        _w(tmp_path / "project.json", {"audio": {"provider": "edge", "voice": "x"}})
        assert _enhance_gate(tmp_path) is None

    def test_corrupt_plan(self, tmp_path):
        _w(tmp_path / "analysis" / "enhancement_plan.json", "{broken")
        assert _enhance_gate(tmp_path) is None


# ── 首条工作流指令契约 ──

class TestEnhanceMessageContract:
    def test_contract_keywords(self, tmp_path):
        msg = _build_enhance_message(
            str(tmp_path / "ws"), "staging/sources/demo.pptx", "语速稍慢")
        # init 命令带 --project-dir（产物全部落在显式目录）；路径引号包裹，
        # Windows 用户名含空格时不截断
        assert 'native_enhance_pptx.py" init' in msg
        assert '--project-dir "' in msg
        assert "enhance_project" in msg
        assert "staging/sources/demo.pptx" in msg
        # 禁 SVG 管线 + 产物为新文件
        assert "禁止 SVG 管线" in msg
        assert "不得覆盖源文件" in msg
        # 两处确认门纪律：提案后立即结束回合
        assert "结束你的回合" in msg
        # notes → validate → apply 链
        assert "validate" in msg
        assert "用户补充要求：语速稍慢" in msg
        # 项目目录以正斜杠形式写入
        assert str((tmp_path / "ws" / "enhance_project")).replace("\\", "/") in msg

    def test_no_notes(self, tmp_path):
        msg = _build_enhance_message(str(tmp_path / "ws"), "staging/sources/a.pptx", None)
        assert "用户补充要求" not in msg


# ── prepare：行字段 / 源就位 / 回滚 ──

class TestPrepareEnhance:
    def test_row_fields_and_staging(self, db, tmp_path):
        src = tmp_path / "demo.pptx"
        src.write_text("PK", encoding="utf-8")
        ppt, message = ppt_service.service.prepare_enhance(
            db, project_id=db.create_project("P", seed_playbook=False)["id"],
            title="发布会演示 · 增强", source_pptx=str(src), notes=None)
        assert ppt["kind"] == "enhanced"
        assert ppt["status"] == "running"
        assert ppt["enhance_source_path"] == "staging/sources/demo.pptx"
        assert Path(ppt["workspace_path"]).name == "enhance_project"
        # 源已拷入任务 staging
        task_ws = Path(db.get_task(ppt["task_id"])["workspace_path"])
        assert (task_ws / "staging" / "sources" / "demo.pptx").is_file()
        # 工作区即 enhance_project 目录（已预建，init --project-dir 目标）
        assert Path(ppt["workspace_path"]).is_dir()
        task = db.get_task(ppt["task_id"])
        assert task["origin"] == "ppt"
        assert task["title"].startswith("[PPT增强]")
        # 首条消息含 init 命令
        assert 'native_enhance_pptx.py" init' in message

    def test_rollback_no_orphan_rows(self, db, tmp_path, monkeypatch):
        """源文件不存在：异常冒泡，task/ppt 行全部回滚。"""
        with pytest.raises(OSError):
            ppt_service.service.prepare_enhance(
                db, project_id="p", title="X",
                source_pptx=str(tmp_path / "gone.pptx"))
        rows = db.list_ppts("p")
        assert rows == []
        task_rows, _ = db.list_tasks(origin="ppt")
        assert task_rows == []


# ── 回合结束终态（产物状态机判定）──

class TestFinalizeEnhanced:
    def _patch_events(self, monkeypatch):
        calls = {"complete": [], "gate": [], "pause": []}
        monkeypatch.setattr(
            ppt_service.activity_events, "complete_ppt",
            lambda d, i, p: calls["complete"].append((i, p)))
        monkeypatch.setattr(
            ppt_service.activity_events, "gate_ppt",
            lambda d, i, m: calls["gate"].append((i, m)))
        monkeypatch.setattr(
            ppt_service.activity_events, "pause_ppt",
            lambda d, i, m="": calls["pause"].append((i, m)))
        return calls

    def test_exports_completed(self, db, tmp_path, monkeypatch):
        ppt, project = _mk_enhanced(db, tmp_path)
        _w(project / "exports" / "demo_enhanced.pptx", "PK")
        calls = self._patch_events(monkeypatch)
        PptService()._finalize_enhanced(db, db.get_ppt(ppt["id"]))
        cur = db.get_ppt(ppt["id"])
        assert cur["status"] == "completed"
        assert cur["export_path"] == "exports/demo_enhanced.pptx"
        assert calls["complete"] == [(ppt["id"], "exports/demo_enhanced.pptx")]

    def test_plan_gate_awaiting(self, db, tmp_path, monkeypatch):
        ppt, project = _mk_enhanced(db, tmp_path)
        _w(project / "analysis" / "enhancement_plan.json", _plan())
        calls = self._patch_events(monkeypatch)
        PptService()._finalize_enhanced(db, db.get_ppt(ppt["id"]))
        cur = db.get_ppt(ppt["id"])
        assert cur["status"] == "awaiting_input"
        assert cur["stage"] == "strategy"
        assert calls["gate"] == [(ppt["id"], "增强计划已就绪，等待你确认")]

    def test_audio_gate_awaiting(self, db, tmp_path, monkeypatch):
        ppt, project = _mk_enhanced(db, tmp_path)
        _w(project / "analysis" / "enhancement_plan.json", _plan())
        _notes_full(project, 2)
        _w(project / "analysis" / "enhancement_plan.json", _plan())
        calls = self._patch_events(monkeypatch)
        PptService()._finalize_enhanced(db, db.get_ppt(ppt["id"]))
        cur = db.get_ppt(ppt["id"])
        assert cur["status"] == "awaiting_input"
        assert cur["stage"] == "authoring"
        assert calls["gate"] == [(ppt["id"], "音频配置已就绪，等待你确认")]

    def test_no_gate_paused(self, db, tmp_path, monkeypatch):
        ppt, _project = _mk_enhanced(db, tmp_path)
        calls = self._patch_events(monkeypatch)
        PptService()._finalize_enhanced(db, db.get_ppt(ppt["id"]))
        cur = db.get_ppt(ppt["id"])
        assert cur["status"] == "paused"
        assert calls["pause"]


# ── 门禁应答路由（answer enhanced 分支）──

class TestAnswerEnhanced:
    def _prepare(self, db, tmp_path, monkeypatch, *, plan=None, notes=False):
        ppt, project = _mk_enhanced(db, tmp_path, status="awaiting_input")
        if plan is not None:
            _w(project / "analysis" / "enhancement_plan.json", plan)
            if notes:
                _notes_full(project, 2)
                _w(project / "analysis" / "enhancement_plan.json", plan)
        monkeypatch.setattr(ppt_service, "_task_running", lambda tid: False)
        calls = []
        monkeypatch.setattr(
            PptService, "_start_drive",
            lambda self_, d, p, m: calls.append(m))
        return ppt, calls

    def test_plan_gate_confirm(self, db, tmp_path, monkeypatch):
        ppt, calls = self._prepare(db, tmp_path, monkeypatch, plan=_plan())
        assert PptService().answer(db, ppt["id"], None) == {"ok": True}
        assert len(calls) == 1
        assert "确认门①" in calls[0]
        assert "confirmed" in calls[0]

    def test_plan_gate_notes(self, db, tmp_path, monkeypatch):
        ppt, calls = self._prepare(db, tmp_path, monkeypatch, plan=_plan())
        PptService().answer(db, ppt["id"], "不要配音")
        assert "修改意见" in calls[0]
        assert "不要配音" in calls[0]

    def test_audio_gate_confirm(self, db, tmp_path, monkeypatch):
        ppt, calls = self._prepare(
            db, tmp_path, monkeypatch, plan=_plan(), notes=True)
        PptService().answer(db, ppt["id"], None)
        assert "确认门②" in calls[0]
        assert "provider" in calls[0]

    def test_audio_gate_notes(self, db, tmp_path, monkeypatch):
        ppt, calls = self._prepare(
            db, tmp_path, monkeypatch, plan=_plan(), notes=True)
        PptService().answer(db, ppt["id"], "换成男声")
        assert "音频配置" in calls[0]
        assert "换成男声" in calls[0]

    def test_no_gate_generic(self, db, tmp_path, monkeypatch):
        ppt, calls = self._prepare(db, tmp_path, monkeypatch)
        PptService().answer(db, ppt["id"], None)
        assert "增强任务" in calls[0]


# ── 启动恢复落位 ──

class TestRecoverOrphansEnhanced:
    def test_exports_complete(self, db, tmp_path):
        ppt, project = _mk_enhanced(db, tmp_path)  # running
        _w(project / "exports" / "demo_enhanced.pptx", "PK")
        PptService().recover_orphans(db)
        cur = db.get_ppt(ppt["id"])
        assert cur["status"] == "completed"
        assert cur["export_path"] == "exports/demo_enhanced.pptx"

    def test_gate_awaiting(self, db, tmp_path):
        """门禁恢复与 _finalize_enhanced 对齐 stage（plan→strategy）。"""
        ppt, project = _mk_enhanced(db, tmp_path)
        _w(project / "analysis" / "enhancement_plan.json", _plan())
        PptService().recover_orphans(db)
        cur = db.get_ppt(ppt["id"])
        assert cur["status"] == "awaiting_input"
        assert cur["stage"] == "strategy"

    def test_audio_gate_awaiting_stage(self, db, tmp_path):
        """门②恢复对齐 stage=authoring（详情首拉前前端按 stage 选门禁卡）。"""
        ppt, project = _mk_enhanced(db, tmp_path)
        _w(project / "analysis" / "enhancement_plan.json", _plan())
        _notes_full(project, 2)
        _w(project / "analysis" / "enhancement_plan.json", _plan())
        PptService().recover_orphans(db)
        cur = db.get_ppt(ppt["id"])
        assert cur["status"] == "awaiting_input"
        assert cur["stage"] == "authoring"

    def test_running_no_artifacts_paused(self, db, tmp_path):
        ppt, _project = _mk_enhanced(db, tmp_path)
        PptService().recover_orphans(db)
        assert db.get_ppt(ppt["id"])["status"] == "paused"


# ── 进度聚合（enhanced 阶段映射）──

class TestSyncEnhanceProgress:
    def test_intake_default(self, db, tmp_path):
        ppt, _project = _mk_enhanced(db, tmp_path)
        sync_progress(db, db.get_ppt(ppt["id"]))
        assert db.get_ppt(ppt["id"])["stage"] == "intake"

    def test_strategy_on_plan(self, db, tmp_path):
        ppt, project = _mk_enhanced(db, tmp_path)
        _w(project / "analysis" / "enhancement_plan.json", _plan())
        sync_progress(db, db.get_ppt(ppt["id"]))
        assert db.get_ppt(ppt["id"])["stage"] == "strategy"

    def test_authoring_on_notes(self, db, tmp_path):
        ppt, project = _mk_enhanced(db, tmp_path)
        _w(project / "analysis" / "enhancement_plan.json", _plan())
        _notes_full(project, 2)
        sync_progress(db, db.get_ppt(ppt["id"]))
        assert db.get_ppt(ppt["id"])["stage"] == "authoring"

    def test_export_on_exports(self, db, tmp_path):
        ppt, project = _mk_enhanced(db, tmp_path)
        _w(project / "exports" / "demo_enhanced.pptx", "PK")
        sync_progress(db, db.get_ppt(ppt["id"]))
        assert db.get_ppt(ppt["id"])["stage"] == "export"

    def test_exports_helper(self, tmp_path):
        assert _enhance_exports(tmp_path) == []
        _w(tmp_path / "exports" / "b.pptx", "PK")
        _w(tmp_path / "exports" / "a.pptx", "PK")
        names = [p.name for p in _enhance_exports(tmp_path)]
        assert sorted(names) == ["a.pptx", "b.pptx"]


# ── router 层：launch 兜底 / 同源去重 ──

class TestLaunchRollback:
    """_launch_or_rollback 非 RuntimeError 兜底：launch 内 DB 写冲突等异常
    也会发生在行已 running 之后——不处置则行卡在无泵状态永久占据活动中心。"""

    def test_non_runtime_error_marks_failed(self, db, tmp_path, monkeypatch):
        import routers.ppts as rp
        ppt, _project = _mk_enhanced(db, tmp_path)
        monkeypatch.setattr(rp.deps, "db", db, raising=False)

        def _boom(d, p, m):
            raise OSError("disk full")

        monkeypatch.setattr(ppt_service.service, "launch", _boom)
        row, err = rp._launch_or_rollback(db.get_ppt(ppt["id"]), "msg")
        assert row is None
        assert err.status_code == 500
        assert "LAUNCH_FAILED" in err.body.decode()
        assert db.get_ppt(ppt["id"])["status"] == "failed"


class TestEnhanceDedup:
    """同源去重：同项目已有进行中的增强（running/awaiting 门禁）时拒绝重复入队。"""

    def test_active_running_409(self, db, tmp_path):
        import routers.ppts as rp
        ppt, _project = _mk_enhanced(db, tmp_path, status="running")
        res = asyncio.run(rp._run_enhance(
            db, ppt["project_id"], "t", "x.pptx", None))
        assert res.status_code == 409
        assert "ENHANCE_IN_PROGRESS" in res.body.decode()

    def test_awaiting_gate_blocked(self, db, tmp_path):
        """门禁等待态同样算进行中：放弃重发起应先取消旧任务。"""
        import routers.ppts as rp
        ppt, _project = _mk_enhanced(db, tmp_path, status="awaiting_input")
        res = asyncio.run(rp._run_enhance(
            db, ppt["project_id"], "t", "x.pptx", None))
        assert res.status_code == 409

    def test_no_active_proceeds(self, db, tmp_path, monkeypatch):
        """无活跃增强 → 正常走 prepare（真实拷源）+ launch（mock）。"""
        import routers.ppts as rp
        src = tmp_path / "demo.pptx"
        src.write_text("PK", encoding="utf-8")
        proj_id = db.create_project("P", seed_playbook=False)["id"]
        monkeypatch.setattr(rp.deps, "db", db, raising=False)
        monkeypatch.setattr(
            ppt_service.service, "launch",
            lambda d, p, m: {**p, "status": "running"})
        res = asyncio.run(rp._run_enhance(
            db, proj_id, "导入增强", str(src), None))
        assert res["ppt"]["kind"] == "enhanced"
        assert res["ppt"]["status"] == "running"

    def test_concurrent_cross_entry_single_pass(self, db, tmp_path, monkeypatch):
        """TOCTOU 回归：enhance / enhance-import 交叉并发，检查与建行之间的
        await 曾让出事件循环致双过 409——per-project 锁串行化后仅一个入队。"""
        import routers.ppts as rp
        src = tmp_path / "demo.pptx"
        src.write_text("PK", encoding="utf-8")
        proj_id = db.create_project("P", seed_playbook=False)["id"]
        monkeypatch.setattr(rp.deps, "db", db, raising=False)
        monkeypatch.setattr(
            ppt_service.service, "launch",
            lambda d, p, m: {**p, "status": "running"})
        rp._enhance_locks.clear()

        async def _race():
            return await asyncio.gather(
                rp._run_enhance(db, proj_id, "a", str(src), None),
                rp._run_enhance(db, proj_id, "b", str(src), None),
            )

        results = asyncio.run(_race())
        ok = [r for r in results if isinstance(r, dict) and "ppt" in r]
        conflict = [r for r in results if hasattr(r, "status_code")]
        assert len(ok) == 1 and len(conflict) == 1
        assert conflict[0].status_code == 409
