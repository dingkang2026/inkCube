"""Schedules router (2.11 §4.1, E3 T4/T10) — 定时订阅管理端点。

错误契约（D1）：非 2xx 用 JSONResponse {error, code}，经 handlePythonResponse
解析为 err.errorCode。
"""
import json
import logging
import os
import shutil
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import deps
from paths import get_data_dir
from scheduler import OUTPUT_CONTRACT, ROLLUP_SECTION_MARK, compute_next_run

logger = logging.getLogger(__name__)

router = APIRouter()

_INTERVAL_TYPES = ("daily", "weekly", "monthly")

# 订阅类型标记（2.11 §12 收敛 2026-10-25）：NULL 情报订阅 / rollup 组合周报；
# focus/sweep 已退役（与分类同源、执行链零差异）
_SCOPE_ROLLUP = "rollup"
# ROLLUP_SECTION_MARK（汇总骨架素材插入点）自 scheduler import——创建校验与
# 执行期替换（_build_rollup_prompt）两处共享，与 OUTPUT_CONTRACT 同款惯例

# camelCase（IPC/前端形态）→ snake_case（db 白名单键）
_FIELD_MAP = {
    "name": "name", "prompt": "prompt", "category": "category",
    "intervalType": "interval_type", "intervalValue": "interval_value",
    "targetType": "target_type", "targetId": "target_id",
    "autoImport": "auto_import", "enabled": "enabled",
    "digestScheduleIds": "digest_schedule_ids",
    # scope 不入白名单：类型由创建入口给定（情报订阅/组合周报），PATCH 不改类型
}


class ScheduleCreateBody(BaseModel):
    name: str | None = None
    prompt: str
    category: str = "general"
    intervalType: str
    intervalValue: str
    targetType: str | None = None
    targetId: str | None = None
    autoImport: bool = True
    scope: str | None = None   # 仅接受 'rollup'；None/缺省=情报订阅（2.11 §12 收敛）
    digestScheduleIds: list[str] | None = None


class ScheduleUpdateBody(BaseModel):
    name: str | None = None
    prompt: str | None = None
    category: str | None = None
    intervalType: str | None = None
    intervalValue: str | None = None
    targetType: str | None = None
    targetId: str | None = None
    autoImport: bool | None = None
    enabled: bool | None = None
    digestScheduleIds: list[str] | None = None


def _err(status: int, message: str, code: str) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"error": message, "code": code})


def _default_name(prompt: str) -> str:
    stripped = (prompt or "").strip()
    first_line = stripped.splitlines()[0] if stripped else "订阅"
    return first_line[:20]


def _validate_digest_ids(db, ids: list[str]):
    """V2.1-T3 汇总范围引用校验（2.11 §12.3 不变量①纵深防御）：id 须存在且
    非 rollup——聚合查询侧恒排除 rollup 保证正确性（不变量），此处 fail-fast 防静默
    截断（勾选 3 个实际只聚 2 个用户不可感知）。校验通过返回 None。"""
    for sid in ids:
        row = db.get_schedule(sid)
        if row is None:
            return _err(400, f"汇总范围引用的订阅不存在：{sid}",
                        "DIGEST_TARGET_NOT_FOUND")
        if row["scope"] == "rollup":
            return _err(400, "汇总范围不能包含组合周报订阅（防递归聚合）",
                        "ROLLUP_DIGEST_RECURSIVE")
    return None


# ── 固定路径端点（注册先于 /{schedule_id} 动态段，防遮蔽）────────


@router.get("/schedules")
async def list_schedules(category: str | None = None,
                         enabled: int | None = None,
                         q: str | None = None,
                         scope: str | None = None,   # rollup|intel（2.11 §12 收敛）；普通默认值（非 Query）兼容测试直调端点
                         limit: int = Query(50, ge=0, description="Page size; 0 = return all"),
                         offset: int = Query(0, ge=0, description="Rows to skip")):
    """订阅列表（管理台左列 / 速览同源，服务端过滤 + 分页）。"""
    db = deps.db
    # limit+1 探测 has_more（list_tasks 同款，免 COUNT）
    schedules = db.list_schedules(
        category=category, enabled=enabled, q=q, scope=scope,
        limit=limit + 1 if limit > 0 else 0, offset=offset)
    has_more = limit > 0 and len(schedules) > limit
    if has_more:
        schedules = schedules[:limit]
    total = db.count_schedules(category=category, enabled=enabled, q=q,
                               scope=scope)
    return {"schedules": schedules, "has_more": has_more, "total": total}


