"""PPT 模板工坊任务测试（2.3 §4.2，E2 库外置）。

覆盖：_stage_ref_files 素材就位（重名序号/缺失跳过）、_parse_spec_frontmatter
（扁平/流式数组/块数组/无 frontmatter）、_template_package_valid 工作区包校验
（限定名 spec + 结构完整）、_collect_template 收录（目录归一 + spec 库形态化 +
索引条目按 KIND_CONFIG 提取）、prepare_template 行字段与回滚、
_build_template_message 执行契约（project scope / init / shared validator /
禁注册 / brief 门）、_sync_template_progress 阶段映射（工作区产物 + 收录态）、
_finalize_template 分支（收录成功 completed / 收录失败 failed / 标记 awaiting /
否则 paused）、answer 模板语义（确认 / 修改意见循环）、recover_orphans 落位
（包完整→收录 / 不完整→暂停）、fetch_active container='templates' 分流、
list_template_jobs 过滤、路由层 id 校验 / delete 端点（用户库目标）。
"""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import Database
import ppt_service
from ppt_service import (
    PptService,
    _build_template_message,
    _collect_template,
    _parse_spec_frontmatter,
    _stage_ref_files,
    _template_collected,
    _template_package_valid,
    sync_progress,
)


@pytest.fixture()
def db():
    database = Database(db_path=":memory:")
    database.initialize()
    return database


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    """prepare_template 内部经 paths.get_data_dir() 建 task 工作区，收录目标
    用户库根也在 data 下：环境变量优先级重定向到 tmp，杜绝测试污染真实数据
    目录（python/data/tasks、python/data/ppt-library）。"""
    monkeypatch.setenv("INKCUBE_DATA_DIR", str(tmp_path / "data"))


# ── 工具 ──

def _mk_tpl(db, tmp_path, *, status=None, kind="brand", tid="tpl_a"):
    """建模板任务行（project_id='' = 全局资产、kind='template'），返回 ppt 行。"""
    task = db.create_task(
        title="[PPT模板] T", data_dir=str(tmp_path / "data"), origin="ppt")
    ws = Path(task["workspace_path"])
    (ws / "ppt_project").mkdir(parents=True, exist_ok=True)
    ppt = db.create_ppt(
        project_id="", task_id=task["id"], title="T", kind="template",
        workspace_path=str(ws / "ppt_project"),
        template_kind=kind, template_id=tid,
    )
    if status:
        db.update_ppt_status(ppt["id"], status)
        ppt = db.get_ppt(ppt["id"])
    return ppt


def _author_package(ppt_row, *, kind="brand", tid="tpl_a", with_svg=False,
                    fm_extra=""):
    """模拟 agent project scope 产物：工作区 ppt_project/templates/ 写限定名
    spec（layout/deck 结构型另加 SVG 页面原型）。"""
    tpl = Path(ppt_row["workspace_path"]) / "templates"
    tpl.mkdir(parents=True, exist_ok=True)
    spec = (
        "---\n"
        f"{kind}_id: {tid}\n"
        f"kind: {kind}\n"
        "summary: 测试模板摘要\n"
        'primary_color: "#336699"\n'
        "keywords: [a, b]\n"
        f"{fm_extra}"
        "---\n\n"
        "# 正文标题\n"
    )
    (tpl / f"design_spec.{kind}.{tid}.md").write_text(spec, encoding="utf-8")
    if with_svg:
        (tpl / "01_cover.svg").write_text("<svg/>", encoding="utf-8")
    return tpl


def _lib_root(tmp_path) -> Path:
    return tmp_path / "data" / "ppt-library" / "templates"


def _fake_init_skeleton(workspace, *, canvas_format="ppt169"):
    """_init_project_skeleton 测试替身：同款目录名契约（带后缀），建目录即返回
    （prepare_template 编排链的系统 init 段替身，对齐 test_ppt_service）。"""
    from datetime import datetime
    target = Path(workspace) / f"ppt_project_{canvas_format}_{datetime.now():%Y%m%d}"
    target.mkdir(parents=True)
    return target


# ── 素材就位 ──

