"""python/tests/test_task_suggestions.py — 3.0 四期 T3:建议生成(3.1 §3.2/§4.2③)。

④里程碑比对 → complete_task / ③风险跟进 → create_task / 幂等与「忽略过不再提」。
整体 review 增:C1 跨项目作用域 / W2 采纳原子性 / W3 fingerprint 规范化。
"""
import datetime
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
    return fp


def _seed_milestone_task(db, pid, title):
    return db.create_phase_task(project_id=pid, phase="during", task_type="milestone",
                                title=title, description="")["id"]


def test_milestone_match_creates_complete_task_suggestion(db):
    pid = db.create_project(name="p-s1", project_type="recruitment")["id"]
    tid = _seed_milestone_task(db, pid, "完成设备进场与投产")
    fp = _seed_entry(db, pid, "milestones", "2026-09", "项目正式投产,设备全部进场", "milestone")
    ledger_service.generate_suggestions(db, pid)
    rows = db.list_suggestions(pid, "pending")
    assert len(rows) == 1 and rows[0]["kind"] == "complete_task"
    assert rows[0]["entry_fingerprint"] == fp and rows[0]["target_task_id"] == tid


def test_risk_entry_creates_create_task_suggestion(db):
    pid = db.create_project(name="p-s2", project_type="recruitment")["id"]
    recent = (datetime.date.today() - datetime.timedelta(days=5)).isoformat()
    _seed_entry(db, pid, "risk_watchlist", recent, "企业被列入经营异常名录", "risk")
    ledger_service.generate_suggestions(db, pid)
    rows = db.list_suggestions(pid, "pending")
    assert len(rows) == 1 and rows[0]["kind"] == "create_task"
    assert "风险" in rows[0]["title"] or "跟进" in rows[0]["title"]
    assert rows[0]["suggested_phase"] == "during" and rows[0]["suggested_task_type"] == "milestone"


def test_old_risk_entry_ignored(db):
    pid = db.create_project(name="p-s3", project_type="recruitment")["id"]
    _seed_entry(db, pid, "risk_watchlist", "2020-01-01", "历史风险", "risk")
    ledger_service.generate_suggestions(db, pid)
    assert db.list_suggestions(pid, "pending") == []


def test_idempotent_and_dismissed_never_regenerated(db):
    pid = db.create_project(name="p-s4", project_type="recruitment")["id"]
    _seed_milestone_task(db, pid, "完成设备进场与投产")
    fp = _seed_entry(db, pid, "milestones", "2026-09", "项目正式投产,设备全部进场", "milestone")
    ledger_service.generate_suggestions(db, pid)
    ledger_service.generate_suggestions(db, pid)          # 重放:pending 已存在 → 不重复
    assert len(db.list_suggestions(pid, "pending")) == 1
    sid = db.list_suggestions(pid, "pending")[0]["id"]
    db.decide_suggestion(sid, "dismissed")
    ledger_service.generate_suggestions(db, pid)          # dismissed → 永不再提(不变量 3)
    assert db.list_suggestions(pid, "pending") == []
    assert db.suggestion_exists(pid, fp, "complete_task", ("dismissed",))


def test_completed_milestone_not_matched_again(db):
    """目标已 done 的里程碑不再出④建议(避免对已完成任务反复骚扰)。"""
    pid = db.create_project(name="p-s5", project_type="recruitment")["id"]
    tid = _seed_milestone_task(db, pid, "完成设备进场与投产")
    db.update_phase_task(tid, {"status": "done"})
    _seed_entry(db, pid, "milestones", "2026-09", "项目正式投产,设备全部进场", "milestone")
    ledger_service.generate_suggestions(db, pid)
    assert db.list_suggestions(pid, "pending") == []


# ── 整体 review C1 回归:fingerprint 必须项目作用域,跨项目同文本互不干扰 ──


def test_same_text_across_projects_entries_isolated(db):
    """两项目同文本条目:后编译项目不清空、先编译项目条目不消失(C1 链 1:
    条目 id 若为全局主键,后编译项目删旧插新会撞 id 被 IGNORE → 整页静默清空)。"""
    pa = db.create_project(name="p-c1a", project_type="recruitment")["id"]
    pb = db.create_project(name="p-c1b", project_type="recruitment")["id"]
    entry = {"page_key": "milestones", "entry_date": "2026-09",
             "entry_type": "milestone", "text": "完成项目签约"}
    ledger_service._store_entries(db, pa, [dict(entry)])
    ledger_service._store_entries(db, pb, [dict(entry)])
    rows_a, rows_b = db.list_ledger_entries(pa), db.list_ledger_entries(pb)
    assert len(rows_a) == 1 and len(rows_b) == 1
    assert rows_a[0]["id"] != rows_b[0]["id"]          # id 含项目作用域


def test_same_text_suggestions_not_suppressed_across_projects(db):
    """A dismissed 同 fp 建议不压制 B 的同文本建议(C1 链 2:suggestion_exists
    缺 project 过滤时,A 的忽略决定会永久沉默 B 的风险跟进)。"""
    pa = db.create_project(name="p-c1c", project_type="recruitment")["id"]
    pb = db.create_project(name="p-c1d", project_type="recruitment")["id"]
    _seed_milestone_task(db, pa, "完成设备进场与投产")
    _seed_milestone_task(db, pb, "完成设备进场与投产")
    entry = {"page_key": "milestones", "entry_date": "2026-09",
             "entry_type": "milestone", "text": "项目正式投产,设备全部进场"}
    ledger_service._store_entries(db, pa, [dict(entry)])
    ledger_service._store_entries(db, pb, [dict(entry)])
    ledger_service.generate_suggestions(db, pa)
    sid = db.list_suggestions(pa, "pending")[0]["id"]
    db.decide_suggestion(sid, "dismissed")
    ledger_service.generate_suggestions(db, pb)   # B 首次生成,不受 A dismissed 影响
    rows = db.list_suggestions(pb, "pending")
    assert len(rows) == 1 and rows[0]["kind"] == "complete_task"


