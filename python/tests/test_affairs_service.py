"""python/tests/test_affairs_service.py — 3.0 二期 Task 3:滚动/激活/事件匹配。"""
import os
import sys
import tempfile
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import activity_events
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


def test_activate_phase_failure_raises_and_rolls_back(monkeypatch, db):
    """M-2 回归：激活中途失败 → 异常上抛（路由据此 500 且不写 stage）+
    半成品回滚（不留部分 open 实例）。原实现吞异常 return 0 且无事务包裹。"""
    pid = _project(db, stage="before")
    original = affairs_service.parse_rule
    seen = {"n": 0}

    def flaky_parse(rule):
        seen["n"] += 1
        if seen["n"] >= 2:
            raise RuntimeError("boom")
        return original(rule)

    monkeypatch.setattr(affairs_service, "parse_rule", flaky_parse)
    with pytest.raises(RuntimeError):
        affairs_service.activate_phase(db, pid, "during")
    rows = [t for t in db.list_phase_tasks(pid) if t["phase"] == "during"]
    assert rows, "during 期应有 pending_activation 实例"
    assert all(t["status"] == "pending_activation" for t in rows)


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

def test_roll_freq_change_no_history_backfill(db):
    """monthly→quarterly:改节奏仅影响未来期次(裁决 4 完整落地,2026-10-21)——
    起点只认同格式(quarterly)实例,旧 monthly key 不参与:仅补当期+下期,
    不回铺历史季度,月度存量原样保留,下次 roll 幂等。
    修复前:起点 2026-10 按 quarterly 推进,静默补出 2026-Q1..Q4 与月度
    实例语义重复并存(open-questions.md Q-1,原混排收敛测试已随根因改写)。"""
    pid = _project(db)
    affairs_service.roll_recurring(db)
    monthly = "playbook_recruitment_after_monthly_report"
    newest = max((t for t in db.list_phase_tasks(pid) if t["template_id"] == monthly),
                 key=lambda t: t["occurrence_key"])
    db.update_phase_task(newest["id"],
                         {"recurrence_rule": '{"freq": "quarterly", "due_day": 15}'})
    created = affairs_service.roll_recurring(db)
    q_rule = {"freq": "quarterly", "due_day": 15}
    m_rule = {"freq": "monthly", "due_day": 15}
    cur_q = affairs_service.current_occurrence(q_rule, date.today())[0]
    nxt_q = affairs_service.next_occurrence(q_rule, cur_q)[0]
    cur_m = affairs_service.current_occurrence(m_rule, date.today())[0]
    nxt_m = affairs_service.next_occurrence(m_rule, cur_m)[0]
    keys = sorted(t["occurrence_key"] for t in db.list_phase_tasks(pid)
                  if t["template_id"] == monthly and t["occurrence_key"])
    assert created == 2
    assert keys == sorted({cur_m, nxt_m, cur_q, nxt_q})
    assert affairs_service.roll_recurring(db) == 0  # 幂等


def test_roll_freq_change_yearly_to_quarterly_rolls_from_current(db):
    """yearly→quarterly:旧格式 key(4 位年份)不参与起点计算,从当期正常滚动。
    修复前该场景 yearly key 遇 quarterly 规则 key[6] 越界 IndexError,
    靠组级隔离跳过整组(原 test_roll_recurring_isolates_bad_group)——
    根因修复后不再炸、不再跳组。"""
    pid = _project(db)
    affairs_service.roll_recurring(db)
    yearly = "playbook_recruitment_after_yearly_data"
    newest = max((t for t in db.list_phase_tasks(pid) if t["template_id"] == yearly),
                 key=lambda t: t["occurrence_key"])
    db.update_phase_task(newest["id"],
                         {"recurrence_rule": '{"freq": "quarterly", "due_day": 15}'})
    pid2 = _project(db)
    created = affairs_service.roll_recurring(db)
    q_rule = {"freq": "quarterly", "due_day": 15}
    y_rule = {"freq": "yearly", "due_day": "04-30"}
    cur_q = affairs_service.current_occurrence(q_rule, date.today())[0]
    nxt_q = affairs_service.next_occurrence(q_rule, cur_q)[0]
    cur_y = affairs_service.current_occurrence(y_rule, date.today())[0]
    nxt_y = affairs_service.next_occurrence(y_rule, cur_y)[0]
    # pid2 的 4 组各补下期 + pid 的 yearly 组按新节奏补当期+下期
    assert created == 6
    keys = {t["occurrence_key"] for t in db.list_phase_tasks(pid)
            if t["template_id"] == yearly}
    assert keys == {cur_y, nxt_y, cur_q, nxt_q}


def test_roll_group_failure_isolated(db, monkeypatch):
    """组级异常隔离(纵深防御):单组运行时异常只跳过该组,其余项目/组照常。
    原触发场景(跨格式混排炸)已根因修复,此处以注入异常锁定隔离语义不回退。"""
    pid = _project(db)
    pid2 = _project(db)
    real_roll = affairs_service._roll_one_group

    def flaky(db_, g, today):
        if g["project_id"] == pid:
            raise RuntimeError("boom")
        return real_roll(db_, g, today)

    monkeypatch.setattr(affairs_service, "_roll_one_group", flaky)
    created = affairs_service.roll_recurring(db)  # 不抛
    assert created == 4  # pid2 的 4 组各补下期;pid 全组跳过
    assert len([t for t in db.list_phase_tasks(pid)
                if t["template_id"] == "playbook_recruitment_after_monthly_report"]) == 1


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