@router.post("/schedules")
async def create_schedule(body: ScheduleCreateBody):
    """创建订阅：prompt 存库前拼接 OUTPUT_CONTRACT（§8.3 存最终 prompt）；
    next_run_at 服务端计算。"""
    if body.intervalType not in _INTERVAL_TYPES:
        return _err(400, "intervalType 须为 daily/weekly/monthly",
                    "INVALID_INTERVAL_TYPE")
    # V2.1-T2 订阅类型校验（2.11 §12 收敛 2026-10-25）：scope 仅接受 rollup
    # 或缺省（NULL=情报订阅）；rollup 不沉淀（阅读产物非档案素材）、prompt 为
    # 汇总骨架须含 {sections} 素材插入点；仅 rollup 可圈选汇总范围
    if body.scope is not None and body.scope != _SCOPE_ROLLUP:
        return _err(400, "scope 须为组合周报标记 rollup 或缺省（情报订阅）",
                    "INVALID_SCOPE")
    if body.scope == _SCOPE_ROLLUP:
        if body.targetType or body.targetId:
            return _err(400, "组合周报不沉淀档案，请清空去向",
                        "ROLLUP_TARGET_FORBIDDEN")
        if ROLLUP_SECTION_MARK not in body.prompt:
            return _err(
                400,
                "组合周报 prompt 须含 {sections} 占位符（期次素材插入点）",
                "ROLLUP_PROMPT_NEEDS_SECTION")
        bad = _validate_digest_ids(deps.db, body.digestScheduleIds or [])
        if bad:
            return bad
    elif body.digestScheduleIds is not None:
        return _err(400, "仅组合周报订阅可配置汇总范围",
                    "ROLLUP_DIGEST_IDS_FORBIDDEN")
    try:
        next_run = compute_next_run(body.intervalType, body.intervalValue)
    except (ValueError, IndexError, KeyError):
        # R2-P2-1：KeyError 与 PATCH 同口径（weekly 非法星期名 _WEEKDAYS[d]），
        # 文案同步对齐——坏值 400 结构化报错而非 500
        return _err(400, "周期格式非法（daily HH:MM / weekly MON,THU HH:MM"
                         " / monthly 1,15 HH:MM）",
                    "INVALID_INTERVAL_VALUE")
    row = deps.db.create_schedule(
        (body.name or "").strip() or _default_name(body.prompt),
        body.prompt.rstrip() + "\n\n" + OUTPUT_CONTRACT,
        "general", body.category, body.intervalType, body.intervalValue,
        target_type=body.targetType, target_id=body.targetId,
        auto_import=int(body.autoImport), next_run_at=next_run,
        scope=body.scope,
        digest_schedule_ids=(json.dumps(body.digestScheduleIds,
                                        ensure_ascii=False)
                             if body.digestScheduleIds is not None else None))
    return {"schedule": row}


@router.get("/schedules/summary")
async def schedule_summary():
    """侧栏摘要行计数（T1 schedule_unread_count 的消费端点）：
    n=订阅总数 / total=未读 done run / abnormal=熔断订阅（enabled=0
    且 consecutive_failures>0，手动暂停不计入）。"""
    return {"totals": deps.db.schedule_unread_count()}


@router.get("/schedules/digest")
async def schedule_digest():
    """首页情报速览：每订阅最新 1 条 done run 摘要。"""
    return {"digest": deps.db.list_run_digest()}


