"""python/tests/test_schedule_rollup.py — 2.11 §12.3 V2.1-T3: 组合周报执行链。

覆盖：聚合素材窗口（rollup 上次 done finished_at → now / 无历史近 7 天）、
digest_schedule_ids 圈定（NULL=全部 enabled=1 非 rollup / 勾选即用户意志
含暂停订阅）、防递归聚合（rollup 期次恒不入聚合源，不变量①）、空窗口
skipped 留痕不静默（G4，error 落库 + 周期照常推进）、_execute 骨架
{sections} 替换（不走增量注入）、_on_finish 同链终态（副本/摘要/通知）
+ 档案合并短路（不落 archive 工作副本）、API 汇总范围引用校验
（不存在 id / 含 rollup id → 400）。

async 逻辑用 asyncio.run 驱动（项目惯例）；时间经 monkeypatch
scheduler._now_dt 注入，断言完全确定。
"""
import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import scheduler as scheduler_mod
from db.database import Database
from scheduler import (
    OUTPUT_CONTRACT,
    ROLLUP_SECTION_MARK,
    ScheduleScheduler,
    compute_next_run,
)

_NOW = datetime(2026, 10, 24, 10, 0, 0)   # 周六


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
    """最小 agent 替身（test_scheduler 同款）：run 在 workspace 写 report.md。"""

    def __init__(self, db, tmp_path):
        self.db = db
        self.tmp_path = tmp_path
        self.prompts: list[str] = []
        self._running_task_ids: set[str] = set()

    def can_start_task(self) -> bool:
        return True

    def try_acquire_task(self, task_id: str) -> bool:
        if task_id in self._running_task_ids:
            return False
        self._running_task_ids.add(task_id)
        return True

    async def run(self, task_id: str, user_message: str):
        self.prompts.append(user_message)
        self._running_task_ids.add(task_id)
        try:
            task = self.db.get_task(task_id)
            report = Path(task["workspace_path"]) / "report.md"
            report.write_text(
                "## 摘要\n- 汇总要点一\n## 正文\n跨订阅汇总内容\n"
                "## 来源\n1. 测试源 · 2026-10-24 · http://x\n",
                encoding="utf-8")
            yield {"type": "done", "data": {}}
        finally:
            self._running_task_ids.discard(task_id)


def _make_sched(db, tmp_path, agent):
    return ScheduleScheduler(db, str(tmp_path), agent=agent)


async def _drain(sched):
    while sched._bg:
        await asyncio.gather(*list(sched._bg))


def _run_dispatch(sched, db, s):
    async def scenario():
        await sched._dispatch(db.get_schedule(s["id"]))
        await _drain(sched)
    asyncio.run(scenario())


def _make_rollup(db, **kw):
    """rollup 订阅（prompt 存库形态模拟 API 落库：骨架 + OUTPUT_CONTRACT）。"""
    args = dict(name="组合周报",
                prompt=(f"汇总各订阅期次：\n{ROLLUP_SECTION_MARK}\n"
                        f"要求：突出风险\n\n{OUTPUT_CONTRACT}"),
                mode="general", category="general", interval_type="weekly",
                interval_value="SAT 09:00",
                next_run_at="2026-10-24T09:00:00", scope="rollup")
    args.update(kw)
    return db.create_schedule(**args)


def _make_source(db, name, **kw):
    args = dict(name=name, prompt="扫描并输出报告", mode="general",
                category="corporate", interval_type="daily",
                interval_value="09:00", next_run_at="2026-10-25T09:00:00")
    args.update(kw)
    return db.create_schedule(**args)


def _add_done_run(db, schedule_id, run_id, finished_at, summary,
                  report_path=None):
    """造一条 done 期次（finished_at 显式注入使窗口断言确定；update 会写
    真实时钟 finished_at，随后 SQL 覆盖为固定值）。"""
    db.insert_schedule_run(run_id, schedule_id, None, status="running")
    db.update_schedule_run(run_id, "done", summary=summary,
                           report_path=report_path)
    db.conn.execute("UPDATE task_schedule_runs SET finished_at = ?"
                    " WHERE id = ?", (finished_at, run_id))
    db.conn.commit()


