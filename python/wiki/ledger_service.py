"""python/wiki/ledger_service.py — 3.0 四期智能层:编译后台账结构化解析(3.1)。

方案 A(编译后落库):wiki 编译终态(task_queue complete_compile 挂点旁)直调
on_compile_complete_sync —— 经 run_in_executor 后台线程执行,整体吞异常(解析
失败绝不影响编译主流程,3.1 §4.4)。流程:is_ledger 页快照 → LLM 结构化解析
→ 按页单事务删旧插新 ledger_entries →(③④)建议生成 →(①)AI 状态句。

活动模型(2.7 统一骨架):kind='ledger_parse' 经 activities_registry 的
FunctionSource 注册;进行中快照 = 进程内存态 _RUNNING(3.1 不变量 4:崩溃
恢复依赖上游编译重放,解析是编译纯下游,不建任务表)。
"""
import asyncio
import datetime
import hashlib
import json
import logging
import re
import threading
import time
import unicodedata

from llm.usage import UsageContext, push_context  # 2.10 §4.3 用量环境

logger = logging.getLogger(__name__)

# 进程内进行中标记:project_id -> {"started_at": epoch}(并发守卫 + 活动快照源)
_RUNNING: dict[str, dict] = {}
_LOCK = threading.Lock()

_SYSTEM_PROMPT = (
    "你是招商引资/投资项目的台账数据分析员。输入是若干台账页(时间线式追加的"
    "监控记录),任务是把它拆成一条条独立条目。只输出 JSON 数组,不要解释。"
)

_PROMPT_TEMPLATE = """\
从下面的台账页内容中提取结构化条目,输出 JSON 数组,每个元素:
{{"page_key": "...", "entry_date": "...", "entry_type": "...", "text": "..."}}

字段要求:
- page_key: 必须原样使用输入标注的 page_key,不得自造
- entry_date: 条目日期,格式 YYYY-MM-DD 或 YYYY-MM;推断不出用 null
- entry_type: milestone(节点)/risk(风险)/policy(政策)/finance(经营财务)/service(服务协调) 之一
- text: 条目原文(一行,去掉日期前缀与 markdown 修饰,保留关键数字与结论)

台账页内容:
{material}
"""

_DATE_RE = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")
_VALID_TYPES = {"milestone", "risk", "policy", "finance", "service"}
# 单页注入 prompt 的内容上限(台账页正常数 KB;防极端大页打爆请求)
_MAX_PAGE_CHARS = 20000

# W3:fingerprint 哈希前规范化 —— LLM 对同一事实的措辞会漂移(标点/空白/全半角),
# 直接哈希原文会导致「内容未变而 fp 变」→ dismissed 建议复活,违背 3.1 不变量 3。
# 日期不归一:日期变化属内容实质变化,重新提醒符合用户利益(3.1 §7 边界)。
_FP_NORM_RE = re.compile(
    r"[\s.,;:!?\u3001\u3002\uff0c\uff1b\uff1a\uff01\uff1f\u00b7\u2026"
    r"()\uff08\uff09\u300a\u300b\u201c\u201d\u2018\u2019'\"\u300c\u300d\u300e\u300f-]+")


def _fingerprint(text: str) -> str:
    """内容指纹:NFKC(全半角归一)+ lower + 去空白/标点后哈希(整体 review W3)。"""
    normalized = unicodedata.normalize("NFKC", text).strip().lower()
    normalized = _FP_NORM_RE.sub("", normalized)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def fetch_running() -> list[dict]:
    """ActivitySource fetch:进行中解析快照(只读,无副作用,0.4 §2.2 契约)。"""
    with _LOCK:
        return [{"entity_type": "ledger_parse", "entity_id": pid,
                 "project_id": pid, "entity_title": "台账解析",
                 "progress_text": "解析台账…"}
                for pid in sorted(_RUNNING)]


def on_compile_complete_sync(db, project_id: str, settings: dict) -> None:
    """同步入口(测试直调;task_queue 经 run_in_executor 后台化)。

    整体吞异常 —— 解析失败绝不影响编译主流程(3.1 §4.4);并发守卫 +
    finally 只清理自己占的坑(acquired 绑定清理责任,无条件 pop 会误删
    他方在途标记击穿单飞,与 profile_service 同修,review M-1 2026-10-25);
    占坑从 _run 上提到此 —— 占坑所有权与清理责任须同函数闭环。
    """
    acquired = False
    try:
        with _LOCK:
            if project_id in _RUNNING:
                logger.info("ledger parse for %s already running — skipped",
                            project_id)
                return                               # 并发守卫(3.1 §4.5)
            _RUNNING[project_id] = {"started_at": time.time()}
            acquired = True
        _run(db, project_id, settings)
    except Exception:
        logger.exception("ledger parse failed for %s", project_id)
    finally:
        if acquired:
            with _LOCK:
                _RUNNING.pop(project_id, None)


