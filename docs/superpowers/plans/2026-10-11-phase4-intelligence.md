# Phase 4 Intelligence Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付 3.0 四期智能层:编译后结构化解析落库(ledger_entries)+ 任务建议(task_suggestions,③④)+ 脉搏带 AI 状态句(①)+ 前端四个消费位。

**Architecture:** 方案 A(编译完成直调钩子 → 后台 ledger_parse 长时活动 → 两新表 + projects 扩 2 列;界面全部毫秒级读表;零新增轮询)。详细设计:**docs/3.1intelligence-design.md**(本计划从它论证,表结构/数据流/交互映射以它为准)。

**Tech Stack:** FastAPI + sqlite(同步,`_LockedConnection`)/ Electron IPC 透传 / React 18 + TS + CSS Modules + vitest / pytest + pytest-asyncio。

**Spec:** docs/3.1intelligence-design.md

## Global Constraints

- 零新增轮询:全部读 = 挂载拉取 + `app:tasks-changed` / `useActivity('ledger_parse')` 边沿(0.4 §3.1)
- 解析失败绝不影响编译主流程:on_compile_complete 全体 try/except 仅记日志(3.1 §4.4)
- `wiki_pages.content` 是唯一事实源;任何代码不得写 ledger_entries 以外路径反推(3.1 不变量 1)
- 建议幂等:`(entry_fingerprint, kind)` 已存在任何记录 → 跳过生成(3.1 不变量 3)
- Python:`C:/Users/Kelvin/.conda/envs/inkcube/python.exe`;测试 `python/tests/`,文件名 `test_<module>.py`
- 前端测试与被测文件同目录惯例不适用(本仓 tests/ 顶层),命名 `<Component>.test.tsx`;`npx vitest run` 不用,用 `node node_modules/vitest/vitest.mjs run <file>`
- i18n:UI 文案双语进 zh-CN/en-US 两 locale,AI 生成内容(状态句/rationale)固定中文
- 日志走 `logging`(RotatingFileHandler),禁 print
- 禁 git 操作;每任务完成后跑定向测试,阶段完成后全量回归
- PowerShell 坑:`$` 变量被剥、内联 python -c 解析失败 → 复杂逻辑写临时 py 脚本执行,用后删除

---

### Task 1: DB 层 — 两新表 + projects 扩 2 列 + wiki_schemas.is_ledger

**Files:**
- Modify: `python/db/database.py`(_create_tables executescript 段 + 迁移段 + seed_wiki_schemas 的 UPSERT 列)
- Modify: `python/db/wiki_seed.py`(各 seed dict 加 `is_ledger` 键)
- Test: `python/tests/test_ledger_schema.py`

**Interfaces(产出,后续任务依赖):**
- 表 `ledger_entries` / `task_suggestions`(DDL = 3.1 §3.1/§3.2,含 UNIQUE 约束与索引)
- 列 `projects.pulse_ai_status TEXT DEFAULT ''`、`projects.pulse_ai_status_at TEXT`、`wiki_schemas.is_ledger INTEGER DEFAULT 0`
- `seed_wiki_schemas()` 落库时写入 is_ledger(台账页=1)

- [ ] **Step 1 写失败测试** `python/tests/test_ledger_schema.py`:

```python
"""四期 T1:schema 就位 — 两新表/三新列/seed 标注(3.1 §3)。"""
import sqlite3


def test_tables_and_columns_exist(db):
    cur = db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('ledger_entries','task_suggestions')")
    assert {r["name"] for r in cur.fetchall()} == {"ledger_entries", "task_suggestions"}
    for table, col in [("projects", "pulse_ai_status"), ("projects", "pulse_ai_status_at"),
                       ("wiki_schemas", "is_ledger")]:
        cols = {r["name"] for r in db.conn.execute(f"PRAGMA table_info({table})").fetchall()}
        assert col in cols, f"{table}.{col} missing"


def test_unique_and_index(db):
    idx = {r["name"] for r in db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    assert "idx_ledger_entries_project" in idx
    assert "idx_task_suggestions_project" in idx


def test_seed_marks_ledger_pages(db):
    # seed 调用形态以现状为准:若 conftest 建库已自动 seed(如 main.py 启动同款)直接查;
    # 否则按其签名 db.seed_wiki_schemas(get_preset_schemas()) 调用(wiki_seed.get_preset_schemas 已存在)
    rows = db.conn.execute(
        "SELECT page_key, is_ledger FROM wiki_schemas WHERE project_type='recruitment'").fetchall()
    marked = {r["page_key"] for r in rows if r["is_ledger"]}
    assert {"milestones", "risk_watchlist", "policy_ledger", "operation_monitor", "service_log"} <= marked
    assert "enterprise_profile" not in marked          # 画像页不标
```

