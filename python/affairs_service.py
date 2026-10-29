"""python/affairs_service.py — 时期任务服务层(3.0 二期)

职责:周期节奏滚动生成 / 阶段切换激活 / 终态事件自动完成匹配。
惯例对齐 activity_events.py:模块级函数、自带 try/except 兜底仅记日志——
闭环是增强不是依赖(§5.4 异常语义),任何失败不阻塞解析/报告/产出主流程。
不做 HTTP;路由层(routers/affairs.py)与终态声明点(pipeline/reports/
ppt_service/forms)直调。
"""
import calendar as _cal
import json
import logging
import re
from datetime import date, timedelta

import activity_events

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


def first_open_occurrence(rule: dict, today: date) -> tuple[str, str]:
    """首个「未出生即逾期」期次(key, due):当期 due 未过 → 当期;已过 → 下一期。
    2026-10-24 裁决:周期任务出生(铺入/激活/自建)前期次不生成、不算缺失——
    否则中途接入的项目会永久挂着一段出生前的逾期。activate_phase 与自建
    recurring 路由共用;DB 层铺入副本 _first_open_occurrence 同构(测试互证)。"""
    occ, due = current_occurrence(rule, today)
    if due < today.isoformat():
        return next_occurrence(rule, occ)
    return occ, due


_VALID_FREQS = ("monthly", "quarterly", "yearly")
_MMDD = re.compile(r"^(\d{2})-(\d{2})$")
# freq → occurrence_key 形态(三格式:2026-10 / 2026-Q4 / 2026,字典序可比)。
# 滚动起点只认同格式实例:改 freq 后旧格式 key 不参与缺口回铺,跨格式混排
# (monthly key 撞 quarterly 推进 → 静默产出重复语义期次/IndexError)从根上
# 消灭(2026-10-21 裁决:方案 1「改节奏仅影响未来期次」完整落地)
_KEY_SHAPES = {
    "monthly": re.compile(r"^\d{4}-\d{2}$"),
    "quarterly": re.compile(r"^\d{4}-Q[1-4]$"),
    "yearly": re.compile(r"^\d{4}$"),
}


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
    """滚动生成缺失期次实例；首查 DB 失败上抛（组级失败内部隔离不拖垮其余）。"""
    return _roll_recurring_inner(db)


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
    known = sorted(k for k in existing if k)
    if not known:
        return 0
    # 迭代起点取「现存同格式最早」(裁决 4 完整落地,2026-10-21:改节奏仅影响
    # 未来期次——起点只认同当前 freq 格式的实例,旧格式 key 不参与,跨格式混排
    # 从根上消灭;NULL occurrence_key 的脏数据行不参与起点计算,
    # 防 min(None) TypeError)。2026-10-24 裁决:不再回退到当期(原
    # min(same_fmt[0], cur_key) 会把铺入/激活时被跳过的出生前期次、以及用户
    # 已删除的过去期次重新补回,重新产出出生即逾期/违背删除意愿)
    shape = _KEY_SHAPES[rule["freq"]]
    same_fmt = [k for k in known if shape.match(k)]
    key = same_fmt[0] if same_fmt else cur_key
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
    """跨日即滚;同日重复调用零开销(lifespan roll 后同进程共享 _last_roll_date)。

    首查 DB 瞬时异常不置当日标记,当日后续心跳/重启可重试(与 reminders/forms
    扫描口径一致,review 问题 2);组级失败属设计容忍,不影响置位。
    """
    global _last_roll_date
    today = date.today().isoformat()
    if _last_roll_date == today:
        return False
    try:
        roll_recurring(db)
    except Exception:
        logger.warning("daily roll failed, will retry", exc_info=True)
        return False
    _last_roll_date = today
    return True


# ── 事务到期/逾期提醒扫描（2.11 §3.2）──

_last_reminder_date: str | None = None


def maybe_scan_reminders(db) -> bool:
    """每日一次事务提醒扫描（心跳入口调用，同日零开销；服务层自行兑底异常）。

    due_date == today → notify(kind='due')；due_date < today → notify(kind='overdue')
    （list_due_phase_tasks(horizon_days=0) 即逾期全部 + 今日，已过滤基础库/归档）。
    按天查重（add_notification_daily）→ 每事务每 kind 每天至多一条（D1 跨天重发）。
    首次调用即扫（覆盖应用当天中途启动）；开关关闭不扫不置标记（开启当日补扫）。
    通知不隶属长时活动，不注册 registry（2.11 D7）。返回是否执行了扫描。
    """
    global _last_reminder_date
    today = date.today().isoformat()
    if _last_reminder_date == today:
        return False
    try:
        # 开关关闭：不扫不置标记（开启当日补扫，2.11 §3.2）
        if not db.get_settings().get("affair_reminder_enabled", True):
            return False
        due_tasks = db.list_due_phase_tasks(horizon_days=0)
    except Exception:
        logger.warning("affair reminder scan failed", exc_info=True)
        return False
    _last_reminder_date = today
    notified = 0
    for pt in due_tasks:
        kind = "due" if pt["due_date"] == today else "overdue"
        try:
            activity_events.notify(
                db,
                entity_type="affair", entity_id=pt["id"],
                project_id=pt["project_id"], container="project",
                kind=kind, entity_title=pt["title"],
            )
            notified += 1
        except Exception:
            logger.warning("affair reminder notify failed for %s", pt["id"], exc_info=True)
    if notified:
        logger.info("Affair reminders sent: %d", notified)
    return True


# ── 阶段切换激活(§5.3)──

