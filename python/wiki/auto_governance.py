"""python/wiki/auto_governance.py — 编译完成后自动治理链（E3-2026-10）.

编译终态且有实际页面产出时后台执行（task_queue 经 run_in_executor 后台化，
同 ledger/profile 先例；治理失败绝不影响编译主流程——本模块整体吞异常）：

  1. 自动清扫 sweep_reviews（Stage1 规则免费恒跑；Stage2 LLM ≤200 条）
     —— 核销已过时的发现项（页面已建/死链已通/已有入链）
  2. 自动死链修复 —— 仅「有可靠建议目标」的死链：服务端正则替换
     （不冻结页面，快照+时间线，§5.4 link-fix 写路径），成功即核销
  3. 自动重复检测 dedup（LLM，≤100 页摘要）—— 组结果落库
     wiki_dedup_groups 供体检视图浮现；合并仍需用户确认（删页不可逆）

LLM 型步骤（Stage2 / 重复检测）三重守卫：
  - 产出守卫：本次编译 ingest 摘要存在 pages_created/updated > 0（空编译不烧 token）
  - 冷却守卫：30 分钟进程内存态（解析密集期连环自动汇编不连环治理；
    人工触发不受限的语义同 auto_compile——本链只有自动路径）
  - 就绪守卫：readiness wiki_compile（复用 auto_compile 判定，同源缓存）

汇总写回 compile task 行 governance_summary（JSON 文本，前端 CompilePanel
编译详情数据源，review m-2 2026-10-27 更正——原误写「toast 数据源」）+
项目级时间线一条（change_log action='governance'）
——「机器自动处理」对用户透明可查。
"""
import asyncio
import logging
import re
import time

logger = logging.getLogger(__name__)

# 治理链 LLM 步骤冷却（秒）：语义同 auto_compile.COOLDOWN_SECONDS（30 分钟）
COOLDOWN_SECONDS = 30 * 60

# project_id -> 冷却截止 epoch（time.time()）；进程内存态，懒清理
_COOLDOWN_UNTIL: dict[str, float] = {}

# 死链发现项文案契约解析（wiki/lint.py L300-319 产出的 title/description 格式 +
# routers/wiki.py _store_findings 追加的 PAGES/建议链接到 行）
_TITLE_DEAD_RE = re.compile(r"死链:\s*\[\[(.+?)(?:\|.+?)?\]\]")
_TARGET_RE = re.compile(r"建议链接到:\s*\[\[(.+?)\]\]")
_PAGES_RE = re.compile(r"^PAGES:\s*(.+)$", re.M)


def _in_cooldown(project_id: str) -> bool:
    """冷却判定（顺带懒清理过期项）。"""
    until = _COOLDOWN_UNTIL.get(project_id)
    if until is None:
        return False
    if time.time() >= until:
        _COOLDOWN_UNTIL.pop(project_id, None)
        return False
    return True


def on_compile_complete_sync(db, project_id: str, task_id: str, settings: dict) -> None:
    """同步入口（task_queue 经 run_in_executor 调用；整体吞异常）。

    治理结果经 governance_summary 落 task 行 + change_log 一条时间线；
    任何一步失败只记日志，绝不影响编译主流程（ledger/profile 同语义）。
    """
    try:
        summary = _run(db, project_id, task_id, settings)
    except Exception:
        logger.exception("auto governance failed for %s (task %s)", project_id, task_id)
        return
    if summary is None:
        return
    try:
        db.set_compile_governance_summary(task_id, summary)
        db.add_wiki_change_log(
            project_id=project_id,
            action="governance",
            summary=(f"自动治理：核销 {summary['swept']} 条过时问题，"
                     f"自动修复 {summary['link_fixed']} 条死链，"
                     f"检出 {summary['dedup_groups']} 组疑似重复页面"),
        )
        # 治理结果主动可达（E3-2026-10）：通知中心弹一条（kind=governance，
        # 复用 wiki_compile 导航）；零动作不发（没消息 = 没问题）
        if summary["swept"] + summary["link_fixed"] + summary["dedup_groups"] > 0:
            from activity_events import governance_wiki
            governance_wiki(db, task_id, summarize(summary))
    except Exception:
        logger.exception("auto governance summary persist failed for %s", project_id)