> `db` fixture 用既有 conftest 模式(参照 `tests/test_affairs_service.py` 头部的建库 fixture;若 conftest 已提供同名 fixture 则直接用)。`seed_wiki_schemas()` 无参调用形态以现有签名为准(conftest 里既有测试已这么用则保持)。

- [ ] **Step 2 跑失败**:`python -m pytest python/tests/test_ledger_schema.py -v` → FAIL(表/列不存在)
- [ ] **Step 3 实现**:
  - `_create_tables` 的 executescript 中,在 `phase_tasks` 建表语句之后追加 3.1 §3.1/§3.2 两段 DDL(逐字照抄,含两条 CREATE INDEX)
  - 迁移段(`wiki_pages.page_type` 迁移行旁)追加三行:`self._migrate_add_column("projects", "pulse_ai_status", "TEXT DEFAULT ''")` / `self._migrate_add_column("projects", "pulse_ai_status_at", "TEXT")` / `self._migrate_add_column("wiki_schemas", "is_ledger", "INTEGER DEFAULT 0")`
  - `seed_wiki_schemas()` 的 INSERT 列清单与占位符、UPSERT 更新列各加 `is_ledger`;值取 `p.get("is_ledger", 0)`
  - `wiki_seed.py`:监控页 dict 加 `"is_ledger": 1`(recruitment:milestones/risk_watchlist/policy_ledger/operation_monitor/service_log;investment:financial_monitor/legal_risk/milestone_tracker/risk_summary/exit_path/deal_terms;general:对应监控页),画像页加 `"is_ledger": 0`
- [ ] **Step 4 跑绿**:定向 3 passed;`python -m pytest python/tests -q` 全量无回归(建库 fixture 会重建 schema,存量测试应全绿)
- [ ] **Step 5 提交建议**:`feat(db): ledger_entries + task_suggestions + pulse_ai_status + schema is_ledger (3.0 P4 T1)`

### Task 2: ledger_service — 解析落库 + 长时活动

**Files:**
- Create: `python/wiki/ledger_service.py`
- Modify: `python/knowledge/task_queue.py`(L580 `activity_events.complete_compile(self.db, task_id)` 之后加直调;该函数作用域内已有 `project_id`)
- Modify: 活动源装配处(grep `activities_registry.register(` 定位现有源注册文件,同处追加 ledger_parse 源)
- Test: `python/tests/test_ledger_service.py`

**Interfaces:**
- 消费:`database.list_wiki_pages(project_id)`(返回含 page_key/content/page_type)、`database.get_project(project_id)`(取 kind/project_type)、`database.get_wiki_schemas(project_type)`(取 is_ledger)
- 产出:`on_compile_complete(db, project_id, settings)`(同步入口,内部 `asyncio.get_event_loop` 不假设——编译 worker 是 async 上下文,直接 `asyncio.create_task` 或按 task_queue 现有 async 形态定,实施时读 L540-581 确认)
- LLM 调用形态:对齐 `python/wiki/ingest.py` 的 `_chat_with_retry`(先读 L150-200 与 `llm/client.py` 的公开 chat 函数,复用同一通道;settings 经 `wiki/llm_routing.py get_task_settings(settings, 'ledger_parse')` 路由)

- [ ] **Step 1 写失败测试**(mock LLM 函数,不联网):

