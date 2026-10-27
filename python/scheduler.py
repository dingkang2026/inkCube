"""python/scheduler.py — 2.11 §5 定时订阅调度器（E3 T2）。

主进程内 asyncio 循环（§5.1）：60s tick 扫描 enabled=1 且到期的订阅，串行派发。
触发 = 创建影子任务（tasks.schedule_id 落列，D2 服务端排除）后复用 agent 既有
执行链（§5.3，SSE 无前台消费仅落库），终态回调回填 run 行 / 解析摘要 / 推进周期。

资源约束（§5.6）：R2 忙时让路（人工任务或模型下载进行中，周期不动，空闲即跑）；
R1 定时全局单并发；闸门满延迟不推进（防跳过）；完成通知默认静默、失败/熔断
必弹（R4，Toast 分级由前端按 kind 处理）。失败熔断（§5.5）：连续 3 次失败自动
暂停订阅。错过补偿（§5.4）：启动扫描，≤24h 补跑（串行间隔 30s），>24h 记
missed + 推进 + 同批合并一条通知。

增量注入（§6）：上期 done 报告副本复制入本期 workspace（上一期报告.md）+
prompt 首部增量指引，使「每期只写新增」成为可能（档案时间线不重复堆叠）。
"""
import asyncio
import base64
import calendar
import logging
import os
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import activity_events
from knowledge.file_cleanup import cleanup_file_data
from knowledge.fts_store import FTSStore
from knowledge.vector_store import VectorStore
from llm.client import LLMClient
from llm.routing import resolve_model

logger = logging.getLogger(__name__)

TICK_SECONDS = 60            # §5.1 tick 间隔（天然覆盖休眠唤醒，无特殊处理）
RECOVER_GAP_SECONDS = 30     # §5.4 补跑串行间隔（防开机并发轰炸）
CATCHUP_MAX_HOURS = 24       # §5.4 ≤24h 补跑，>24h 记 missed
BREAKER_THRESHOLD = 3        # §5.5 连续失败熔断

_WEEKDAYS = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6}

# §6 review P2-9：时间线节标题 = `### YYYY-MM-DD`（报告正文风格的 `### ` 子标题
# 不得截断日期节，否则窗口区间错位、期数失真）
_SECTION_RE = re.compile(r"^### \d{4}-\d{2}-\d{2}")

# §8.3 产出结构约定（D4）逐字常量：订阅创建时拼接进 prompt 存库（routers/
# schedules.py），执行侧不再重复拼接——「存最终 prompt」原则，两处共享本常量
OUTPUT_CONTRACT = (
    "输出为工作区根目录单个报告文件 report.md，结构：\n"
    "## 摘要\n"
    "- 3~5 条要点，每条一行（含定性：正面/负面/中性）\n"
    "## 正文\n"
    "（分节详述，数据标注来源与日期）\n"
    "## 来源\n"
    "1. {来源名} · {日期} · {URL}"
)

# V2.1-T3 聚合简报（2.11 §12）：汇总骨架素材插入点，routers/schedules.py
# 同名常量自本模块 import（与 OUTPUT_CONTRACT 同款两处共享惯例）
ROLLUP_SECTION_MARK = "{sections}"

ROLLUP_WINDOW_DAYS = 7      # §12.3 无 done 历史时的聚合窗口（近 7 天）

# §12.3 素材期数上限（CodeReview m-2 防无界 prompt：源订阅多/期次密集时
# token/费用风险）。超出取最近 N 期并在素材尾留标注（禁止静默截断）
ROLLUP_MAX_SOURCE_RUNS = 50

ARCHIVE_WINDOW = 30         # §6 进入 LLM 的近期节数上限（早期节不进 LLM，单次成本恒定）
ARCHIVE_MERGE_BATCH = 10    # §6 review P2-5：tick 路径单批合并上限（多期分周期补完，
                            # 防 long-fail 后 pending 无界增长击穿模型上下文自锁）
KEEP_RUNS = 10              # §6 workspace 保留期：每订阅保留最近 N 期终态
                            # run（done/failed，review P2-8）的目录

# §6 档案合并 system prompt：LLM 只输出时间线窗口（头部/早期节由机械替换保障，
# 「机械替换窗口区间」原则——LLM 输出永不越过窗口边界）
ARCHIVE_SYS_PROMPT = (
    "你是情报档案维护员。输入 = 档案头部与近期时间线节 + 一至多期新报告全文。"
    "请：①把新动态并入时间线，每期一个 `### YYYY-MM-DD` 节（日期沿用报告期）；"
    "②与近期节重复的表述精简合并，状态变化（进展/反转）修正到对应节；"
    "③不改动窗口以外的节；"
    "④只输出 `## 时间线` 及其窗口内全部节（修订后），无其他文字。"
)


def _now_dt() -> datetime:
    """当前时间（测试经 monkeypatch 注入固定时钟，断言确定）。"""
    return datetime.now()