def _run(db, project_id: str, settings: dict) -> None:
    project = db.get_project(project_id)
    if not project:
        return
    schemas = db.get_wiki_schemas(project.get("type") or "general")
    ledger_keys = {s["page_key"] for s in schemas if s.get("is_ledger")}
    if not ledger_keys:
        return
    pages = [p for p in db.list_wiki_pages(project_id)
             if p["page_key"] in ledger_keys and (p.get("content") or "").strip()]
    if not pages:
        return
    # 2.10 §4.3: 用量环境（source=wiki / purpose=ledger_parse）——本函数运行于
    # run_in_executor 线程，contextvar 不跨线程传播，须在线程体内自行 push
    with push_context(UsageContext(
            source="wiki", purpose="ledger_parse", project_id=project_id)):
        entries = _parse_entries(pages, settings)        # raise → 上层吞,旧条目保持
    _store_entries(db, project_id, entries)
    try:
        generate_suggestions(db, project_id)
    except Exception:
        # 建议生成失败仅日志,不影响条目落库(3.1 §4.4);下次编译补上
        logger.exception("suggestion generation failed for %s", project_id)
    update_pulse_status(db, project_id, settings)    # 自身吞异常,失败保旧值


# ③④风险条目新近窗口:仅近 90 天风险出跟进建议(老风险无行动价值,3.1 §4.2③)
_RISK_WINDOW_DAYS = 90
# ④标题去前缀词:任务标题常带「完成/项目/公司」开头,比对前去掉
_TITLE_PREFIX_RE = re.compile(r"^(完成|项目|公司)+")


def generate_suggestions(db, project_id: str) -> int:
    """规则比对生成建议(无需 LLM,3.1 §4.2③)。返回新建条数。

    ④ milestones 条目 × open 里程碑任务:标题去前缀后任一 ≥2 连续子串命中
      条目内容(大小写不敏感)→ complete_task 建议(宽松召回,人来裁决);
    ③ risk_watchlist 近 90 天条目 → create_task 跟进建议;
    幂等:(entry_fingerprint, kind) 已存在任一状态记录 → 跳过
    (3.1 不变量 3:重放不重复,忽略过不再提)。
    """
    entries = db.list_ledger_entries(project_id, limit=200)
    tasks = db.list_phase_tasks(project_id)
    open_milestones = [t for t in tasks
                       if t["status"] == "open" and t["task_type"] == "milestone"]
    cutoff = (datetime.date.today()
              - datetime.timedelta(days=_RISK_WINDOW_DAYS)).isoformat()
    created = 0
    for e in entries:
        fp, content = e["fingerprint"], e["content"]
        if e["page_key"] == "milestones" and open_milestones:
            content_lower = content.lower()
            for t in open_milestones:
                if db.suggestion_exists(project_id, fp, "complete_task"):
                    break                                  # 该条目已出过④建议
                core = _TITLE_PREFIX_RE.sub("", t["title"]).strip().lower()
                if len(core) < 2:
                    continue
                hit = any(core[i:j] in content_lower
                          for i in range(len(core))
                          for j in range(i + 2, len(core) + 1))
                if not hit:
                    continue
                row = db.create_suggestion(
                    project_id, kind="complete_task", title=t["title"],
                    rationale=f"台账 {e.get('entry_date') or '近期'} 记录:{content[:40]}",
                    entry_fingerprint=fp, target_task_id=t["id"])
                if row:
                    created += 1
        elif e["page_key"] == "risk_watchlist":
            entry_date = e.get("entry_date") or ""
            if not entry_date or entry_date < cutoff:
                continue                                   # 老风险/无日期不提
            if db.suggestion_exists(project_id, fp, "create_task"):
                continue
            row = db.create_suggestion(
                project_id, kind="create_task",
                title=f"跟进风险:{content[:20]}",
                rationale=f"风险台账 {entry_date} 新增:{content[:60]}",
                entry_fingerprint=fp,
                suggested_phase="during", suggested_task_type="milestone")
            if row:
                created += 1
    return created


# ①AI 状态句素材:最近条目条数(3.1 §4.2④) + 输出上限(§7 决策 6:一句 ≤80 字)
_PULSE_ENTRY_COUNT = 10
_PULSE_MAX_CHARS = 80


def update_pulse_status(db, project_id: str, settings: dict) -> None:
    """①AI 状态句:最近条目摘要 + 发现项计数 → 轻模型一句 → 写 projects(3.1 §3.3)。

    失败仅日志保持旧值(§4.4);无素材显式清空(前端回落既有规则句,§5.2)。
    """
    try:
        entries = db.list_ledger_entries(project_id, limit=_PULSE_ENTRY_COUNT)
        # count_pending_reviews_by_project V2-E4 升级分型桶 {pid: {total, contradiction, gap}}——
        # 脉搏句只消费总数,解包 total 防 dict repr 拼进 prompt 污染 LLM 输入(CodeReview Blocker #1)
        reviews_bucket = db.count_pending_reviews_by_project().get(project_id) or {}
        reviews_count = int(reviews_bucket.get("total", 0))
        if not entries and not reviews_count:
            db.set_pulse_ai_status(project_id, "")   # 无素材:回落规则句,不浪费调用
            return
        # 2.10 §4.3: 用量环境（source=wiki / purpose=ledger_pulse，AI 状态句）——
        # 同为 executor 线程上下文，体内自行 push
        with push_context(UsageContext(
                source="wiki", purpose="ledger_pulse", project_id=project_id)):
            sentence = _pulse_sentence(entries, reviews_count, settings)
        if sentence:
            db.set_pulse_ai_status(project_id, sentence.strip()[:_PULSE_MAX_CHARS])
    except Exception:
        logger.exception("pulse ai status failed for %s", project_id)