# ── 空窗口：skipped 留痕不静默（G4）──────────────────────────


def test_rollup_empty_window_records_skipped(tmp_db, tmp_path, frozen_now):
    agent = _FakeAgent(tmp_db, tmp_path)
    sched = _make_sched(tmp_db, tmp_path, agent)
    r = _make_rollup(tmp_db)
    _run_dispatch(sched, tmp_db, r)
    runs = tmp_db.list_schedule_runs(r["id"])
    assert len(runs) == 1 and runs[0]["status"] == "skipped"
    assert runs[0]["error"] == "窗口内无期次"          # ReadView 留痕可读
    assert runs[0]["task_id"] is None                  # 不建影子任务
    assert agent.prompts == []                         # 不执行
    row = tmp_db.get_schedule(r["id"])
    # 周期照常推进（weekly SAT 09:00，now=周六 10:00 → 下周六）
    assert row["next_run_at"] == "2026-10-31T09:00:00"


# ── 聚合与骨架替换（§12.3 prompt 组装）───────────────────────


def test_rollup_aggregates_summaries_into_prompt(tmp_db, tmp_path, frozen_now):
    agent = _FakeAgent(tmp_db, tmp_path)
    sched = _make_sched(tmp_db, tmp_path, agent)
    a = _make_source(tmp_db, "华润舆情")
    b = _make_source(tmp_db, "光伏产业", category="industry")
    _add_done_run(tmp_db, a["id"], "run_a",
                  "2026-10-23T09:00:00", ["要点A1", "要点A2"])
    _add_done_run(tmp_db, b["id"], "run_b",
                  "2026-10-22T09:00:00", ["要点B1"])
    r = _make_rollup(tmp_db)
    _run_dispatch(sched, tmp_db, r)
    assert len(agent.prompts) == 1
    prompt = agent.prompts[0]
    # 时间基准行在首部（仅执行时刻，无「仅报新增」增量语义）
    assert prompt.startswith("【本期执行：2026-10-24 10:00】")
    # 素材块：### 订阅名 · M/D + 摘要要点（升序）
    assert "### 光伏产业 · 10/22\n- 要点B1" in prompt
    assert "### 华润舆情 · 10/23\n- 要点A1\n- 要点A2" in prompt
    # 骨架文字保留 + OUTPUT_CONTRACT 尾巴保留（存最终 prompt 契约）
    assert "要求：突出风险" in prompt
    assert prompt.rstrip().endswith(OUTPUT_CONTRACT.rstrip())
    # 影子任务 + run 行 done + 副本/摘要（_on_finish 同链）
    run = tmp_db.list_schedule_runs(r["id"])[0]
    assert run["status"] == "done"
    assert run["summary"] == ["汇总要点一"]
    assert Path(run["report_path"]).is_file()
    task = tmp_db.get_task(run["task_id"])
    assert task["schedule_id"] == r["id"] and task["mode"] == "general"


def test_rollup_window_from_last_done(tmp_db, tmp_path, frozen_now):
    agent = _FakeAgent(tmp_db, tmp_path)
    sched = _make_sched(tmp_db, tmp_path, agent)
    a = _make_source(tmp_db, "华润舆情")
    _add_done_run(tmp_db, a["id"], "run_old",
                  "2026-10-16T09:00:00", ["窗口外要点"])
    _add_done_run(tmp_db, a["id"], "run_new",
                  "2026-10-20T09:00:00", ["窗口内要点"])
    r = _make_rollup(tmp_db)
    # rollup 上一期 done 于 10/17 → 窗口起点
    _add_done_run(tmp_db, r["id"], "run_prev",
                  "2026-10-17T09:00:00", ["上期汇总"])
    _run_dispatch(sched, tmp_db, r)
    prompt = agent.prompts[0]
    assert "窗口内要点" in prompt
    assert "窗口外要点" not in prompt
    assert "上期汇总" not in prompt                    # 自身上期非素材