def _run(db, project_id: str, task_id: str, settings: dict) -> dict | None:
    """治理链执行主体。返回汇总 dict；无治理动作时也返回全零汇总（调用方
    据此落库——「这轮没清理任何东西」本身是有效信息）。"""
    from llm.usage import UsageContext, push_context  # 2.10 §4.3 用量环境
    from wiki.sweep_reviews import sweep_reviews

    try:
        summaries = db.get_ingest_summaries(task_id)
    except Exception:
        logger.exception("auto governance: load ingest summaries failed for %s", project_id)
        return None
    produced = any(
        not s.get("skipped")
        and (s.get("pages_created") or 0) + (s.get("pages_updated") or 0) > 0
        for s in summaries
    )

    # LLM 客户端三重守卫（产出/冷却/就绪）；拿到即占冷却槽
    llm_client = None
    if produced and not _in_cooldown(project_id):
        try:
            from wiki.auto_compile import _readiness_ready  # 同包私有复用：判定口径单一事实源
            if _readiness_ready("wiki_compile"):
                llm_client = _build_llm_client(db)
                if llm_client is not None:
                    _COOLDOWN_UNTIL[project_id] = time.time() + COOLDOWN_SECONDS
        except Exception:
            logger.exception("auto governance: readiness guard failed for %s", project_id)

    # 三步各自隔离（review s-3 2026-10-27）：单步异常不吞掉已完成的前段
    # 动作——汇总带已完成部分照常落库/时间线（后段异常不再无痕迹）
    swept = 0
    try:
        # 1. 清扫（Stage1 免费；Stage2 仅在 llm_client 提供时跑）
        with push_context(UsageContext(source="wiki", purpose="sweep", project_id=project_id)):
            sweep_result = asyncio.run(sweep_reviews(db, project_id, llm_client))
        swept = sweep_result.get("stage1_resolved", 0) + sweep_result.get("stage2_resolved", 0)
    except Exception:
        logger.exception("auto governance: sweep failed for %s", project_id)

    link_fixed = 0
    if produced:
        try:
            # 2. 死链修复（纯机械不耗 LLM；有产出才值得再扫一遍发现项）
            link_fixed = _fix_broken_links(db, project_id)
        except Exception:
            logger.exception("auto governance: link fix failed for %s", project_id)

    dedup_groups = 0
    if llm_client is not None:
        try:
            # 3. 重复检测（LLM；dedup 档独立构造——与手动 dedup 端点同档
            #    同口径，review m-1 2026-10-27 修正：原误用 standard 档 client）
            dedup_client = _build_dedup_client(db)
            if dedup_client is not None:
                from wiki.dedup import extract_entity_summaries, detect_duplicates
                page_summaries = extract_entity_summaries(db, project_id)
                if len(page_summaries) >= 2:
                    with push_context(UsageContext(
                            source="wiki", purpose="dedup", project_id=project_id)):
                        groups = asyncio.run(detect_duplicates(page_summaries, dedup_client))
                    if groups:
                        db.replace_dedup_groups(project_id, groups)
                    dedup_groups = len(groups)
        except Exception:
            logger.exception("auto governance: dedup failed for %s", project_id)

    summary = {
        "swept": swept,
        "link_fixed": link_fixed,
        "dedup_groups": dedup_groups,
    }
    logger.info("auto governance for %s (task %s): %s", project_id, task_id, summary)
    return summary


def _build_llm_client(db):
    """构造标准档 LLM 客户端（sweep router 同口径：resolve_model(standard)）。"""
    try:
        from llm import LLMClient
        from llm.routing import resolve_model
        settings = db.get_settings()
        settings["model"] = resolve_model(settings, "standard")
        return LLMClient(settings)
    except Exception as e:
        logger.info("auto governance: LLM unavailable — Stage2/dedup skipped: %s", e)
        return None


def _build_dedup_client(db):
    """构造 dedup 档客户端（手动 dedup 端点同口径：get_task_settings(dedup)，
    review m-1 2026-10-27）；构造失败仅跳过 dedup 步骤，不影响 sweep。"""
    try:
        from llm import LLMClient
        from llm.routing import get_task_settings
        return LLMClient(get_task_settings(db.get_settings(), "dedup"))
    except Exception as e:
        logger.info("auto governance: dedup-tier LLM unavailable — dedup skipped: %s", e)
        return None


def _fix_broken_links(db, project_id: str) -> int:
    """扫描待处理死链发现项，对有可靠建议目标的执行机械修复并核销。

    无建议目标 / 解析失配的发现项原样保留（留人工）；单条失败隔离
    （一条写失败不影响其余修复）。返回修复成功条数。
    """
    fixed = 0
    for review in db.list_wiki_reviews(project_id, resolved=False):
        if review.get("type") != "broken-link":
            continue
        title = review.get("title") or ""
        desc = review.get("description") or ""
        dead = _TITLE_DEAD_RE.search(title)
        target = _TARGET_RE.search(desc)
        pages_line = _PAGES_RE.search(desc)
        if not (dead and target and pages_line):
            continue  # 无建议目标或文案契约失配：留人工（§4.4 宁可留待人工不误改）
        source_key = pages_line.group(1).split(",")[0].strip()
        if not source_key:
            continue
        try:
            result = db.fix_wiki_page_wikilink(
                project_id=project_id,
                page_key=source_key,
                dead_key=dead.group(1).strip(),
                target_key=target.group(1).strip(),
                change_summary=f"自动修复死链 [[{dead.group(1)}]] → [[{target.group(1)}]]",
            )
        except Exception:
            logger.warning("auto fix broken link failed for review %s", review["id"], exc_info=True)
            continue
        if result.get("success"):
            db.resolve_wiki_review(project_id, review["id"], "fixed")
            fixed += 1
        elif result.get("reason") == "content_changed":
            # review M-1 2026-10-27：读-改-写间源页被并发修改（用户编辑/他方
            # 写入先行）——DB 层 CAS 拒绝写入，发现项仍然真实，不核销，
            # 留下一轮治理或人工
            logger.info("auto fix link skipped (content_changed) for review %s", review["id"])
    if fixed:
        logger.info("auto governance: fixed %d broken link(s) in %s", fixed, project_id)
    return fixed


def summarize(summary: dict) -> str:
    """governance_summary 的用户可读单行（前端 toast 备用；JSON 为准）。"""
    return (f"核销 {summary.get('swept', 0)} 条过时问题，"
            f"修复 {summary.get('link_fixed', 0)} 条死链，"
            f"检出 {summary.get('dedup_groups', 0)} 组疑似重复")