# ── 整体 review W2 回归:采纳原子性(db 层单事务 + pending 守卫)──


def _seed_suggestion(db, pid, kind, **kw):
    row = db.create_suggestion(pid, kind=kind, title=kw.pop("title", "跟进风险:测试"),
                               entry_fingerprint=kw.pop("fp", None) or f"fp_w2_{kw.pop('tag')}",
                               **kw)
    assert row is not None
    return row


def test_accept_create_task_atomic(db):
    """create_task 采纳:单事务内建任务 + 回填 accepted_task_id。"""
    pid = db.create_project(name="p-w2a", project_type="recruitment")["id"]
    sug = _seed_suggestion(db, pid, "create_task", tag="a",
                           suggested_phase="during", suggested_task_type="milestone")
    result = db.accept_suggestion_atomic(sug["id"])
    assert result["task"]["status"] == "open"
    assert result["task"]["phase"] == "during"
    assert result["suggestion"]["status"] == "accepted"
    assert result["suggestion"]["accepted_task_id"] == result["task"]["id"]
    assert db.get_phase_task(result["task"]["id"]) is not None


def test_accept_twice_second_returns_none(db):
    """重复采纳(双击/并发):第二次 pending 守卫拦截返回 None(路由转 409)。"""
    pid = db.create_project(name="p-w2b", project_type="recruitment")["id"]
    sug = _seed_suggestion(db, pid, "create_task", tag="b")
    assert db.accept_suggestion_atomic(sug["id"]) is not None
    assert db.accept_suggestion_atomic(sug["id"]) is None


def test_accept_complete_task_marks_done_with_accepted_task_id(db):
    """complete_task 采纳:目标置 done(completed_by='manual') + accepted_task_id 回填。"""
    pid = db.create_project(name="p-w2c", project_type="recruitment")["id"]
    tid = _seed_milestone_task(db, pid, "完成设备进场与投产")
    sug = _seed_suggestion(db, pid, "complete_task", tag="c", target_task_id=tid)
    result = db.accept_suggestion_atomic(sug["id"])
    task = db.get_phase_task(tid)
    assert task["status"] == "done" and task["completed_by"] == "manual"
    assert result["suggestion"]["accepted_task_id"] == tid


def test_accept_complete_task_target_missing_turns_dismissed(db):
    """目标任务已删:建议事务内转 dismissed(永不再提) + reason=target_missing。"""
    pid = db.create_project(name="p-w2d", project_type="recruitment")["id"]
    tid = _seed_milestone_task(db, pid, "完成设备进场与投产")
    sug = _seed_suggestion(db, pid, "complete_task", tag="d", target_task_id=tid)
    db.delete_phase_task(tid)
    result = db.accept_suggestion_atomic(sug["id"])
    assert result["task"] is None and result["reason"] == "target_missing"
    assert db.get_suggestion(sug["id"])["status"] == "dismissed"


def test_dismiss_only_pending_cannot_override_decided(db):
    """accept×dismiss 并发:已 accepted 后 dismiss(only_pending) 返回 None
    且不改变状态(悬挂态防护)。"""
    pid = db.create_project(name="p-w2e", project_type="recruitment")["id"]
    sug = _seed_suggestion(db, pid, "create_task", tag="e")
    db.accept_suggestion_atomic(sug["id"])
    assert db.decide_suggestion(sug["id"], "dismissed", only_pending=True) is None
    assert db.get_suggestion(sug["id"])["status"] == "accepted"


# ── 整体 review W3 回归:fingerprint 规范化(LLM 措辞漂移不复活 dismissed)──


def test_fingerprint_normalizes_punctuation_and_whitespace():
    """标点/空白/全半角归一:同内容不同措辞修饰 → 同 fp(W3 漂移面收窄)。"""
    assert (ledger_service._fingerprint("完成签约。")
            == ledger_service._fingerprint("完成 签约")
            == ledger_service._fingerprint("完成签约"))
    assert (ledger_service._fingerprint("Risk: delay")
            == ledger_service._fingerprint("risk：delay"))
    # 日期不归一:日期变化属内容实质变化,fp 必须不同(3.1 §7 边界)
    assert ledger_service._fingerprint("2026-09 投产") != ledger_service._fingerprint("2026-10 投产")


def test_dismissed_survives_minor_wording_drift(db):
    """dismissed 后同内容措辞微调(加句号/空格)不复活建议(W3:
    重编译时 LLM 输出漂移 → 规范化后同 fp → suggestion_exists 仍命中)。"""
    pid = db.create_project(name="p-w3a", project_type="recruitment")["id"]
    _seed_milestone_task(db, pid, "完成设备进场与投产")
    ledger_service._store_entries(db, pid, [
        {"page_key": "milestones", "entry_date": "2026-09",
         "entry_type": "milestone", "text": "项目正式投产,设备全部进场"}])
    ledger_service.generate_suggestions(db, pid)
    sid = db.list_suggestions(pid, "pending")[0]["id"]
    db.decide_suggestion(sid, "dismissed")
    # 重编译:LLM 输出措辞漂移(句号/空格变化) → 同 fp → 永不再提
    ledger_service._store_entries(db, pid, [
        {"page_key": "milestones", "entry_date": "2026-09",
         "entry_type": "milestone", "text": "项目正式投产, 设备全部进场。"}])
    ledger_service.generate_suggestions(db, pid)
    assert db.list_suggestions(pid, "pending") == []