def _pulse_sentence(entries: list[dict], reviews_count: int, settings: dict) -> str:
    """轻模型生成一句中文状态句(句式=台账动态+建议,§7 决策 6);
    routing 同 'ledger_parse'(未配专用模型则全局默认)。"""
    from llm import LLMClient
    from llm.routing import get_task_settings

    effective = get_task_settings(settings, "ledger_parse")
    lines = [f"- [{e.get('entry_date') or '?'}] {e['entry_type']}: {e['content']}"
             for e in entries]
    material = "\n".join(lines)
    reviews_note = f"\n另有 {reviews_count} 条待处理发现项。" if reviews_count else ""
    messages = [
        {"role": "system", "content":
            "你是项目助手。根据台账动态写一句不超过 80 字的中文状态句,"
            "句式:现状 + 一条建议。只输出这一句,不要解释、不要引号。"},
        {"role": "user", "content":
            f"最近台账动态:\n{material}{reviews_note}\n请写状态句。"},
    ]
    raw = asyncio.run(LLMClient(effective).chat_full(messages))["content"]
    # 空白输出 splitlines 为空列表,直接 [0] 会 IndexError(靠上层吞异常幸存的
    # 异常控制流,整体 review S2)→ 显式判空返回空句
    lines = (raw or "").strip().splitlines()
    return lines[0].strip() if lines else ""


def _store_entries(db, project_id: str, entries: list[dict]) -> None:
    """按页单事务删旧插新(3.1 §3.1:content 变则集合变,fingerprint 定锚幂等)。"""
    by_page: dict[str, list[dict]] = {}
    for e in entries:
        by_page.setdefault(e["page_key"], []).append(e)
    with db.conn.transaction():
        for page_key, items in by_page.items():
            db.conn.execute(
                "DELETE FROM ledger_entries WHERE project_id=? AND page_key=?",
                (project_id, page_key))
            for e in items:
                text = (e.get("text") or "").strip()
                if not text:
                    continue
                fp = _fingerprint(text)
                # id 含项目+页作用域(整体 review C1:纯 fp 全局主键会让跨项目同文本
                # 撞车 INSERT IGNORE → 后编译项目整页静默清空);同批同页重复文本
                # 同 id → INSERT OR IGNORE 仍兜住
                eid = f"le_{project_id}_{page_key}_{fp}"
                db.conn.execute(
                    "INSERT OR IGNORE INTO ledger_entries "
                    "(id,project_id,page_key,entry_date,entry_type,content,"
                    "source_file_id,fingerprint,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,NULL,?,datetime('now'),datetime('now'))",
                    (eid, project_id, page_key, e.get("entry_date"),
                     e.get("entry_type") or "milestone", text, fp))


def _parse_entries(pages: list[dict], settings: dict) -> list[dict]:
    """LLM 结构化解析(同步:executor 线程内 asyncio.run 包装 async client)。

    模型经 llm_routing 'ledger_parse' 路由(未配置则全局默认);输出 JSON
    数组经字段白名单清洗(page_key 白名单/日期格式/类型枚举/空文本)。
    v1 不提取 source_file_id(表列留 NULL,3.1 §3.1)。
    """
    from llm import LLMClient
    from llm.routing import get_task_settings

    effective = get_task_settings(settings, "ledger_parse")
    material = "\n\n".join(
        f"## {p['title']} (page_key={p['page_key']})\n{p['content'][:_MAX_PAGE_CHARS]}"
        for p in pages)
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _PROMPT_TEMPLATE.format(material=material)},
    ]
    raw = asyncio.run(LLMClient(effective).chat_full(messages))["content"]
    return _clean_entries(raw, {p["page_key"] for p in pages})


def _clean_entries(raw: str, allowed_keys: set[str]) -> list[dict]:
    """白名单清洗;整体解析失败抛异常(调用方兜,保持旧条目,3.1 §4.4)。"""
    m = re.search(r"\[.*\]", raw, re.S)
    if not m:
        raise ValueError("ledger parse: no JSON array in LLM output")
    data = json.loads(m.group(0))
    if not isinstance(data, list):
        raise ValueError("ledger parse: LLM output is not a JSON array")
    cleaned: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        page_key = item.get("page_key")
        text = (item.get("text") or "").strip()
        if page_key not in allowed_keys or not text:
            continue
        date = item.get("entry_date")
        if not (isinstance(date, str) and _DATE_RE.match(date.strip())):
            date = None
        etype = item.get("entry_type")
        if etype not in _VALID_TYPES:
            etype = "milestone"
        cleaned.append({"page_key": page_key, "entry_date": date,
                        "entry_type": etype, "text": text})
    return cleaned