class TestStageRefFiles:
    def test_copies_relative_paths(self, tmp_path):
        src = tmp_path / "ref.pptx"
        src.write_text("PK", encoding="utf-8")
        ws = tmp_path / "ws"
        staged = _stage_ref_files(str(ws), [str(src)])
        assert staged == ["staging/refs/ref.pptx"]
        assert (ws / "staging" / "refs" / "ref.pptx").read_text(encoding="utf-8") == "PK"

    def test_duplicate_names_numbered(self, tmp_path):
        a = tmp_path / "a" / "logo.png"
        b = tmp_path / "b" / "logo.png"
        for p in (a, b):
            p.parent.mkdir(parents=True)
            p.write_text("x", encoding="utf-8")
        staged = _stage_ref_files(str(tmp_path / "ws"), [str(a), str(b)])
        assert staged == ["staging/refs/logo.png", "staging/refs/logo_1.png"]

    def test_missing_skipped(self, tmp_path):
        src = tmp_path / "ok.md"
        src.write_text("c", encoding="utf-8")
        staged = _stage_ref_files(
            str(tmp_path / "ws"), [str(src), str(tmp_path / "gone.pptx")])
        assert staged == ["staging/refs/ok.md"]

    def test_empty_no_dir_content(self, tmp_path):
        assert _stage_ref_files(str(tmp_path / "ws"), []) == []


# ── frontmatter 扁平解析（收录条目提取源）──

class TestParseSpecFrontmatter:
    def test_flat_quoted_and_flow_list(self):
        fm = _parse_spec_frontmatter(
            "---\nkind: brand\nsummary: 带 引号 的摘要\n"
            'primary_color: "#336699"\nkeywords: [a, b]\n---\n\n# 正文')
        assert fm == {"kind": "brand", "summary": "带 引号 的摘要",
                      "primary_color": "#336699", "keywords": ["a", "b"]}

    def test_block_list(self):
        fm = _parse_spec_frontmatter(
            "---\npage_types:\n  - hero_full\n  - split\n---\n正文")
        assert fm == {"page_types": ["hero_full", "split"]}

    def test_no_frontmatter(self):
        assert _parse_spec_frontmatter("# 无 frontmatter") == {}

    def test_stops_at_closing_fence(self):
        fm = _parse_spec_frontmatter("---\nkind: brand\n---\n| primary | #111 |")
        assert fm == {"kind": "brand"}


# ── 工作区包校验（E2 完成判定基线）──

class TestTemplatePackageValid:
    def test_brand_spec_only(self, db, tmp_path):
        ppt = _mk_tpl(db, tmp_path)
        _author_package(db.get_ppt(ppt["id"]))
        assert _template_package_valid(db.get_ppt(ppt["id"])) is True

    def test_missing_spec_false(self, db, tmp_path):
        ppt = _mk_tpl(db, tmp_path)
        assert _template_package_valid(db.get_ppt(ppt["id"])) is False

    def test_layout_requires_svg(self, db, tmp_path):
        """结构型（Layout/Deck）无 SVG 页面原型 = 结构不完整。"""
        ppt = _mk_tpl(db, tmp_path, kind="layout", tid="lay_a")
        _author_package(db.get_ppt(ppt["id"]), kind="layout", tid="lay_a")
        assert _template_package_valid(db.get_ppt(ppt["id"])) is False
        _author_package(db.get_ppt(ppt["id"]), kind="layout", tid="lay_a",
                        with_svg=True)
        assert _template_package_valid(db.get_ppt(ppt["id"])) is True


# ── 收录（E2 库外置核心步骤）──