def compute_next_run(interval_type: str, interval_value: str,
                     from_dt: datetime | None = None) -> str:
    """按周期计算下一次运行时间（ISO 无时区字符串，严格晚于基准时刻）。

    daily "09:00"；weekly "MON,THU 09:00"（ISO 星期映射）；
    monthly "1,15 09:00"（日超当月天数取当月最后一天）。
    """
    now = from_dt or _now_dt()
    if " " in interval_value:
        days_part, time_part = interval_value.rsplit(" ", 1)
        days = [d.strip().upper() for d in days_part.split(",") if d.strip()]
    else:
        days, time_part = [], interval_value.strip()
    hh, mm = (int(x) for x in time_part.split(":"))
    base = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if interval_type == "daily":
        return (base if base > now else base + timedelta(days=1)).isoformat()
    if interval_type == "weekly":
        targets = {_WEEKDAYS[d] for d in days}
        for offset in range(8):                  # 7 天内必有匹配
            cand = base + timedelta(days=offset)
            if cand.weekday() in targets and cand > now:
                return cand.isoformat()
        return (base + timedelta(days=7)).isoformat()
    if interval_type == "monthly":
        for offset in range(3):                  # 两个月内必有匹配（跨月截断）
            idx = now.year * 12 + (now.month - 1) + offset
            year, month = idx // 12, idx % 12 + 1
            last_day = calendar.monthrange(year, month)[1]
            for day in sorted(int(d) for d in days):
                cand = datetime(year, month, min(day, last_day), hh, mm)
                if cand > now:
                    return cand.isoformat()
    raise ValueError(f"unsupported interval: {interval_type} {interval_value!r}")


def _split_sections(tail: str) -> list[str]:
    """§6 时间线切节：「## 时间线」之后仅认 `### YYYY-MM-DD` 日期标题切块
    （review P2-9：`### ` 子标题留在所属日期节内）；前导非节内容（LLM 输出
    可能残余的寒暄/头部）丢弃，节内换行原样保留。"""
    sections: list[str] = []
    cur: list[str] | None = None
    for line in tail.splitlines(keepends=True):
        if _SECTION_RE.match(line):
            if cur is not None:
                sections.append("".join(cur))
            cur = [line]
        elif cur is not None:
            cur.append(line)
    if cur is not None:
        sections.append("".join(cur))
    return sections


def _archive_header(s: dict, sections: int) -> str:
    """§6 档案头部（本地机械生成，不进 LLM）：标题 + 元信息 + 期数/更新日期。"""
    return (f"# {s['name']}·情报档案\n\n"
            f"- 订阅：{s['name']}\n"
            f"- 周期：{s['interval_type']} {s['interval_value']}\n"
            f"- 期数：{sections}\n"
            f"- 更新：{_now_dt().strftime('%Y-%m-%d')}\n\n")


def _parse_summary(text: str) -> list[str]:
    """§8.3 摘要解析：首个「## 摘要」节下的列表项；无该节 → 空数组（不阻塞阅读）。"""
    items: list[str] = []
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "## 摘要":
            in_section = True
            continue
        if in_section:
            if stripped.startswith("## "):
                break
            if stripped.startswith("- "):
                items.append(stripped[2:].strip())
    return items


