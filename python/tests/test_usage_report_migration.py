# -*- coding: utf-8 -*-
"""python/tests/test_usage_report_migration.py — 报告 runner 单源迁移回归
（2.10 §3.4/§4.4/§8）。

锁定三个迁移终态：
① 旧 TokenTracker 模块与 reports.token_usage 列彻底移除（单源 = llm_usage 表）
② checkpoint 结构无 token 字段（token 用量不再进 checkpoint）
③ 用途上下文（5 处 push_purpose 调用点）+ by_section 经 llm_usage.detail 还原
（SQL json_extract 路径可执行）
"""
import asyncio
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report_generator.runner import ReportRunner
from report_generator.registry import CitationRegistry

_PY_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def db():
    from db.database import Database
    with tempfile.TemporaryDirectory() as td:
        db = Database(os.path.join(td, "t.db"))
        db.initialize()
        yield db
        db.conn.close()


@pytest.fixture()
def runner(tmp_path):
    """最小 ReportRunner：构造期零外部副作用（全在 run()），可直测 checkpoint。"""
    return ReportRunner(
        template={"report_depth": "full"},
        variables={},
        project_id="p1",
        search_tool=None,
        llm_client=None,
        llm_settings={},
        output_dir=str(tmp_path),
    )


# ── ① 旧源移除 ──


def test_token_tracker_module_removed():
    """旧 TokenTracker 整文件删除（§3.4）：模块与残留 .pyc 均不存在。"""
    assert not (_PY_ROOT / "report_generator" / "token_tracker.py").exists()
    pycache = _PY_ROOT / "report_generator" / "__pycache__"
    if pycache.is_dir():
        assert not any(p.name.startswith("token_tracker") for p in pycache.iterdir())


def test_reports_table_has_no_token_usage_column(db):
    """reports.token_usage 列已删（迁移 DROP COLUMN；新库本就无此列）。"""
    cols = {r["name"] for r in db.conn.execute("PRAGMA table_info(reports)")}
    assert "token_usage" not in cols


# ── ② checkpoint 无 token 字段 ──


def test_checkpoint_schema_has_no_token_fields(runner, tmp_path):
    """checkpoint 只含内容性状态六键，无任何 token 字段（用量归 llm_usage 单源）。"""
    runner.outputs = {"s1": "正文"}
    runner.digests = {"s1": "摘要"}
    runner.order = ["s1"]
    runner.registry = CitationRegistry()
    runner._save_checkpoint()
    data = json.loads((tmp_path / "checkpoint.json").read_text(encoding="utf-8"))
    assert set(data.keys()) == {
        "outputs", "digests", "order", "registry",
        "resolved_variables", "resolution_evidence",
    }
    assert not any("token" in k.lower() for k in data)


def test_checkpoint_roundtrip_restores_outputs(runner, tmp_path):
    """断点往返：outputs/digests/order 恢复（用量迁移不得破坏断点续跑）。"""
    runner.outputs = {"s1": "正文", "s2": "第二节"}
    runner.digests = {"s1": "摘要1"}
    runner.order = ["s1", "s2"]
    runner._save_checkpoint()
    other = ReportRunner(
        template={"report_depth": "full"}, variables={}, project_id="p1",
        search_tool=None, llm_client=None, llm_settings={},
        output_dir=str(tmp_path),
    )
    assert other._load_checkpoint() is None
    assert other.outputs == {"s1": "正文", "s2": "第二节"}
    assert other.order == ["s1", "s2"]


# ── ③ 用途上下文与 by_section 还原 ──


def test_runner_call_sites_declare_purpose():
    """runner 全部 LLM 调用点声明 purpose（5 处，防调用点回退为漏账）。"""
    source = (_PY_ROOT / "report_generator" / "runner.py").read_text(encoding="utf-8")
    declared = set(
        m.strip('"\'') for m in
        __import__("re").findall(r'push_purpose\(\s*["\'](\w+)["\']', source)
    )
    assert declared == {"retrieval", "variable_resolution",
                        "generation", "digest", "extract_numbers"}


def test_by_section_restore_via_detail(db):
    """by_section 还原路径：entity_id=report_id + detail.section_id →
    GROUP BY json_extract(detail,'$.section_id') 可执行且聚合正确
    （§4.4：TokenTracker by_section 的 llm_usage 等价物）。"""
    from llm.usage import UsageContext, configure, push_context, push_purpose, record

    report_id = "R-mig"
    rows = []
    configure(rows.append)
    try:
        with push_context(UsageContext(
                source="report", project_id="p1", entity_id=report_id)):
            for sid, (pt, ct) in {"s1": (100, 50), "s2": (200, 80)}.items():
                with push_purpose("generation", detail={"section_id": sid}):
                    record("m-writer", "full", {
                        "prompt_tokens": pt, "completion_tokens": ct,
                        "total_tokens": pt + ct,
                    })
    finally:
        configure(None)

    assert len(rows) == 2
    # 记录经 sink 行形状：source/entity_id/purpose/detail 就位（db 层负责落库）
    assert rows[0]["source"] == "report"
    assert rows[0]["entity_id"] == report_id
    assert rows[0]["purpose"] == "generation"

    # 落库后按 section 还原（SQL 路径与 2.10 §4.4 描述一致）
    for r in rows:
        db.insert_llm_usage(r)
    grouped = db.conn.execute(
        "SELECT json_extract(detail, '$.section_id') AS section_id, "
        "SUM(total_tokens) AS total_tokens, COUNT(*) AS calls "
        "FROM llm_usage WHERE entity_id = ? GROUP BY section_id",
        (report_id,),
    ).fetchall()
    by_section = {r["section_id"]: r["total_tokens"] for r in grouped}
    assert by_section == {"s1": 150, "s2": 280}
    # 按 purpose 还原（by_type 等价路径）同样可用
    by_purpose = db.conn.execute(
        "SELECT purpose, SUM(total_tokens) AS t FROM llm_usage "
        "WHERE entity_id = ? GROUP BY purpose", (report_id,)).fetchall()
    assert {r["purpose"]: r["t"] for r in by_purpose} == {"generation": 430}