def test_rollup_window_default_7d(tmp_db, tmp_path, frozen_now):
    agent = _FakeAgent(tmp_db, tmp_path)
    sched = _make_sched(tmp_db, tmp_path, agent)
    a = _make_source(tmp_db, "华润舆情")
    _add_done_run(tmp_db, a["id"], "run_old",
                  "2026-10-16T09:00:00", ["8天前要点"])   # 早于近 7 天窗口
    _add_done_run(tmp_db, a["id"], "run_new",
                  "2026-10-19T09:00:00", ["5天前要点"])
    r = _make_rollup(tmp_db)                            # 无 done 历史
    _run_dispatch(sched, tmp_db, r)
    prompt = agent.prompts[0]
    assert "5天前要点" in prompt
    assert "8天前要点" not in prompt


# ── 圈定与防递归（§12.3 不变量①）─────────────────────────────


def test_rollup_null_ids_excludes_disabled_and_rollup(tmp_db, tmp_path,
                                                      frozen_now):
    agent = _FakeAgent(tmp_db, tmp_path)
    sched = _make_sched(tmp_db, tmp_path, agent)
    a = _make_source(tmp_db, "活跃订阅")
    b = _make_source(tmp_db, "暂停订阅")
    tmp_db.update_schedule(b["id"], {"enabled": 0})
    c = _make_rollup(tmp_db, name="另一个周报")
    _add_done_run(tmp_db, a["id"], "run_a",
                  "2026-10-23T09:00:00", ["活跃要点"])
    _add_done_run(tmp_db, b["id"], "run_b",
                  "2026-10-23T09:00:00", ["暂停要点"])
    _add_done_run(tmp_db, c["id"], "run_c",
                  "2026-10-23T09:00:00", ["周报要点"])
    r = _make_rollup(tmp_db)                            # digest NULL = 全量
    _run_dispatch(sched, tmp_db, r)
    prompt = agent.prompts[0]
    assert "活跃要点" in prompt
    assert "暂停要点" not in prompt                     # enabled=0 不入全量
    assert "周报要点" not in prompt                     # rollup 期次恒不入源


def test_rollup_digest_ids_explicit_scope(tmp_db, tmp_path, frozen_now):
    agent = _FakeAgent(tmp_db, tmp_path)
    sched = _make_sched(tmp_db, tmp_path, agent)
    a = _make_source(tmp_db, "华润舆情")
    b = _make_source(tmp_db, "暂停订阅")
    tmp_db.update_schedule(b["id"], {"enabled": 0})
    c = _make_source(tmp_db, "未勾选订阅")
    _add_done_run(tmp_db, a["id"], "run_a",
                  "2026-10-23T09:00:00", ["要点A"])
    _add_done_run(tmp_db, b["id"], "run_b",
                  "2026-10-23T09:00:00", ["要点B"])
    _add_done_run(tmp_db, c["id"], "run_c",
                  "2026-10-23T09:00:00", ["要点C"])
    # 勾选即用户意志：含已暂停订阅的窗口内期次（期次已产出仍可汇总）
    r = _make_rollup(
        tmp_db, digest_schedule_ids=f'["{a["id"]}","{b["id"]}"]')
    _run_dispatch(sched, tmp_db, r)
    prompt = agent.prompts[0]
    assert "要点A" in prompt and "要点B" in prompt
    assert "要点C" not in prompt                        # 未勾选不入