class ScheduleScheduler:
    """定时订阅调度器（依赖显式注入：db / data_dir / task_agent）。"""

    def __init__(self, db, data_dir: str, agent,
                 recover_gap: float = RECOVER_GAP_SECONDS,
                 worker=None):
        self.db = db
        self.data_dir = data_dir
        self.agent = agent
        self.recover_gap = recover_gap           # 测试注入 0 免等待
        self.worker = worker                     # §6 档案入库链（_upload_core 入队解析）
        self._task: asyncio.Task | None = None
        self._bg: set[asyncio.Task] = set()      # 在途 _execute（测试 drain 用）
        # review P2-4：run_id → (schedule_id, task)，删除订阅时取消在途定位用
        self._exec_bg: dict[str, tuple[str, asyncio.Task]] = {}
        # 档案互斥（review P1-1）：merge（tick 路径）与 rebuild/retarget 两路
        # 都在 LLM await 处交出事件循环，无锁则后写覆盖先写（期次节丢失且
        # 锚点已推进永不补并）
        self._archive_lock = asyncio.Lock()
        # R3-P1/R4-P1：换向重建后台任务册 schedule_id → tasks（受理即返回，
        # 测试 drain 用；retarget/delete 前置按订阅取消在途——R4-P1/P2-1）
        self._archive_bg: dict[str, set[asyncio.Task]] = {}

    # ── 生命周期 ─────────────────────────────────────────────

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        # R2-P1-1：启动补偿整体兑底——单项隔离之外的意外不得杀死 _loop
        # （_task 非 None 时 start() 不重建，loop 死亡 = 全部订阅停摆且重启复现）
        try:
            await self._recover_missed()
        except Exception:
            logger.exception("schedule recover failed on startup")
        while True:
            try:
                await self._tick()
            except Exception:
                logger.exception("schedule tick failed")
            await asyncio.sleep(TICK_SECONDS)

    # ── 错过补偿（§5.4）──────────────────────────────────────

    async def _recover_missed(self) -> None:
        """启动扫描：到期未跑的订阅 ≤24h 补跑（串行间隔 recover_gap）；
        >24h 情报已衰减 → 记 missed + 推进，同批合并一条通知（R4 必弹）。
        先清扫进程中断残留的 running run 行（review P0-1：否则 R1 全局闸门
        被永久占住 → 全部订阅 deferred，跨重启不自愈）。"""
        now = _now_dt()
        swept = self.db.fail_stale_running_runs("进程中断，重启后标记失败")
        if swept:
            logger.warning("swept stale running runs on startup: %s", swept)
            activity_events.notify(
                self.db, entity_type="schedule",
                entity_id=f"sweep_{now.strftime('%Y%m%d%H%M%S')}",
                project_id=None, container="project", kind="interrupted",
                entity_title="定时订阅",
                detail=f"上次退出时 {len(swept)} 期执行被中断，已标记失败")
        missed: list[str] = []
        for s in self.db.list_due_schedules(now.isoformat()):
            # R2-P1-1：单项隔离（与 _tick 同规格）——一条脏数据（next_run_at
            # 非法日期 / 坏 interval 使 compute_next_run 报 KeyError）只跳过
            # 自身，不得饿死同批其余订阅
            try:
                overdue_h = (now - datetime.fromisoformat(s["next_run_at"])
                             ).total_seconds() / 3600
                if overdue_h <= CATCHUP_MAX_HOURS:
                    await self._dispatch(s)
                    if self.recover_gap:
                        await asyncio.sleep(self.recover_gap)
                else:
                    run_id = f"run_{uuid4().hex[:8]}"
                    self.db.insert_schedule_run(run_id, s["id"], None, status="missed")
                    self.db.set_schedule_state(
                        s["id"],
                        next_run_at=compute_next_run(
                            s["interval_type"], s["interval_value"], now),
                        last_run_at=now.isoformat())
                    missed.append(s["name"])
            except Exception:
                logger.exception(
                    "schedule recover skipped: schedule=%s", s["id"])
        if missed:
            activity_events.notify(
                self.db,
                entity_type="schedule",
                entity_id=f"recover_{now.strftime('%Y%m%d%H%M%S')}",
                project_id=None,
                container="project",
                kind="missed",
                entity_title="定时订阅",
                detail=f"{len(missed)} 个订阅因长期未运行已跳过：{'、'.join(missed)}",
            )

    # ── 触发与派发（§5.2）────────────────────────────────────

    async def _tick(self) -> None:
        for s in self.db.list_due_schedules(_now_dt().isoformat()):
            try:
                await self._dispatch(s)
            except Exception:
                # review P2-2：单项异常（如坏 interval 历史数据在 compute_next_run
                # 处炸）不得中断本轮 tick——否则排在其后的订阅全部饿死且无报错
                logger.exception("schedule dispatch failed: schedule=%s", s["id"])

    async def _dispatch(self, s: dict, *, advance: bool = True):
        """派发一次执行。判定顺序：R2 忙让路（周期不动）→ 防重入记 skipped
        （推进）→ R1 定时单并发（下个 tick 重试）→ 全局闸门满（延迟不推进，
        防跳过）→【rollup】聚合素材，空窗口记 skipped 留痕不静默（G4，推进）
        → 建影子任务 + run 行 + 推进 → 后台执行。
        返回在途执行 Task（dispatch_now await 用）；未派发返回 None。
        advance=False（run-now）：不动周期，其余判定不变。"""
        if self._r2_busy():
            return None
        if self.db.schedule_last_run_running(s["id"]):
            run_id = f"run_{uuid4().hex[:8]}"
            self.db.insert_schedule_run(run_id, s["id"], None, status="skipped")
            if advance:
                self._advance(s)
            return None
        if self.db.count_running_runs() >= 1:
            return None
        if not self.agent.can_start_task():
            return None
        now = _now_dt()
        # V2.1-T3 聚合简报（2.11 §12.3）：空窗口判定放在 R1/闸门之后——
        # 让路/延迟期间源订阅可能产出新期次，空窗口结论须在「确定本次执行」
        # 时作出，否则误记 skipped 错过本该有的汇总
        rollup_prompt: str | None = None
        if s.get("scope") == "rollup":
            rollup_prompt = self._build_rollup_prompt(s, now)
            if rollup_prompt is None:
                run_id = f"run_{uuid4().hex[:8]}"
                self.db.insert_schedule_run(run_id, s["id"], None,
                                            status="skipped",
                                            error="窗口内无期次")
                if advance:
                    self._advance(s, now=now)
                return None
        title = f"{s['name']} · {now.month}/{now.day}"
        task = self.db.create_task(title, self.data_dir, mode="general",
                                   schedule_id=s["id"])
        run_id = f"run_{uuid4().hex[:8]}"
        self.db.insert_schedule_run(run_id, s["id"], task["id"])
        if advance:
            self._advance(s, now=now)
        bg = asyncio.create_task(
            self._execute(s, task["id"], run_id, rollup_prompt=rollup_prompt))
        self._bg.add(bg)
        self._exec_bg[run_id] = (s["id"], bg)

        def _done(t: asyncio.Task, _run_id: str = run_id) -> None:
            self._bg.discard(t)
            self._exec_bg.pop(_run_id, None)

        bg.add_done_callback(_done)
        return bg

    async def dispatch_now(self, s: dict) -> dict:
        """手动立即执行一次（§4.1 run-now）：绕过 next_run_at 到期判定，
        仍走 R1/R2/防重入；不动周期。异步受理（review P2-6）：受理即返回
        {ok: True}（执行在后台完成，终态经通知中心 R4 反馈——同步等待最长
        600s 会击穿 HTTP/IPC 超时且前端全程无反馈）；忙时让路返回
        {ok: False, deferred: True}（不建行，用户稍后重试）。"""
        bg = await self._dispatch(s, advance=False)
        if bg is None:
            return {"ok": False, "deferred": True}
        return {"ok": True}

    async def cancel_running(self, schedule_id: str) -> list[str]:
        """取消该订阅的在途执行（review P2-4，删除订阅前调用）：cancel 后台
        _execute——CancelledError 穿透终态保护（_on_finish 不执行，不再产生
        孤儿 run 回填/通知/档案写）；被取消 run 行就地置 failed（error 落库，
        端点随后级联删行；独立调用亦不留 running 残留）。返回被取消的 run id。"""
        cancelled: list[str] = []
        for run_id, (sid, bg) in list(self._exec_bg.items()):
            if sid != schedule_id:
                continue
            bg.cancel()
            try:
                await bg
            except asyncio.CancelledError:
                pass
            self._exec_bg.pop(run_id, None)
            row = self.db.get_run(run_id)
            if row is not None and row["status"] == "running":
                self.db.update_schedule_run(
                    run_id, "failed", error="订阅已删除，执行取消")
            cancelled.append(run_id)
        return cancelled

    def _advance(self, s: dict, now: datetime | None = None) -> None:
        now = now or _now_dt()
        self.db.set_schedule_state(
            s["id"],
            next_run_at=compute_next_run(
                s["interval_type"], s["interval_value"], now),
            last_run_at=now.isoformat())

    def _r2_busy(self) -> bool:
        """R2 忙信号（§5.6）：运行中人工任务（非影子）或模型下载进行中。"""
        for tid in list(self.agent._running_task_ids):
            row = self.db.get_task(tid)
            if row is not None and not row.get("schedule_id"):
                return True
        row = self.db.conn.execute(
            "SELECT 1 FROM model_downloads WHERE status = 'downloading' LIMIT 1"
        ).fetchone()
        return row is not None

    # ── 执行链（§5.3，复用 D2）───────────────────────────────

    def _build_rollup_prompt(self, s: dict, now: datetime) -> str | None:
        """聚合简报素材聚合 + prompt 组装（2.11 §12.3）：窗口 = 本订阅上次
        done finished_at → now（无 done 历史 → 近 ROLLUP_WINDOW_DAYS 天）；
        素材 = 圈定订阅（digest_schedule_ids，NULL=全部 enabled=1 非 rollup）
        的 done 期次摘要；空窗口返回 None（调用方记 skipped 留痕）。
        骨架 {sections} 以 str.replace 替换（不经 str.format，骨架中用户
        自定义花括号安全）。素材期数超 ROLLUP_MAX_SOURCE_RUNS 取最近 N 期
        并在素材尾留标注（禁止静默截断）。"""
        prev = self.db.last_done_run(s["id"])
        if prev and prev["finished_at"]:
            window_start = prev["finished_at"]
        else:
            window_start = (now - timedelta(days=ROLLUP_WINDOW_DAYS)).isoformat()
        # +1 探测截断（has_more 同款）：超出上限保留最近 N 期（升序尾段）
        rows = self.db.list_digest_source_runs(
            s.get("digest_schedule_ids"), window_start,
            max_runs=ROLLUP_MAX_SOURCE_RUNS + 1)
        truncated = len(rows) > ROLLUP_MAX_SOURCE_RUNS
        if truncated:
            rows = rows[-ROLLUP_MAX_SOURCE_RUNS:]   # 升序尾段 = 最近 N 期
        if not rows:
            return None
        lines: list[str] = []
        for r in rows:
            dt = datetime.fromisoformat(r["finished_at"])
            lines.append(f"### {r['schedule_name']} · {dt.month}/{dt.day}")
            lines.extend(f"- {item}" for item in r["summary"])
        if truncated:
            lines.append(
                f"-（窗口内期次较多，仅聚合最近 {ROLLUP_MAX_SOURCE_RUNS} 期）")
        return s["prompt"].replace(ROLLUP_SECTION_MARK, "\n".join(lines))

    async def _execute(self, s: dict, task_id: str, run_id: str,
                       rollup_prompt: str | None = None) -> None:
        error: str | None = None
        try:
            now = _now_dt()
            if rollup_prompt is not None:
                # V2.1-T3 聚合简报（2.11 §12.3）：不走增量注入（上一期 rollup
                # 报告非聚合素材）；时间基准仅保留执行时刻（「仅报新增」是
                # 增量语义，汇总型不适用）
                prompt_full = (f"【本期执行：{now:%Y-%m-%d %H:%M}】\n"
                               f"{rollup_prompt}")
            else:
                # 时间基准（2.11 §5.3，2026-10-23 决议）：LLM 无当前时刻则「仅报新增」
                # 无从判断，历史动态会被误报为本期新情况（档案日期节随之失真）。
                # 执行侧每期拼接不存库（OUTPUT_CONTRACT 是静态契约才存库）。
                prev = self.db.last_done_run(s["id"])
                n_th = self.db.count_schedule_runs(s["id"])
                if prev:
                    time_base = (f"【本期执行：{now:%Y-%m-%d %H:%M} · 第 {n_th} 期"
                                 f" · 上期：{(prev['finished_at'] or '')[:16]}】"
                                 "仅报告上期之后的新动态；确需提及的历史背景必须"
                                 "显式标注原始日期，不得作为本期新情况报告。\n")
                else:
                    time_base = (f"【本期执行：{now:%Y-%m-%d %H:%M} · 第 {n_th} 期】"
                                 "报告本期新动态；确需提及的历史背景必须显式标注"
                                 "原始日期，不得作为本期新情况报告。\n")
                incr_note = ""
                if prev and prev.get("report_path") and os.path.isfile(prev["report_path"]):
                    try:
                        shutil.copy(prev["report_path"], Path(self._workspace_of(task_id))
                                    / "上一期报告.md")
                        incr_note = ("工作区有《上一期报告.md》：重点报告与其相比的"
                                     "新增动态，延续内容简要带过。\n")
                    except Exception:
                        # §6 注入失败仅告警不阻塞本期执行（去重降级为全量重述）
                        logger.warning("inject previous report failed: run=%s",
                                       run_id, exc_info=True)
                # s["prompt"] 存库时已含 OUTPUT_CONTRACT（T4 创建端点拼接），此处仅加前缀
                prompt_full = time_base + incr_note + s["prompt"]
            self.db.save_task_message(task_id, "user", prompt_full)
            if not self.agent.try_acquire_task(task_id):
                # dispatch 检查与执行间的竞态窗口（人工任务抢先占满闸门）：
                # 按失败终态收场，不执行（纵深防御，非主路径）
                self._on_failed(s, run_id, "闸门被占")
                return
            async for _ev in self.agent.run(task_id, prompt_full):
                pass                                  # 事件落库在 agent 内；无前台消费
            ok = self._report_exists(task_id)
            if not ok:
                error = "report.md 未产出或为空"
        except Exception as e:
            ok = False
            error = str(e)
        try:
            await self._on_finish(s, task_id, run_id, ok, error=error)
        except Exception as e:
            # review P0-2：终态回调异常不得逃逸（bg task 无人 retrieve → run
            # 永卡 running，R1 全局闸门被占 → 全部订阅停摆）；行未到终态时按
            # 失败收场（幂等：已 done/failed 不覆写），双层防御防兜底自身抛
            logger.exception("on_finish failed: run=%s", run_id)
            row = self.db.get_run(run_id)
            if row is not None and row["status"] == "running":
                try:
                    self._on_failed(s, run_id, f"终态回调异常（{e}）")
                except Exception:
                    logger.exception("on_finish backstop failed: run=%s", run_id)
        return ok

    async def _on_finish(self, s: dict, task_id: str, run_id: str,
                         ok: bool, error: str | None = None) -> None:
        """终态回调（§5.5）：done → 副本落盘 + 摘要解析 + 失败计数归零 + 静默通知
        + 档案合并（§6，T10 接线；失败内部消化不影响 run 终态）+ workspace
        保留期清理；failed → 计数 +1，≥3 熔断（enabled=0，通知带暂停文案）；
        周期在触发时已推进，此处不再动（失败恢复后从下个周期继续）。"""
        if ok:
            src = Path(self._workspace_of(task_id)) / "report.md"
            dest = Path(self.data_dir) / "schedules" / f"{run_id}.md"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, dest)
            self.db.update_schedule_run(
                run_id, "done", report_path=str(dest),
                summary=_parse_summary(dest.read_text(encoding="utf-8")))
            self.db.set_schedule_state(s["id"], consecutive_failures=0)
            # R4：完成默认静默——通知中心记录 + 速览更新，不弹 Toast
            activity_events.complete_schedule_run(self.db, run_id, s["name"])
            async with self._archive_lock:        # review P1-1：与 rebuild/retarget 互斥
                # §6 档案合并（失败内部消化 + 通知）；review P2-5：每批上限，
                # 多期 pending 分周期补完
                await self._merge_archive(s, limit=ARCHIVE_MERGE_BATCH)
            self._cleanup_workspaces(s)           # §6 保留期清理（静默）
        else:
            self._on_failed(s, run_id, error)

    # ── 情报档案（§6，T10）────────────────────────────────

    async def _merge_archive(self, s: dict, limit: int | None = None) -> None:
        """§6 档案合并（LLM 窗口制四步）：①取锚点后的待合并 done 副本（升序）
        ②LLM 合并：输入 = 头部 + 最近 ARCHIVE_WINDOW 期节 + 报告全文（按序）；
        输出整窗口 ③机械替换窗口区间写回工作副本（早期节不进 LLM）+ 绑定去向
        时清旧重入库 ④回填 archive_last_run_id。失败 → 档案与锚点不动，通知
        单列「产出已生成，档案合并失败」，下期 done 自动补并（步骤①天然带回）。
        limit（rebuild 分批用）：单批最多合并期数；None = 全部待合并。
        返回 True/False（review P2-3）：tick 路径忽略返回值；rebuild 据此
        分批报告真实态（部分批失败不得虚报成功）。"""
        s = self.db.get_schedule(s["id"]) or s
        if s.get("scope") == "rollup":
            # V2.1-T3 沉淀裁决（2.11 §12.3）：rollup 产出不沉淀档案（阅读产物
            # 非档案素材，target 强制 NULL）——短路防 rebuild/retarget 误触发
            # LLM 合并 + 孤儿 archive 工作副本落盘
            return True
        pending = self.db.pending_archive_runs(s["id"])
        if limit is not None:
            pending = pending[:limit]
        if not pending:
            return True
        try:
            archive = self._archive_path(s["id"])
            tail = ""
            if archive.exists():
                _, _, tail = archive.read_text(
                    encoding="utf-8").partition("## 时间线\n")
            old_sections = _split_sections(tail)
            window = old_sections[-ARCHIVE_WINDOW:]
            reports = "".join(
                f"\n## 待并入报告（{r['id']}）\n"
                + Path(r["report_path"]).read_text(encoding="utf-8")
                for r in pending)
            user = (_archive_header(s, 0) + "## 时间线\n"
                    + "".join(window) + reports)
            base = self.db.get_settings()
            # 档位制路由注入（与 routers/schedules 同口径）：settings["model"]
            # 是派生键不落库，漏注入则空串发网关报 1211「模型不存在」——
            # 档位制改造（2026-10-19）唯一漏网消费点，期次首次 done 触发
            # 归档合并才暴露（2026-10-22 实证：sch_ebed51d9）。
            settings = {**base, "model": resolve_model(base)}
            resp = await LLMClient(settings).chat_full([
                {"role": "system", "content": ARCHIVE_SYS_PROMPT},
                {"role": "user", "content": user},
            ])
            merged = _split_sections(resp["content"].partition("## 时间线\n")[2])
            # review P2-9：节数不足窗口 = 丢节（静默落盘会永久丢失期次节且锚点
            # 已推进永不补并）→ 拒绝落盘走失败补并
            if not merged or len(merged) < len(window):
                raise ValueError(
                    f"LLM 输出节缺失（{len(merged)} 节 < 窗口 {len(window)} 节），"
                    "拒绝落盘")
            keep = old_sections[:len(old_sections) - len(window)]
            text = (_archive_header(s, len(keep) + len(merged))
                    + "## 时间线\n" + "".join(keep) + "".join(merged))
            archive.parent.mkdir(parents=True, exist_ok=True)
            archive.write_text(text, encoding="utf-8")
            await self._import_archive(s, text)
            self.db.update_schedule(
                s["id"], {"archive_last_run_id": pending[-1]["id"]})
            return True
        except Exception as e:
            logger.exception("archive merge failed: schedule=%s", s["id"])
            activity_events.notify(
                self.db, entity_type="schedule", entity_id=s["id"],
                project_id=None, container="project", kind="archive_failed",
                entity_title=s["name"],
                detail=f"产出已生成，档案合并失败（{e}）；下期完成时自动补并")
            return False

    async def _import_archive(self, s: dict, text: str) -> None:
        """§6 步骤 3：target 绑定即入库（2026-10-26 auto_import 开关退役，
        「绑定去向=全自动沉淀」，入库决策发生在绑定时刻）→ 旧档清数据
        （cleanup_file_data 全链：向量 + FTS + 磁盘）→ _upload_core 重入库
        （建档链复用，filename={订阅名}·档案.md）→ 回填 archive_file_id；
        未绑定 → 只更新工作副本（no-op）。
        routers.knowledge 延迟 import：其 readiness → deps → 本模块，
        顶层 import 成环。"""
        if not s.get("target_id"):
            return
        from routers.knowledge import FileUpload, _upload_core
        if s.get("archive_file_id"):
            vs = VectorStore(str(Path(self.data_dir) / "chroma"))
            fts = FTSStore(self.db)
            await cleanup_file_data(self.db, vs, fts, s["archive_file_id"],
                                    s["target_id"], self.data_dir,
                                    remove_file_record=True)
        upload = FileUpload(
            projectId=s["target_id"], filename=f"{s['name']}·档案.md",
            fileType=".md",
            data=base64.b64encode(text.encode("utf-8")).decode("ascii"))
        res = await _upload_core(s["target_id"], upload, self.db,
                                 self.data_dir, self.worker)
        if res.get("file_id"):
            self.db.update_schedule(
                s["id"], {"archive_file_id": res["file_id"]})

    async def rebuild_archive(self, schedule_id: str) -> dict:
        """§6 重建档案（绑定/更换去向后的首次构建、档案修复同用）：锚点清零 +
        工作副本删除 → 全部 done 副本按 ARCHIVE_WINDOW 分批循环合并（首批即含
        头部初始化），每批走完整四步（含入库与锚点回填）；批间锚点不再前进
        （无待合并副本或本批失败）即止。与 tick 合并同锁互斥（review P1-1）。"""
        if self.db.get_schedule(schedule_id) is None:
            return {"ok": False, "error": "SCHEDULE_NOT_FOUND"}
        async with self._archive_lock:
            return await self._rebuild_locked(schedule_id)

    async def _rebuild_locked(self, schedule_id: str) -> dict:
        """rebuild 循环（调用方必须已持 _archive_lock）。返回真实态 {ok, batches}
        （review P2-3）：batches = 成功批数；批失败返回 ok=False 且锚点停在最后
        成功批（已完成部分不回滚——下期合并/再次重建可续），前端分级提示。"""
        self.db.update_schedule(schedule_id, {"archive_last_run_id": None})
        archive = self._archive_path(schedule_id)
        if archive.exists():
            archive.unlink()
        batches = 0
        while True:
            s = self.db.get_schedule(schedule_id)
            if s is None:
                break
            before = s["archive_last_run_id"]
            if not await self._merge_archive(s, limit=ARCHIVE_WINDOW):
                return {"ok": False, "batches": batches}
            s = self.db.get_schedule(schedule_id)
            if s is None or s["archive_last_run_id"] in (None, before):
                break                     # 无待合并副本，正常收尾
            batches += 1
        return {"ok": True, "batches": batches}

    async def retarget_archive(self, schedule_id: str, old: dict):
        """§6 去向切换（review P1-2，PATCH target 变更时调用）：以【旧
        target_id + 旧 archive_file_id】完整清理旧容器档案数据（向量按容器
        定位 collection，用新 target 清不到旧数据 → 幽灵检索 + 记录误删），
        置空入库锚点——两步持锁同步完成（必须在任何后续合并前落定，否则
        「旧 file_id + 新 target」清旧复现 P1-2 幽灵数据）。
        R3-P1：从全部 done 副本重建（§6「绑定即重建」）为分批 LLM 长任务，
        转后台执行——同步等待会击穿 schedules:update 通道 15s IPC 超时
        （前端假报保存失败而服务端已落库）。返回重建后台 Task（受理方
        无需等待；测试 await drain）。
        R4-P1：前置取消本订阅在途重建（cancel_archive_bg）——旧任务每批
        重读实时行，连续换向会把档案传到最新目标并双重回填（锚点外孤儿
        档案永久残留）；不取消则旧任务持锁分钟级，锁竞争击穿 15s 超时
        复发。残余窗口：tick 合并持锁（单批 LLM 数十秒）期间换向仍需
        等锁——全局锁粒度成本，2.11 §6 已注明。"""
        await self.cancel_archive_bg(schedule_id)
        async with self._archive_lock:
            if old.get("archive_file_id") and old.get("target_id"):
                vs = VectorStore(str(Path(self.data_dir) / "chroma"))
                fts = FTSStore(self.db)
                await cleanup_file_data(
                    self.db, vs, fts, old["archive_file_id"],
                    old["target_id"], self.data_dir, remove_file_record=True)
            self.db.update_schedule(schedule_id, {"archive_file_id": None})
        bg = asyncio.create_task(self._retarget_rebuild_bg(schedule_id))
        self._track_archive_bg(schedule_id, bg)
        return bg

    def _track_archive_bg(self, schedule_id: str, bg: asyncio.Task) -> None:
        """入册 + 终态出册（完成/异常/取消三态都触发；空集清键防积攒）。"""
        tasks = self._archive_bg.setdefault(schedule_id, set())
        tasks.add(bg)

        def _done(t: asyncio.Task) -> None:
            cur = self._archive_bg.get(schedule_id)
            if cur is not None:
                cur.discard(t)
                if not cur:
                    self._archive_bg.pop(schedule_id, None)

        bg.add_done_callback(_done)

    async def cancel_archive_bg(self, schedule_id: str) -> None:
        """取消并等待本订阅全部在途换向重建（R4-P1/P2-1）：retarget 换向与
        delete 删除的前置联动——旧任务每批重读实时行，删除后跑完当前批会
        向容器孤儿上传 + 复活已删工作副本；换向前不取消则双重建竞态 + 等锁
        击穿 PATCH 超时。取消点在分批 LLM await 处，亚秒级返回；
        CancelledError 由 _retarget_rebuild_bg 透传，gather
        return_exceptions 收集，不波及调用方。"""
        tasks = list(self._archive_bg.get(schedule_id, ()))
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _retarget_rebuild_bg(self, schedule_id: str) -> None:
        """后台重建（R3-P1）：异常不得逃逸（bg task 无人 retrieve → 警示
        刷屏）；批失败经 _merge_archive 的 archive_failed 通知反馈，下期
        done 自动补并、详情卡「重建档案」可续（订阅删除由 rebuild_archive
        的 SCHEDULE_NOT_FOUND 分支安全退出）。"""
        try:
            res = await self.rebuild_archive(schedule_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("retarget rebuild failed: schedule=%s", schedule_id)
            return
        if not res.get("ok"):
            logger.warning("retarget rebuild incomplete: schedule=%s res=%s",
                           schedule_id, res)

    def _cleanup_workspaces(self, s: dict) -> None:
        """§6 保留期：删除超出最近 KEEP_RUNS 期终态 run（done/failed，review
        P2-8：failed 亦占目录）的 workspace 目录（任务行保留，RunReplay 消息
        档案仍在；目录缺失静默跳过）。"""
        for ws in self.db.count_schedule_workspaces_keep(s["id"], KEEP_RUNS):
            shutil.rmtree(ws, ignore_errors=True)

    def _archive_path(self, schedule_id: str) -> Path:
        """§6 档案工作副本：data/schedules/archive_{schedule_id}.md
        （随订阅删除清理，routers/schedules.py delete 端点）。"""
        return Path(self.data_dir) / "schedules" / f"archive_{schedule_id}.md"

    def _on_failed(self, s: dict, run_id: str,
                   error: str | None = None) -> None:
        """失败终态（§5.5）：run 行回填（error 落列，§8.4 ReadView 失败态）+
        计数 +1；≥3 熔断 enabled=0（通知带暂停文案）；周期在触发时已推进，
        此处不再动（恢复后从下个周期继续）。"""
        self.db.update_schedule_run(run_id, "failed", error=error)
        row = self.db.get_schedule(s["id"])
        fails = (row["consecutive_failures"] or 0) + 1
        breaker = fails >= BREAKER_THRESHOLD
        self.db.set_schedule_state(
            s["id"], consecutive_failures=fails,
            enabled=0 if breaker else None)
        # R4：失败/熔断必弹（kind='failed'，Toast 分级由前端处理）
        activity_events.fail_schedule_run(
            self.db, run_id, s["name"],
            error=(f"连续 {fails} 次失败已自动暂停，请检查网络或模型配置"
                   if breaker else error))

    # ── 辅助 ─────────────────────────────────────────────────

    def _workspace_of(self, task_id: str) -> str:
        row = self.db.get_task(task_id)
        return row["workspace_path"] if row else ""

    def _report_exists(self, task_id: str) -> bool:
        path = Path(self._workspace_of(task_id)) / "report.md"
        return path.is_file() and path.stat().st_size > 0


# ── 活动源（coding-habits §2.7）：订阅期次执行注册进活动注册表 ──

def fetch_active(db) -> list[dict]:
    """运行中订阅期次（activity 源 entity_type='schedule'）。影子任务借道
    agent 执行（§5.3），task 源须排除（schedule_id 非空，与 ppt 借道同构：
    双源上报 → 任务假角标 + 点击落任务页扑空——影子任务不进人工任务列表，
    D2）；本源是订阅执行的全局唯一呈现。R1 定时单并发 + run-now 同闸门，
    常态至多 1 条。"""
    rows = db.conn.execute(
        "SELECT r.id, s.name FROM task_schedule_runs r "
        "JOIN task_schedules s ON s.id = r.schedule_id "
        "WHERE r.status = 'running' ORDER BY r.started_at"
    ).fetchall()
    return [
        {"entity_type": "schedule", "entity_id": row["id"],
         "entity_title": row["name"]}
        for row in rows
    ]
