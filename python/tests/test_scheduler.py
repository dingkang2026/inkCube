"""python/tests/test_scheduler.py — 2.11 E3 T2: 调度器核心单测（2.11 §5）。

覆盖：compute_next_run（daily 今天未到/已过、weekly 跨周、monthly 月末截断）、
_dispatch 触发判定顺序（§5.2：R2 忙让路不动周期 / 上次 running 记 skipped /
R1 定时单并发 / 闸门满延迟 / 正常路径建任务+run+推进）、_on_finish（done 副本
落盘 + 摘要解析 + 连续失败熔断 enabled=0）、增量注入（§6 上一期报告.md +
prompt 首部指引 / 无上期不注入）、_recover_missed（≤24h 补跑 / >24h 记
missed + 推进 + 合并通知）。

async 逻辑用 asyncio.run 驱动（项目惯例，不引入 pytest-asyncio）；
时间经 monkeypatch scheduler._now_dt 注入，断言完全确定。
"""
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import scheduler as scheduler_mod
from db.database import Database
from scheduler import (
    BREAKER_THRESHOLD,
    OUTPUT_CONTRACT,
    TICK_SECONDS,
    ScheduleScheduler,
    _parse_summary,
    compute_next_run,
)

_NOW = datetime(2026, 10, 19, 10, 0, 0)   # 周一


@pytest.fixture
def tmp_db():
    database = Database(db_path=":memory:")
    database.initialize()
    yield database
    database.conn.close()


@pytest.fixture
def frozen_now(monkeypatch):
    monkeypatch.setattr(scheduler_mod, "_now_dt", lambda: _NOW)
    return _NOW


class _FakeAgent:
    """最小 agent 替身：_running_task_ids 语义对齐真实 agent；
    run 在 workspace 写 report.md（成功）或不写（失败），驱动 done/failed 分流。"""

    def __init__(self, db, tmp_path, fail=False, gate_full=False, human_busy_id=None):
        self.db = db
        self.tmp_path = tmp_path
        self.fail = fail
        self.gate_full = gate_full
        self.prompts: list[str] = []
        self._running_task_ids: set[str] = {human_busy_id} if human_busy_id else set()

    def can_start_task(self) -> bool:
        return not self.gate_full

    def try_acquire_task(self, task_id: str) -> bool:
        if task_id in self._running_task_ids or not self.can_start_task():
            return False
        self._running_task_ids.add(task_id)
        return True

    async def run(self, task_id: str, user_message: str):
        self.prompts.append(user_message)
        self._running_task_ids.add(task_id)
        try:
            if not self.fail:
                task = self.db.get_task(task_id)
                report = Path(task["workspace_path"]) / "report.md"
                report.write_text(
                    "## 摘要\n- 要点一\n- 要点二\n## 正文\n内容\n"
                    "## 来源\n1. 测试源 · 2026-10-19 · http://x\n",
                    encoding="utf-8")
            yield {"type": "done", "data": {}}
        finally:
            self._running_task_ids.discard(task_id)


def _make_sched(db, tmp_path, agent, **kw):
    return ScheduleScheduler(db, str(tmp_path), agent=agent, **kw)


async def _drain(sched):
    """等待 _dispatch 后台 _execute 全部收尾（production 走 asyncio.create_task）。"""
    while sched._bg:
        await asyncio.gather(*list(sched._bg))


def _run_dispatch(sched, db, s):
    """dispatch + drain 同一事件循环（后台 _execute 任务须活在同一 loop）。"""
    async def scenario():
        await sched._dispatch(db.get_schedule(s["id"]))
        await _drain(sched)
    asyncio.run(scenario())


def _run_recover(sched):
    async def scenario():
        await sched._recover_missed()
        await _drain(sched)
    asyncio.run(scenario())


def _make_schedule(db, **kw):
    args = dict(name="华润舆情", prompt="扫描并输出报告", mode="general",
                category="corporate", interval_type="daily",
                interval_value="09:00",
                next_run_at="2026-10-19T09:00:00")
    args.update(kw)
    return db.create_schedule(**args)


