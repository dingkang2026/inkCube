"""python/tests/test_affairs_service.py — 3.0 二期 Task 3:滚动/激活/事件匹配。"""
import os
import sys
import tempfile
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import affairs_service
from db.database import Database
from db.playbook_seed import get_all_playbook_templates


@pytest.fixture()
def db():
    with tempfile.TemporaryDirectory() as td:
        db = Database(os.path.join(td, "t.db"))
        db.initialize()
        db.ensure_preset_base_libs()  # create_project 的 auto-link 依赖 preset 基础库
        db.seed_playbook_templates(get_all_playbook_templates())
        yield db
        db.conn.close()


def _project(db, ptype="recruitment", stage="after"):
    return db.create_project(name="P", project_type=ptype, stage=stage)["id"]


def test_occurrence_math():
    rule = {"freq": "monthly", "due_day": 15}
    assert affairs_service.current_occurrence(rule, date(2026, 10, 9)) == ("2026-10", "2026-10-15")
    assert affairs_service.next_occurrence(rule, "2026-10") == ("2026-11", "2026-11-15")
    assert affairs_service.prev_occurrence(rule, "2026-10") == ("2026-09", "2026-09-15")
    q = {"freq": "quarterly", "due_day": "end"}
    assert affairs_service.current_occurrence(q, date(2026, 10, 9)) == ("2026-Q4", "2026-12-31")
    # 季度跨年
    assert affairs_service.next_occurrence(q, "2026-Q4") == ("2027-Q1", "2027-03-31")
    assert affairs_service.prev_occurrence(q, "2027-Q1") == ("2026-Q4", "2026-12-31")
    y = {"freq": "yearly", "due_day": "04-30"}
    assert affairs_service.current_occurrence(y, date(2026, 10, 9)) == ("2026", "2026-04-30")
    assert affairs_service.next_occurrence(y, "2026") == ("2027", "2027-04-30")
    # monthly clamp:31 号规则遇短月归到月末
    m31 = {"freq": "monthly", "due_day": 31}
    assert affairs_service.next_occurrence(m31, "2026-01") == ("2026-02", "2026-02-28")
    assert affairs_service.current_occurrence(m31, date(2026, 4, 10)) == ("2026-04", "2026-04-30")


def test_roll_recurring_creates_next_once(db):
    pid = _project(db)
    created = affairs_service.roll_recurring(db)
    # 铺入已给 4 个 rule 组各当期一期(3 recurring + service_report[report+rule]);
    # roll 按 recurrence_rule IS NOT NULL 分组,补齐至当期+下期 → 恰各补 1
    assert created == 4
    again = affairs_service.roll_recurring(db)
    assert again == 0    # 幂等


def test_roll_next_occurrence_due_is_per_period(db):
    """下期实例的 due 按其自身期次推导,不得错记当期 due。"""
    pid = _project(db)
    affairs_service.roll_recurring(db)
    monthly = "playbook_recruitment_after_monthly_report"
    rows = [t for t in db.list_phase_tasks(pid) if t["template_id"] == monthly]
    rule = rows[0]["recurrence_rule"]
    cur = affairs_service.current_occurrence(rule, date.today())[0]
    nxt_key, nxt_due = affairs_service.next_occurrence(rule, cur)
    row = next(t for t in rows if t["occurrence_key"] == nxt_key)
    assert row["due_date"] == nxt_due


def test_roll_recurring_fills_gap_after_delete(db):
    pid = _project(db)
    affairs_service.roll_recurring(db)
    # 模拟缺口:删掉月报当期实例 → roll 应重新补出
    monthly = next(t for t in db.list_phase_tasks(pid)
                   if t["template_id"] == "playbook_recruitment_after_monthly_report")
    db.delete_phase_task(monthly["id"])
    created = affairs_service.roll_recurring(db)
    assert created >= 1
    keys = [t["occurrence_key"] for t in db.list_phase_tasks(pid)
            if t["template_id"] == "playbook_recruitment_after_monthly_report"]
    assert len(keys) == 2  # 当期 + 下期


def test_roll_recurring_covers_report_with_rule(db):
    """report+rule 组合(service_report)同样滚动:roll 后存在下期实例。"""
    pid = _project(db)
    affairs_service.roll_recurring(db)
    keys = sorted(t["occurrence_key"] for t in db.list_phase_tasks(pid)
                  if t["template_id"] == "playbook_recruitment_after_service_report")
    assert len(keys) == 2
    assert keys[1] == str(int(keys[0]) + 1)