@router.get("/schedules/runs/inbox")
async def schedule_inbox(
    unread: int | None = None,
    sort: str = Query("unread", pattern="^(unread|latest)$"),
    category: str = "",   # 空=不过滤；普通默认值（非 Query）兼容测试直调端点
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """跨订阅期次流（情报流卡片流，0.6 §3.2）：done、sort=unread 未读优先
    （read_at NULL 前置）/latest 纯 finished_at 倒序（排序独立于类别筛选，
    §6.3-3）；category=订阅分类直读过滤（corporate/industry/region/general）；
    offset 翻页。2026-10-23 V2-E1a 扩展（原仅 unread/limit/offset）。"""
    db = deps.db
    # limit+1 探测 has_more（list_tasks 同款，免 COUNT）
    runs = db.list_run_inbox(unread_only=bool(unread), sort=sort,
                             category=category, limit=limit + 1, offset=offset)
    has_more = len(runs) > limit
    if has_more:
        runs = runs[:limit]
    # total 与列表同口径（unread/category 过滤后），供分页控件
    sql = ("SELECT COUNT(*) AS cnt FROM task_schedule_runs r"
           " JOIN task_schedules s ON s.id = r.schedule_id"
           " WHERE r.status = 'done'")
    params: list = []
    if unread:
        sql += " AND r.read_at IS NULL"
    if category:
        sql += " AND s.category = ?"
        params.append(category)
    total = db.conn.execute(sql, params).fetchone()["cnt"]
    return {"runs": runs, "has_more": has_more, "total": total}


@router.post("/schedules/runs/{run_id}/read")
async def mark_run_read(run_id: str):
    if not deps.db.mark_run_read(run_id):
        return _err(404, "期次不存在", "RUN_NOT_FOUND")
    return {"ok": True}


@router.delete("/schedules/runs/{run_id}")
async def delete_schedule_run(run_id: str):
    """单条删除期次（管理台收件箱/期次历史行内操作，2026-10-20 审计补齐）。

    仅终态 run 可删：running 在途拒删 409（单 run 无法安全取消——
    cancel_running 是订阅级，等终态后删）；不存在 404。
    清理对齐 delete_schedule 的单 run 子集：报告副本 unlink（行删后成
    孤儿，B5 同源）；cancelled run 的半成品 workspace rmtree（对齐
    delete_schedule 对被取消 run 的清理）；done/failed workspace 不在此
    入口删（保留期 keep=10 治理）。锚点悬空防护：删的恰是
    archive_last_run_id 指向的 run → 锚点置 NULL（悬空锚点会使
    pending_archive_runs 永远空集阻断后续增量合并；置空后下次合并
    全量重建，幂等）。
    注册于固定路径段（先于 /schedules/{schedule_id} 动态段，防遮蔽）。
    """
    run = deps.db.get_run(run_id)
    if not run:
        return _err(404, "期次不存在", "RUN_NOT_FOUND")
    if run["status"] == "running":
        return _err(409, "期次执行中，完成或失败后才能删除", "RUN_RUNNING")
    if not deps.db.delete_schedule_run(run_id):
        return _err(404, "期次不存在", "RUN_NOT_FOUND")
    # 线索溯源置 NULL（0.6 §4.1：期次删除不删线索，订阅名快照兑底可读）
    detached = deps.db.detach_leads_for_run(run_id)
    if detached:
        logger.info("Detached %d lead(s) from deleted run %s", detached, run_id)
    if run["report_path"]:
        try:
            Path(run["report_path"]).unlink(missing_ok=True)
        except OSError:
            logger.warning("run report copy cleanup failed: %s",
                           run["report_path"], exc_info=True)
    if run["status"] == "cancelled" and run["task_id"]:
        t = deps.db.get_task(run["task_id"])
        ws = t["workspace_path"] if t else ""
        if ws:
            shutil.rmtree(ws, ignore_errors=True)   # 半成品目录
    if run["archive_last_run_id"] == run_id:
        deps.db.update_schedule(run["schedule_id"], {"archive_last_run_id": None})
    return {"ok": True}


@router.get("/schedules/runs/{run_id}/report")
async def read_run_report(run_id: str):
    """读 D5 报告副本全文；路径白名单限 data/schedules 目录内。"""
    run = deps.db.get_run(run_id)
    if not run:
        return _err(404, "期次不存在", "RUN_NOT_FOUND")
    if not run["report_path"]:
        if run["status"] in ("failed", "running"):
            # T9 修订② + 2026-10-23 running 扩展：无副本 → 元数据返回（content 空）。
            # failed：ReadView 失败卡（§8.4）；running：报告页执行中态，
            # 头部「执行过程」实时回放可达（原 running 落 404 使期次历史行点击死胡同）
            return {"run": run,
                    "schedule": deps.db.get_schedule(run["schedule_id"]),
                    "content": ""}
        return _err(404, "本期无报告副本", "REPORT_NOT_READY")
    base = (Path(get_data_dir()) / "schedules").resolve()
    target = Path(run["report_path"]).resolve()
    if not str(target).startswith(str(base) + os.sep):
        return _err(403, "报告路径越界", "REPORT_PATH_OUTSIDE")
    if not target.is_file():
        return _err(404, "报告副本文件缺失", "REPORT_FILE_MISSING")
    # ReadView 契约（§8.2）：run 元数据（标题行/失败态）+ 订阅行（订阅设置/
    # 沉淀态目标解析）随全文一次返回，免二次请求
    return {"run": run,
            "schedule": deps.db.get_schedule(run["schedule_id"]),
            "content": target.read_text(encoding="utf-8")}


# ── 动态路径端点（/{schedule_id}）────────────────────────────


@router.patch("/schedules/{schedule_id}")
async def update_schedule(schedule_id: str, body: ScheduleUpdateBody):
    """白名单字段更新；enabled 翻转或周期变化 → 重算 next_run_at
    （enabled=0 → NULL 暂停不再调度；启用 → 下个到期时刻）。"""
    current = deps.db.get_schedule(schedule_id)
    if not current:
        return _err(404, "订阅不存在", "SCHEDULE_NOT_FOUND")
    # R2-P1-3：exclude_unset + 字段级 null 语义——targetType/targetId 显式
    # null = 清空去向（前端「不沉淀」恒发 null，exclude_none 旧实现丢弃 →
    # 清不掉且无任何 API 路径可清除）；其余字段 null = 未提供，保持现值
    # （防 name: null 误清名字）
    fields = {
        _FIELD_MAP[key]: value
        for key, value in body.model_dump(exclude_unset=True).items()
        if key in _FIELD_MAP
        and (value is not None or key in ("targetType", "targetId",
                                          "digestScheduleIds"))
    }
    # V2.1-T2 scope 校验 + rollup 约束（2.11 §12）：scope 已移出白名单——
    # 类型由创建入口给定不可 PATCH 切换；rollup 约束按存量行类型判定
    final_scope = current["scope"]
    if final_scope == _SCOPE_ROLLUP:
        if fields.get("target_type") or fields.get("target_id"):
            return _err(400, "组合周报不沉淀档案，请清空去向",
                        "ROLLUP_TARGET_FORBIDDEN")
    elif fields.get("digest_schedule_ids") is not None:
        return _err(400, "仅组合周报订阅可配置汇总范围",
                    "ROLLUP_DIGEST_IDS_FORBIDDEN")
    if (final_scope == _SCOPE_ROLLUP
            and isinstance(fields.get("digest_schedule_ids"), list)):
        bad = _validate_digest_ids(deps.db, fields["digest_schedule_ids"])
        if bad:
            return bad
    if "prompt" in fields:
        # R2-P2-3：与 create 同口径——存库前拼接 OUTPUT_CONTRACT（D4「存最终
        # prompt」原则，执行侧不重复拼接）；已带 contract 的输入先剥防双拼
        # （如回存从库里读出的完整 prompt）
        p = fields["prompt"].rstrip()
        if p.endswith(OUTPUT_CONTRACT):
            p = p[:-len(OUTPUT_CONTRACT)].rstrip()
        if final_scope == _SCOPE_ROLLUP and ROLLUP_SECTION_MARK not in p:
            return _err(
                400,
                "组合周报 prompt 须含 {sections} 占位符（期次素材插入点）",
                "ROLLUP_PROMPT_NEEDS_SECTION")
        fields["prompt"] = p + "\n\n" + OUTPUT_CONTRACT
    if isinstance(fields.get("digest_schedule_ids"), list):
        # list 形态（IPC 透传）→ TEXT(JSON) 落库；显式 null 保持（清圈选回全量）
        fields["digest_schedule_ids"] = json.dumps(
            fields["digest_schedule_ids"], ensure_ascii=False)
    target_changed = ("target_id" in fields
                      and fields["target_id"] != current["target_id"])
    # review P2-2：先验证后落库——坏 interval 若先落库，tick 会在
    # compute_next_run 处反复炸且拒不推进（饿死整批）；同请求内其他字段
    # 亦不得半提交
    if "interval_type" in fields or "interval_value" in fields:
        try:
            compute_next_run(
                fields.get("interval_type", current["interval_type"]),
                fields.get("interval_value", current["interval_value"]))
        except (ValueError, KeyError):
            return _err(400, "周期格式非法（daily HH:MM / weekly MON,THU HH:MM"
                             " / monthly 1,15 HH:MM）",
                        "INVALID_INTERVAL_VALUE")
    if fields:
        deps.db.update_schedule(schedule_id, fields)
    # R2-P2-4：手动恢复（enabled 0→1，含 toggle 批量恢复同路径）归零熔断计数——
    # 否则恢复后首次失败立即再熔断（fails=4≥3），恢复动作形同虚设
    if fields.get("enabled") and not current["enabled"]:
        deps.db.set_schedule_state(schedule_id, consecutive_failures=0)
    new = deps.db.get_schedule(schedule_id)
    period_changed = (new["interval_type"], new["interval_value"]) != (
        current["interval_type"], current["interval_value"])
    if new["enabled"]:
        if period_changed or not current["enabled"]:
            deps.db.update_schedule(schedule_id, {
                "next_run_at": compute_next_run(
                    new["interval_type"], new["interval_value"])})
    else:
        deps.db.update_schedule(schedule_id, {"next_run_at": None})
    # §6 去向切换（review P1-2）：旧容器清旧 + 置空锚点同步完成（「绑定即
    # 重建」）；R3-P1：分批 LLM 重建转调度器后台任务（同步等待会击穿
    # schedules:update 15s IPC 超时）；调度器未就绪时仅置空锚点防跨容器
    # 错位清理，下期 done 合并或手动重建时补建
    if target_changed:
        if deps.scheduler is not None:
            await deps.scheduler.retarget_archive(schedule_id, current)
        else:
            deps.db.update_schedule(schedule_id, {"archive_file_id": None})
    return {"schedule": deps.db.get_schedule(schedule_id)}


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str):
    """删订阅（runs 级联删；历史任务行 schedule_id 置 NULL 保留）。

    §6：档案工作副本随订阅删除清理（archive_{id}.md）；入库的档案文件
    归目标容器资产，不随删（用户可能仍需检索）。
    review P2-4：运行中订阅 → 先取消在途执行（CancelledError 使 _on_finish
    不再产生孤儿 run 回填/通知/档案写），并清理孤儿文件：per-run 报告副本
    （行级联删后成孤儿）与被取消执行的 workspace 半成品目录。
    R4-P2-1：先取消在途换向重建（旧任务每批重读实时行，删除后跑完当前批
    会向容器孤儿上传 + 复活下方 unlink 的工作副本）；置于 cancel_running
    之前保持其「返回到落库零挂起点」论证（本 await 期间若有在途执行，run
    行仍 running → tick 防重入挡住，无新执行窗口）。
    """
    if deps.scheduler is not None:
        await deps.scheduler.cancel_archive_bg(schedule_id)
    cancelled: list[str] = []
    if deps.scheduler is not None:
        cancelled = await deps.scheduler.cancel_running(schedule_id)
    runs = deps.db.list_schedule_runs(schedule_id, limit=-1)  # SQLite -1 = 全部
    # 线索溯源置 NULL（0.6 §4.1）：须在删订阅（runs 级联删）前，否则关联断尽
    deps.db.detach_leads_for_schedule(schedule_id)
    # rollup 圈定引用剔除（V2.1 CodeReview Major，同 detach_leads 联动纪律）：
    # 悬空 id → 编辑回填含悬空 id、勾选框只渲染现存候选无法移除、
    # _validate_digest_ids fail-fast → 编辑保存 400 死锁。剔空保留 []（NULL=
    # 全量会突变聚合范围；[] → 空窗口 skipped 留痕，用户主动修正）
    detached_digest = deps.db.detach_rollup_digest_references(schedule_id)
    if detached_digest:
        logger.info("Detached %d rollup digest reference(s) from deleted "
                    "schedule %s", detached_digest, schedule_id)
    if not deps.db.delete_schedule(schedule_id):
        return _err(404, "订阅不存在", "SCHEDULE_NOT_FOUND")
    # R3-P2-3：无落库后复扫——首次 cancel 返回到 delete_schedule 落库之间
    # 零挂起点（事件循环无法插入 tick / run-now），cancel 内部 await 期间
    # 防重入（run 行仍 running → skipped）亦挡住重派发：复扫扫不到任何东西
    archive = Path(get_data_dir()) / "schedules" / f"archive_{schedule_id}.md"
    try:
        archive.unlink(missing_ok=True)
    except OSError:
        logger.warning("archive workfile cleanup failed: %s", archive,
                       exc_info=True)
    cancelled_set = set(cancelled)
    for r in runs:
        if r["report_path"]:
            try:
                Path(r["report_path"]).unlink(missing_ok=True)
            except OSError:
                logger.warning("run report copy cleanup failed: %s",
                               r["report_path"], exc_info=True)
        if r["id"] in cancelled_set and r["task_id"]:
            t = deps.db.get_task(r["task_id"])
            ws = t["workspace_path"] if t else ""
            if ws:
                shutil.rmtree(ws, ignore_errors=True)   # 半成品目录
    return {"ok": True}


