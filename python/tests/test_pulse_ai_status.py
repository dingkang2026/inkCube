"""python/tests/test_pulse_ai_status.py — 3.0 四期 T4:AI 状态句(3.1 §3.3/§4.2④)。

写入/失败保持旧值/无素材清空回落规则句;整体 review S2 增:空白输出防御。
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import Database
from db.wiki_seed import get_all_wiki_schemas
from wiki import ledger_service


@pytest.fixture()
def db():
    with tempfile.TemporaryDirectory() as td:
        db = Database(os.path.join(td, "t.db"))
        db.initialize()
        db.ensure_preset_base_libs()
        db.seed_wiki_schemas(get_all_wiki_schemas())
        yield db
        db.conn.close()


def _seed_entry(db, pid, page_key, date, text, etype):
    # 与实现同源:_fingerprint + 作用域 id(防 helper 与实现漂移,整体 review C1/W3)
    fp = ledger_service._fingerprint(text)
    db.conn.execute(
        "INSERT INTO ledger_entries (id,project_id,page_key,entry_date,entry_type,"
        "content,source_file_id,fingerprint,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,NULL,?,datetime('now'),datetime('now'))",
        (f"le_{pid}_{page_key}_{fp}", pid, page_key, date, etype, text, fp))
    db.conn.commit()


def test_status_written(db, monkeypatch):
    pid = db.create_project(name="p-a1", project_type="recruitment")["id"]
    _seed_entry(db, pid, "milestones", "2026-09", "项目正式投产", "milestone")
    seen = {}

    def fake_sentence(entries, reviews_count, settings):
        seen["n"] = len(entries)
        return "投产 3 个月,本周新增行政处罚,建议拜访时确认整改。"
    monkeypatch.setattr(ledger_service, "_pulse_sentence", fake_sentence)
    ledger_service.update_pulse_status(db, pid, settings={})
    row = db.get_project(pid)
    assert row["pulse_ai_status"].startswith("投产 3 个月")
    assert row["pulse_ai_status_at"]
    assert seen["n"] == 1                              # 最近条目进了 prompt


def test_failure_keeps_old(db, monkeypatch):
    pid = db.create_project(name="p-a2", project_type="recruitment")["id"]
    _seed_entry(db, pid, "milestones", "2026-09", "项目正式投产", "milestone")  # 有素材才会走到 LLM 调用(否则先命中无素材清空分支)
    db.set_pulse_ai_status(pid, "旧句")

    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(ledger_service, "_pulse_sentence", boom)
    ledger_service.update_pulse_status(db, pid, settings={})   # 吞异常
    assert db.get_project(pid)["pulse_ai_status"] == "旧句"


def test_no_material_clears_to_rule_sentence(db, monkeypatch):
    """无条目且无发现项 → 显式清空(PulseBar 回落既有规则句,§5.2)。"""
    pid = db.create_project(name="p-a3", project_type="recruitment")["id"]
    db.set_pulse_ai_status(pid, "旧句")

    def must_not_call(*a, **k):
        raise AssertionError("无素材不应调用 LLM")
    monkeypatch.setattr(ledger_service, "_pulse_sentence", must_not_call)
    ledger_service.update_pulse_status(db, pid, settings={})
    row = db.get_project(pid)
    assert row["pulse_ai_status"] == "" and row["pulse_ai_status_at"]


def test_pulse_sentence_blank_output_returns_empty(monkeypatch):
    """S2 回归:LLM 返回全空白 → 返回空句(保旧值),不 IndexError。"""
    class _BlankLLM:
        def __init__(self, *a, **k):
            pass

        async def chat(self, *a, **k):
            return "  \n  "
    monkeypatch.setattr("llm.LLMClient", _BlankLLM)
    assert ledger_service._pulse_sentence(
        [{"entry_date": "2026-09", "entry_type": "milestone", "content": "投产"}],
        0, {}) == ""
