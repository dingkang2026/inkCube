"""python/tests/test_reports_list_stage.py — list_reports stage 回填回归测试。

背景（2026-10-07）：报告看板列 key 为 before/during/after/none，但 list_reports
此前从 DB templates 行顶层取 stage —— stage 实际存于 template_json 字符串内部，
行顶层无此列，tmpl.get("stage", "report") 永远返回无效兜底值 "report"，四列均
不匹配，报告生成中列表却显示为空。修复后从 template_json 解析 stage；模板缺失、
无 stage 字段、JSON 损坏时归入前端 other（none）列。
"""
import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import deps
import routers.reports as reports_router
from db.database import Database


@pytest.fixture
def db():
    d = Database(db_path=":memory:")
    d.initialize()
    yield d
    d.conn.close()


@pytest.fixture
def patched(db, monkeypatch):
    """router 依赖注入：deps.db → 内存 DB；预建真实项目（reports.project_id 外键）。"""
    monkeypatch.setattr(deps, "db", db)
    db._project_id = db.create_project("阶段测试")["id"]
    return db


def _seed_template(patched, template_id: str, stage, *, corrupt: bool = False):
    tj = "not-valid-json{{" if corrupt else json.dumps(
        {"template_id": template_id, "template_name": template_id, "stage": stage},
        ensure_ascii=False,
    )
    return patched.save_template(template_id, f"模板{template_id}", tj, is_custom=False, version="1.0")


def _seed_report(patched, template_id: str, title: str) -> str:
    report_id = f"r-{template_id}"
    patched.conn.execute(
        "INSERT INTO reports (id, project_id, template_id, title, output_format, status, progress,"
        " content_path, variables, base_project_ids, section_internet, internet_enabled, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, 'docx', 'generating', 0, '', '{}', '[]', '{}', 0, '2026-10-07T10:00:00', '2026-10-07T10:00:00')",
        (report_id, patched._project_id, template_id, title),
    )
    patched.conn.commit()
    return report_id


def _list_stages(patched) -> dict[str, dict]:
    result = asyncio.run(reports_router.list_reports(patched._project_id))
    return {r["template_id"]: r for r in result["reports"]}


def test_stage_parsed_from_template_json(patched):
    """核心回归：stage 从 template_json 内部解析（修复前永远返回无效值 "report"）。"""
    _seed_template(patched, "due_diligence", "during")
    _seed_report(patched, "due_diligence", "尽调报告")

    rows = _list_stages(patched)
    assert rows["due_diligence"]["stage"] == "during"


def test_all_valid_stages_mapped(patched):
    """before/during/after 三值均原样回填，前端看板各归其列。"""
    for stage in ("before", "during", "after"):
        tid = f"t-{stage}"
        _seed_template(patched, tid, stage)
        _seed_report(patched, tid, f"报告{stage}")

    rows = _list_stages(patched)
    assert {r["stage"] for r in rows.values()} == {"before", "during", "after"}


def test_template_without_stage_falls_to_none(patched):
    """模板无 stage 字段（如项目简报）→ 归入 other（none）列。"""
    _seed_template(patched, "project_brief", None)
    _seed_report(patched, "project_brief", "项目简报")

    rows = _list_stages(patched)
    assert rows["project_brief"]["stage"] == "none"
    assert rows["project_brief"]["template_name"] == "模板project_brief"


def test_missing_template_falls_to_none(patched):
    """模板已被删除 → stage=none 且 template_name=Unknown，报告不得消失。"""
    _seed_report(patched, "deleted_tpl", "孤儿报告")

    rows = _list_stages(patched)
    assert rows["deleted_tpl"]["stage"] == "none"
    assert rows["deleted_tpl"]["template_name"] == "Unknown"


def test_corrupt_template_json_falls_to_none(patched):
    """template_json 损坏 → 容错归入 none 列，不抛 500。"""
    _seed_template(patched, "broken_tpl", "during", corrupt=True)
    _seed_report(patched, "broken_tpl", "坏JSON报告")

    rows = _list_stages(patched)
    assert rows["broken_tpl"]["stage"] == "none"


def test_invalid_stage_value_falls_to_none(patched):
    """stage 值越界（如修复前的 "report"）→ 归入 none 列，不透传无效值。"""
    _seed_template(patched, "odd_tpl", "report")
    _seed_report(patched, "odd_tpl", "越界值报告")

    rows = _list_stages(patched)
    assert rows["odd_tpl"]["stage"] == "none"