def activate_phase(db, project_id: str, new_stage: str) -> int:
    """激活指定时期的 pending_activation 实例（§5.3）。

    异常上抛不吞（review M-2）：调用方（knowledge.update_project）依赖异常
    阻断 stage 写入——吞掉会「阶段已换、任务未铺出」且用户零感知。实例铺出
    整体单事务，中途失败回滚半成品，不留部分 open 实例。
    """
    phase = phase_of(new_stage)
    with db.conn.transaction():
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
            # rule 实例(report+rule 组合同样适用):按首个未过期期次算 occ+due,
            # 否则 roll 会把缺失的当期 occurrence 当缺口补出重复实例;
            # 当期已过时直接取下一期(2026-10-24 裁决:激活不产出出生即逾期实例)
            rule = parse_rule(r["recurrence_rule"])
            if rule:
                occ, due = first_open_occurrence(rule, today)
            elif r["due_date_fixed"]:
                due = r["due_date_fixed"]
            elif r["due_offset_days"]:
                due = (today + timedelta(days=int(r["due_offset_days"]))).isoformat()
            db.conn.execute(
                "UPDATE phase_tasks SET status='open', due_date=?, occurrence_key=? WHERE id=?",
                (due, occ, r["id"]))
            n += 1
    return n


# ── 终态事件自动完成(§5.4 事件路径)──

def _match_hit(auto: dict, text: str) -> bool:
    m = str(auto.get("match") or "")
    if m.startswith("title~"):
        m = m[6:]
    return any(kw and kw in text for kw in m.split("|"))


def _auto_of(raw) -> dict | None:
    """auto_complete 列(str JSON 或已解码 dict)→ dict;无效返回 None。"""
    try:
        auto = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return None
    return auto if isinstance(auto, dict) else None


def _complete_by_rule(db, project_id: str, text: str, rule_on: str,
                      entity_type: str, entity_id: str,
                      task_type: str | None = None) -> int:
    """按规则类型扫描 open 任务,产出/事件文本命中即闭环;返回完成数。

    output_completed(产出维度,2026-10-28)不限 task_type——材料/报告/节点/
    周期任务均可挂产出闭环;report_completed 存量语义保持仅 report 类
    (§3.3 模板种子契约,不动)。周期任务命中仅闭最早一期(裁决 5,与
    on_file_parsed 同构)——roll 已预铺下期,全闭会误掉下期且不重铺。"""
    sql = ("SELECT id, task_type, occurrence_key, auto_complete FROM phase_tasks "
           "WHERE project_id=? AND status='open'")
    params: list = [project_id]
    if task_type:
        sql += " AND task_type=?"
        params.append(task_type)
    hits = []
    for r in db.conn.execute(sql, params).fetchall():
        auto = _auto_of(r["auto_complete"])
        if not auto or auto.get("on") != rule_on:
            continue
        if _match_hit(auto, text or ""):
            hits.append(r)
    done = 0
    for r in hits:  # 一次性任务(material/report/milestone):命中实例全完成
        if r["task_type"] == "recurring":
            continue
        _complete(db, r["id"], entity_type, entity_id)
        done += 1
    recurring_hits = [r for r in hits if r["task_type"] == "recurring"]
    if recurring_hits:
        # recurring:仅最早 open 期(裁决 5);NULL occurrence_key 脏行不参与选取
        valid = [r for r in recurring_hits if r["occurrence_key"]]
        if valid:
            earliest = min(valid, key=lambda r: r["occurrence_key"])
            _complete(db, earliest["id"], entity_type, entity_id)
            done += 1
    return done


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
        # 双扫:存量 report_completed 仅 report 类(§3.3 种子契约不动);
        # 产出规则 output_completed 不限类型(2026-10-28 产出维度)
        done = _complete_by_rule(db, rep["project_id"], rep["title"] or "",
                                 "report_completed", "report", report_id,
                                 task_type="report")
        done += _complete_by_rule(db, rep["project_id"], rep["title"] or "",
                                  "output_completed", "report", report_id)
        if done:
            db.conn.commit()
        return done
    except Exception:
        logger.warning("on_report_completed failed for %s", report_id, exc_info=True)
        return 0


def on_ppt_completed(db, ppt_id: str) -> int:
    """PPT 交付完成闭环(产出维度):交付物标题命中 output_completed 规则即完成。"""
    try:
        ppt = db.conn.execute(
            "SELECT project_id, title FROM ppts WHERE id = ?", (ppt_id,)).fetchone()
        if not ppt or not ppt["project_id"]:
            return 0  # 行不存在/已删:静默
        done = _complete_by_rule(db, ppt["project_id"], ppt["title"] or "",
                                 "output_completed", "ppt", ppt_id)
        if done:
            db.conn.commit()
        return done
    except Exception:
        logger.warning("on_ppt_completed failed for %s", ppt_id, exc_info=True)
        return 0


def on_form_fill_completed(db, fill_id: str) -> int:
    """填表完成闭环(产出维度):产物标题命中 output_completed 规则即完成。"""
    try:
        fill = db.conn.execute(
            "SELECT project_id, title FROM form_fills WHERE id = ?", (fill_id,)).fetchone()
        if not fill or not fill["project_id"]:
            return 0  # 行不存在/已删:静默
        done = _complete_by_rule(db, fill["project_id"], fill["title"] or "",
                                 "output_completed", "form_fill", fill_id)
        if done:
            db.conn.commit()
        return done
    except Exception:
        logger.warning("on_form_fill_completed failed for %s", fill_id, exc_info=True)
        return 0