def test_rollup_run_not_source_for_other_rollup(tmp_db, tmp_path, frozen_now):
    """端到端防递归：rollup B 产出后，rollup A 的聚合源不含 B 的期次。"""
    agent = _FakeAgent(tmp_db, tmp_path)
    sched = _make_sched(tmp_db, tmp_path, agent)
    src = _make_source(tmp_db, "华润舆情")
    _add_done_run(tmp_db, src["id"], "run_src",
                  "2026-10-23T09:00:00", ["源要点"])
    rb = _make_rollup(tmp_db, name="周报B")
    _run_dispatch(sched, tmp_db, rb)                    # B 先产出
    assert tmp_db.list_schedule_runs(rb["id"])[0]["status"] == "done"
    ra = _make_rollup(tmp_db, name="周报A")
    _run_dispatch(sched, tmp_db, ra)
    prompt = agent.prompts[-1]
    assert "源要点" in prompt
    assert "周报B" not in prompt                        # B 的期次不入 A


# ── 执行链差异：无增量注入 + 档案合并短路 ────────────────────


def test_rollup_no_prev_report_injection(tmp_db, tmp_path, frozen_now):
    agent = _FakeAgent(tmp_db, tmp_path)
    sched = _make_sched(tmp_db, tmp_path, agent)
    a = _make_source(tmp_db, "华润舆情")
    _add_done_run(tmp_db, a["id"], "run_a",
                  "2026-10-23T09:00:00", ["要点A"])
    r = _make_rollup(tmp_db)
    # rollup 自身上一期 done（带报告副本）——汇总型不注入上一期报告
    prev_copy = Path(tmp_path) / "schedules" / "run_prev.md"
    prev_copy.parent.mkdir(parents=True, exist_ok=True)
    prev_copy.write_text("## 摘要\n- 上期汇总\n", encoding="utf-8")
    _add_done_run(tmp_db, r["id"], "run_prev", "2026-10-17T09:00:00", [],
                  report_path=str(prev_copy))
    _run_dispatch(sched, tmp_db, r)
    prompt = agent.prompts[0]
    assert "仅报新增" not in prompt
    assert "上期：" not in prompt
    run = tmp_db.list_schedule_runs(r["id"])[0]
    task = tmp_db.get_task(run["task_id"])
    assert not (Path(task["workspace_path"]) / "上一期报告.md").exists()


def test_rollup_on_finish_skips_archive_merge(tmp_db, tmp_path, frozen_now):
    """沉淀裁决（§12.3）：rollup done 不触发档案合并——archive 工作副本
    不落盘（防孤儿文件），终态其余链路（副本/摘要/通知）与普通 run 同构。"""
    agent = _FakeAgent(tmp_db, tmp_path)
    sched = _make_sched(tmp_db, tmp_path, agent)
    a = _make_source(tmp_db, "华润舆情")
    _add_done_run(tmp_db, a["id"], "run_a",
                  "2026-10-23T09:00:00", ["要点A"])
    r = _make_rollup(tmp_db)
    _run_dispatch(sched, tmp_db, r)
    assert not (Path(tmp_path) / "schedules"
                / f"archive_{r['id']}.md").exists()
    run = tmp_db.list_schedule_runs(r["id"])[0]
    assert run["status"] == "done"
    notif = tmp_db.conn.execute(
        "SELECT kind FROM notifications WHERE entity_id = ?",
        (run["id"],)).fetchone()
    assert notif is not None and notif["kind"] == "completed"


# ── API 汇总范围引用校验（不变量①纵深防御）──────────────────


@pytest.fixture()
def client_and_db(monkeypatch):
    import deps
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import schedules as schedules_router
    with tempfile.TemporaryDirectory() as td:
        db = Database(os.path.join(td, "t.db"))
        db.initialize()
        monkeypatch.setattr(deps, "db", db)
        app = FastAPI()
        app.include_router(schedules_router.router)
        yield TestClient(app), db
        db.conn.close()


_ROLLUP_BODY = {
    "name": "组合周报",
    "prompt": "汇总本期要点\n{sections}",
    "intervalType": "weekly", "intervalValue": "MON 09:00",
    "scope": "rollup",
}


