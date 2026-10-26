"""python/tests/test_ledger_service.py — 3.0 四期 T2:编译后台账解析落库(3.1 §3.1/§4.2)。

范围选取(is_ledger 页)/删旧插新/失败保旧/活动注册与清理/并发守卫
(2026-10-25 review M-1 同修:占坑上提到挂钩,跳过不误删他方坑,
清理责任与占坑所有权绑定)。LLM 一律 monkeypatch,不联网。
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
        db.ensure_preset_base_libs()  # create_project 的 auto-link 依赖 preset 基础库
        db.seed_wiki_schemas(get_all_wiki_schemas())
        yield db
        db.conn.close()


@pytest.fixture(autouse=True)
def _no_llm_pulse(monkeypatch):
    """_run 流水线尾部的 update_pulse_status 会真实调 LLM;T2 只关注解析落库,
    统一屏蔽(AI 句行为由 test_pulse_ai_status.py 专测),防用例联网慢跑。"""
    monkeypatch.setattr(ledger_service, "_pulse_sentence", lambda *a, **k: "")


@pytest.fixture(autouse=True)
def _clean_running():
    """_RUNNING 是模块级进程态，用例间必须隔离（占坑残留会污染后续用例）。"""
    with ledger_service._LOCK:
        ledger_service._RUNNING.clear()
    yield
    with ledger_service._LOCK:
        ledger_service._RUNNING.clear()


@pytest.fixture()
def ledger_project(db):
    pid = db.create_project(name="p-ledger", project_type="recruitment")["id"]
    db.create_or_update_wiki_page(pid, "milestones", "项目里程碑台账",
                                  content="- 2026-09 | 项目正式投产 _(来源: 9月月报)_")
    return pid


def test_scope_only_ledger_pages(db, ledger_project, monkeypatch):
    """解析范围 = is_ledger=1 页;画像页不进 prompt(3.1 §3.4)。"""
    db.create_or_update_wiki_page(ledger_project, "enterprise_profile", "画像", content="x")
    captured = {}
    monkeypatch.setattr(ledger_service, "_parse_entries",
                        lambda pages, settings: captured.update(pages=pages) or [])
    ledger_service.on_compile_complete_sync(db, ledger_project, settings={})
    assert [p["page_key"] for p in captured["pages"]] == ["milestones"]


def test_entries_replaced_atomically(db, ledger_project, monkeypatch):
    monkeypatch.setattr(ledger_service, "_parse_entries", lambda pages, settings: [
        {"page_key": "milestones", "entry_date": "2026-09", "entry_type": "milestone",
         "text": "项目正式投产"}])
    ledger_service.on_compile_complete_sync(db, ledger_project, settings={})
    rows = db.list_ledger_entries(ledger_project, limit=10)
    assert len(rows) == 1 and rows[0]["content"].startswith("项目正式投产")
    # 第二次解析产出不同条目 → 删旧插新,不留旧集(3.1 §3.1 写行为)
    monkeypatch.setattr(ledger_service, "_parse_entries", lambda pages, settings: [
        {"page_key": "milestones", "entry_date": "2026-10", "entry_type": "milestone",
         "text": "一期设备全部进场"}])
    ledger_service.on_compile_complete_sync(db, ledger_project, settings={})
    rows = db.list_ledger_entries(ledger_project, limit=10)
    assert len(rows) == 1 and rows[0]["entry_date"] == "2026-10"


def test_parse_failure_keeps_old_entries(db, ledger_project, monkeypatch):
    """LLM 挂 → 不抛、不删旧条目(3.1 §4.4:保持上一版,下次编译自然重试)。"""
    monkeypatch.setattr(ledger_service, "_parse_entries", lambda pages, settings: [
        {"page_key": "milestones", "entry_date": "2026-09", "entry_type": "milestone",
         "text": "项目正式投产"}])
    ledger_service.on_compile_complete_sync(db, ledger_project, settings={})

    def boom(pages, settings):
        raise RuntimeError("llm down")
    monkeypatch.setattr(ledger_service, "_parse_entries", boom)
    ledger_service.on_compile_complete_sync(db, ledger_project, settings={})  # 不抛
    assert len(db.list_ledger_entries(ledger_project, limit=10)) == 1


def test_activity_visible_during_parse_and_cleared(db, ledger_project, monkeypatch):
    """解析进行中 ledger_parse 活动已注册(0.4 统一模型);终态后无残留。"""
    from activities_registry import snapshot

    def slow_parse(pages, settings):
        items = [i for i in snapshot()
                 if i["entity_type"] == "ledger_parse" and i.get("project_id") == ledger_project]
        assert items, "解析进行中活动应已注册"
        return []
    monkeypatch.setattr(ledger_service, "_parse_entries", slow_parse)
    ledger_service.on_compile_complete_sync(db, ledger_project, settings={})
    assert all(i["entity_type"] != "ledger_parse" for i in snapshot())


def test_concurrent_guard_skips_when_running(db, ledger_project, monkeypatch):
    """同一项目解析进行中再触发 → 跳过(3.1 §4.5:防并发重复解析);
    他方坑幸存 —— 旧实现 finally 无条件 pop 会误删他方在途标记击穿单飞
    (review M-1 2026-10-25,与 profile_service 同修;自占坑的终态清理
    由下方两用例覆盖)。"""
    ran = []
    monkeypatch.setattr(ledger_service, "_parse_entries",
                        lambda pages, settings: ran.append(1) or [])
    with ledger_service._LOCK:
        ledger_service._RUNNING[ledger_project] = {"started_at": 0.0}  # 模拟另一线程进行中
    ledger_service.on_compile_complete_sync(db, ledger_project, settings={})
    assert not ran                                        # 守卫生效
    with ledger_service._LOCK:
        assert ledger_project in ledger_service._RUNNING  # M-1:他方坑不被误删


def test_self_acquired_lock_cleared_on_normal_exit(db, ledger_project, monkeypatch):
    """自占坑后无素材正常 return（非 is_ledger 项目）→ finally 清理，活动不残留。"""
    pid = db.create_project(name="p-plain")["id"]   # general 无 is_ledger 页
    monkeypatch.setattr(ledger_service, "_parse_entries",
                        lambda pages, settings: [])
    ledger_service.on_compile_complete_sync(db, pid, settings={})
    with ledger_service._LOCK:
        assert pid not in ledger_service._RUNNING


def test_self_acquired_lock_cleared_on_failure(db, ledger_project, monkeypatch):
    """自占坑后 LLM 失败 → 吞异常不上抛（§4.4），仍清自己的坑。"""

    def boom(pages, settings):
        raise RuntimeError("llm down")
    monkeypatch.setattr(ledger_service, "_parse_entries", boom)
    ledger_service.on_compile_complete_sync(db, ledger_project, settings={})  # 不抛
    with ledger_service._LOCK:
        assert ledger_project not in ledger_service._RUNNING
