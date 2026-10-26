"""python/wiki/profile_service.py — 编译后产业画像提取（2.6 §4.8）.

Wiki 编译终态（completed）后的附加提取步骤：把 overview + 已编译页面中的
产业链知识结构化为项目产业画像（chain/segment 成对），回写 projects 两列，
供详情头部画像行展示与订阅创建预填 {产业} 占位符（2.11 §8.1「从项目画像」渠道）。

方案 A(编译后落库)：task_queue completed 分支直调 on_compile_complete_sync ——
run_in_executor 后台线程执行,整体吞异常(提取失败绝不影响编译主流程,画像
保持旧值,下次编译自动重试)。

活动模型(2.7 统一骨架)：kind='profile_extract' 经 activities_registry 的
FunctionSource 注册;进行中快照 = 进程内存态 _RUNNING(崩溃恢复依赖上游
编译重放,提取是编译纯下游,不建任务表,同 ledger_service)。

manual 保护(2.1 §4.5 人工优先)：profile_source='manual'（头部人工编辑过）
的行编译自动提取跳过（占坑前短路 + 回写前复检,窗口缩到单条 UPDATE）；
显式「重新提取」（try_acquire + run_manual_extract_sync,端点 POST
/knowledge/projects/{id}/profile/extract）可覆盖 manual。
"""
import asyncio
import json
import logging
import re
import threading
import time

from llm.usage import UsageContext, push_context  # 2.10 §4.3 用量环境

logger = logging.getLogger(__name__)

# 进程内进行中标记:project_id -> {"started_at": epoch}(并发守卫 + 活动快照源)
_RUNNING: dict[str, dict] = {}
_LOCK = threading.Lock()

_SYSTEM_PROMPT = (
    "你是招商引资/投资项目的产业分析员。输入是项目 Wiki 的全局摘要与页面内容,"
    "任务是提炼该企业所处的产业链与细分产业领域。只输出 JSON 数组,不要解释。"
)

_PROMPT_TEMPLATE = """\
从下面的项目资料中提炼企业产业画像,输出 JSON 数组,每个元素:
{{"chain": "...", "segment": "..."}}

字段要求:
- chain: 所属产业链名称(如「具身智能」),不带「产业链」后缀
- segment: 该链条下的细分产业领域(如「四足机器狗」),推断不出用空字符串
- 最多 {max_entries} 条,按相关度排序;资料中没有可靠依据时输出 []

项目资料:
{material}
"""

# 产出上限(2.6 §4.8)：白名单清洗后保留条数
_MAX_PROFILE_ENTRIES = 5
# 单页注入 prompt 的内容上限(防极端大页打爆请求,同 ledger_service)
_MAX_PAGE_CHARS = 20000
# 全部素材总字符上限(页数多时按结构序截断——结构页排前,信息密度最高)
_MAX_TOTAL_CHARS = 60000


def fetch_running() -> list[dict]:
    """ActivitySource fetch:进行中提取快照(只读,无副作用,0.4 §2.2 契约)。"""
    with _LOCK:
        return [{"entity_type": "profile_extract", "entity_id": pid,
                 "project_id": pid, "entity_title": "产业画像提取",
                 "progress_text": "提取产业画像…"}
                for pid in sorted(_RUNNING)]


def try_acquire(project_id: str) -> bool:
    """占坑（端点受理「重新提取」用）；已在途返回 False → 端点转 409。"""
    with _LOCK:
        if project_id in _RUNNING:
            return False
        _RUNNING[project_id] = {"started_at": time.time()}
        return True


def release(project_id: str) -> None:
    """归还占坑（端点 executor 提交失败时兑底，review Info 2026-10-25）。

    正常路径占坑由各执行主体的 finally 清理；此处仅兑提交失败
    （get_running_loop 无事件循环等）——泄漏则该项目恒 409 直至重启。
    幂等 pop，双重调用安全。"""
    with _LOCK:
        _RUNNING.pop(project_id, None)


def on_compile_complete_sync(db, project_id: str, settings: dict) -> None:
    """同步入口(测试直调;task_queue 经 run_in_executor 后台化,§4.8)。

    manual 保护短路(人工修正优先,编译不覆盖) + 并发守卫(已在途静默跳过);
    整体吞异常 —— 提取失败绝不影响编译主流程;finally 只清理自己占的坑
    (acquired 绑定清理责任:无条件 pop 会误删「重新提取」端点持有的在途
    标记,击穿单飞 —— 双提取并发 + 活动提前消失,review M-1 2026-10-25)。
    """
    acquired = False
    try:
        project = db.get_project(project_id)
        if not project or project.get("profile_source") == "manual":
            return
        acquired = try_acquire(project_id)
        if not acquired:
            logger.info("profile extract for %s already running — skipped",
                        project_id)
            return
        _run(db, project_id, settings, overwrite_manual=False)
    except Exception:
        logger.exception("profile extract failed for %s", project_id)
    finally:
        if acquired:
            with _LOCK:
                _RUNNING.pop(project_id, None)