def test_api_digest_ids_target_not_found(client_and_db):
    client, _db = client_and_db
    r = client.post("/schedules", json={
        **_ROLLUP_BODY, "digestScheduleIds": ["ghost"]})
    assert r.status_code == 400
    assert r.json()["code"] == "DIGEST_TARGET_NOT_FOUND"


def test_api_digest_ids_reject_rollup_source(client_and_db):
    client, db = client_and_db
    r1 = client.post("/schedules", json=dict(_ROLLUP_BODY, name="周报甲"))
    assert r1.status_code == 200
    rollup_id = r1.json()["schedule"]["id"]
    r2 = client.post("/schedules", json={
        **_ROLLUP_BODY, "name": "周报乙", "digestScheduleIds": [rollup_id]})
    assert r2.status_code == 400
    assert r2.json()["code"] == "ROLLUP_DIGEST_RECURSIVE"


def test_api_update_digest_ids_validate(client_and_db):
    client, db = client_and_db
    s = db.create_schedule("w", "骨架 {sections}", "general", "general",
                           "weekly", "MON 09:00", scope="rollup")
    r1 = client.patch(f"/schedules/{s['id']}",
                      json={"digestScheduleIds": ["ghost"]})
    assert r1.status_code == 400
    assert r1.json()["code"] == "DIGEST_TARGET_NOT_FOUND"
    other = db.create_schedule("w2", "骨架 {sections}", "general", "general",
                               "weekly", "MON 09:00", scope="rollup")
    r2 = client.patch(f"/schedules/{s['id']}",
                      json={"digestScheduleIds": [other["id"]]})
    assert r2.status_code == 400
    assert r2.json()["code"] == "ROLLUP_DIGEST_RECURSIVE"


# ── db 层：空圈选集防御 ──────────────────────────────────────


def test_list_digest_source_runs_empty_ids(tmp_db):
    a = _make_source(tmp_db, "华润舆情")
    _add_done_run(tmp_db, a["id"], "run_a",
                  "2026-10-23T09:00:00", ["要点A"])
    # 空圈选集 = 显式聚合零订阅 → 无素材（同时规避 IN () 语法错）
    assert tmp_db.list_digest_source_runs([], "2000-01-01T00:00:00") == []


# ── CodeReview 修复回归（2026-10-24）────────────────────────


def test_detach_rollup_digest_references(tmp_db):
    """M-4：删订阅联动剔除 rollup 圈定引用（悬空 id → 编辑保存 400 死锁）。"""
    a = _make_source(tmp_db, "华润舆情")
    b = _make_source(tmp_db, "光伏产业")
    r1 = _make_rollup(tmp_db, name="周报甲")
    tmp_db.update_schedule(r1["id"], {"digest_schedule_ids": [a["id"], b["id"]]})
    r2 = _make_rollup(tmp_db, name="周报乙")
    tmp_db.update_schedule(r2["id"], {"digest_schedule_ids": [a["id"]]})

    touched = tmp_db.detach_rollup_digest_references(a["id"])

    assert touched == 2
    ids1 = tmp_db.get_schedule(r1["id"])["digest_schedule_ids"]
    ids2 = tmp_db.get_schedule(r2["id"])["digest_schedule_ids"]
    assert ids1 == [b["id"]]
    assert ids2 == []                      # 剔空保留 [] 不转 NULL（NULL=全量突变）


def test_detach_rollup_digest_references_tolerates_bad_json(tmp_db):
    """坏 JSON 行跳过不炸（存量数据健壮性）。"""
    r = _make_rollup(tmp_db)
    tmp_db.conn.execute(
        "UPDATE task_schedules SET digest_schedule_ids = '{broken' WHERE id = ?",
        (r["id"],))
    tmp_db.conn.commit()
    assert tmp_db.detach_rollup_digest_references("ghost") == 0