# ── compute_next_run ─────────────────────────────────────────


def test_compute_next_run_daily():
    base = datetime(2026, 10, 19, 8, 0)          # 今天 09:00 未到 → 今天
    assert compute_next_run("daily", "09:00", base) == "2026-10-19T09:00:00"
    base2 = datetime(2026, 10, 19, 9, 0)         # 已到（==）→ 明天
    assert compute_next_run("daily", "09:00", base2) == "2026-10-20T09:00:00"
    base3 = datetime(2026, 10, 19, 23, 30)       # 已过 → 明天
    assert compute_next_run("daily", "09:00", base3) == "2026-10-20T09:00:00"


def test_compute_next_run_weekly():
    base = datetime(2026, 10, 19, 10, 0)         # 周一 10 点（本周 09:00 已过）
    assert compute_next_run("weekly", "MON,THU 09:00", base) == "2026-10-22T09:00:00"
    base2 = datetime(2026, 10, 19, 8, 0)         # 周一 8 点 → 今天 09:00
    assert compute_next_run("weekly", "MON,THU 09:00", base2) == "2026-10-19T09:00:00"
    base3 = datetime(2026, 10, 22, 10, 0)        # 周四 10 点 → 下周一（跨周）
    assert compute_next_run("weekly", "MON,THU 09:00", base3) == "2026-10-26T09:00:00"


def test_compute_next_run_monthly():
    base = datetime(2026, 11, 1, 10, 0)          # 11 月无 31 日 → 月末截断 11-30
    assert compute_next_run("monthly", "31 09:00", base) == "2026-11-30T09:00:00"
    base2 = datetime(2026, 10, 19, 10, 0)        # 1/15 均已过 → 下月 1 日
    assert compute_next_run("monthly", "1,15 09:00", base2) == "2026-11-01T09:00:00"
    assert compute_next_run("monthly", "25 09:00", base2) == "2026-10-25T09:00:00"


# ── _dispatch 触发判定（§5.2 + R1/R2）────────────────────────


def test_dispatch_r2_human_busy_defers(tmp_db, tmp_path, frozen_now):
    human = tmp_db.create_task("人工任务", data_dir=str(tmp_path))
    fake = _FakeAgent(tmp_db, tmp_path, human_busy_id=human["id"])
    sched = _make_sched(tmp_db, tmp_path, fake)
    s = _make_schedule(tmp_db)
    asyncio.run(sched._dispatch(tmp_db.get_schedule(s["id"])))
    row = tmp_db.get_schedule(s["id"])
    assert tmp_db.list_schedule_runs(s["id"]) == []      # 不建 run 行
    assert row["next_run_at"] == "2026-10-19T09:00:00"   # 周期不动（deferred）
    assert fake.prompts == []


def test_dispatch_r2_download_busy_defers(tmp_db, tmp_path, frozen_now):
    now_iso = _NOW.isoformat()
    tmp_db.conn.execute(
        "INSERT INTO model_downloads (name, status, started_at, updated_at)"
        " VALUES ('m1', 'downloading', ?, ?)", (now_iso, now_iso))
    tmp_db.conn.commit()
    fake = _FakeAgent(tmp_db, tmp_path)
    sched = _make_sched(tmp_db, tmp_path, fake)
    s = _make_schedule(tmp_db)
    asyncio.run(sched._dispatch(tmp_db.get_schedule(s["id"])))
    assert tmp_db.list_schedule_runs(s["id"]) == []
    assert tmp_db.get_schedule(s["id"])["next_run_at"] == "2026-10-19T09:00:00"


def test_dispatch_prev_running_records_skipped(tmp_db, tmp_path, frozen_now):
    fake = _FakeAgent(tmp_db, tmp_path)
    sched = _make_sched(tmp_db, tmp_path, fake)
    s = _make_schedule(tmp_db)
    tmp_db.insert_schedule_run("run_prev", s["id"], None)   # 上期仍 running
    asyncio.run(sched._dispatch(tmp_db.get_schedule(s["id"])))
    runs = tmp_db.list_schedule_runs(s["id"])
    assert sorted(r["status"] for r in runs) == ["running", "skipped"]  # 防重入记 skipped（同秒 tie 不论序）
    assert tmp_db.get_schedule(s["id"])["next_run_at"] == "2026-10-20T09:00:00"  # 正常推进
    assert fake.prompts == []                                # 不执行