```python
"""四期 T2:编译后解析落库 — 范围选取/幂等落库/活动注册/失败不删旧(3.1 §3.1/§4.2)。"""
import pytest
from wiki import ledger_service


@pytest.fixture
def ledger_project(db):
    pid = db.create_project("p-ledger", kind="project", project_type="recruitment")
    db.create_or_update_wiki_page(pid, "milestones", "项目里程碑台账",
                                  content="- 2026-09 | 项目正式投产 _(来源: 9月月报)_")
    return pid


def test_scope_only_ledger_pages(db, ledger_project, monkeypatch):
    db.create_or_update_wiki_page(ledger_project, "enterprise_profile", "画像", content="x")
    captured = {}
    monkeypatch.setattr(ledger_service, "_parse_entries",
                        lambda pages, settings: captured.update(pages=pages) or [])
    ledger_service.on_compile_complete(db, ledger_project, settings={})
    assert [p["page_key"] for p in captured["pages"]] == ["milestones"]   # 画像页不解析


def test_entries_replaced_atomically(db, ledger_project, monkeypatch):
    monkeypatch.setattr(ledger_service, "_parse_entries", lambda pages, settings: [
        {"page_key": "milestones", "entry_date": "2026-09", "entry_type": "milestone",
         "text": "项目正式投产"}])
    ledger_service.on_compile_complete(db, ledger_project, settings={})
    rows = db.list_ledger_entries(ledger_project, limit=10)
    assert len(rows) == 1 and rows[0]["content"].startswith("项目正式投产")
    # 第二次解析产出不同条目 → 删旧插新,不留旧集
    monkeypatch.setattr(ledger_service, "_parse_entries", lambda pages, settings: [
        {"page_key": "milestones", "entry_date": "2026-10", "entry_type": "milestone",
         "text": "一期设备全部进场"}])
    ledger_service.on_compile_complete(db, ledger_project, settings={})
    rows = db.list_ledger_entries(ledger_project, limit=10)
    assert len(rows) == 1 and rows[0]["entry_date"] == "2026-10"


def test_parse_failure_keeps_old_entries(db, ledger_project, monkeypatch):
    monkeypatch.setattr(ledger_service, "_parse_entries", lambda pages, settings: [
        {"page_key": "milestones", "entry_date": "2026-09", "entry_type": "milestone",
         "text": "项目正式投产"}])
    ledger_service.on_compile_complete(db, ledger_project, settings={})
    def boom(pages, settings):
        raise RuntimeError("llm down")
    monkeypatch.setattr(ledger_service, "_parse_entries", boom)
    ledger_service.on_compile_complete(db, ledger_project, settings={})   # 不抛
    assert len(db.list_ledger_entries(ledger_project, limit=10)) == 1     # 旧条目保持


def test_activity_registered_and_cleared(db, ledger_project, monkeypatch):
    monkeypatch.setattr(ledger_service, "_parse_entries", lambda pages, settings: [])
    ledger_service.on_compile_complete_sync(db, ledger_project, settings={})
    from activities_registry import snapshot
    assert all(i["entity_type"] != "ledger_parse" for i in snapshot())    # 终态后无残留
```

> `db.create_project` / `create_or_update_wiki_page` 签名以 database.py 实际为准(实施者先 grep 现有测试用法)。`on_compile_complete_sync` 为同步包装(测试直接驱动);task_queue 直调用异步形态。

- [ ] **Step 2 跑失败** → ImportError(模块不存在)
- [ ] **Step 3 实现** `python/wiki/ledger_service.py`:

```python
"""wiki/ledger_service.py — 四期智能层:编译后台账结构化解析 + 建议生成 + AI 状态句(3.1)。"""
import hashlib
import json
import logging
import threading

logger = logging.getLogger(__name__)
_RUNNING: dict[str, dict] = {}          # project_id -> {started_at};进程内存态(3.1 不变量 4)
_LOCK = threading.Lock()


def fetch_running() -> list[dict]:
    """ActivitySource fetch:进行中解析快照(只读,无副作用)。"""
    with _LOCK:
        return [{"entity_type": "ledger_parse", "project_id": pid,
                 "entity_title": "台账解析", "progress_text": "解析台账…"}
                for pid in sorted(_RUNNING)]


def on_compile_complete_sync(db, project_id: str, settings: dict) -> None:
    """同步驱动(测试/直调);整体吞异常 — 解析失败绝不影响编译主流程(3.1 §4.4)。"""
    try:
        _run(db, project_id, settings)
    except Exception:
        logger.exception("ledger parse failed for %s", project_id)
    finally:
        with _LOCK:
            _RUNNING.pop(project_id, None)


def _run(db, project_id: str, settings: dict) -> None:
    with _LOCK:
        if project_id in _RUNNING:
            return                                   # 并发守卫:进行中即跳过(3.1 §4.5)
        import time
        _RUNNING[project_id] = {"started_at": time.time()}
    project = db.get_project(project_id)
    schemas = db.get_wiki_schemas(project.get("project_type") or project.get("kind") or "general")
    ledger_keys = {s["page_key"] for s in schemas if s.get("is_ledger")}
    pages = [p for p in db.list_wiki_pages(project_id)
             if p["page_key"] in ledger_keys and (p.get("content") or "").strip()]
    entries = _parse_entries(pages, settings)        # LLM;raise → 上层吞
    _store_entries(db, project_id, entries)


def _parse_entries(pages: list[dict], settings: dict) -> list[dict]:
    """LLM 结构化解析。prompt 要求输出 JSON 数组 [{page_key,entry_date,entry_type,text}];
    entry_date 尽力 YYYY-MM-DD/YYYY-MM,解析不出 null。调用通道对齐 ingest.py _chat_with_retry
    (先读该函数与 llm/client.py 公开接口;模型经 llm_routing.get_task_settings(settings,'ledger_parse'))。
    输出经 json.loads + 字段白名单清洗;解析失败抛异常由调用方兜。"""
    ...  # 按 ingest 形态实现;v1 不提取 source_file_id(表列留 NULL,3.1 §3.1)


def _store_entries(db, project_id: str, entries: list[dict]) -> None:
    by_page: dict[str, list[dict]] = {}
    for e in entries:
        by_page.setdefault(e["page_key"], []).append(e)
    with db.conn:                                    # 每页单事务删旧插新(3.1 §3.1 写行为)
        for page_key, items in by_page.items():
            db.conn.execute(
                "DELETE FROM ledger_entries WHERE project_id=? AND page_key=?",
                (project_id, page_key))
            for e in items:
                fp = hashlib.sha256(e["text"].strip().encode()).hexdigest()[:16]
                db.conn.execute(
                    "INSERT OR IGNORE INTO ledger_entries (id,project_id,page_key,entry_date,"
                    "entry_type,content,source_file_id,fingerprint,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))",
                    (f"le_{fp}", project_id, page_key, e.get("entry_date"),
                     e.get("entry_type") or "milestone", e["text"].strip(), None, fp))
```