def run_manual_extract_sync(db, project_id: str, settings: dict) -> None:
    """显式「重新提取」执行主体(覆盖 manual,§4.8)。

    调用方（knowledge 端点）须先 try_acquire 成功再经 run_in_executor 调用
    本函数;失败只记日志(受理已成立,前端经心跳看到活动消失,画像保旧值)。
    """
    try:
        _run(db, project_id, settings, overwrite_manual=True)
    except Exception:
        logger.exception("manual profile extract failed for %s", project_id)
    finally:
        with _LOCK:
            _RUNNING.pop(project_id, None)


def _run(db, project_id: str, settings: dict, overwrite_manual: bool) -> None:
    project = db.get_project(project_id)
    if not project:
        return
    # 素材:overview + 有内容的页面(结构序,取材口径同 ledger_service)
    pages = [p for p in db.list_wiki_pages(project_id)
             if (p.get("content") or "").strip()]
    overview = (project.get("wiki_overview") or "").strip()
    if not pages and not overview:
        return                       # 无素材:不动现状(不产空提取,§4.8)
    # 2.10 §4.3: 用量环境（source=wiki / purpose=profile）——本函数运行于
    # run_in_executor 线程，contextvar 不跨线程传播，须在线程体内自行 push
    with push_context(UsageContext(
            source="wiki", purpose="profile", project_id=project_id)):
        entries = _extract_entries(overview, pages, settings)  # raise → 上层吞,画像保旧值
    # 回写前复检 manual(占坑窗口内用户编辑的保护,竞态缩到单条 UPDATE);
    # 显式重新提取(overwrite_manual=True)不受保护
    if not overwrite_manual:
        current = db.get_project(project_id)
        if current and current.get("profile_source") == "manual":
            logger.info("profile extract for %s: manual edit detected "
                        "during run — write skipped", project_id)
            return
    payload = json.dumps(entries, ensure_ascii=False)
    db.update_project(project_id, industry_profile=payload,
                      profile_source="wiki_compile")


def _extract_entries(overview: str, pages: list[dict], settings: dict) -> list[dict]:
    """LLM 结构化提取(同步:executor 线程内 asyncio.run 包装 async client)。

    模型经 llm_routing 'profile' 路由(未配置则全局默认);输出 JSON 数组
    经白名单清洗(chain 必填非空、segment 可空、同批去重、上限 5 条)。
    识别不出任何画像 → 返回 [](落库 '[]',与 NULL=未提取区分,§4.8)。
    """
    from llm import LLMClient
    from llm.routing import get_task_settings

    effective = get_task_settings(settings, "profile")
    blocks: list[str] = []
    if overview:
        blocks.append(f"## 全局摘要\n{overview[:_MAX_PAGE_CHARS]}")
    total = sum(len(b) for b in blocks)
    for p in pages:
        block = (f"## {p.get('title') or p['page_key']} "
                 f"(page_key={p['page_key']})\n"
                 f"{p['content'][:_MAX_PAGE_CHARS]}")
        if blocks and total + len(block) > _MAX_TOTAL_CHARS:
            break                                    # 总量受控:结构序靠前页优先
        blocks.append(block)
        total += len(block)
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _PROMPT_TEMPLATE.format(
            material="\n\n".join(blocks), max_entries=_MAX_PROFILE_ENTRIES)},
    ]
    raw = asyncio.run(LLMClient(effective).chat_full(messages))["content"]
    return _clean_entries(raw)


def _clean_entries(raw: str) -> list[dict]:
    """白名单清洗;整体解析失败抛异常(调用方兜,保持旧画像,§4.8)。"""
    m = re.search(r"\[.*\]", raw, re.S)
    if not m:
        raise ValueError("profile extract: no JSON array in LLM output")
    data = json.loads(m.group(0))
    if not isinstance(data, list):
        raise ValueError("profile extract: LLM output is not a JSON array")
    cleaned: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        chain = (item.get("chain") or "").strip()
        if not chain:
            continue                                 # 无链条名的元素不可用
        entry = {"chain": chain,
                 "segment": (item.get("segment") or "").strip()}
        if entry not in cleaned:                     # 同批去重(LLM 复述同一链条)
            cleaned.append(entry)
    return cleaned[:_MAX_PROFILE_ENTRIES]