def test_dispatch_gate_full_defers(tmp_db, tmp_path, frozen_now):
    fake = _FakeAgent(tmp_db, tmp_path, gate_full=True)
    sched = _make_sched(tmp_db, tmp_path, fake)
    s = _make_schedule(tmp_db)
    asyncio.run(sched._dispatch(tmp_db.get_schedule(s["id"])))
    assert tmp_db.list_schedule_runs(s["id"]) == []          # 闸门满不建行
    assert tmp_db.get_schedule(s["id"])["next_run_at"] == "2026-10-19T09:00:00"


def test_dispatch_normal_path(tmp_db, tmp_path, frozen_now):
    fake = _FakeAgent(tmp_db, tmp_path)
    sched = _make_sched(tmp_db, tmp_path, fake)
    s = _make_schedule(tmp_db)
    _run_dispatch(sched, tmp_db, s)
    runs = tmp_db.list_schedule_runs(s["id"])
    assert len(runs) == 1 and runs[0]["status"] == "done"
    run = runs[0]
    # 影子任务行：schedule_id 落列、标题「{订阅名} · M/D」、mode=general
    task = tmp_db.get_task(run["task_id"])
    assert task["schedule_id"] == s["id"]
    assert task["title"] == "华润舆情 · 10/19"
    assert task["mode"] == "general"
    # D2：人工任务列表恒不含影子行，get_task 直查可回放
    assert all(t["id"] != run["task_id"] for t in tmp_db.list_tasks()[0])
    # 期次行：副本落盘 + 摘要解析
    report_copy = Path(tmp_path) / "schedules" / f"{run['id']}.md"
    assert report_copy.is_file()
    assert run["report_path"] == str(report_copy)
    assert run["summary"] == ["要点一", "要点二"]
    # 周期推进：daily 09:00 从 now（10 点）→ 明天
    row = tmp_db.get_schedule(s["id"])
    assert row["next_run_at"] == "2026-10-20T09:00:00"
    assert row["last_run_at"] == "2026-10-19T10:00:00"
    # 终态通知经 schedule_run（entity_type 校验）
    notif = tmp_db.conn.execute(
        "SELECT entity_type, kind FROM notifications"
        " WHERE entity_id = ?", (run["id"],)).fetchone()
    assert notif is not None
    assert (notif["entity_type"], notif["kind"]) == ("schedule_run", "completed")


# ── _on_finish / 摘要解析 / 熔断 ─────────────────────────────


def test_parse_summary():
    text = "# 报告\n## 摘要\n- 要点 A（负面）\n- 要点 B\n## 正文\nxx\n"
    assert _parse_summary(text) == ["要点 A（负面）", "要点 B"]
    assert _parse_summary("无摘要节的报告\n## 正文\n- 列表不算\n") == []
    assert _parse_summary("## 摘要\n（非列表行不收）\n- 只收列表\n") == ["只收列表"]


def test_output_contract_matches_design():
    assert OUTPUT_CONTRACT.startswith("输出为工作区根目录单个报告文件 report.md")
    assert "## 摘要" in OUTPUT_CONTRACT and "## 来源" in OUTPUT_CONTRACT