- db 层新增(同文件底部或 phase_tasks 区旁):`list_ledger_entries(project_id, limit=20)`(entry_date 倒序,NULL 置尾:`ORDER BY (entry_date IS NULL), entry_date DESC`)、`latest_ledger_entries()`(跨项目每项目最新 1 条,JOIN projects 出 project_name)
- 活动源装配:grep `activities_registry.register(` 现有调用处,追加 `FunctionSource("ledger_parse", lambda: __import__("wiki.ledger_service", fromlist=["x"]).fetch_running())`(按该文件既有 import 风格写)
- task_queue L580 后追加:`ledger_service.on_compile_complete_sync(self.db, project_id, self.settings)`(import 放模块顶部;sync 形态在本 task 内不阻塞 worker——若 L580 处于 async 函数且解析要后台化,用 `asyncio.get_running_loop().run_in_executor(None, ...)` 包装,实施时按上下文选,注释说明选择)
- [ ] **Step 4 跑绿**:定向 4 passed;node 不涉及
- [ ] **Step 5 提交建议**:`feat(wiki): ledger parse service + activity source (3.0 P4 T2)`

### Task 3: 建议生成(③风险跟进 / ④里程碑比对)

**Files:**
- Modify: `python/wiki/ledger_service.py`(_run 内条目落库后调 `_generate_suggestions`)
- Modify: `python/db/database.py`(suggestions CRUD 6 方法,见 Interfaces)
- Test: `python/tests/test_task_suggestions.py`

**Interfaces(db 层,后续任务依赖):**
- `list_suggestions(project_id, status="pending") -> list[dict]`
- `get_suggestion(suggestion_id)` / `create_suggestion(**cols) -> dict|None`(UNIQUE 冲突返回 None)
- `decide_suggestion(suggestion_id, status, accepted_task_id=None) -> dict`(置 decided_at)
- `suggestion_exists(fingerprint, kind, statuses) -> bool`(生成幂等查询)

- [ ] **Step 1 写失败测试**:

```python
"""四期 T3:建议生成 — ④里程碑比对/③风险跟进/幂等与不再提(3.1 §3.2/§4.2③)。"""
import json

from wiki import ledger_service


def _seed_entry(db, pid, page_key, date, text, etype):
    import hashlib
    fp = hashlib.sha256(text.encode()).hexdigest()[:16]
    db.conn.execute(
        "INSERT INTO ledger_entries (id,project_id,page_key,entry_date,entry_type,content,"
        "source_file_id,fingerprint,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))",
        (f"le_{fp}", pid, page_key, date, etype, text, None, fp))
    db.conn.commit()
    return fp


def _seed_milestone_task(db, pid, title):
    db.create_phase_task(project_id=pid, phase="during", task_type="milestone",
                         title=title, description="")


def test_milestone_match_creates_complete_task_suggestion(db):
    pid = db.create_project("p-s1", kind="project", project_type="recruitment")
    _seed_milestone_task(db, pid, "完成设备进场与投产")
    fp = _seed_entry(db, pid, "milestones", "2026-09", "项目正式投产,设备全部进场", "milestone")
    ledger_service.generate_suggestions(db, pid)
    rows = db.list_suggestions(pid, "pending")
    assert len(rows) == 1 and rows[0]["kind"] == "complete_task"
    assert rows[0]["entry_fingerprint"] == fp and rows[0]["target_task_id"]


def test_risk_entry_creates_create_task_suggestion(db):
    pid = db.create_project("p-s2", kind="project", project_type="recruitment")
    import datetime
    recent = (datetime.date.today() - datetime.timedelta(days=5)).isoformat()
    _seed_entry(db, pid, "risk_watchlist", recent, "企业被列入经营异常名录", "risk")
    ledger_service.generate_suggestions(db, pid)
    rows = db.list_suggestions(pid, "pending")
    assert len(rows) == 1 and rows[0]["kind"] == "create_task"
    assert "风险" in rows[0]["title"] or "跟进" in rows[0]["title"]


def test_old_risk_entry_ignored(db):
    pid = db.create_project("p-s3", kind="project", project_type="recruitment")
    _seed_entry(db, pid, "risk_watchlist", "2020-01-01", "历史风险", "risk")
    ledger_service.generate_suggestions(db, pid)
    assert db.list_suggestions(pid, "pending") == []


def test_idempotent_and_dismissed_never_regenerated(db):
    pid = db.create_project("p-s4", kind="project", project_type="recruitment")
    _seed_milestone_task(db, pid, "完成设备进场与投产")
    fp = _seed_entry(db, pid, "milestones", "2026-09", "项目正式投产,设备全部进场", "milestone")
    ledger_service.generate_suggestions(db, pid)
    ledger_service.generate_suggestions(db, pid)          # 重放:pending 已存在 → 不重复
    assert len(db.list_suggestions(pid, "pending")) == 1
    sid = db.list_suggestions(pid, "pending")[0]["id"]
    db.decide_suggestion(sid, "dismissed")
    ledger_service.generate_suggestions(db, pid)          # dismissed → 永不再提
    assert db.list_suggestions(pid, "pending") == []
```

- [ ] **Step 2 跑失败** → AttributeError(generate_suggestions 不存在)
- [ ] **Step 3 实现**:
  - `_run` 中 `_store_entries` 之后调 `generate_suggestions(db, project_id)`(建议生成失败仅日志,不中断——3.1 §4.4)
  - `generate_suggestions(db, project_id)`:
    - ④:`entries = db.list_ledger_entries(project_id, limit=200)` 中 `page_key=='milestones'` 条目 × `db.list_phase_tasks(project_id)` 中 `status=='open' and task_type=='milestone'`;匹配规则 = 任务标题去前缀词(「完成」「项目」「公司」)后的任一长度≥2 连续子串出现在条目 content(大小写不敏感)→ `create_suggestion(kind='complete_task', title=任务标题, rationale=f"台账 {entry_date} 记录:{content[:40]}", entry_fingerprint=fp, target_task_id=任务id)`
    - ③:`page_key=='risk_watchlist'` 且 `entry_date >= (today-90d)` 的条目 → `create_suggestion(kind='create_task', title=f"跟进风险:{content[:20]}", suggested_phase='during', suggested_task_type='milestone', rationale=...)`
    - 每条生成前先 `suggestion_exists(fp, kind, ('pending','accepted','dismissed'))` → 存在即跳过(幂等+不再提)
- [ ] **Step 4 跑绿**:定向 4 passed;Task 2 的 4 用例回归(条目落库后现在会触发建议生成——`test_entries_replaced_atomically` 断言不受影响即可,若 _RUNNING 状态串扰则拆 fixture)
- [ ] **Step 5 提交建议**:`feat(wiki): task suggestion generation rules (3.0 P4 T3)`

### Task 4: AI 状态句(①)

**Files:**
- Modify: `python/wiki/ledger_service.py`(_run 尾部 `_update_pulse_status`)
- Test: `python/tests/test_pulse_ai_status.py`

**Interfaces:** `UPDATE projects SET pulse_ai_status=?, pulse_ai_status_at=?`(db 加 `set_pulse_ai_status(project_id, status)`);读取端零新请求(projects 行随 loadProjects 流出)。

- [ ] **Step 1 写失败测试**(mock `_pulse_sentence`):

```python
"""四期 T4:AI 状态句 — 写入/失败保持旧值(3.1 §3.3/§4.4)。"""
from wiki import ledger_service


def test_status_written(db):
    pid = db.create_project("p-a1", kind="project", project_type="recruitment")
    monkey_llm = lambda *a, **k: "投产 3 个月,本周新增行政处罚,建议拜访时确认整改。"
    ledger_service._pulse_sentence = monkey_llm
    ledger_service.update_pulse_status(db, pid, settings={})
    row = db.get_project(pid)
    assert row["pulse_ai_status"].startswith("投产 3 个月") and row["pulse_ai_status_at"]


def test_failure_keeps_old(db):
    pid = db.create_project("p-a2", kind="project", project_type="recruitment")
    db.set_pulse_ai_status(pid, "旧句")
    def boom(*a, **k):
        raise RuntimeError("down")
    ledger_service._pulse_sentence = boom
    ledger_service.update_pulse_status(db, pid, settings={})   # 吞异常
    assert db.get_project(pid)["pulse_ai_status"] == "旧句"
```