def test_activate_phase_opens_pending_and_computes_due(db):
    pid = _project(db, stage="before")
    assert not [t for t in db.list_phase_tasks(pid)
                if t["phase"] == "during" and t["status"] == "open"]
    n = affairs_service.activate_phase(db, pid, "during")
    assert n == 10  # recruitment during: 3 material + 2 report + 5 milestone
    during = [t for t in db.list_phase_tasks(pid) if t["phase"] == "during"]
    assert all(t["status"] == "open" for t in during)
    # 有截止规则的模板必须换算出 due;无规则的 milestone/material due=None 属设计本意
    with_due = {t["id"] for t in get_all_playbook_templates()
                if t["phase"] == "during"
                and (t.get("due_offset_days") or t.get("due_date_fixed") or t.get("recurrence_rule"))}
    assert all(t["due_date"] for t in during if t["template_id"] in with_due)


def test_on_file_parsed_completes_material_and_earliest_recurring(db):
    pid = _project(db)  # stage=after: recurring open,material 全 pending
    bp = next(t for t in db.list_phase_tasks(pid)
              if t["template_id"] == "playbook_recruitment_before_bp")
    db.update_phase_task(bp["id"], {"status": "open", "due_date": "2026-10-20"})
    done = affairs_service.on_file_parsed(db, pid, "XX科技商业计划书v3.pdf", "f1")
    assert done == 1
    assert db.get_phase_task(bp["id"])["status"] == "done"
    assert db.get_phase_task(bp["id"])["completed_by"] == "auto"
    # 幂等:已 done 不再作为候选
    assert affairs_service.on_file_parsed(db, pid, "BP.pdf", "f2") == 0
    # recurring:仅最早 open 期完成
    monthly_open = sorted(
        (t for t in db.list_phase_tasks(pid)
         if t["template_id"] == "playbook_recruitment_after_monthly_report"
         and t["status"] == "open"),
        key=lambda t: t["occurrence_key"])
    earliest = monthly_open[0]["occurrence_key"]
    assert affairs_service.on_file_parsed(db, pid, "9月月报.xlsx", "f3") == 1
    after = [t["occurrence_key"] for t in db.list_phase_tasks(pid)
             if t["template_id"] == "playbook_recruitment_after_monthly_report"
             and t["status"] == "done"]
    assert earliest in after


def test_on_report_completed_title_match_only(db):
    pid = _project(db, stage="before")
    initial = next(t for t in db.list_phase_tasks(pid)
                   if t["template_id"] == "playbook_recruitment_before_initial_report")
    db.update_phase_task(initial["id"], {"status": "open", "due_date": "2026-10-20"})

    def _report(title):
        rid = db._uuid()
        db.conn.execute(
            "INSERT INTO reports (id, project_id, template_id, title, output_format, status, created_at, updated_at) "
            "VALUES (?, ?, 't', ?, 'docx', 'done', '2026-10-09', '2026-10-09')",
            (rid, pid, title))
        db.conn.commit()
        return rid

    miss = _report("随便什么报告")
    hit = _report("企业初评报告")
    assert affairs_service.on_report_completed(db, miss) == 0  # 未命中保留手动
    assert affairs_service.on_report_completed(db, hit) == 1
    assert db.get_phase_task(initial["id"])["status"] == "done"


def test_on_report_completed_missing_report_silent(db):
    assert affairs_service.on_report_completed(db, "no-such-report") == 0


def test_on_file_parsed_swallows_db_errors(db):
    pid = _project(db)
    db.conn.execute("DROP TABLE phase_tasks")
    db.conn.commit()
    assert affairs_service.on_file_parsed(db, pid, "BP.pdf", "f1") == 0  # 不抛(§5.4)


# ── 评审修复回归:组间隔离 / 白名单 / NULL key / 逐日节流 / 上限 ──

def test_roll_recurring_isolates_bad_group(db):
    """freq 变更致跨格式 key 解析炸(yearly key "2026" 遇 quarterly 规则 key[6] 越界):
    坏组跳过(不抛、不落半组脏数据),其余项目/组照常滚动。"""
    pid = _project(db)
    affairs_service.roll_recurring(db)
    yearly = "playbook_recruitment_after_yearly_data"
    newest = max((t for t in db.list_phase_tasks(pid) if t["template_id"] == yearly),
                 key=lambda t: t["occurrence_key"])
    # 模拟用户改节奏:最新期实例 rule 改 quarterly → 下次 roll 解析旧 key 即炸
    db.update_phase_task(newest["id"],
                         {"recurrence_rule": '{"freq": "quarterly", "due_day": 15}'})
    pid2 = _project(db)
    created = affairs_service.roll_recurring(db)  # 修复前:IndexError 穿透,返回 0
    assert created == 4                            # pid2 的 4 组各补下期
    # 坏组仅跳过:pid 的 yearly 组不变(pid2 同名组正常滚动)
    assert len([t for t in db.list_phase_tasks(pid)
                if t["template_id"] == yearly]) == 2
    assert len([t for t in db.list_phase_tasks(pid2)
                if t["template_id"] == yearly]) == 2