def test_dispatch_consecutive_failures_breaker(tmp_db, tmp_path, frozen_now):
    fake = _FakeAgent(tmp_db, tmp_path, fail=True)
    sched = _make_sched(tmp_db, tmp_path, fake)
    s = _make_schedule(tmp_db)
    async def breaker():
        for _ in range(BREAKER_THRESHOLD):
            await sched._dispatch(tmp_db.get_schedule(s["id"]))
            await _drain(sched)
    asyncio.run(breaker())
    row = tmp_db.get_schedule(s["id"])
    assert row["consecutive_failures"] == 3
    assert row["enabled"] == 0                       # 熔断自动暂停
    assert row["next_run_at"] == "2026-10-20T09:00:00"  # 触发时已推进，保留
    runs = tmp_db.list_schedule_runs(s["id"])
    assert sorted(r["status"] for r in runs) == ["failed"] * 3
    notifs = tmp_db.conn.execute(
        "SELECT detail FROM notifications WHERE entity_type = 'schedule_run'"
        " AND kind = 'failed'").fetchall()
    assert len(notifs) == 3                          # 每次失败均通知（R4 必弹）
    assert any("自动暂停" in r["detail"] for r in notifs)   # 第 3 次带熔断文案


def test_on_finish_done_resets_failures(tmp_db, tmp_path, frozen_now):
    fake = _FakeAgent(tmp_db, tmp_path)
    sched = _make_sched(tmp_db, tmp_path, fake)
    s = _make_schedule(tmp_db)
    tmp_db.set_schedule_state(s["id"], consecutive_failures=2)  # 前两期失败
    _run_dispatch(sched, tmp_db, s)
    row = tmp_db.get_schedule(s["id"])
    assert row["consecutive_failures"] == 0          # done 归零，不熔断
    assert row["enabled"] == 1


# ── 增量注入（§6）───────────────────────────────────────────


def test_execute_injects_previous_report(tmp_db, tmp_path, frozen_now):
    fake = _FakeAgent(tmp_db, tmp_path)
    sched = _make_sched(tmp_db, tmp_path, fake)
    s = _make_schedule(tmp_db)
    prev_copy = Path(tmp_path) / "schedules" / "run_prev.md"
    prev_copy.parent.mkdir(parents=True, exist_ok=True)
    prev_copy.write_text("## 摘要\n- 旧要点\n", encoding="utf-8")
    tmp_db.insert_schedule_run("run_prev", s["id"], None)
    tmp_db.update_schedule_run("run_prev", "done", report_path=str(prev_copy))
    _run_dispatch(sched, tmp_db, s)
    task = tmp_db.get_task(tmp_db.list_schedule_runs(s["id"])[0]["task_id"])
    assert (Path(task["workspace_path"]) / "上一期报告.md").is_file()
    prompt = fake.prompts[0]
    assert prompt.startswith("工作区有《上一期报告.md》")   # 增量指引在首部
    assert prompt.endswith(s["prompt"])                      # 订阅 prompt 完整保留


def test_execute_no_previous_no_injection(tmp_db, tmp_path, frozen_now):
    fake = _FakeAgent(tmp_db, tmp_path)
    sched = _make_sched(tmp_db, tmp_path, fake)
    s = _make_schedule(tmp_db)
    _run_dispatch(sched, tmp_db, s)
    assert fake.prompts[0] == s["prompt"]            # 无上期 → 零注入


# ── _recover_missed（§5.4）──────────────────────────────────


def test_recover_catchup_within_24h(tmp_db, tmp_path, frozen_now):
    fake = _FakeAgent(tmp_db, tmp_path)
    sched = _make_sched(tmp_db, tmp_path, fake, recover_gap=0)
    s = _make_schedule(tmp_db, next_run_at="2026-10-19T08:00:00")  # 过期 2h
    _run_recover(sched)
    runs = tmp_db.list_schedule_runs(s["id"])
    assert [r["status"] for r in runs] == ["done"]   # 补跑执行
    assert len(fake.prompts) == 1
    row = tmp_db.get_schedule(s["id"])
    assert row["next_run_at"] == "2026-10-20T09:00:00"
    assert row["last_run_at"] == "2026-10-19T10:00:00"