class TestCollectTemplate:
    def test_collect_brand(self, db, tmp_path):
        """收录成功：库目录归一（spec 库形态 design_spec.md）+ 索引条目按
        KIND_CONFIG 提取；工作区原包保留（收录是拷贝不是搬移）。"""
        ppt = _mk_tpl(db, tmp_path)
        row = db.get_ppt(ppt["id"])
        ws = _author_package(row)
        ok, reason = _collect_template(row)
        assert (ok, reason) == (True, "")
        lib_ws = _lib_root(tmp_path) / "brands" / "tpl_a"
        assert (lib_ws / "templates" / "design_spec.md").is_file()
        assert not (lib_ws / "templates" / "design_spec.brand.tpl_a.md").exists()
        index = json.loads(
            (_lib_root(tmp_path) / "brands" / "brands_index.json").read_text(
                encoding="utf-8"))
        assert index["tpl_a"]["summary"] == "测试模板摘要"
        assert index["tpl_a"]["primary_color"] == "#336699"
        assert (ws / "design_spec.brand.tpl_a.md").is_file()
        assert _template_collected(row) is True

    def test_collect_carries_assets(self, db, tmp_path):
        """images/icons/exports 存在才随包拷入（Review PPTX 预览数据源）。"""
        ppt = _mk_tpl(db, tmp_path)
        row = db.get_ppt(ppt["id"])
        project = Path(row["workspace_path"])
        _author_package(row)
        (project / "images").mkdir()
        (project / "images" / "logo.png").write_bytes(b"png")
        (project / "exports").mkdir()
        (project / "exports" / "tpl_a_template_preview.pptx").write_bytes(b"PK")
        ok, _ = _collect_template(row)
        assert ok
        lib_ws = _lib_root(tmp_path) / "brands" / "tpl_a"
        assert (lib_ws / "images" / "logo.png").is_file()
        assert (lib_ws / "exports" / "tpl_a_template_preview.pptx").is_file()

    def test_collect_invalid_package_rejected(self, db, tmp_path):
        ppt = _mk_tpl(db, tmp_path)
        ok, reason = _collect_template(db.get_ppt(ppt["id"]))
        assert ok is False
        assert "不完整" in reason

    def test_collect_failure_reason(self, db, tmp_path, monkeypatch):
        """收录 IO 失败 → (False, 原因)（finalize 据此置 failed，禁静默降级）。"""
        ppt = _mk_tpl(db, tmp_path)
        row = db.get_ppt(ppt["id"])
        _author_package(row)

        def _boom(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr(ppt_service, "shutil",
                            SimpleNamespace(rmtree=lambda *a, **k: None,
                                            copytree=_boom))
        ok, reason = _collect_template(row)
        assert ok is False
        assert "收录失败" in reason and "disk full" in reason


# ── 收录重试（review P2-3 回归：failed 行免 agent 自助收录）──

class TestRetryCollect:
    def test_success_collects_and_completes(self, db, tmp_path):
        """包完整 + failed → 重收录成功置 completed，不跑 agent（纯本地幂等）。"""
        ppt = _mk_tpl(db, tmp_path)
        _author_package(db.get_ppt(ppt["id"]))
        db.update_ppt_status(ppt["id"], "failed", error="收录失败: disk full")
        assert ppt_service.service.retry_collect(db, ppt["id"]) == {"ok": True}
        assert db.get_ppt(ppt["id"])["status"] == "completed"
        assert (_lib_root(tmp_path) / "brands" / "tpl_a"
                / "templates" / "design_spec.md").is_file()

    def test_incomplete_package_rejected(self, db, tmp_path):
        ppt = _mk_tpl(db, tmp_path)
        db.update_ppt_status(ppt["id"], "failed", error="x")
        res = ppt_service.service.retry_collect(db, ppt["id"])
        assert res["code"] == "TEMPLATE_PACKAGE_INCOMPLETE"
        assert db.get_ppt(ppt["id"])["status"] == "failed"  # 状态不被误动

    def test_non_failed_rejected(self, db, tmp_path):
        """非 failed（running/paused/completed）不可重试：在跑行与收录
        重试并发会双写库；已完成行重收录属用户库管理范畴（重扫/删除）。"""
        ppt = _mk_tpl(db, tmp_path)
        _author_package(db.get_ppt(ppt["id"]))
        for status in ("running", "paused", "completed"):
            db.update_ppt_status(ppt["id"], status)
            res = ppt_service.service.retry_collect(db, ppt["id"])
            assert res["code"] == "TEMPLATE_JOB_NOT_FAILED"

    def test_non_template_rejected(self, db, tmp_path):
        """deck 行不支持收录重试（收录是模板工坊语义）。"""
        task = db.create_task(
            title="[PPT] D", data_dir=str(tmp_path / "data"), origin="ppt")
        ppt = db.create_ppt(
            project_id="", task_id=task["id"], title="D", kind="deck",
            workspace_path=str(Path(task["workspace_path"]) / "ppt_project"),
        )
        assert ppt_service.service.retry_collect(db, ppt["id"])["code"] == \
            "NOT_TEMPLATE_JOB"

    def test_not_found(self, db):
        assert ppt_service.service.retry_collect(db, "nope")["code"] == \
            "PPT_NOT_FOUND"

    def test_collect_failure_stays_failed(self, db, tmp_path, monkeypatch):
        """重试仍失败（IO 再坏）：保持 failed 带新原因，fail 通知去重读。"""
        ppt = _mk_tpl(db, tmp_path)
        row = db.get_ppt(ppt["id"])
        _author_package(row)
        db.update_ppt_status(ppt["id"], "failed", error="old")

        def _boom(*a, **k):
            raise OSError("disk full again")

        monkeypatch.setattr(ppt_service, "shutil",
                            SimpleNamespace(rmtree=lambda *a, **k: None,
                                            copytree=_boom))
        res = ppt_service.service.retry_collect(db, ppt["id"])
        assert res["code"] == "TEMPLATE_COLLECT_FAILED"
        assert "disk full again" in db.get_ppt(ppt["id"])["error"]


# ── prepare：行字段 / 素材 / 首条消息 / 回滚 ──

class TestPrepareTemplate:
    def test_row_fields_global_asset(self, db, tmp_path, monkeypatch):
        monkeypatch.setattr(ppt_service, "_init_project_skeleton",
                            _fake_init_skeleton)
        ppt, message = ppt_service.service.prepare_template(
            db, kind="brand", template_id="co_blue",
            template_name="公司品牌蓝", ref_paths=[], notes=None)
        assert ppt["project_id"] == ""       # 全局资产不隶属项目
        assert ppt["kind"] == "template"
        assert ppt["template_kind"] == "brand"
        assert ppt["template_id"] == "co_blue"
        assert ppt["title"] == "公司品牌蓝"
        assert ppt["status"] == "running"
        task = db.get_task(ppt["task_id"])
        assert task["origin"] == "ppt"
        assert task["title"].startswith("[PPT模板]")

    def test_refs_staged(self, db, tmp_path, monkeypatch):
        monkeypatch.setattr(ppt_service, "_init_project_skeleton",
                            _fake_init_skeleton)
        ref = tmp_path / "deck.pptx"
        ref.write_text("PK", encoding="utf-8")
        ppt, message = ppt_service.service.prepare_template(
            db, kind="deck", template_id="d1", template_name="D",
            ref_paths=[str(ref)], notes=None)
        assert "staging/refs/deck.pptx" in message
        task_ws = Path(db.get_task(ppt["task_id"])["workspace_path"])
        assert (task_ws / "staging" / "refs" / "deck.pptx").is_file()

    def test_rollback_no_orphan_rows(self, db, tmp_path, monkeypatch):
        """首条消息组装失败（如素材异常冒泡）：task/ppt 行全部回滚。"""
        def _boom(*a, **k):
            raise OSError("disk full")
        # 系统先 init 再组装消息：init 用替身，让失败点落在消息组装
        monkeypatch.setattr(ppt_service, "_init_project_skeleton",
                            _fake_init_skeleton)
        monkeypatch.setattr(ppt_service, "_build_template_message", _boom)
        with pytest.raises(OSError):
            ppt_service.service.prepare_template(
                db, kind="brand", template_id="x", template_name="X")
        assert db.list_template_jobs() == ([], False)
        task_rows, _ = db.list_tasks(origin="ppt")
        assert task_rows == []


class TestTemplateMessageContract:
    def test_contract_keywords(self, tmp_path):
        msg = _build_template_message(
            str(tmp_path / "ws" / "ppt_project"), str(tmp_path / "ws"),
            "brand", "co_blue", "公司品牌蓝",
            ["staging/refs/logo.png"], "深蓝主色")
        # 固定入口与类型锁定
        assert "Create Template" in msg
        assert "brand" in msg
        # project scope：init 已由系统执行（声明目录 + 禁止重复 init）
        # + 限定名 spec + shared validator
        assert "系统已初始化项目骨架" in msg
        assert "禁止重复执行 init" in msg
        assert "design_spec.brand.co_blue.md" in msg
        assert "svg_quality_checker.py" in msg
        assert "--template-mode" in msg
        # 文字门纪律：echo brief 后发标记并立即结束回合
        assert "[TEMPLATE_BRIEF_CONFIRMED]" in msg
        assert "结束你的回合" in msg
        # 禁注册：禁令语境提及（不运行 register_template.py、不写全局索引），
        # 收录由系统负责
        assert "不运行 register_template.py" in msg
        assert "收录" in msg
        # 素材与补充要求
        assert "staging/refs/logo.png" in msg
        assert "深蓝主色" in msg

    def test_no_refs_type_e(self, tmp_path):
        msg = _build_template_message(
            str(tmp_path / "ws" / "ppt_project"), str(tmp_path / "ws"),
            "style", "s1", "S", [], None)
        assert "Type E" in msg


# ── 阶段映射（工作区产物 + 收录态）──

class TestSyncTemplateProgress:
    def test_intake_default(self, db, tmp_path):
        ppt = _mk_tpl(db, tmp_path)
        sync_progress(db, db.get_ppt(ppt["id"]))
        assert db.get_ppt(ppt["id"])["stage"] == "intake"

    def test_gate_awaiting_stays_strategy(self, db, tmp_path):
        """brief 门等待态：sync 短路为 strategy，不因产物缺失回落 intake。"""
        ppt = _mk_tpl(db, tmp_path, status="awaiting_input")
        sync_progress(db, db.get_ppt(ppt["id"]))
        assert db.get_ppt(ppt["id"])["stage"] == "strategy"

    def test_authoring_on_workspace_spec(self, db, tmp_path):
        ppt = _mk_tpl(db, tmp_path)
        _author_package(db.get_ppt(ppt["id"]))
        sync_progress(db, db.get_ppt(ppt["id"]))
        assert db.get_ppt(ppt["id"])["stage"] == "authoring"

    def test_export_on_collected(self, db, tmp_path):
        ppt = _mk_tpl(db, tmp_path)
        row = db.get_ppt(ppt["id"])
        _author_package(row)
        assert _collect_template(row)[0]
        sync_progress(db, db.get_ppt(ppt["id"]))
        assert db.get_ppt(ppt["id"])["stage"] == "export"


# ── 回合结束终态（收录 / 文字门）──

class TestFinalizeTemplate:
    def test_package_collected_completed(self, db, tmp_path):
        """工作区包就绪 → 收录进用户库 → completed + completed 通知。"""
        ppt = _mk_tpl(db, tmp_path)
        _author_package(db.get_ppt(ppt["id"]))
        PptService()._finalize(db, ppt["id"])
        cur = db.get_ppt(ppt["id"])
        assert cur["status"] == "completed"
        assert any(
            n["entity_id"] == ppt["id"] and n["kind"] == "completed"
            for n in db.list_unread_notifications()
        )

    def test_collect_failure_failed_with_reason(self, db, tmp_path, monkeypatch):
        """收录失败 → failed（带原因），不静默降级 paused。"""

        def _boom(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr(ppt_service, "shutil",
                            SimpleNamespace(rmtree=lambda *a, **k: None,
                                            copytree=_boom))
        ppt = _mk_tpl(db, tmp_path)
        _author_package(db.get_ppt(ppt["id"]))
        PptService()._finalize(db, ppt["id"])
        cur = db.get_ppt(ppt["id"])
        assert cur["status"] == "failed"
        assert "收录失败" in cur["error"]
        assert any(
            n["entity_id"] == ppt["id"] and n["kind"] == "failed"
            for n in db.list_unread_notifications()
        )

    def test_brief_marker_awaiting(self, db, tmp_path):
        """Step 3 门：echo brief + 标记后结束回合 → awaiting_input 等确认。"""
        ppt = _mk_tpl(db, tmp_path)
        db.save_task_message(
            ppt["task_id"], "assistant",
            "## 模板 brief\n...\n\n[TEMPLATE_BRIEF_CONFIRMED]")
        PptService()._finalize(db, ppt["id"])
        cur = db.get_ppt(ppt["id"])
        assert cur["status"] == "awaiting_input"
        assert cur["stage"] == "strategy"
        assert any(
            n["entity_id"] == ppt["id"] and n["kind"] == "awaiting_input"
            for n in db.list_unread_notifications()
        )

    def test_no_marker_paused(self, db, tmp_path):
        """回合结束既无包也无门标记（迭代耗尽/中途收尾）→ 可恢复暂停。"""
        ppt = _mk_tpl(db, tmp_path)
        db.save_task_message(ppt["task_id"], "assistant", "分析了一半素材")
        PptService()._finalize(db, ppt["id"])
        cur = db.get_ppt(ppt["id"])
        assert cur["status"] == "paused"
        assert any(
            n["entity_id"] == ppt["id"] and n["kind"] == "paused"
            for n in db.list_unread_notifications()
        )

    def test_terminal_untouched(self, db, tmp_path):
        ppt = _mk_tpl(db, tmp_path, status="completed")
        PptService()._finalize(db, ppt["id"])
        assert db.get_ppt(ppt["id"])["status"] == "completed"


# ── answer 模板语义（确认 / 修改意见循环）──

class TestAnswerTemplate:
    def _prepare(self, db, tmp_path, monkeypatch, status="awaiting_input"):
        ppt = _mk_tpl(db, tmp_path, status=status)
        monkeypatch.setattr(ppt_service, "_task_running", lambda tid: False)
        calls = []
        monkeypatch.setattr(
            PptService, "_start_drive", lambda self_, d, p, m: calls.append(m))
        return ppt, calls

    def test_confirm_null_text(self, db, tmp_path, monkeypatch):
        """空文本 = 确认：前提是 agent 已提案（最后一条 assistant 带标记，
        P2-1 后无标记的行不再注入「已确认」虚假前提）。"""
        ppt, calls = self._prepare(db, tmp_path, monkeypatch)
        db.save_task_message(
            ppt["task_id"], "assistant", "提案如下……\n[TEMPLATE_BRIEF_CONFIRMED]")
        assert PptService().answer(db, ppt["id"], None) == {"ok": True}
        assert "已确认模板 brief" in calls[0]
        assert "svg_quality_checker" in calls[0]
        assert "收录" in calls[0]
        assert "register_template.py" not in calls[0]

    def test_confirm_without_marker_neutral(self, db, tmp_path, monkeypatch):
        """无标记（brief 未提案/回合中断）：空文本不发「已确认」，
        改发中性继续消息并要求按契约补提案（P2-1 回归，另见
        test_ppt_service.py TestAnswerGateAware 全套）。"""
        ppt, calls = self._prepare(db, tmp_path, monkeypatch)
        db.save_task_message(ppt["task_id"], "assistant", "你好，我是创建助手。")
        PptService().answer(db, ppt["id"], None)
        assert "已确认模板 brief" not in calls[0]
        assert "TEMPLATE_BRIEF_CONFIRMED" in calls[0]

    def test_notes_repropose_loop(self, db, tmp_path, monkeypatch):
        """修改意见：要求重新提案并再次走门（循环至确认）。"""
        ppt, calls = self._prepare(db, tmp_path, monkeypatch)
        assert PptService().answer(db, ppt["id"], "主色改成绿色") == {"ok": True}
        assert "修改意见" in calls[0]
        assert "主色改成绿色" in calls[0]
        assert "[TEMPLATE_BRIEF_CONFIRMED]" in calls[0]

    def test_rejected_while_running(self, db, tmp_path, monkeypatch):
        ppt = _mk_tpl(db, tmp_path)
        monkeypatch.setattr(ppt_service, "_task_running", lambda tid: True)
        assert PptService().answer(db, ppt["id"])["code"] == "TASK_RUNNING"


# ── 启动恢复落位（工作区产物 + 收录态）──

class TestRecoverOrphansTemplate:
    def test_package_complete_collected_completed(self, db, tmp_path):
        """重启时包完整但未收录 → 补收录 → completed。"""
        ppt = _mk_tpl(db, tmp_path)  # running
        _author_package(db.get_ppt(ppt["id"]))
        PptService().recover_orphans(db)
        assert db.get_ppt(ppt["id"])["status"] == "completed"
        assert _template_collected(db.get_ppt(ppt["id"])) is True

    def test_package_incomplete_paused(self, db, tmp_path):
        ppt = _mk_tpl(db, tmp_path)
        PptService().recover_orphans(db)
        assert db.get_ppt(ppt["id"])["status"] == "paused"
        assert any(
            n["entity_id"] == ppt["id"] and n["kind"] == "paused"
            for n in db.list_unread_notifications()
        )


# ── 活动注册表 source 分流（全局资产导航到模板分区）──

class TestFetchActiveTemplate:
    def test_container_templates(self, db, tmp_path):
        ppt = _mk_tpl(db, tmp_path, status="awaiting_input")
        items = PptService.fetch_active(db)
        assert len(items) == 1
        item = items[0]
        assert item["entity_id"] == ppt["id"]
        assert item["container"] == "templates"   # 前端导航分流到模板管理页
        assert item["project_name"] is None       # 无项目实体
        assert item["status"] == "awaiting_input"

    def test_deck_task_still_project(self, db, tmp_path):
        """普通 PPT 任务不受分流影响（LEFT JOIN 后 kind 判定正确）。"""
        proj_id = db.create_project("P", seed_playbook=False)["id"]
        task = db.create_task(title="t", data_dir=str(tmp_path / "d"), origin="ppt")
        ws = Path(task["workspace_path"])
        (ws / "ppt_project").mkdir(parents=True, exist_ok=True)
        db.create_ppt(project_id=proj_id, task_id=task["id"], title="T",
                      workspace_path=str(ws / "ppt_project"))
        items = PptService.fetch_active(db)
        assert len(items) == 1
        assert items[0]["container"] == "project"
        assert items[0]["project_name"] == "P"


# ── 任务列表过滤（模板分区轮询数据源）──

class TestListTemplateJobs:
    def test_only_template_rows(self, db, tmp_path):
        tpl = _mk_tpl(db, tmp_path)
        proj_id = db.create_project("P", seed_playbook=False)["id"]
        task = db.create_task(title="t", data_dir=str(tmp_path / "d"), origin="ppt")
        ws = Path(task["workspace_path"])
        (ws / "ppt_project").mkdir(parents=True, exist_ok=True)
        db.create_ppt(project_id=proj_id, task_id=task["id"], title="T",
                      workspace_path=str(ws / "ppt_project"))
        rows, has_more = db.list_template_jobs()
        assert not has_more
        assert [r["id"] for r in rows] == [tpl["id"]]
        assert rows[0]["template_kind"] == "brand"
        assert rows[0]["template_id"] == "tpl_a"


# ── 路由层：id 校验收紧 / delete 端点（E2：目标恒为用户库）──

class TestValidateTemplateId:
    """_ID_SAFE_RE 禁空白与引号：含空格/引号的 id 在命令拼装场景会 shell
    分词断裂、引号上下文破坏，一律拒绝。"""

    def test_accepts_plain_and_chinese(self):
        import routers.ppt_templates as rt
        for tid in ("co_blue", "Co-Blue.1", "公司品牌蓝"):
            assert rt._validate_template_id(tid) is None

    def test_rejects_whitespace_and_quotes(self):
        import routers.ppt_templates as rt
        for tid in ("co blue", "co'blue", 'co"blue', "a\tb"):
            res = rt._validate_template_id(tid)
            assert res is not None and res.status_code == 400
            assert "INVALID_TEMPLATE_ID" in res.body.decode()


class TestDeleteTemplateEndpoint:
    def _mk_lib(self, tmp_path, monkeypatch):
        """伪造用户库（含已收录 brand/co_blue）并注入 _templates_root。"""
        import routers.ppt_templates as rt
        root = tmp_path / "data" / "ppt-library" / "templates"
        ws = root / "brands" / "co_blue"
        (ws / "templates").mkdir(parents=True)
        (ws / "templates" / "design_spec.md").write_text("s", encoding="utf-8")
        (root / "brands" / "brands_index.json").write_text(
            json.dumps({"co_blue": {"summary": "s"}}, ensure_ascii=False),
            encoding="utf-8")
        monkeypatch.setattr(rt, "_templates_root", lambda: root)
        # 内置判定与本组用例无关：源定位显式置空，杜绝开发机回退真实 resources 的环境耦合
        monkeypatch.setattr(rt, "builtin_skill_source", lambda name: None)
        # 6b 删除连带清理走 deps.db：注入内存库，防触及全局单例
        import deps
        from db.database import Database
        database = Database(db_path=":memory:")
        database.initialize()
        monkeypatch.setattr(deps, "db", database)
        return root

    def test_delete_registered_template(self, tmp_path, monkeypatch):
        """回归：路径穿越终检曾因 (…).resolve() + os.sep（Path+str）TypeError
        致端点恒 500、模板删除整体不可用——本用例踩完整删除链。"""
        import asyncio
        import routers.ppt_templates as rt
        root = self._mk_lib(tmp_path, monkeypatch)
        res = asyncio.run(rt.delete_template("brand", "co_blue"))
        assert res["ok"] is True
        assert not (root / "brands" / "co_blue").exists()
        index = json.loads(
            (root / "brands" / "brands_index.json").read_text(encoding="utf-8"))
        assert "co_blue" not in index

    def test_delete_rejects_dotdot(self, tmp_path, monkeypatch):
        import asyncio
        import routers.ppt_templates as rt
        self._mk_lib(tmp_path, monkeypatch)
        res = asyncio.run(rt.delete_template("brand", ".."))
        assert res.status_code == 400