- [ ] **Step 2 跑失败 → Step 3 实现**:`update_pulse_status(db, project_id, settings)`:取最近 10 条 entries + wiki_reviews 未裁决计数,拼中文 prompt(`_pulse_sentence` 内部走 LLM,routing 'ledger_parse' 同模型;输出限一句 ≤80 字,中文,§7 决策 6)→ `db.set_pulse_status`;异常仅日志。`_run` 尾部调用。
- [ ] **Step 4 跑绿**:定向 2 passed + T2/T3 回归
- [ ] **Step 5 提交建议**:`feat(wiki): pulse ai status via lightweight model (3.0 P4 T4)`

### Task 5: 读端点 + 采纳/忽略端点 + IPC

**Files:**
- Modify: `python/db/database.py`(Task 2/3 已加读方法;本任务补 `get_suggestion`)
- Modify: `python/routers/wiki.py`(两 GET;**`/ledger/latest` 声明在 `/{project_id}/...` 之前**,affairs.py `/due` 同款防遮蔽)
- Modify: `python/routers/affairs.py`(GET suggestions + POST accept/dismiss)
- Modify: `electron/ipc-handlers.js`(5 通道:wiki:listLedgerEntries / ledger:listLatest / affairs:listSuggestions / affairs:acceptSuggestion / affairs:dismissSuggestion;affairs:createTask 同款透传形态)
- Test: `python/tests/test_ledger_api.py`;`node --check electron/ipc-handlers.js`

**Interfaces:**
- `GET /projects/{id}/ledger/entries?limit=20`(1-100 校验,越界 422)→ `{"entries":[...]}`
- `GET /ledger/latest` → `{"latest":[{project_id, project_name, page_key, entry_date, entry_type, content}]}`
- `GET /projects/{id}/suggestions?status=pending` → `{"suggestions":[...]}`
- `POST .../suggestions/{sid}/accept`:create_task → `db.create_phase_task(...)` 回填 accepted_task_id,返回 `{"task":...}`;complete_task → 校验目标存在且 open(`db.get_phase_task`),PATCH done,返回 `{"task":...}`;目标失效 → 404 `{"detail":"target_task_missing"}` **且建议已转 dismissed**
- `POST .../suggestions/{sid}/dismiss` → `{"suggestion":...}`
- IPC 命名/透传形态照抄 affairs:listDue 三期实现(L1595-1679 区段)

- [ ] **Step 1 写失败测试**(TestClient,参照 `tests/test_affairs_api.py` 的 fixture 与头注):
  - entries:空项目 200 `{"entries":[]}`;limit=0 → 422;插入 2 条 → 倒序返回
  - latest:两项目各 1 条 → 各返回最新 1 条含 project_name
  - suggestions 列表/accept create_task(断言 phase_tasks 新增+accepted_task_id 回填)/accept complete_task 目标已删 → 404 且建议转 dismissed/dismiss → status=dismissed
- [ ] **Step 2 跑失败 → Step 3 实现**(affairs.py accept 逻辑):

```python
@router.post("/{project_id}/suggestions/{suggestion_id}/accept")
async def accept_suggestion(project_id: str, suggestion_id: str):
    db = _db()
    sug = db.get_suggestion(suggestion_id)
    if not sug or sug["project_id"] != project_id or sug["status"] != "pending":
        raise HTTPException(status_code=404, detail="suggestion not found")
    if sug["kind"] == "create_task":
        task = db.create_phase_task(
            project_id=project_id, phase=sug["suggested_phase"] or "during",
            task_type=sug["suggested_task_type"] or "milestone",
            title=sug["title"], description=sug["description"] or "")
        db.decide_suggestion(suggestion_id, "accepted", accepted_task_id=task["id"])
        return {"task": task}
    target = db.get_phase_task(sug["target_task_id"] or "")
    if not target or target["project_id"] != project_id or target["status"] != "open":
        db.decide_suggestion(suggestion_id, "dismissed")       # 失效 → 永不再提(3.1 §4.4)
        raise HTTPException(status_code=404, detail="target_task_missing")
    task = db.update_phase_task(target["id"], {"status": "done"})
    db.decide_suggestion(suggestion_id, "accepted", accepted_task_id=target["id"])
    return {"task": task}
```