def test_recover_missed_after_48h_merged_notify(tmp_db, tmp_path, frozen_now):
    fake = _FakeAgent(tmp_db, tmp_path)
    sched = _make_sched(tmp_db, tmp_path, fake, recover_gap=0)
    s1 = _make_schedule(tmp_db, name="订阅甲",
                        next_run_at="2026-10-17T09:00:00")   # 过期 49h
    s2 = _make_schedule(tmp_db, name="订阅乙",
                        next_run_at="2026-10-16T09:00:00")
    _run_recover(sched)
    assert fake.prompts == []                        # 不执行
    for s in (s1, s2):
        runs = tmp_db.list_schedule_runs(s["id"])
        assert [r["status"] for r in runs] == ["missed"]
        assert tmp_db.get_schedule(s["id"])["next_run_at"] == "2026-10-20T09:00:00"
    notifs = tmp_db.conn.execute(
        "SELECT entity_title, detail FROM notifications WHERE kind = 'missed'"
    ).fetchall()
    assert len(notifs) == 1                          # 同批合并一条
    assert "订阅甲" in notifs[0]["detail"] and "订阅乙" in notifs[0]["detail"]


def test_recover_survives_bad_next_run_data(tmp_db, tmp_path, frozen_now):
    """R2-P1-1：一条脏数据（next_run_at 非法日期，fromisoformat 炸）只跳过
    自身——不得杀死整批补跑，更不得杀死 _loop（start 不重建，loop 死亡即
    全部订阅停摆且重启复现）。"""
    fake = _FakeAgent(tmp_db, tmp_path)
    sched = _make_sched(tmp_db, tmp_path, fake, recover_gap=0)
    bad = _make_schedule(tmp_db, name="脏数据",
                         next_run_at="2026-09-31T09:00:00")  # 9 月无 31 日
    good = _make_schedule(tmp_db, name="好订阅",
                          next_run_at="2026-10-19T08:00:00")  # 过期 2h
    _run_recover(sched)                          # 异常逃逸则此行抛
    assert [r["status"] for r in
            tmp_db.list_schedule_runs(good["id"])] == ["done"]
    # 坏项跳过不推进（保持原值，不误标 missed）
    assert tmp_db.get_schedule(bad["id"])["next_run_at"] == "2026-09-31T09:00:00"


def test_recover_survives_bad_interval_value(tmp_db, tmp_path, frozen_now):
    """R2-P1-1：>24h 分支的 compute_next_run KeyError（weekly 非法星期名）
    同样单项隔离，同批其余订阅照常记 missed。"""
    fake = _FakeAgent(tmp_db, tmp_path)
    sched = _make_sched(tmp_db, tmp_path, fake, recover_gap=0)
    _make_schedule(tmp_db, name="坏周期", interval_type="weekly",
                   interval_value="ABC 09:00",
                   next_run_at="2026-10-17T09:00:00")    # 过期 49h
    good = _make_schedule(tmp_db, name="好订阅",
                          next_run_at="2026-10-16T09:00:00")
    _run_recover(sched)
    assert [r["status"] for r in
            tmp_db.list_schedule_runs(good["id"])] == ["missed"]


def test_loop_survives_recover_crash(tmp_db, tmp_path, frozen_now, monkeypatch):
    """R2-P1-1：启动补偿意外崩溃不得杀死 _loop（_task 非 None 时 start()
    不重建——loop 死亡 = 全部订阅停摆且重启复现）。"""
    fake = _FakeAgent(tmp_db, tmp_path)
    sched = _make_sched(tmp_db, tmp_path, fake, recover_gap=0)

    async def boom():
        raise RuntimeError("补偿阶段意外崩溃")

    monkeypatch.setattr(sched, "_recover_missed", boom)

    async def scenario():
        await sched.start()
        await asyncio.sleep(0.05)
        assert sched._task is not None and not sched._task.done()
        await sched.stop()

    asyncio.run(scenario())


# ── 生命周期与常量 ───────────────────────────────────────────


def test_constants():
    assert TICK_SECONDS == 60


