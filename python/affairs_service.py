"""python/affairs_service.py — 时期任务服务层(3.0 二期)

职责:周期节奏滚动生成 / 阶段切换激活 / 终态事件自动完成匹配。
惯例对齐 activity_events.py:模块级函数、自带 try/except 兜底仅记日志——
闭环是增强不是依赖(§5.4 异常语义),任何失败不阻塞解析/报告主流程。
不做 HTTP;路由层(routers/affairs.py)与终态声明点(pipeline/reports)直调。
"""
import calendar as _cal
import json
import logging
import re
from datetime import date, timedelta

logger = logging.getLogger(__name__)

_MAX_ROLL_PERIODS = 120  # 滚动迭代上限(裁决 3,防御坏 rule 死循环)


def phase_of(stage: str) -> str:
    return stage if stage in ("before", "during", "after") else "before"


# ── 期次推导(key 字典序可比:2026-10 / 2026-Q4 / 2026)──

def current_occurrence(rule: dict, today: date) -> tuple[str, str]:
    freq, day = rule.get("freq"), rule.get("due_day")
    if freq == "monthly":
        d = min(int(day), _cal.monthrange(today.year, today.month)[1])
        return (today.strftime("%Y-%m"),
                f"{today.year:04d}-{today.month:02d}-{d:02d}")
    if freq == "quarterly":
        q = (today.month - 1) // 3 + 1
        last_month = q * 3
        due = date(today.year, last_month, 1).replace(
            day=_cal.monthrange(today.year, last_month)[1])
        return f"{today.year}-Q{q}", due.isoformat()
    mmdd = str(day or "12-31")
    return today.strftime("%Y"), f"{today.year}-{mmdd}"


def next_occurrence(rule: dict, key: str) -> tuple[str, str]:
    freq, day = rule.get("freq"), rule.get("due_day")
    if freq == "monthly":
        y, m = int(key[:4]), int(key[5:7])
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        d = min(int(day), _cal.monthrange(y, m)[1])
        return f"{y:04d}-{m:02d}", f"{y:04d}-{m:02d}-{d:02d}"
    if freq == "quarterly":
        y, q = int(key[:4]), int(key[6])
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)
        last_month = q * 3
        due = date(y, last_month, 1).replace(day=_cal.monthrange(y, last_month)[1])
        return f"{y}-Q{q}", due.isoformat()
    return str(int(key) + 1), f"{int(key) + 1}-{day or '12-31'}"


def prev_occurrence(rule: dict, key: str) -> tuple[str, str]:
    freq, day = rule.get("freq"), rule.get("due_day")
    if freq == "monthly":
        y, m = int(key[:4]), int(key[5:7])
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
        d = min(int(day), _cal.monthrange(y, m)[1])
        return f"{y:04d}-{m:02d}", f"{y:04d}-{m:02d}-{d:02d}"
    if freq == "quarterly":
        y, q = int(key[:4]), int(key[6])
        y, q = (y - 1, 4) if q == 1 else (y, q - 1)
        last_month = q * 3
        due = date(y, last_month, 1).replace(day=_cal.monthrange(y, last_month)[1])
        return f"{y}-Q{q}", due.isoformat()
    return str(int(key) - 1), f"{int(key) - 1}-{day or '12-31'}"


_VALID_FREQS = ("monthly", "quarterly", "yearly")
_MMDD = re.compile(r"^(\d{2})-(\d{2})$")