- [ ] **Step 4 跑绿**:定向 pytest passed;`node --check electron/ipc-handlers.js` OK
- [ ] **Step 5 提交建议**:`feat(api): ledger/suggestions endpoints + ipc channels (3.0 P4 T5)`

### Task 6: 前端 PulseBar — AI 状态句 + 建议 chip

**Files:**
- Modify: `src/features/project/PulseBar.tsx`(props +`aiStatus?: string` +`suggestionCount?: number`;句位三元最前:`aiStatus ? aiStatus : <既有规则句链>`;chips 数组 dueTotal 之前加 suggestion chip → `onOpenAffairs()`)
- Modify: `src/features/project/ProjectDetailPage.tsx`(①挂载拉 `affairs:listSuggestions` 计数 + `app:tasks-changed` 边沿重拉——复用三期 S2 的事件监听模式;②`pulse_ai_status` 从 project prop 直传;③`useActivity('ledger_parse')` 活动从有到无边沿 → 触发既有 projects 刷新回调(新 AI 句浮现,3.1 §4.3))
- Modify: 两 locale(pulse 段加 `chipSuggestions`:"建议"/"Suggestions")
- Test: `tests/PulseBar.test.tsx`(追加 describe)+ `tests/ProjectDetailPage.test.tsx`(如该文件存在计数拉取断言则追加;无则 PulseBar 侧覆盖)

- [ ] **Step 1 写失败测试**(PulseBar.test 追加,mock 惯例同文件现有 describe):

```tsx
describe('PulseBar ai status + suggestions (3.0 P4)', () => {
  it('renders ai sentence when present, chips unchanged', () => {
    const { container } = render(<PulseBar {...base} aiStatus="已投产 3 个月,建议确认整改。"
      reviews={{ contradiction: 1, gap: 0 }} />)
    expect(container.textContent).toContain('已投产 3 个月')
    expect(container.textContent).not.toContain('pulse.sentenceContradiction')
  })
  it('falls back to rule sentence when aiStatus empty', () => {
    const { container } = render(<PulseBar {...base} reviews={{ contradiction: 2, gap: 0 }} />)
    expect(container.textContent).toContain('pulse.sentenceContradiction')
  })
  it('shows suggestion chip with count and opens affairs', () => {
    const onOpenAffairs = vi.fn()
    const { getByText } = render(<PulseBar {...base} suggestionCount={2} onOpenAffairs={onOpenAffairs} />)
    fireEvent.click(getByText(/pulse\.chipSuggestions/).closest('button')!)
    expect(onOpenAffairs).toHaveBeenCalled()
  })
  it('hides suggestion chip when zero', () => {
    const { container } = render(<PulseBar {...base} suggestionCount={0} />)
    expect(container.textContent).not.toContain('pulse.chipSuggestions')
  })
})
```

- [ ] **Step 2 跑失败 → Step 3 实现(按上方 Files 描述)→ Step 4 定向绿 + tsc**
- [ ] **Step 5 提交建议**:`feat(pulse): ai status sentence + suggestion chip (3.0 P4 T6)`

### Task 7: WikiHomePage 时间轴卡

**Files:**
- Modify: `src/features/project/tabs/WikiHomePage.tsx`(props +`ledgerEntries?: LedgerEntry[]` +`ledgerParsing?: boolean`;健康卡区块之后插时间轴卡:空数组/undefined 不渲染;卡头 `ledgerParsing && 「解析中…」`灰字)
- Modify: `src/features/project/tabs/WikiTab.tsx`(挂载拉 `wiki:listLedgerEntries`;`useActivity('ledger_parse')` items 含本项目 → ledgerParsing)
- Modify: `src/types.ts` 或 affairs/types 旁(LedgerEntry 接口:`{id,page_key,entry_date,entry_type,content}`)
- Modify: 两 locale(`wiki.timeline.*`:title/empty/parsing)
- Test: `tests/WikiHomePage.test.tsx`(追加 3 用例:空不渲染/渲染分组+点击 onNavigateToPage(page_key)/parsing 显示解析中且旧条目照常)

- [ ] 实现要点:分组 = `reduce` 按 `entry_date ?? '—'` 归并,组内保持倒序;条目行 = 日期 | `pages` 中按 page_key 找标题(找不到显示 page_key)| content 一行截断;点击行调 `onNavigateToPage(page_key)`(该 prop 已存在)
- [ ] TDD 步骤同前(红→实现→绿+tsc);提交建议:`feat(wiki): ledger timeline card (3.0 P4 T7)`

### Task 8: AffairsTab 建议卡