def test_api_delete_source_detaches_rollup_refs(client_and_db):
    """M-4 端到端：DELETE 源订阅 → rollup 的 digest_schedule_ids 同步剔除。"""
    import json as _json
    client, db = client_and_db
    src = db.create_schedule("源", "扫描", "general", "corporate",
                             "daily", "09:00")
    rollup = db.create_schedule("周报", "骨架 {sections}", "general",
                                "general", "weekly", "MON 09:00",
                                scope="rollup",
                                digest_schedule_ids=_json.dumps([src["id"]]))
    r = client.delete(f"/schedules/{src['id']}")
    assert r.status_code == 200
    assert db.get_schedule(rollup["id"])["digest_schedule_ids"] == []


def test_list_digest_source_runs_max_runs_keeps_latest(tmp_db):
    """m-2：max_runs 截断保留窗口内最近 N 期且返回仍升序（素材时间序）。"""
    a = _make_source(tmp_db, "华润舆情")
    for rid, ts in [("run_1", "2026-10-20T09:00:00"),
                    ("run_2", "2026-10-21T09:00:00"),
                    ("run_3", "2026-10-22T09:00:00")]:
        _add_done_run(tmp_db, a["id"], rid, ts, [f"要点{rid}"])
    rows = tmp_db.list_digest_source_runs(
        [a["id"]], "2000-01-01T00:00:00", max_runs=2)
    assert [r["finished_at"] for r in rows] == [
        "2026-10-21T09:00:00", "2026-10-22T09:00:00"]
    assert [r["summary"] for r in rows] == [["要点run_2"], ["要点run_3"]]


def test_list_digest_source_runs_max_runs_under_limit_keeps_all(tmp_db):
    """第二轮 CodeReview B-1：max_runs > 窗口期数 → 全量返回（负起始切片曾
    静默丢头部；scheduler 传探测值 51，窗口 26–50 期为必经路径）。"""
    a = _make_source(tmp_db, "华润舆情")
    base = datetime(2026, 10, 1, 9, 0, 0)
    for i in range(30):
        _add_done_run(tmp_db, a["id"], f"run_{i:02d}",
                      (base + timedelta(days=i)).isoformat(), [f"要点{i}"])
    rows = tmp_db.list_digest_source_runs(
        [a["id"]], "2000-01-01T00:00:00", max_runs=51)
    assert len(rows) == 30                       # 不丢任何一期
    assert rows[0]["finished_at"].startswith("2026-10-01")   # 最早一期仍在且升序


def test_rollup_prompt_truncation_annotated(tmp_db, tmp_path, frozen_now):
    """m-2 端到端：素材超上限取最近 N 期且素材尾留标注（禁止静默截断）。"""
    from scheduler import ROLLUP_MAX_SOURCE_RUNS
    agent = _FakeAgent(tmp_db, tmp_path)
    sched = _make_sched(tmp_db, tmp_path, agent)
    a = _make_source(tmp_db, "华润舆情")
    n = ROLLUP_MAX_SOURCE_RUNS + 2
    # 窗口内铺满：now=10-24 10:00，窗口起点=10-17 10:00；2h 间隔 52 期落在
    # 10-17 12:00 ~ 10-21 18:00（全部在窗内且不超过 now）
    base = datetime(2026, 10, 17, 12, 0, 0)
    for i in range(n):
        _add_done_run(tmp_db, a["id"], f"run_{i:03d}",
                      (base + timedelta(hours=2 * i)).isoformat(), [f"要点{i}"])
    r = _make_rollup(tmp_db)
    prompt = sched._build_rollup_prompt(tmp_db.get_schedule(r["id"]), _NOW)
    assert prompt is not None
    # 精确行断言：避免「要点0」⊂「要点10」子串误匹配
    assert "- 要点0\n" not in prompt         # 窗口最早两期被截去
    assert "- 要点1\n" not in prompt
    assert f"- 要点{n - 1}\n" in prompt      # 最新期保留
    assert f"仅聚合最近 {ROLLUP_MAX_SOURCE_RUNS} 期" in prompt