def test_execute_acquire_race_records_failed(tmp_db, tmp_path, frozen_now):
    """dispatch 检查与执行间闸门被人工任务抢占（竞态窗口）：按失败终态收场。"""
    fake = _FakeAgent(tmp_db, tmp_path)
    sched = _make_sched(tmp_db, tmp_path, fake)
    s = _make_schedule(tmp_db)
    fake.try_acquire_task = lambda tid: False      # 竞态注入：执行期闸门被占
    _run_dispatch(sched, tmp_db, s)
    runs = tmp_db.list_schedule_runs(s["id"])
    assert [r["status"] for r in runs] == ["failed"]
    assert fake.prompts == []                      # 未执行
    row = tmp_db.get_schedule(s["id"])
    assert row["consecutive_failures"] == 1        # 计入失败计数（含熔断语义）


def test_failed_run_records_error(tmp_db, tmp_path, frozen_now):
    """§8.4 ReadView 失败态：_on_failed 将失败原因落 run.error（ReadView 显示）。"""
    fake = _FakeAgent(tmp_db, tmp_path, fail=True)
    sched = _make_sched(tmp_db, tmp_path, fake)
    s = _make_schedule(tmp_db)
    _run_dispatch(sched, tmp_db, s)
    run = tmp_db.list_schedule_runs(s["id"])[0]
    assert run["status"] == "failed"
    assert run["error"] == "report.md 未产出或为空"


def test_start_stop_lifecycle(tmp_db, tmp_path):
    fake = _FakeAgent(tmp_db, tmp_path)
    sched = _make_sched(tmp_db, tmp_path, fake, recover_gap=0)

    async def scenario():
        await sched.start()
        assert sched._task is not None and not sched._task.done()
        await sched.stop()
        assert sched._task is None                   # stop 后清引用
        await sched.stop()                           # 幂等

    asyncio.run(scenario())


# ── 终态可靠性（review P0-1/P0-2）──────────────────


def test_recover_sweeps_stale_running_runs(tmp_db, tmp_path, frozen_now):
    """P0-1：进程中断残留的 running run 行无启动恢复路径时，R1 全局闸门
    被永久占住（count_running_runs 全局计数）→ 全部订阅永久 deferred。启动
    清扫：running → failed（error 落库供 ReadView 显示）+ 合并一条通知
    （G4 失败可见），非 running 行不动。"""
    fake = _FakeAgent(tmp_db, tmp_path)
    sched = _make_sched(tmp_db, tmp_path, fake, recover_gap=0)
    s = _make_schedule(tmp_db, next_run_at="2026-10-20T09:00:00")  # 未到期，不进补跑
    tmp_db.insert_schedule_run("run_stale", s["id"], None)          # 默认 running
    tmp_db.insert_schedule_run("run_ok", s["id"], None, status="done")

    _run_recover(sched)

    rows = {r["id"]: r["status"] for r in tmp_db.list_schedule_runs(s["id"])}
    assert rows["run_stale"] == "failed"            # 清扫为失败终态
    stale = tmp_db.conn.execute(
        "SELECT error FROM task_schedule_runs WHERE id = 'run_stale'").fetchone()
    assert stale["error"]                            # 中断原因落库（ReadView 可见）
    assert tmp_db.count_running_runs() == 0          # R1 闸门归零
    assert rows["run_ok"] == "done"                  # 终态行不受影响
    notifs = tmp_db.conn.execute(
        "SELECT kind, detail FROM notifications"
        " WHERE entity_type = 'schedule'").fetchall()
    assert len(notifs) == 1 and notifs[0]["kind"] == "interrupted"
    assert "1" in notifs[0]["detail"]


def test_on_finish_crash_still_reaches_terminal(tmp_db, tmp_path, frozen_now,
                                                monkeypatch):
    """P0-2：_on_finish 内部异常（副本 copy 被 Windows 文件锁/杀软占用打断等）
    不得逃逸 _execute——run 行必须到达 failed 终态，R1 闸门不残留。
    红灯形态：异常穿透 _run_dispatch（gather re-raise）。"""
    fake = _FakeAgent(tmp_db, tmp_path)
    sched = _make_sched(tmp_db, tmp_path, fake)
    s = _make_schedule(tmp_db)

    def locked_copy(src, dst):
        raise PermissionError("报告副本被另一个程序占用")

    monkeypatch.setattr(scheduler_mod.shutil, "copy", locked_copy)
    _run_dispatch(sched, tmp_db, s)                  # 异常逃逸则此行抛
    run = tmp_db.list_schedule_runs(s["id"])[0]
    assert run["status"] == "failed"
    assert "占用" in (run["error"] or "")
    assert tmp_db.count_running_runs() == 0