def parse_rule(raw) -> dict | None:
    """recurrence_rule 列(str JSON 或已解码 dict)→ dict;无效返回 None。
    freq 白名单 + due_day 类型/值域校验:白名单外 freq 落 yearly 分支静默错行;
    monthly 非整数/越界 due_day 会在 current_occurrence 炸(int("end")/int(None))
    或产出 "2026-10-00" 类非法日期;yearly 非 "MM-DD" 真实日历日期同理。
    路由层校验用户提交的 rule 同样走此入口。"""
    try:
        rule = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(rule, dict) or rule.get("freq") not in _VALID_FREQS:
        return None
    day = rule.get("due_day")
    if rule["freq"] == "monthly":
        if isinstance(day, bool) or not isinstance(day, int) or not 1 <= day <= 31:
            return None
    elif rule["freq"] == "yearly" and day is not None:
        m = _MMDD.match(day) if isinstance(day, str) else None
        if not m:
            return None
        try:
            # 用非闰年试解析:拒 13 月/00 日及 02-30 等伪日期(current_occurrence 直拼不 clamp)
            date(2001, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    # quarterly 忽略 due_day,不校验(种子契约 "end" 仅此消费)
    return rule


# ── 滚动生成器(裁决:lifespan 启动 + /activities/status check-date)──

_last_roll_date: str | None = None


def roll_recurring(db) -> int:
    try:
        return _roll_recurring_inner(db)
    except Exception:
        logger.warning("roll_recurring failed", exc_info=True)
        return 0


def _roll_one_group(db, g, today: date) -> int:
    """单组滚动;失败抛异常由调用方隔离(组间互不拖垮),成功才提交。"""
    rows = db.conn.execute(
        """SELECT id, occurrence_key, recurrence_rule FROM phase_tasks
           WHERE project_id=? AND template_id=? AND recurrence_rule IS NOT NULL
           ORDER BY occurrence_key ASC""",
        (g["project_id"], g["template_id"]),
    ).fetchall()
    if not rows:
        return 0
    # 裁决 4:节奏取最新期实例的 rule(改节奏仅影响未来期次)
    rule = parse_rule(rows[-1]["recurrence_rule"])
    if not rule:
        logger.warning("bad recurrence_rule on %s", rows[-1]["id"])
        return 0
    cur_key, _ = current_occurrence(rule, today)
    nxt_key, _ = next_occurrence(rule, cur_key)
    existing = {r["occurrence_key"] for r in rows if r["occurrence_key"]}
    # 迭代起点取「组内最早期」与「当期」的较小者:组内最早期晚于当期时
    # (如用户删掉当期实例),仍能扫到并补出当期,不能只从现存最早期起扫;
    # NULL occurrence_key 的脏数据行不参与起点计算(防 min(None) TypeError)
    known = sorted(k for k in existing if k)
    if not known:
        return 0
    key = min(known[0], cur_key)
    proto = rows[-1]  # 新期次实例的列原型(同 project/phase/type/title/rule)
    created = 0
    # 事务级锁 + 异常自动回滚(Task 2 落地的 transaction()):组内部分插入
    # 不残留——否则会随下一组的 commit 一起落库,产生半组脏数据
    with db.conn.transaction():
        for _ in range(_MAX_ROLL_PERIODS):
            if key > nxt_key:
                break
            if key not in existing:
                # 各期 due 不同:从当期出发,晚于当期正向推、早于当期回推,
                # 得到 key 期的准确 due(否则下期实例会错记当期 due)
                w_key, w_due = current_occurrence(rule, today)
                while w_key > key:
                    w_key, w_due = prev_occurrence(rule, w_key)
                while w_key < key:
                    w_key, w_due = next_occurrence(rule, w_key)
                db.conn.execute(
                    """INSERT OR IGNORE INTO phase_tasks
                       (id, project_id, phase, task_type, title, description, template_id,
                        status, occurrence_key, due_date, recurrence_rule, auto_complete)
                       SELECT ?, project_id, phase, task_type, title, description, template_id,
                              'open', ?, ?, recurrence_rule, auto_complete
                       FROM phase_tasks WHERE id = ?""",
                    (db._uuid(), key, w_due, proto["id"]),
                )
                created += 1
            key, _ = next_occurrence(rule, key)
    return created


def _roll_recurring_inner(db) -> int:
    today = date.today()
    # 节奏分组按 recurrence_rule IS NOT NULL 而非 task_type:report 类可带
    # rule(§3.3 服务报告/投后报告),同样需要滚动生成(与铺入口径一致)
    groups = db.conn.execute(
        """SELECT project_id, template_id FROM phase_tasks
           WHERE recurrence_rule IS NOT NULL AND template_id IS NOT NULL
           GROUP BY project_id, template_id"""
    ).fetchall()
    created = 0
    # 组级异常隔离:单组坏 rule / 用户改 freq 致跨格式 key 混排时,只跳过
    # 该组(日志告警),不影响其余项目/组的滚动——否则每日启动+心跳反复失败
    for g in groups:
        try:
            created += _roll_one_group(db, g, today)
        except Exception:
            logger.warning("roll group %s/%s failed",
                           g["project_id"], g["template_id"], exc_info=True)
            continue
    return created


def maybe_roll_daily(db) -> bool:
    """跨日即滚;同日重复调用零开销(lifespan roll 后同进程共享 _last_roll_date)。"""
    global _last_roll_date
    today = date.today().isoformat()
    if _last_roll_date == today:
        return False
    _last_roll_date = today
    roll_recurring(db)
    return True


# ── 阶段切换激活(§5.3)──

def activate_phase(db, project_id: str, new_stage: str) -> int:
    try:
        phase = phase_of(new_stage)
        rows = db.conn.execute(
            """SELECT pt.id, pt.recurrence_rule, tpl.due_offset_days, tpl.due_date_fixed
               FROM phase_tasks pt
               LEFT JOIN playbook_templates tpl ON tpl.id = pt.template_id
               WHERE pt.project_id = ? AND pt.phase = ? AND pt.status = 'pending_activation'""",
            (project_id, phase),
        ).fetchall()
        today = date.today()
        n = 0
        for r in rows:
            due = None
            occ = None
            # rule 实例(report+rule 组合同样适用)按当期算 occ+due,
            # 否则 roll 会把缺失的当期 occurrence 当缺口补出重复实例
            rule = parse_rule(r["recurrence_rule"])
            if rule:
                occ, due = current_occurrence(rule, today)
            elif r["due_date_fixed"]:
                due = r["due_date_fixed"]
            elif r["due_offset_days"]:
                due = (today + timedelta(days=int(r["due_offset_days"]))).isoformat()
            db.conn.execute(
                "UPDATE phase_tasks SET status='open', due_date=?, occurrence_key=? WHERE id=?",
                (due, occ, r["id"]))
            n += 1
        if n:
            db.conn.commit()
        return n
    except Exception:
        logger.warning("activate_phase failed for %s", project_id, exc_info=True)
        return 0


# ── 终态事件自动完成(§5.4 事件路径)──

def _match_hit(auto: dict, text: str) -> bool:
    m = str(auto.get("match") or "")
    if m.startswith("title~"):
        m = m[6:]
    return any(kw and kw in text for kw in m.split("|"))


def _complete(db, task_id: str, entity_type: str, entity_id: str) -> None:
    db.conn.execute(
        """UPDATE phase_tasks SET status='done', completed_at=?, completed_by='auto',
           linked_entity_type=?, linked_entity_id=? WHERE id=?""",
        (db._now(), entity_type, entity_id, task_id))


def on_file_parsed(db, project_id: str, filename: str, file_id: str) -> int:
    try:
        rows = db.conn.execute(
            """SELECT id, task_type, occurrence_key, auto_complete FROM phase_tasks
               WHERE project_id=? AND status='open' AND task_type IN ('material','recurring')""",
            (project_id,)).fetchall()
        material_hits, recurring_hits = [], []
        for r in rows:
            try:
                auto = (json.loads(r["auto_complete"])
                        if isinstance(r["auto_complete"], str) else r["auto_complete"])
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(auto, dict) or auto.get("on") != "file_parsed":
                continue
            if _match_hit(auto, filename or ""):
                (recurring_hits if r["task_type"] == "recurring" else material_hits).append(r)
        done = 0
        for r in material_hits:  # material:全部命中实例完成(裁决 5)
            _complete(db, r["id"], "file", file_id)
            done += 1
        if recurring_hits:       # recurring:仅最早 open 期(裁决 5)
            # NULL occurrence_key 的脏行不参与 earliest 选取(or "" 会使其恒最小被优先闭环)
            valid = [r for r in recurring_hits if r["occurrence_key"]]
            if valid:
                earliest = min(valid, key=lambda r: r["occurrence_key"])
                _complete(db, earliest["id"], "file", file_id)
                done += 1
        if done:
            db.conn.commit()
        return done
    except Exception:
        logger.warning("on_file_parsed failed for %s", project_id, exc_info=True)
        return 0


def on_report_completed(db, report_id: str) -> int:
    try:
        rep = db.conn.execute(
            "SELECT project_id, title FROM reports WHERE id = ?", (report_id,)).fetchone()
        if not rep or not rep["project_id"]:
            return 0  # 报告不存在/已删:静默
        rows = db.conn.execute(
            """SELECT id, auto_complete FROM phase_tasks
               WHERE project_id=? AND status='open' AND task_type='report'""",
            (rep["project_id"],)).fetchall()
        done = 0
        for r in rows:
            try:
                auto = (json.loads(r["auto_complete"])
                        if isinstance(r["auto_complete"], str) else r["auto_complete"])
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(auto, dict) or auto.get("on") != "report_completed":
                continue
            if _match_hit(auto, rep["title"] or ""):
                _complete(db, r["id"], "report", report_id)
                done += 1
        if done:
            db.conn.commit()
        return done
    except Exception:
        logger.warning("on_report_completed failed for %s", report_id, exc_info=True)
        return 0