**Files:**
- Create: `src/features/project/affairs/SuggestionCard.tsx` + `.module.css`
- Modify: `src/features/project/affairs/AffairsTab.tsx`(视图顶部渲染;数据:挂载+`refreshSignal` 变化时拉 `affairs:listSuggestions`;[采纳] invoke `affairs:acceptSuggestion` → 成功 dispatch `app:tasks-changed` + 本地移除 + 重拉;catch 404 target_task_missing → Toast「任务已不存在,建议已失效」+ 移除;[忽略] invoke dismiss → 移除。Toast 走项目既有 useToast)
- Modify: `tests/AffairsTab.test.tsx`(TaskDrawer 探针旁加 SuggestionCard 探针或直测 SuggestionCard 新文件;建议后者:新建 `tests/SuggestionCard.test.tsx`,mock invoke,4 用例:pending 列表渲染/采纳 create_task 成功派发事件/采纳 404 显示失效 Toast/忽略移除)
- Modify: 两 locale(`affairs.suggestion.*`:title/accept/dismiss/invalidated/acceptedToast)

- [ ] 关键交互不变量:建议 decided 后从 pending 消失;采纳 complete_task 成功后既有任务域刷新链路自动生效(dispatch app:tasks-changed,三期已铺)
- [ ] TDD;提交建议:`feat(affairs): suggestion card with accept/dismiss (3.0 P4 T8)`

### Task 9: ProjectCardWall 摘要行 + 下一事务名

**Files:**
- Modify: `src/features/home/ProjectCardWall.tsx`(props +`latestEntryByProject?: Record<string, LatestEntry>`;每卡下一事务行下追加摘要行,空不渲染;**顺带补齐下一事务名**:props `nextDueByProject` 值形状 `string|null` → `{date: string|null, title: string|null}`,渲染「MM-DD · 上传BP(逾期 3 天)」形态)
- Modify: `src/App.tsx`(快照块:挂载拉 `ledger:listLatest` → state;`useActivity('ledger_parse')` 活动从有到无边沿重拉;nextDue 派生处补 title——数据已在 dueData)
- Modify: `src/features/home/HomePage.tsx`(透传)
- Modify: 两 locale(`home.entry.*`)
- Test: `tests/ProjectCardWall.test.tsx`(追加:摘要行渲染/空不渲染/下一事务名+逾期文案);`tests/HomePage.test.tsx` / `tests/App.test` 若因 prop 形状变更受影响则同步修正(先跑全量定位)

- [ ] TDD;提交建议:`feat(home): ledger entry line + due title on cards (3.0 P4 T9)`

### Task 10: 设置页模型项 + Schema 台账开关

**Files:**
- Modify: 设置页模型设置组件(grep `wiki_dedup_model` 的 UI 消费处,同列表加 `wiki_ledger_parse_model`「台账解析模型」,含「跟随默认」空值语义)
- Modify: Schema 管理视图(grep `schemaCompileHint` 消费处定位编辑表单;加「台账页(监控式,只追加)」checkbox;保存时并入 IPC 字段 `isLedger` → ipc-handlers.js 对应 create/updateSchema 通道加透传 → routers/wiki.py schema 端点 + `db.create_wiki_schema`/`update_wiki_schema` 加参数)
- Modify: 两 locale
- Test: 设置页/Schema 表单对应既有测试文件追加用例(先 grep 测试文件名定位);Python 侧 `test_ledger_schema.py` 补 1 用例:create_wiki_schema(is_ledger=True) 落库读回

- [ ] TDD;提交建议:`feat(settings): ledger_parse model + schema ledger flag (3.0 P4 T10)`

### Task 11: 全量回归 + 文档回改 + ledger

- [ ] `node node_modules/typescript/lib/tsc.js --noEmit` 零错误
- [ ] `node node_modules/vitest/vitest.mjs run` 全量(基线 48 files/317 passed,只增不减)
- [ ] `python -m pytest python/tests -q` 全量(基线 958+1)
- [ ] 交付核对清单:0.4 §3.1 零新增轮询 ✓;新增 i18n 键双语一致 ✓;新图标走 icons.tsx 中央注册 ✓;解析失败不影响编译主流程(测试已证)✓
- [ ] 文档回改:3.0 §5.1 通道清单(+5)/§6.2 组件清单(+SuggestionCard 等)/§8 四期行「✅ 四期已交付(日期)」;3.1 头部状态改「已交付」;2.6 §4 补 is_ledger 列说明;0.4 §3.1 若列活动种类则补 ledger_parse
- [ ] ledger:`.superpowers/sdd/2026-10-11-phase4-intelligence/progress.md` 逐任务记录(模式对齐三期 ledger)
