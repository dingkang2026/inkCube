"""python/tests/test_forms_api.py — 填表系统路由层测试（2.4 §7 步骤 2 验收）。

覆盖：DB CRUD / seed 幂等 / 崩溃恢复 / 进度单调、两个后台任务全链
（fake LLM + fake 检索 → 通知落库）、活动快照（form_template / form_fill）、
终态通知查重、路由准入（409 模板未就绪 / 429 并发限制 / 409 被引用删除阻断）、
人工修订全量重放（真实 docx 写入）。
"""
import asyncio
import io
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import Database
from docx import Document


# ── fixtures / 桩 ──

@pytest.fixture()
def db(monkeypatch):
    import deps
    with tempfile.TemporaryDirectory() as td:
        db = Database(os.path.join(td, "t.db"))
        db.initialize()
        db.ensure_preset_base_libs()
        # 后台任务经 forms.get_db() → deps.db 取库，fixture 级统一注入
        monkeypatch.setattr(deps, "db", db)
        yield db, td
        db.conn.close()


class _FakeLLM:
    """LLMClient 桩：按调用序返回预设响应"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def chat_full(self, messages):
        self.calls.append(messages)
        return {"content": self._responses.pop(0), "finish_reason": "stop", "usage": {}}


def _install_fake_llm(monkeypatch, responses):
    import llm.client as llm_mod
    fake = _FakeLLM(responses)
    monkeypatch.setattr(llm_mod, "LLMClient", lambda settings: fake)
    return fake


class _FakeSearch:
    async def search(self, query, project_id, top_k=5, **kw):
        return [{"id": f"c-{query}", "content": f"关于{query}的资料",
                 "_score": 0.9, "metadata": {}}]


@pytest.fixture()
def client(monkeypatch, db):
    import deps
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import forms as forms_mod
    db_, td = db
    monkeypatch.setattr(deps, "db", db_)
    monkeypatch.setattr(forms_mod, "get_data_dir", lambda: td)
    monkeypatch.setattr(forms_mod, "ensure_ready", lambda feature: None)
    # 后台任务桩：记录调用，不真跑分析/填表（真跑链路有专项用例）
    calls = {"analysis": [], "fill": []}

    async def _fake_analysis(template_id):
        calls["analysis"].append(template_id)

    async def _fake_fill(fill_id, data_dir):
        calls["fill"].append(fill_id)

    monkeypatch.setattr(forms_mod, "_run_template_analysis", _fake_analysis)
    monkeypatch.setattr(forms_mod, "_run_form_fill", _fake_fill)
    app = FastAPI()
    app.include_router(forms_mod.router)
    yield TestClient(app), db_, td, calls


# ── 测试数据 ──

def _form_docx(path):
    """最小可用表单：2 行 2 列（标签列 + 值列），1 个 text + 1 个 doc_section"""
    doc = Document()
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).paragraphs[0].add_run("项目名称")
    table.cell(1, 0).paragraphs[0].add_run("公司简介")
    table.cell(1, 1).paragraphs[0].add_run("请说明公司主营业务与核心产品（不少于200字）")
    doc.save(path)
    return path


_FIELD_TEXT = {
    "field_key": "project_name", "label": "项目名称", "fill_type": "text",
    "fill_mode": "kb_fill", "target": "top0/r0c1", "placeholder": None,
    "context": {"left_label": "项目名称", "section_chain": [],
                "table_header": None, "instruction": None},
    "kb_query": [], "confidence": "high",
}
_FIELD_DOC = {
    "field_key": "company_profile", "label": "公司简介", "fill_type": "doc_section",
    "fill_mode": "kb_fill", "target": "top0/r1c1", "placeholder": None,
    "context": {"left_label": "公司简介", "section_chain": [],
                "table_header": None, "instruction": "一、主营业务 二、核心产品"},
    "instruction": "一、主营业务 二、核心产品",
    "sections": ["一、主营业务与核心产品情况", "二、核心产品竞争力说明"],
    "length_hint": 200, "kb_query": ["主营业务"], "confidence": "high",
}
_SCHEMA = {
    "schema_version": "2.0", "template_name": "测试模板",
    "doc_stats": {"top_tables": 1, "nested_tables": 0},
    "fields": [_FIELD_TEXT, _FIELD_DOC],
    "batches": [{"seq": 1, "name": "全部",
                 "field_keys": ["project_name", "company_profile"],
                 "depends_on": []}],
    "unmapped_cells": [],
}
_LLM_ANALYSIS_OUTPUT = {
    "fields": [
        {"index": 0, "field_key": "project_name", "fill_type": "text",
         "fill_mode": "kb_fill", "kb_query": ["项目名称"], "length_hint": None,
         "confidence": "high", "multi_select": None},
        {"index": 1, "field_key": "company_profile", "fill_type": "doc_section",
         "fill_mode": "kb_fill", "kb_query": ["主营业务"], "length_hint": 200,
         "confidence": "high", "multi_select": None},
    ],
    "batches": [{"seq": 1, "name": "全部",
                 "field_keys": ["project_name", "company_profile"],
                 "depends_on": []}],
    "skipped_indexes": [],
}
_FILL_OUTPUT = {
    "project_name": "智造科技有限公司",
    "company_profile": "一、公司主营智能制造装备的研发与销售。",
}


# ── DB 层：seed / CRUD / 恢复 / 进度单调 ──

def test_seed_form_templates_idempotent(db):
    db_, _ = db
    tpl = {"id": "tpl_x", "name": "预置甲", "source": "preset", "file_path": "x.docx"}
    assert db_.seed_form_templates([tpl]) == ["tpl_x"]
    # 已存在（且已分析成功）→ 不覆盖分析产物
    db_.update_form_template_schema("tpl_x", {"fields": [1]}, {"f": 1}, 1)
    assert db_.seed_form_templates([tpl]) == []
    row = db_.get_form_template("tpl_x")
    assert row["status"] == "success" and row["template_schema"] is not None


def test_seed_form_templates_migrates_preset_path(db):
    """预置文件随版本移动/改名：已存在记录仅对齐 file_path，分析产物保留不重分析"""
    db_, _ = db
    old = {"id": "tpl_x", "name": "预置甲", "source": "preset", "file_path": "old/甲.docx"}
    assert db_.seed_form_templates([old]) == ["tpl_x"]
    db_.update_form_template_schema("tpl_x", {"fields": [1]}, {"f": 1}, 1)
    new = {"id": "tpl_x", "name": "预置甲", "source": "preset", "file_path": "new/x.docx"}
    assert db_.seed_form_templates([new]) == []  # 不重新插入/不置回 pending
    row = db_.get_form_template("tpl_x")
    assert row["file_path"] == "new/x.docx"
    assert row["status"] == "success" and row["template_schema"] is not None


def test_seed_form_templates_with_preset_schema(db):
    """带出厂预解析产物的条目 seed 即 success（新用户零 LLM 开箱即用，2.4 §3.1）"""
    db_, _ = db
    tpl = {"id": "tpl_x", "name": "预置甲", "source": "preset", "file_path": "x.docx",
           "template_schema": json.dumps(_SCHEMA, ensure_ascii=False),
           "llm_mapping": json.dumps(_LLM_ANALYSIS_OUTPUT, ensure_ascii=False),
           "field_count": 2, "file_hash": "abc123"}
    assert db_.seed_form_templates([tpl]) == ["tpl_x"]
    row = db_.get_form_template("tpl_x")
    assert row["status"] == "success"
    assert row["field_count"] == 2 and row["file_hash"] == "abc123"
    assert json.loads(row["template_schema"])["schema_version"] == "2.0"
    assert json.loads(row["llm_mapping"])["fields"][0]["field_key"] == "project_name"
    # 直接可用：不落 pending，启动拾取零拾取
    assert db_.list_form_template_ids_by_status("pending") == []


def test_load_preset_schema_hash_pairing(tmp_path):
    """出厂产物 hash 配对校验（2.4 §3.1）：配对带出产物；
    docx 变更未重预生成 / 产物缺失 → 回退 None（绝不落错位 schema）"""
    from routers.forms import _load_preset_schema, sha256_file
    docx_path = str(tmp_path / "t.docx")
    _form_docx(docx_path)
    preset = {"preset_schema_version": 1, "field_count": 2,
              "file_hash": sha256_file(docx_path),
              "template_schema": _SCHEMA, "llm_mapping": _LLM_ANALYSIS_OUTPUT}
    schema_path = tmp_path / "t.schema.json"
    schema_path.write_text(json.dumps(preset, ensure_ascii=False), encoding="utf-8")
    out = _load_preset_schema(docx_path)
    assert out is not None and out["field_count"] == 2
    assert json.loads(out["template_schema"])["template_name"] == "测试模板"
    # docx 变更未重跑预生成：hash 不配对 → 回退 None
    doc = Document(docx_path)
    doc.add_paragraph("变更内容")
    doc.save(docx_path)
    assert _load_preset_schema(docx_path) is None
    # 产物缺失 / 非法 JSON / 形状错误 / 版本不认识 → 均回退 None
    os.remove(schema_path)
    assert _load_preset_schema(docx_path) is None
    schema_path.write_text("{invalid", encoding="utf-8")
    assert _load_preset_schema(docx_path) is None
    schema_path.write_text(
        json.dumps({"preset_schema_version": 1,
                    "file_hash": sha256_file(docx_path),
                    "template_schema": ["not-a-dict"],
                    "llm_mapping": {}}), encoding="utf-8")
    assert _load_preset_schema(docx_path) is None
    schema_path.write_text(
        json.dumps({"preset_schema_version": 99,
                    "file_hash": sha256_file(docx_path),
                    "template_schema": _SCHEMA,
                    "llm_mapping": _LLM_ANALYSIS_OUTPUT}), encoding="utf-8")
    assert _load_preset_schema(docx_path) is None


def test_form_template_crud_flow(db):
    db_, _ = db
    db_.create_form_template("t1", "上传甲", "upload", "a.docx")
    # 列表轻量视图：不含 schema/mapping 大列（file_path/file_hash 为比对所需）
    rows = db_.list_form_templates()
    assert len(rows) == 1 and "template_schema" not in rows[0]
    assert "llm_mapping" not in rows[0] and "file_path" in rows[0]
    # 状态流转 + schema 落盘
    db_.update_form_template_status("t1", "analyzing")
    assert db_.get_form_template("t1")["status"] == "analyzing"
    db_.update_form_template_schema("t1", {"fields": []}, {"fields": []}, 0)
    row = db_.get_form_template("t1")
    assert row["status"] == "success" and row["field_count"] == 0 and row["error"] is None
    # 重解析 hint
    db_.update_form_template_hint("t1", "选项拆分不对")
    row = db_.get_form_template("t1")
    assert row["user_hint"] == "选项拆分不对" and row["status"] == "pending"
    assert db_.delete_form_template("t1") is True
    assert db_.get_form_template("t1") is None


def test_form_fill_progress_monotonic(db):
    """进度单调递增（2.4 §5.4 不变量 4）：SQL MAX 保护，不因批次失败回退"""
    db_, _ = db
    db_.create_form_template("t1", "T", "preset", "x.docx")
    pid = db_.create_project(name="P")["id"]
    fill = db_.create_form_fill(pid, "t1", "F")
    db_.update_form_fill_status(fill["id"], "filling", progress=50)
    db_.update_form_fill_status(fill["id"], "filling", progress=10)  # 回退被 MAX 拦下
    assert db_.get_form_fill(fill["id"])["progress"] == 50
    db_.update_form_fill_status(fill["id"], "filling", progress=80)
    assert db_.get_form_fill(fill["id"])["progress"] == 80


def test_form_fill_result_then_revision(db):
    db_, _ = db
    db_.create_form_template("t1", "T", "preset", "x.docx")
    pid = db_.create_project(name="P")["id"]
    fill = db_.create_form_fill(pid, "t1", "F")
    db_.update_form_fill_result(fill["id"], [{"k": 1}], {"a": "b"}, "out.docx")
    row = db_.get_form_fill(fill["id"])
    assert row["status"] == "success" and row["progress"] == 100
    # 人工修订：值集/报告更新，status 保持 success
    db_.update_form_fill_values(fill["id"], {"a": "c"}, [{"k": 2}], "out2.docx")
    row = db_.get_form_fill(fill["id"])
    assert row["status"] == "success" and json.loads(row["filled_values"]) == {"a": "c"}
    assert json.loads(row["report"]) == [{"k": 2}] and row["output_path"] == "out2.docx"


def test_count_active_form_fills(db):
    db_, _ = db
    db_.create_form_template("t1", "T", "preset", "x.docx")
    pid = db_.create_project(name="P")["id"]
    assert db_.count_active_form_fills() == 0
    f1 = db_.create_form_fill(pid, "t1", "F1")
    f2 = db_.create_form_fill(pid, "t1", "F2")
    assert db_.count_active_form_fills() == 2  # pending
    db_.update_form_fill_status(f1["id"], "filling")
    assert db_.count_active_form_fills() == 2  # pending + filling
    db_.update_form_fill_status(f2["id"], "failed", error="x")
    assert db_.count_active_form_fills() == 1  # 终态不计
    db_.update_form_fill_status(f1["id"], "success")
    assert db_.count_active_form_fills() == 0


def test_recover_stale_form_templates_and_fills(db):
    db_, _ = db
    db_.create_form_template("t1", "T", "preset", "x.docx")
    db_.update_form_template_status("t1", "analyzing")
    assert db_.recover_stale_form_templates() == ["t1"]
    assert db_.get_form_template("t1")["status"] == "pending"
    assert db_.recover_stale_form_templates() == []  # 幂等
    # 终态不动
    db_.update_form_template_status("t1", "success")
    assert db_.recover_stale_form_templates() == []
    # fill：filling → pending
    pid = db_.create_project(name="P")["id"]
    fill = db_.create_form_fill(pid, "t1", "F")
    db_.update_form_fill_status(fill["id"], "filling")
    assert db_.recover_stale_form_fills() == [fill["id"]]
    assert db_.get_form_fill(fill["id"])["status"] == "pending"


def test_delete_template_with_fills_succeeds(db):
    """模板被实例引用仍可删（FK 已摘除，2.4 不变量 11）：实例悬空存活"""
    db_, _ = db
    db_.create_form_template("t1", "T", "preset", "x.docx")
    pid = db_.create_project(name="P")["id"]
    fill = db_.create_form_fill(pid, "t1", "F")
    assert db_.delete_form_template("t1") is True
    assert db_.get_form_template("t1") is None
    assert db_.get_form_fill(fill["id"]) is not None
    # 无引用可删
    db_.create_form_template("t2", "T2", "preset", "y.docx")
    assert db_.delete_form_template("t2") is True


# ── 后台任务全链（fake LLM / fake 检索 → DB + 通知）──

def test_run_template_analysis_success(db, monkeypatch):
    db_, td = db
    from routers import forms as forms_mod
    path = _form_docx(os.path.join(td, "t.docx"))
    db_.create_form_template("t1", "测试模板", "upload", path)
    _install_fake_llm(monkeypatch, [json.dumps(_LLM_ANALYSIS_OUTPUT, ensure_ascii=False)])
    asyncio.run(forms_mod._run_template_analysis("t1"))
    row = db_.get_form_template("t1")
    assert row["status"] == "success" and row["field_count"] == 2
    schema = json.loads(row["template_schema"])
    assert [f["field_key"] for f in schema["fields"]] == ["project_name", "company_profile"]
    assert row["llm_mapping"] is not None
    notifs = db_.list_recent_notifications(10)
    assert any(n["entity_type"] == "form_template" and n["kind"] == "completed"
               for n in notifs)


def test_run_template_analysis_invalid_docx(db, monkeypatch):
    """无效 docx → invalid 终态 + fail 通知"""
    db_, td = db
    from routers import forms as forms_mod
    bad = os.path.join(td, "bad.docx")
    Path(bad).write_bytes(b"not a docx")
    db_.create_form_template("t1", "坏模板", "upload", bad)
    _install_fake_llm(monkeypatch, ["{}"])
    asyncio.run(forms_mod._run_template_analysis("t1"))
    row = db_.get_form_template("t1")
    assert row["status"] == "invalid" and row["error"]
    assert any(n["entity_type"] == "form_template" and n["kind"] == "failed"
               for n in db_.list_recent_notifications(10))


def test_run_template_analysis_missing_file(db):
    """模板文件缺失 → failed 终态（不悬挂 analyzing）"""
    db_, _ = db
    from routers import forms as forms_mod
    db_.create_form_template("t1", "丢文件", "upload", "Z:/nowhere/x.docx")
    asyncio.run(forms_mod._run_template_analysis("t1"))
    row = db_.get_form_template("t1")
    assert row["status"] == "failed" and "不存在" in row["error"]


def test_run_template_analysis_import_error_lands_failed(db, monkeypatch):
    """ImportError（环境缺失）→ failed 终态 + 通知，不得 raise 悬挂 analyzing
    （m-1：原 raise 使每次重启被 resume_pending_work 拾取 → 无限循环白跑）"""
    db_, td = db
    from routers import forms as forms_mod
    path = _form_docx(os.path.join(td, "t.docx"))
    db_.create_form_template("t1", "测试模板", "upload", path)
    import sys
    monkeypatch.setitem(sys.modules, "llm.client", None)  # 延迟 import 抛 ImportError
    asyncio.run(forms_mod._run_template_analysis("t1"))  # 不得抛出
    row = db_.get_form_template("t1")
    assert row["status"] == "failed" and "环境缺失" in row["error"]
    assert any(n["entity_type"] == "form_template" and n["kind"] == "failed"
               for n in db_.list_recent_notifications(10))


def test_run_form_fill_success(db, monkeypatch):
    db_, td = db
    from routers import forms as forms_mod
    path = _form_docx(os.path.join(td, "t.docx"))
    db_.create_form_template("t1", "测试模板", "upload", path)
    db_.update_form_template_schema("t1", _SCHEMA, {"fields": []}, 2)
    pid = db_.create_project(name="P", company_name="某公司")["id"]
    fill = db_.create_form_fill(pid, "t1", "F")
    _install_fake_llm(monkeypatch, [json.dumps(_FILL_OUTPUT, ensure_ascii=False)])
    monkeypatch.setattr(forms_mod, "_get_search_tool", lambda db, data_dir: _FakeSearch())
    asyncio.run(forms_mod._run_form_fill(fill["id"], td))
    row = db_.get_form_fill(fill["id"])
    assert row["status"] == "success" and row["progress"] == 100
    assert os.path.exists(row["output_path"]) and row["output_path"].endswith(f"{fill['id']}.docx")
    values = json.loads(row["filled_values"])
    assert values["project_name"] == "智造科技有限公司"
    report = json.loads(row["report"])
    assert len(report) == 2  # 每字段一行
    assert any(n["entity_type"] == "form_fill" and n["kind"] == "completed"
               for n in db_.list_recent_notifications(10))


def test_run_form_fill_template_not_ready(db):
    """模板未就绪 → fill failed + 通知（不悬挂 pending）"""
    db_, td = db
    from routers import forms as forms_mod
    db_.create_form_template("t1", "T", "preset", "x.docx")  # status=pending，无 schema
    pid = db_.create_project(name="P")["id"]
    fill = db_.create_form_fill(pid, "t1", "F")
    asyncio.run(forms_mod._run_form_fill(fill["id"], td))
    row = db_.get_form_fill(fill["id"])
    assert row["status"] == "failed" and "尚未分析完成" in row["error"]
    assert any(n["entity_type"] == "form_fill" and n["kind"] == "failed"
               for n in db_.list_recent_notifications(10))


# ── 活动快照 + 终态通知 ──

def test_active_form_snapshots(db):
    db_, _ = db
    from routers.forms import active_form_fills, active_form_templates
    db_.create_form_template("t1", "模板甲", "preset", "x.docx")  # pending → 快照
    pid = db_.create_project(name="P")["id"]
    fill = db_.create_form_fill(pid, "t1", "F")
    tpl_active = active_form_templates(db_)
    assert [a["entity_id"] for a in tpl_active] == ["t1"]
    assert tpl_active[0]["entity_title"] == "模板甲"
    fills_active = active_form_fills(db_)
    assert fills_active[0]["entity_id"] == fill["id"]
    assert fills_active[0]["project_id"] == pid and fills_active[0]["progress"] == 0
    # 终态后不在快照
    db_.update_form_template_status("t1", "success")
    db_.update_form_fill_result(fill["id"], [], {}, "out.docx")
    assert active_form_templates(db_) == []
    assert active_form_fills(db_) == []


def test_terminal_event_dedup(db):
    """未读查重幂等（activity_events 契约）：重复终态声明只产生一条通知"""
    import activity_events
    db_, _ = db
    db_.create_form_template("t1", "模板甲", "preset", "x.docx")
    activity_events.complete_form_template(db_, "t1", "第一次")
    activity_events.complete_form_template(db_, "t1", "第二次")
    rows = [n for n in db_.list_recent_notifications(10)
            if n["entity_type"] == "form_template"]
    assert len(rows) == 1 and rows[0]["entity_title"] == "模板甲"


def test_form_fill_notification_carries_project(db):
    """fill 通知 JOIN projects 带项目上下文；模板通知无项目（全局资产）"""
    import activity_events
    db_, _ = db
    db_.create_form_template("t1", "模板甲", "preset", "x.docx")
    pid = db_.create_project(name="项目乙")["id"]
    fill = db_.create_form_fill(pid, "t1", "F")
    activity_events.complete_form_template(db_, "t1")
    activity_events.complete_form_fill(db_, fill["id"])
    rows = {n["entity_type"]: n for n in db_.list_recent_notifications(10)}
    assert rows["form_template"]["project_id"] is None
    assert rows["form_fill"]["project_id"] == pid
    assert rows["form_fill"]["entity_title"] == "F"


# ── 路由层 ──

def test_upload_template_route(client):
    c, db_, td, calls = client
    # 非 docx 拒绝
    r = c.post("/forms/templates/upload",
               files={"file": ("a.txt", b"x", "text/plain")})
    assert r.status_code == 400
    assert r.json()["code"] == "UNSUPPORTED_FORM_TEMPLATE_FORMAT"
    # docx 落盘 + pending + 后台分析任务被调度
    buf = io.BytesIO()
    Document().save(buf)
    r = c.post("/forms/templates/upload", files={
        "file": ("我的模板.docx", buf.getvalue(),
                 "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
    assert r.status_code == 200
    tid = r.json()["template_id"]
    assert r.json()["status"] == "pending"
    row = db_.get_form_template(tid)
    assert row["source"] == "upload" and row["name"] == "我的模板"
    assert os.path.exists(row["file_path"])  # 落在 data_dir/form_templates/
    assert calls["analysis"] == [tid]


def test_reanalyze_route(client):
    c, db_, td, calls = client
    assert c.post("/forms/templates/nope/reanalyze",
                  json={"user_hint": "x"}).status_code == 404
    db_.create_form_template("t1", "T", "upload", "x.docx")
    db_.update_form_template_status("t1", "analyzing")
    # 分析中 409
    r = c.post("/forms/templates/t1/reanalyze", json={"user_hint": "选项拆分不对"})
    assert r.status_code == 409 and r.json()["code"] == "FORM_TEMPLATE_ANALYZING"
    # 正常：hint 落库 + 回 pending + 后台任务
    db_.update_form_template_status("t1", "failed")
    r = c.post("/forms/templates/t1/reanalyze", json={"user_hint": "选项拆分不对"})
    assert r.status_code == 200 and r.json()["status"] == "pending"
    row = db_.get_form_template("t1")
    assert row["user_hint"] == "选项拆分不对" and row["status"] == "pending"
    # m-2 回归：已存 hint 时 analyzing 同样 409 且不重复入队
    # （原 not user_hint 短路使守门失效 → 双跑：LLM 双份调用 + 后到者覆盖 schema）
    db_.update_form_template_status("t1", "analyzing")
    r = c.post("/forms/templates/t1/reanalyze", json={"user_hint": "再改一次"})
    assert r.status_code == 409 and r.json()["code"] == "FORM_TEMPLATE_ANALYZING"
    assert calls["analysis"] == ["t1"]


def test_delete_template_route(client):
    c, db_, td, calls = client
    src = os.path.join(td, "t1.docx")
    db_.create_form_template("t1", "T", "upload", src)
    # 分析中 409
    db_.update_form_template_status("t1", "analyzing")
    assert c.delete("/forms/templates/t1").status_code == 409
    # 引用容错（FK 已摘除，2.4 不变量 11）：被实例引用仍可删，实例悬空存活
    db_.update_form_template_status("t1", "success")
    pid = db_.create_project(name="P")["id"]
    fill = db_.create_form_fill(pid, "t1", "F")
    Path(src).write_bytes(b"x")
    assert c.delete("/forms/templates/t1").status_code == 200
    assert db_.get_form_template("t1") is None
    assert db_.get_form_fill(fill["id"]) is not None
    assert not os.path.exists(src)


def test_create_fill_route(client):
    c, db_, td, calls = client
    pid = db_.create_project(name="P")["id"]
    db_.create_form_template("t1", "模板甲", "preset", "x.docx")
    # 模板 404
    assert c.post("/forms/fills",
                  json={"project_id": pid, "template_id": "nope"}).status_code == 404
    # 模板未就绪 409
    r = c.post("/forms/fills", json={"project_id": pid, "template_id": "t1"})
    assert r.status_code == 409 and r.json()["code"] == "FORM_TEMPLATE_NOT_READY"
    # 项目 404
    db_.update_form_template_schema("t1", _SCHEMA, {"fields": []}, 2)
    assert c.post("/forms/fills",
                  json={"project_id": "nope", "template_id": "t1"}).status_code == 404
    # 并发限制 429（默认上限 2，两个 active 即满）
    f1 = db_.create_form_fill(pid, "t1", "F1")
    f2 = db_.create_form_fill(pid, "t1", "F2")
    r = c.post("/forms/fills", json={"project_id": pid, "template_id": "t1"})
    assert r.status_code == 429 and r.json()["code"] == "FORM_FILL_CONCURRENT_LIMIT"
    # 释放后成功；缺省标题 =「{模板名} {YYYY-MM-DD}」
    db_.update_form_fill_status(f1["id"], "failed")
    db_.update_form_fill_status(f2["id"], "failed")
    r = c.post("/forms/fills", json={"project_id": pid, "template_id": "t1"})
    assert r.status_code == 200 and r.json()["status"] == "pending"
    fid = r.json()["fill_id"]
    assert calls["fill"] == [fid]
    row = db_.get_form_fill(fid)
    assert row["title"].startswith("模板甲 ") and len(row["title"]) == len("模板甲 0000-00-00")


def test_fill_lifecycle_routes(client):
    c, db_, td, calls = client
    pid = db_.create_project(name="P")["id"]
    path = _form_docx(os.path.join(td, "t1.docx"))
    db_.create_form_template("t1", "模板甲", "upload", path)
    db_.update_form_template_schema("t1", _SCHEMA, {"fields": []}, 2)
    fill = db_.create_form_fill(pid, "t1", "模板甲 2026-10-14")
    fid = fill["id"]
    assert c.get("/forms/fills/nope").status_code == 404
    # 列表：轻量视图 + template_name
    rows = c.get("/forms/fills", params={"project_id": pid}).json()["fills"]
    assert len(rows) == 1
    assert rows[0]["template_name"] == "模板甲"
    assert "filled_values" not in rows[0] and "report" not in rows[0]
    # 修订：进行中 409（pending 亦拦）
    r = c.post(f"/forms/fills/{fid}/update", json={"values": {"project_name": "X"}})
    assert r.status_code == 409 and r.json()["code"] == "FORM_FILL_RUNNING"
    # 置 success（产物路径留空 → 修订端点按默认路径生成）
    db_.update_form_fill_result(fid, [], {}, "")
    # 修订：空值 → no_data 不写入；有值 → 全量重放生成产物
    r = c.post(f"/forms/fills/{fid}/update", json={
        "values": {"project_name": "智造科技", "company_profile": ""}})
    assert r.status_code == 200
    body = r.json()
    assert body["fill"]["filled_values"] == {"project_name": "智造科技"}
    statuses = {row["field_key"]: row["status"] for row in body["report"]}
    assert statuses == {"project_name": "filled", "company_profile": "no_data"}
    out = body["fill"]["output_path"]
    assert os.path.exists(out)
    # 详情：schema/值集解析为对象
    detail = c.get(f"/forms/fills/{fid}").json()
    assert detail["schema"]["template_name"] == "测试模板"
    assert detail["fill"]["filled_values"]["project_name"] == "智造科技"
    # 下载
    assert c.get(f"/forms/fills/{fid}/download").status_code == 200
    assert c.get("/forms/fills/nope/download").status_code == 404
    # 删除：终态可删 + 产物清理
    assert c.delete(f"/forms/fills/{fid}").status_code == 200
    assert not os.path.exists(out)
    assert db_.get_form_fill(fid) is None


def test_delete_fill_running_rejected(client):
    c, db_, td, calls = client
    pid = db_.create_project(name="P")["id"]
    db_.create_form_template("t1", "T", "preset", "x.docx")
    fill = db_.create_form_fill(pid, "t1", "F")
    db_.update_form_fill_status(fill["id"], "filling")
    r = c.delete(f"/forms/fills/{fill['id']}")
    assert r.status_code == 409 and r.json()["code"] == "FORM_FILL_RUNNING"
    assert db_.get_form_fill(fill["id"]) is not None


def test_template_detail_route(client):
    c, db_, td, calls = client
    assert c.get("/forms/templates/nope").status_code == 404
    db_.create_form_template("t1", "T", "preset", "x.docx")
    db_.update_form_template_schema("t1", _SCHEMA, {"fields": []}, 2)
    tpl = c.get("/forms/templates/t1").json()["template"]
    assert tpl["template_schema"]["template_name"] == "测试模板"  # 已解析为对象
    assert tpl["status"] == "success"
    assert tpl["file_changed"] is False  # file_hash=NULL（存量）不提示
    assert "file_path" not in tpl and "file_hash" not in tpl  # 比对列不出路由层


# ── 文件变更检测（2.4 §2.4）──


def test_form_template_hash_written_on_success(db):
    """分析成功写 file_hash：get/list 可见（列表轻量视图保留该列供路由层比对）"""
    db_, _ = db
    db_.create_form_template("t1", "T", "preset", "x.docx")
    db_.update_form_template_schema("t1", _SCHEMA, {"m": 1}, 2, file_hash="ab" * 32)
    row = db_.get_form_template("t1")
    assert row["file_hash"] == "ab" * 32 and row["status"] == "success"
    assert db_.list_form_templates()[0]["file_hash"] == "ab" * 32


def test_file_changed_dynamic_compare(db):
    """file_changed 动态比对（不落标记列）：改内容 True、重解析后 False、
    无 hash/文件缺失不提示（缺失走 failed 独立表现）"""
    db_, td = db
    from routers.forms import _file_changed, _with_file_changed, sha256_file
    path = _form_docx(os.path.join(td, "t1.docx"))
    db_.create_form_template("t1", "T", "upload", path)
    row = db_.get_form_template("t1")
    assert _file_changed(row) is False  # file_hash 为 NULL → 不提示
    db_.update_form_template_schema("t1", _SCHEMA, {}, 2, file_hash=sha256_file(path))
    row = db_.get_form_template("t1")
    assert _file_changed(row) is False  # 未变
    Path(path).write_bytes(b"changed content")
    assert _file_changed(row) is True   # 内容变了
    view = _with_file_changed(dict(row))  # 响应视图：bool 化 + hash 不出路由层
    assert view["file_changed"] is True and "file_hash" not in view
    db_.update_form_template_schema("t1", _SCHEMA, {}, 2, file_hash=sha256_file(path))
    assert _file_changed(db_.get_form_template("t1")) is False  # 重解析对齐后不提示
    os.remove(path)
    assert _file_changed(db_.get_form_template("t1")) is False  # 文件缺失不掺和变更提示


def test_create_fill_blocked_on_file_changed(client):
    """文件变更准入拦截：409 FORM_TEMPLATE_CHANGED；重解析对齐后放行"""
    c, db_, td, calls = client
    from routers.forms import sha256_file
    pid = db_.create_project(name="P")["id"]
    path = _form_docx(os.path.join(td, "t1.docx"))
    db_.create_form_template("t1", "T", "upload", path)
    db_.update_form_template_schema("t1", _SCHEMA, {}, 2, file_hash=sha256_file(path))
    # 未变更：正常放行
    assert c.post("/forms/fills",
                  json={"project_id": pid, "template_id": "t1"}).status_code == 200
    # 文件内容变了 → 列表/详情提示 + 发起 409（响应不露比对列）
    Path(path).write_bytes(b"new template revision")
    tpl = c.get("/forms/templates").json()["templates"][0]
    assert tpl["file_changed"] is True
    assert "file_path" not in tpl and "file_hash" not in tpl
    r = c.post("/forms/fills", json={"project_id": pid, "template_id": "t1"})
    assert r.status_code == 409 and r.json()["code"] == "FORM_TEMPLATE_CHANGED"
    # 重解析（对齐 hash）→ 提示消失 + 放行
    db_.update_form_template_schema("t1", _SCHEMA, {}, 2, file_hash=sha256_file(path))
    assert c.get("/forms/templates/t1").json()["template"]["file_changed"] is False
    assert c.post("/forms/fills",
                  json={"project_id": pid, "template_id": "t1"}).status_code == 200


# ── 模板文件变更扫描（2.11 §3.3）──


def _reset_change_scan(monkeypatch):
    """重置每日扫描标记（模块级状态，用例间隔离）"""
    import routers.forms as forms_mod
    monkeypatch.setattr(forms_mod, "_last_change_scan_date", None)


def test_change_scan_notifies_on_modified_template(db, monkeypatch):
    """源文件已变更的 success 模板 → kind='file_changed' 通知（detail=None）"""
    db_, td = db
    _reset_change_scan(monkeypatch)
    from routers.forms import maybe_scan_template_changes, sha256_file
    path = _form_docx(os.path.join(td, "t1.docx"))
    db_.create_form_template("t1", "T", "upload", path)
    db_.update_form_template_schema("t1", _SCHEMA, {}, 2, file_hash=sha256_file(path))
    Path(path).write_bytes(b"changed content")
    maybe_scan_template_changes(db_)
    notes = db_.list_unread_notifications()
    assert len(notes) == 1
    assert notes[0]["entity_type"] == "form_template"
    assert notes[0]["entity_id"] == "t1"
    assert notes[0]["kind"] == "file_changed"
    assert notes[0]["entity_title"] == "T"
    assert notes[0]["detail"] is None  # 后端零文案，语义在 i18n 主键


def test_change_scan_skips_unchanged_or_ineligible(db, monkeypatch):
    """未变更 / 未分析（无 hash）/ 文件缺失 → 不通知"""
    db_, td = db
    _reset_change_scan(monkeypatch)
    from routers.forms import maybe_scan_template_changes, sha256_file
    path = _form_docx(os.path.join(td, "t1.docx"))
    db_.create_form_template("t1", "T", "upload", path)
    db_.update_form_template_schema("t1", _SCHEMA, {}, 2, file_hash=sha256_file(path))  # 未变
    db_.create_form_template("t2", "T2", "upload", path)  # pending 无 hash：不入查询
    db_.create_form_template("t3", "T3", "upload", "Z:/gone/missing.docx")
    db_.update_form_template_schema("t3", _SCHEMA, {}, 2, file_hash="ab" * 32)  # 文件缺失
    maybe_scan_template_changes(db_)
    assert db_.list_unread_notifications() == []


def test_change_scan_daily_gate_and_dedup(db, monkeypatch):
    """每日一次门（同日零开销）+ 未读查重双层防重：重置标记强制重扫也只一条"""
    db_, td = db
    _reset_change_scan(monkeypatch)
    import routers.forms as forms_mod
    from routers.forms import maybe_scan_template_changes, sha256_file
    path = _form_docx(os.path.join(td, "t1.docx"))
    db_.create_form_template("t1", "T", "upload", path)
    db_.update_form_template_schema("t1", _SCHEMA, {}, 2, file_hash=sha256_file(path))
    Path(path).write_bytes(b"changed")
    maybe_scan_template_changes(db_)
    assert forms_mod._last_change_scan_date  # 已置当日标记
    maybe_scan_template_changes(db_)  # 同日：每日门直接拦截
    assert len(db_.list_unread_notifications()) == 1
    monkeypatch.setattr(forms_mod, "_last_change_scan_date", None)  # 模拟次日重扫
    maybe_scan_template_changes(db_)
    assert len(db_.list_unread_notifications()) == 1  # 未读查重兑底


def test_change_scan_hash_failure_isolated(db, monkeypatch):
    """单模板 hash 失败（文件被占用）不拖垮其余模板扫描、不上抛"""
    db_, td = db
    _reset_change_scan(monkeypatch)
    import routers.forms as forms_mod
    from routers.forms import maybe_scan_template_changes, sha256_file
    path1 = _form_docx(os.path.join(td, "t1.docx"))
    path2 = _form_docx(os.path.join(td, "t2.docx"))
    db_.create_form_template("t1", "T1", "upload", path1)
    db_.update_form_template_schema("t1", _SCHEMA, {}, 2, file_hash=sha256_file(path1))
    Path(path1).write_bytes(b"changed one")
    db_.create_form_template("t2", "T2", "upload", path2)
    db_.update_form_template_schema("t2", _SCHEMA, {}, 2, file_hash=sha256_file(path2))
    Path(path2).write_bytes(b"changed two")
    orig_sha = forms_mod.sha256_file

    def flaky_sha(p):
        if str(p).endswith("t1.docx"):
            raise OSError("file locked")
        return orig_sha(p)

    monkeypatch.setattr(forms_mod, "sha256_file", flaky_sha)
    maybe_scan_template_changes(db_)  # t1 抛 OSError 被隔离
    notes = db_.list_unread_notifications()
    assert len(notes) == 1 and notes[0]["entity_id"] == "t2"


def test_change_scan_db_failure_retries_same_day(db, monkeypatch):
    """首查 DB 异常不置当日标记 → 当日后续心跳可重试（review m-2，同 reminders 口径）"""
    db_, td = db
    _reset_change_scan(monkeypatch)
    from routers.forms import maybe_scan_template_changes, sha256_file
    path = _form_docx(os.path.join(td, "t1.docx"))
    db_.create_form_template("t1", "T", "upload", path)
    db_.update_form_template_schema("t1", _SCHEMA, {}, 2, file_hash=sha256_file(path))
    Path(path).write_bytes(b"changed")
    gate = {"fail": True}
    real_conn = db_.conn

    class _FlakyConn:
        def execute(self, sql, *a, **k):
            if gate["fail"]:
                raise RuntimeError("database is locked")
            return real_conn.execute(sql, *a, **k)

        def __getattr__(self, name):
            return getattr(real_conn, name)

    monkeypatch.setattr(db_, "conn", _FlakyConn())
    maybe_scan_template_changes(db_)  # 首查失败：不上抛、不置标记
    import routers.forms as forms_mod
    assert forms_mod._last_change_scan_date is None
    gate["fail"] = False  # 瞬时故障恢复
    maybe_scan_template_changes(db_)  # 同日重试：扫描执行
    notes = db_.list_unread_notifications()
    assert len(notes) == 1 and notes[0]["entity_id"] == "t1"
    assert forms_mod._last_change_scan_date  # 成功后才置位


# ── 启动拾取 ──

def test_resume_pending_work(db, monkeypatch):
    db_, td = db
    from routers import forms as forms_mod
    db_.create_form_template("t1", "T", "preset", "x.docx")  # pending → 拾取分析
    pid = db_.create_project(name="P")["id"]
    fill = db_.create_form_fill(pid, "t1", "F")  # pending → 拾取填表
    calls = {"analysis": [], "fill": []}

    async def _fa(t):
        calls["analysis"].append(t)

    async def _ff(f, d):
        calls["fill"].append(f)

    monkeypatch.setattr(forms_mod, "_run_template_analysis", _fa)
    monkeypatch.setattr(forms_mod, "_run_form_fill", _ff)

    async def _main():
        forms_mod.resume_pending_work(db_, td)
        await asyncio.sleep(0.05)
        assert not forms_mod._BG_TASKS  # done 回调自清，无引用泄漏

    asyncio.run(_main())
    assert calls["analysis"] == ["t1"]
    assert calls["fill"] == [fill["id"]]