def test_roll_freq_change_mixed_keys_converges(db):
    """monthly→quarterly 不炸(q=0 经 q+1 归为 Q1 合法):按新节奏补未来期,
    混排下次 roll 幂等收敛,符合裁决 4「改节奏仅影响未来期次」。"""
    pid = _project(db)
    affairs_service.roll_recurring(db)
    monthly = "playbook_recruitment_after_monthly_report"
    newest = max((t for t in db.list_phase_tasks(pid) if t["template_id"] == monthly),
                 key=lambda t: t["occurrence_key"])
    db.update_phase_task(newest["id"],
                         {"recurrence_rule": '{"freq": "quarterly", "due_day": 15}'})
    created = affairs_service.roll_recurring(db)
    # 旧 monthly key 迭代按 quarterly 规则 next:起点 2026-10 起补出
    # 2026-Q1..Q4 + 2027-Q1 共 5 期(当期至下期)
    assert created == 5
    # 未来期 due 同样按自身期次推导(quarterly 固定季末,不依赖 due_day)
    fut = next(t for t in db.list_phase_tasks(pid)
               if t["template_id"] == monthly and t["occurrence_key"] == "2027-Q1")
    assert fut["due_date"] == "2027-03-31"
    assert affairs_service.roll_recurring(db) == 0  # 混排态下下次 roll 幂等


def test_roll_recurring_skips_invalid_rule(db):
    """白名单外 freq(weekly):组静默跳过并告警,其余组照常。"""
    pid = _project(db)
    monthly = "playbook_recruitment_after_monthly_report"
    newest = max((t for t in db.list_phase_tasks(pid) if t["template_id"] == monthly),
                 key=lambda t: t["occurrence_key"])
    db.update_phase_task(newest["id"],
                         {"recurrence_rule": '{"freq": "weekly", "due_day": 1}'})
    created = affairs_service.roll_recurring(db)
    assert created == 3  # 坏组跳过,其余 3 组各补下期
    assert len([t for t in db.list_phase_tasks(pid)
                if t["template_id"] == monthly]) == 1


def test_on_file_parsed_ignores_null_occurrence_key(db):
    """NULL occurrence_key 脏行不参与 earliest 选取(or "" 会使其恒最小)。"""
    pid = _project(db)
    affairs_service.roll_recurring(db)  # monthly 组:当期 + 下期
    monthly = "playbook_recruitment_after_monthly_report"
    rows = sorted((t for t in db.list_phase_tasks(pid) if t["template_id"] == monthly),
                  key=lambda t: t["occurrence_key"])
    db.conn.execute("UPDATE phase_tasks SET occurrence_key = NULL WHERE id = ?",
                    (rows[0]["id"],))
    db.conn.commit()
    assert affairs_service.on_file_parsed(db, pid, "10月月报.xlsx", "f1") == 1
    assert db.get_phase_task(rows[1]["id"])["status"] == "done"  # 有效 key 最早者被闭环
    assert db.get_phase_task(rows[0]["id"])["status"] == "open"  # 脏行不动


def test_maybe_roll_daily(db, monkeypatch):
    """跨日首滚返回 True;同日重复调用零开销;日期变化即滚。"""
    pid = _project(db)
    monkeypatch.setattr(affairs_service, "_last_roll_date", None)
    assert affairs_service.maybe_roll_daily(db) is True
    assert affairs_service.maybe_roll_daily(db) is False
    monkeypatch.setattr(affairs_service, "_last_roll_date", "2020-01-01")
    assert affairs_service.maybe_roll_daily(db) is True


def test_roll_capped_at_max_periods(db):
    """缺口超过 _MAX_ROLL_PERIODS:补出数不超上限,不补穿到下期,不死循环。"""
    pid = _project(db)
    monthly = "playbook_recruitment_after_monthly_report"
    src = next(t for t in db.list_phase_tasks(pid) if t["template_id"] == monthly)
    # 造 2016-01 远古实例 → 与当期之间是百余期缺口(> 上限)
    db.conn.execute(
        """INSERT INTO phase_tasks (id, project_id, phase, task_type, title, template_id,
           status, occurrence_key, recurrence_rule)
           SELECT ?, project_id, phase, task_type, title, template_id,
                  'open', '2016-01', recurrence_rule FROM phase_tasks WHERE id = ?""",
        (db._uuid(), src["id"]))
    db.conn.commit()
    affairs_service.roll_recurring(db)
    cur_key = affairs_service.current_occurrence(
        {"freq": "monthly", "due_day": 15}, date.today())[0]
    keys = [t["occurrence_key"] for t in db.list_phase_tasks(pid)
            if t["template_id"] == monthly and t["occurrence_key"]]
    assert min(keys) == "2016-01"
    new_keys = set(keys) - {"2016-01", cur_key}
    assert len(new_keys) <= affairs_service._MAX_ROLL_PERIODS  # 上限生效
    assert affairs_service.next_occurrence({"freq": "monthly", "due_day": 15},
                                           cur_key)[0] not in new_keys  # 未补穿下期