def test_maybe_roll_daily_db_failure_retries_same_day(db, monkeypatch):
    """首查 DB 异常不置当日标记 → 当日后续心跳/重启可重试
    （review 问题 2，同 reminders/forms 扫描口径；lifespan/心跳两入口均依赖
    服务层不抛，本用例同时锁定「失败不上抛」语义）。"""
    monkeypatch.setattr(affairs_service, "_last_roll_date", None)
    _project(db)  # 带 4 个 rule 组，roll 有实际工作可做
    gate = {"fail": True}
    real_conn = db.conn

    class _FlakyConn:
        def execute(self, sql, *a, **k):
            if gate["fail"]:
                raise RuntimeError("database is locked")
            return real_conn.execute(sql, *a, **k)

        def __getattr__(self, name):
            return getattr(real_conn, name)

    monkeypatch.setattr(db, "conn", _FlakyConn())
    assert affairs_service.maybe_roll_daily(db) is False  # 首查失败：不抛、不置标记
    assert affairs_service._last_roll_date is None
    gate["fail"] = False  # 瞬时故障恢复
    assert affairs_service.maybe_roll_daily(db) is True  # 同日重试：执行成功
    assert affairs_service._last_roll_date  # 成功后才置位
    assert affairs_service.maybe_roll_daily(db) is False  # 同日幂等门


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


# ── 事务到期/逾期提醒扫描（2.11 §3.2）──

def _monthly_rows(db, pid):
    """roll 后 monthly 组当期 + 下期两行（按 occurrence_key 升序）。"""
    return sorted(
        (t for t in db.list_phase_tasks(pid)
         if t["template_id"] == "playbook_recruitment_after_monthly_report"),
        key=lambda t: t["occurrence_key"])


def test_scan_reminders_due_and_overdue(db, monkeypatch):
    """due_date==today → due；<today → overdue；未来（horizon 外）不通知（2.11 §3.2）。"""
    monkeypatch.setattr(affairs_service, "_last_reminder_date", None)
    pid = _project(db)
    affairs_service.roll_recurring(db)
    today = date.today()
    rows = _monthly_rows(db, pid)
    due_row, overdue_row = rows[0], rows[1]
    yearly = sorted(
        (t for t in db.list_phase_tasks(pid)
         if t["template_id"] == "playbook_recruitment_after_yearly_data"),
        key=lambda t: t["occurrence_key"])
    future_row = yearly[1]
    db.conn.execute("UPDATE phase_tasks SET due_date=? WHERE id=?",
                    (today.isoformat(), due_row["id"]))
    db.conn.execute("UPDATE phase_tasks SET due_date=? WHERE id=?",
                    ((today - timedelta(days=1)).isoformat(), overdue_row["id"]))
    db.conn.execute("UPDATE phase_tasks SET due_date=? WHERE id=?",
                    ((today + timedelta(days=10)).isoformat(), future_row["id"]))
    db.conn.commit()

    assert affairs_service.maybe_scan_reminders(db) is True
    by_entity = {n["entity_id"]: n for n in db.list_unread_notifications()}
    assert by_entity[due_row["id"]]["kind"] == "due"
    assert by_entity[overdue_row["id"]]["kind"] == "overdue"
    assert future_row["id"] not in by_entity
    # affair 导航取数依赖的字段（ActivityMonitor navigateToNotification affair 分支）
    n = by_entity[due_row["id"]]
    assert n["entity_type"] == "affair"
    assert n["project_id"] == pid
    assert n["container"] == "project"
    assert n["entity_title"] == due_row["title"]


def test_scan_reminders_disabled_defers_scan(db, monkeypatch):
    """开关关闭不扫不置标记；重新开启当日即补扫（2.11 §3.2）。"""
    monkeypatch.setattr(affairs_service, "_last_reminder_date", None)
    db.save_settings({"affair_reminder_enabled": False})
    assert affairs_service.maybe_scan_reminders(db) is False
    assert db.list_unread_notifications() == []
    db.save_settings({"affair_reminder_enabled": True})
    assert affairs_service.maybe_scan_reminders(db) is True  # 标记未被关闭分支置位 → 补扫


def test_scan_reminders_same_day_idempotent(db, monkeypatch):
    """同日第二次调用零开销（标记短路，心跳每次拉取都进不来）。"""
    monkeypatch.setattr(affairs_service, "_last_reminder_date", None)
    assert affairs_service.maybe_scan_reminders(db) is True
    assert affairs_service.maybe_scan_reminders(db) is False


def test_scan_reminders_notify_failure_isolated(db, monkeypatch):
    """单事务 notify 失败只丢该条（per-task 隔离），其余照发、不抛、标记照置。"""
    monkeypatch.setattr(affairs_service, "_last_reminder_date", None)
    pid = _project(db)
    affairs_service.roll_recurring(db)
    rows = _monthly_rows(db, pid)
    bad_row, other_row = rows[0], rows[1]
    today = date.today().isoformat()
    db.conn.execute("UPDATE phase_tasks SET due_date=? WHERE id=?", (today, bad_row["id"]))
    db.conn.execute("UPDATE phase_tasks SET due_date=? WHERE id=?", (today, other_row["id"]))
    db.conn.commit()
    real_notify = activity_events.notify

    def flaky(db_, **kwargs):
        if kwargs.get("entity_id") == bad_row["id"]:
            raise RuntimeError("boom")
        return real_notify(db_, **kwargs)

    monkeypatch.setattr(activity_events, "notify", flaky)
    assert affairs_service.maybe_scan_reminders(db) is True  # 不抛
    ids = {n["entity_id"] for n in db.list_unread_notifications()}
    assert bad_row["id"] not in ids
    assert other_row["id"] in ids
    assert affairs_service.maybe_scan_reminders(db) is False  # 标记已置（同日不重扫）