def test_tick_item_failure_does_not_starve_rest(tmp_db, tmp_path, frozen_now,
                                                monkeypatch):
    """P2-2：单个订阅 dispatch 抛错（如历史坏数据使 compute_next_run 炸）
    不得中断本轮 tick——否则排在其后的订阅全部饿死且无任何报错。"""
    fake = _FakeAgent(tmp_db, tmp_path)
    sched = _make_sched(tmp_db, tmp_path, fake)
    s1 = _make_schedule(tmp_db, name="坏订阅",
                        next_run_at="2026-10-19T09:00:00")
    s2 = _make_schedule(tmp_db, name="好订阅",
                        next_run_at="2026-10-19T09:00:00")
    called: list[str] = []

    async def boom(s, **kw):
        called.append(s["id"])
        if s["id"] == s1["id"]:
            raise ValueError("坏 interval")
        return None

    monkeypatch.setattr(sched, "_dispatch", boom)

    asyncio.run(sched._tick())

    assert called == [s1["id"], s2["id"]]            # 坏项拋后后续仍被派发


# ── run-now 异步受理 + 删除取消在途（review P2-6/P2-4）──────


class _SlowAgent(_FakeAgent):
    """P2-4 用：run 挂起模拟 LLM 长执行（cancel 注入点 = await 处）。"""

    async def run(self, task_id: str, user_message: str):
        self.prompts.append(user_message)
        self._running_task_ids.add(task_id)
        try:
            await asyncio.sleep(30)
            yield {"type": "done", "data": {}}
        finally:
            self._running_task_ids.discard(task_id)


def test_dispatch_now_accepts_without_waiting(tmp_db, tmp_path, frozen_now):
    """P2-6：run-now 异步受理——受理即返回 {ok: True}（不等待本期完成，
    同步等待最长 600s 会击穿 HTTP/IPC 超时且前端全程无反馈）；run 行
    running 在途；drain 后到达 done（终态经通知中心反馈）。"""
    fake = _FakeAgent(tmp_db, tmp_path)
    sched = _make_sched(tmp_db, tmp_path, fake)
    s = _make_schedule(tmp_db)

    async def scenario():
        res = await sched.dispatch_now(tmp_db.get_schedule(s["id"]))
        assert res == {"ok": True}
        runs = tmp_db.list_schedule_runs(s["id"])
        assert [r["status"] for r in runs] == ["running"]   # 受理即在途
        await _drain(sched)
        runs = tmp_db.list_schedule_runs(s["id"])
        assert [r["status"] for r in runs] == ["done"]

    asyncio.run(scenario())


def test_cancel_running_kills_inflight(tmp_db, tmp_path, frozen_now):
    """P2-4：取消在途执行——run 行就地置 failed（error 落库，端点随后
    级联删行；独立调用亦不留 running 残留）+ agent 闸门释放 + 后台任务
    收尾；CancelledError 穿透终态保护（_on_finish 不再写孤儿）。"""
    fake = _SlowAgent(tmp_db, tmp_path)
    sched = _make_sched(tmp_db, tmp_path, fake)
    s = _make_schedule(tmp_db)

    async def scenario():
        res = await sched.dispatch_now(tmp_db.get_schedule(s["id"]))
        assert res == {"ok": True}
        run = tmp_db.list_schedule_runs(s["id"])[0]
        assert await sched.cancel_running(s["id"]) == [run["id"]]

    asyncio.run(scenario())
    run = tmp_db.list_schedule_runs(s["id"])[0]
    assert run["status"] == "failed"
    assert "取消" in (run["error"] or "")
    assert fake._running_task_ids == set()           # agent 闸门已释放
    assert sched._bg == set()
    assert tmp_db.count_running_runs() == 0          # R1 闸门无残留