@router.get("/schedules/{schedule_id}/runs")
async def list_schedule_runs(
    schedule_id: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    if not deps.db.get_schedule(schedule_id):
        return _err(404, "订阅不存在", "SCHEDULE_NOT_FOUND")
    db = deps.db
    # limit+1 探测 has_more（list_tasks 同款，免 COUNT）
    runs = db.list_schedule_runs(schedule_id, limit=limit + 1, offset=offset)
    has_more = len(runs) > limit
    if has_more:
        runs = runs[:limit]
    total = db.conn.execute(
        "SELECT COUNT(*) AS cnt FROM task_schedule_runs WHERE schedule_id = ?",
        (schedule_id,),
    ).fetchone()["cnt"]
    return {"runs": runs, "has_more": has_more, "total": total}


@router.post("/schedules/{schedule_id}/run-now")
async def run_now(schedule_id: str):
    """手动立即执行一次：复用触发路径（仍走 R1/R2/防重入），不动周期；
    异步受理（review P2-6）：受理即返回 {ok}，执行在后台完成（终态经
    通知中心 R4 反馈）；忙时 {ok: False, deferred: True} 稍后重试。"""
    s = deps.db.get_schedule(schedule_id)
    if not s:
        return _err(404, "订阅不存在", "SCHEDULE_NOT_FOUND")
    if deps.scheduler is None:
        return _err(503, "调度器未就绪", "SCHEDULER_UNAVAILABLE")
    return await deps.scheduler.dispatch_now(s)


@router.post("/schedules/{schedule_id}/rebuild-archive")
async def rebuild_archive(schedule_id: str):
    """§6 重建情报档案（T10，端点 11）：绑定/更换去向后的首次构建、档案修复
    同用；全部 done 副本按窗口分批重合并（同步等待，批数 = ⌈期数/30⌉）。"""
    if not deps.db.get_schedule(schedule_id):
        return _err(404, "订阅不存在", "SCHEDULE_NOT_FOUND")
    if deps.scheduler is None:
        return _err(503, "调度器未就绪", "SCHEDULER_UNAVAILABLE")
    return await deps.scheduler.rebuild_archive(schedule_id)
