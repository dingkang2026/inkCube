# 二期:时期任务模型 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 3.0 二期「时期任务模型」——playbook_templates/phase_tasks 双表 + 46 条种子 + 创建挂钩/存量回填 + 事务 Tab 三视图(时期看板/周期节奏/日历)+ 执行抽屉 + CRUD + material/report 完成闭环事件 + 周期滚动生成器 + 脉搏带应办 chip + 知识 Tab 顶栏待编译通知合并。

**Architecture:** 后端新增 `python/db/playbook_seed.py`(种子数据,对齐 wiki_seed.py)、`python/affairs_service.py`(服务层:滚动/激活/事件匹配,模块级函数对齐 activity_events.py 惯例)、`python/routers/affairs.py`(HTTP 路由挂 `/projects/{id}/tasks`);事件消费按 3.0 §5.4 实施裁决在终态声明点旁**直调**(pipeline.py complete_file 后 / reports.py complete_report 后),滚动生成器 = lifespan 启动 roll + `/activities/status` 入口 check-date roll(不新增轮询端点);创建挂钩 = `db.create_project` 同事务铺入。前端新增 `src/features/project/affairs/`(AffairsTab 容器 + PhaseBoard + RhythmCards + CalendarView + TaskFormModal + TaskDrawer),PulseBar 扩展应办 chip 与折叠态逾期徽记。

**Tech Stack:** Python 3 + sqlite3 + FastAPI(后端);React 18 + TypeScript + react-i18next + vitest/@testing-library + CSS Modules;Lucide 图标(`CalendarDays`/`Plus` 本期注册)。

**Spec:** [docs/3.0project-lifecycle-design.md](../../3.0project-lifecycle-design.md) §3(时期任务模型/种子清单/节奏规则)、§4(表结构)、§5(数据流 + §5.4 实施裁决表)、§6.1.3-6.1.7(事务 Tab 交互)、§6.3(状态映射)、§6.4(不变量)、§8(二期范围)、§10(决策);交互原型 [prototypes/3.0-project-lifecycle-prototype.html](../../prototypes/3.0-project-lifecycle-prototype.html)。

## Global Constraints

- **禁止执行任何 git 操作**(add/commit/push 等)——每个任务的提交步骤由用户手动完成;执行者验证通过后输出建议提交信息并暂停等待用户
- **0.4 轮询清单不增长**:任务数据读路径 = 打开 Tab 拉取 + 操作后刷新 + 心跳终态边沿补拉(复用 `useEdgeRefetch`,挂既有全局心跳派生的 files/compiles 活跃布尔);**不新增任何轮询与 SSE 解析**
- **IPC 通道前缀裁决:用 `affairs:*`,不用设计文档 §5 的 `tasks:*`**——`tasks:*` 已被 TaskAgent 长任务占用(ipc-handlers.js L695-1007 共 24 通道);`affairs` 与 Tab key、AffairsTab 组件命名一致。此裁决需在 3.0 §5.1 组件图同步改注(收尾任务执行)
- **HTTP 路径挂 `/projects/{project_id}/tasks`**(新 router `prefix="/projects"`,与 knowledge.py 的 `/knowledge/projects/...` 无冲突);stage 激活挂钩在 knowledge.py 既有 `PATCH /knowledge/projects/{project_id}` 内
- **i18n 零硬编码**:新增文案进 `src/i18n/locales/zh-CN.json` 与 `en-US.json`,key 前缀 `affairs.*`(事务 Tab)、`pulse.*` 增量键(脉搏带);组件内零硬编码
- **图标**:Lucide 经中央注册,`CalendarDays`(事务 Tab)/`Plus`(添加任务)新增导入,禁 emoji;状态色用既有 token(`--error`/`--warning`/`--accent`/`--text-muted`/`--success`,0.3 §4.3;`--warning` 一期已确认存在)
- **不变量**(3.0 §6.4,每任务自觉遵守):任务实例与模板解耦(改实例不回写模板);阶段切换不删历史任务、回退时期仅展开视图不激活;每期一条实例 occurrence_key 幂等;自动完成幂等(completed_by='auto' 只写一次)、手动永远可用;Chat 面板与右侧面板互斥(执行抽屉同款)
- **执行抽屉交互**:复用 WikiTab 预览面板模式——拖宽 260-720px、遮罩点击收起、打开时关闭 Chat 面板
- 验证命令:Python `C:/Users/Kelvin/.conda/envs/inkcube/python.exe -m pytest python/tests -x -q`(工作目录=仓库根);前端 `npx tsc --noEmit`、`npx vitest run tests/<file>`、全量 `npx vitest run`
- 测试落位:Python `python/tests/test_affairs_*.py`;前端 `tests/<Component>.test.tsx`(CSS Modules 用 Proxy mock,t 返回 defaultValue)
- **写测试文件前先查重**(一期事故教训):`tests/` 与 `python/tests/` 下同名文件先确认不存在再新建

## 关键裁决(计划前置调研,2026-10-09 代码实证)

| # | 裁决点 | 事实 | 裁决 |
|---|---|---|---|
| 1 | 服务/路由命名 | `routers/tasks.py` 已存在(TaskAgent 长任务) | 新路由 `routers/affairs.py`、新服务 `python/affairs_service.py`;3.0 §5.1 文字由收尾任务回改 |
| 2 | 自建周期任务幂等键 | UNIQUE(project_id, template_id, occurrence_key) 对 NULL 不防重 | 自建 recurring 创建时生成 `template_id = 'user_' + uuid4 片段` 供全部期次共享,数据模型与模板带入统一 |
| 3 | 滚动生成范围 | 「预生成下 1 期 + 补历史缺失」 | 从该节奏组 MIN(occurrence_key) 起逐期 INSERT OR IGNORE 迭代至「当期 + 下 1 期」,上限 120 期防御死循环 |
| 4 | 改节奏影响范围 | §6.1.7「仅影响未来期次」 | 滚动生成器取该组最新期实例(MAX(occurrence_key))的 recurrence_rule,旧期次天然不动 |
| 5 | recurring 匹配完成哪期 | 「本期关联文件解析完成即本期闭环」+ 历史补传 | 完成该节奏**最早 open 期**;material 类全部 open 命中实例完成;report 类标题匹配未命中保留手动 |
| 6 | 待编译 chip 触发编译 | §2.3「→ 知识 Tab 触发编译」;现状顶栏「编译」按钮随 pendingNotice 一并移除 | focusSignal view 扩为 `'lint' \| 'compile'`,WikiTab 消费 compile 信号直接 handleCompile()(与点按钮等价,无新风险) |
| 7 | 抽屉「发起报告」 | ReportsTab 由当前项目页渲染,天然预选项目 | 抽屉按钮 → `onOpenReports()` 切到 reports Tab,不新增跨 Tab 向导信号 |
| 8 | 向导轻调整载体 | 创建后立即打开项目是现状行为(App.handleCreateProject L203-224) | 创建成功后 modal 不关闭,切换为确认态「已按 XX 带入 N 项任务与月报节奏」+ [进入项目] / [调整任务 →](后者打开项目并发 `app:navigate-project-tab` affairs 信号);POST /projects 响应加 `seeded_tasks_count` |
| 9 | 抽屉关联文件显示 | 真实文件名需二次查询 | v1 显示「完成规则」文案与完成状态,不显示关联文件名;上传后任务自动 done 由列表可见 |

---

### Task 1: DB 建表 + playbook_seeded 迁移列 + 种子文件 + seed + 启动序列

**Files:**
- Modify: `python/db/database.py`(_create_tables 加两表 DDL 与迁移列;新方法 `seed_playbook_templates`/`list_playbook_templates`)
- Create: `python/db/playbook_seed.py`(46 条模板数据,对齐 wiki_seed.py 模式)
- Modify: `python/main.py`(lifespan:seed_wiki_schemas 之后插 seed_playbook_templates)
- Test: `python/tests/test_affairs_seed.py`

**Interfaces:**
- Consumes: `_create_tables()` 现有 executescript 模式;`_migrate_add_column(table, column, col_type)`(database.py L501)
- Produces(后续任务依赖):
  - 表 `playbook_templates`(§4.1 DDL)与 `phase_tasks`(§4.2 DDL,含两个索引)
  - `projects.playbook_seeded INTEGER DEFAULT 0`(迁移列)
  - `Database.seed_playbook_templates(templates: list[dict]) -> None`(INSERT OR REPLACE by id 幂等)
  - `Database.list_playbook_templates(project_type) -> list[dict]`(JSON 字段解码为 dict,按 phase+position 排序)
  - `python/db/playbook_seed.py` 提供 `get_all_playbook_templates() -> list[dict]`;键:`id/project_type/phase/task_type/title/description/due_offset_days/due_date_fixed/recurrence_rule/auto_complete/position`
  - lifespan 顺序:`ensure_preset_base_libs → seed_wiki_schemas → seed_playbook_templates →(Task 2 接 migrate_backfill_playbooks)→ TemplateLoader ...`

- [ ] **Step 1: 写失败测试** `python/tests/test_affairs_seed.py`:

```python
"""python/tests/test_affairs_seed.py — 3.0 二期 Task 1:建表/迁移列/种子幂等。"""
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import Database
from db.playbook_seed import get_all_playbook_templates


@pytest.fixture()
def db():
    with tempfile.TemporaryDirectory() as td:
        db = Database(os.path.join(td, "t.db"))
        db.initialize()
        yield db
        db.conn.close()


def test_tables_and_migration_column_created(db):
    cur = db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
        "('playbook_templates','phase_tasks')"
    )
    assert {r["name"] for r in cur.fetchall()} == {"playbook_templates", "phase_tasks"}
    cols = [r["name"] for r in db.conn.execute("PRAGMA table_info(projects)").fetchall()]
    assert "playbook_seeded" in cols


def test_seed_inserts_46_templates_idempotent(db):
    db.seed_playbook_templates(get_all_playbook_templates())
    n1 = db.conn.execute("SELECT COUNT(*) c FROM playbook_templates").fetchone()["c"]
    assert n1 == 46
    db.seed_playbook_templates(get_all_playbook_templates())  # 幂等
    n2 = db.conn.execute("SELECT COUNT(*) c FROM playbook_templates").fetchone()["c"]
    assert n2 == 46


def test_seed_covers_types_phases_and_json_fields(db):
    db.seed_playbook_templates(get_all_playbook_templates())
    rows = [dict(r) for r in db.conn.execute("SELECT * FROM playbook_templates").fetchall()]
    by_type = {}
    for r in rows:
        by_type.setdefault(r["project_type"], []).append(r)
    assert len(by_type["recruitment"]) == 24
    assert len(by_type["investment"]) == 20
    assert len(by_type["general"]) == 2
    # JSON 字段合法且口径正确(§3.3:月报 = 每月 15 日前 + 文件名含月报)
    monthly = next(r for r in rows if r["id"] == "playbook_recruitment_after_monthly_report")
    assert json.loads(monthly["recurrence_rule"]) == {"freq": "monthly", "due_day": 15}
    assert json.loads(monthly["auto_complete"]) == {"on": "file_parsed", "match": "月报|月度"}
    # 报告类 match 带 title~ 前缀(§3.3)
    report_t = next(r for r in rows if r["id"] == "playbook_recruitment_before_initial_report")
    assert json.loads(report_t["auto_complete"])["match"] == "title~初评|评估"


def test_list_by_type_ordered(db):
    db.seed_playbook_templates(get_all_playbook_templates())
    rows = db.list_playbook_templates("recruitment")
    assert len(rows) == 24
    poss = [r["position"] for r in rows]
    assert poss == sorted(poss)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `C:/Users/Kelvin/.conda/envs/inkcube/python.exe -m pytest python/tests/test_affairs_seed.py -x -q`
Expected: FAIL——`ModuleNotFoundError: db.playbook_seed`

- [ ] **Step 3: 建表与迁移列**——`database.py` 的 `_create_tables()` 内 wiki 表脚本块之后(约 L498 `self.conn.commit()` 之后)追加:

```python
        # ── Affairs: Playbook & Phase Tasks (3.0 §4, phase 2) ──
        # playbook_templates: 类型 × 时期任务模板(种子化,运行期只读);
        # phase_tasks: 项目任务实例(脉搏带/事务 Tab/首页待办唯一数据源)
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS playbook_templates (
                id TEXT PRIMARY KEY,
                project_type TEXT NOT NULL,
                phase TEXT NOT NULL,
                task_type TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                due_offset_days INTEGER,
                due_date_fixed TEXT,
                recurrence_rule TEXT,
                auto_complete TEXT,
                position INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_playbook_type_phase
                ON playbook_templates(project_type, phase, position);

            CREATE TABLE IF NOT EXISTS phase_tasks (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                phase TEXT NOT NULL,
                task_type TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                template_id TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                occurrence_key TEXT,
                due_date TEXT,
                recurrence_rule TEXT,
                auto_complete TEXT,
                linked_entity_type TEXT,
                linked_entity_id TEXT,
                completed_at DATETIME,
                completed_by TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(project_id, template_id, occurrence_key)
            );
            CREATE INDEX IF NOT EXISTS idx_phase_tasks_project
                ON phase_tasks(project_id, status, due_date);
            CREATE INDEX IF NOT EXISTS idx_phase_tasks_due
                ON phase_tasks(status, due_date);
        """)
        self.conn.commit()

        # Migrate: projects — playbook 铺入标记(防删光任务后重铺,3.0 §4.3)
        self._migrate_add_column("projects", "playbook_seeded", "INTEGER DEFAULT 0")
```

并在 `database.py` 的 `seed_wiki_schemas` 方法之后新增(方法区「── Wiki: Schemas ──」与「── Wiki: Pages ──」之间):

```python
    # ── Affairs: Playbook templates (3.0 §4.1, phase 2) ──

    def seed_playbook_templates(self, templates: list[dict]) -> None:
        """Seed playbook_templates (idempotent: INSERT OR REPLACE by id).

        对齐 seed_wiki_schemas 模式(§4.1):应用更新可迭代行业最佳实践,
        运行期只读;已生成实例与模板解耦,重种子不影响存量任务。
        """
        for t in templates:
            self.conn.execute(
                """INSERT OR REPLACE INTO playbook_templates
                   (id, project_type, phase, task_type, title, description,
                    due_offset_days, due_date_fixed, recurrence_rule, auto_complete, position)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    t["id"], t["project_type"], t["phase"], t["task_type"], t["title"],
                    t.get("description", ""), t.get("due_offset_days"),
                    t.get("due_date_fixed"),
                    json.dumps(t["recurrence_rule"], ensure_ascii=False) if t.get("recurrence_rule") else None,
                    json.dumps(t["auto_complete"], ensure_ascii=False) if t.get("auto_complete") else None,
                    t.get("position", 0),
                ),
            )
        self.conn.commit()

    def list_playbook_templates(self, project_type: str) -> list[dict]:
        cursor = self.conn.execute(
            "SELECT * FROM playbook_templates WHERE project_type = ? ORDER BY phase ASC, position ASC",
            (project_type,),
        )
        rows = []
        for row in cursor.fetchall():
            d = dict(row)
            for key in ("recurrence_rule", "auto_complete"):
                if d.get(key):
                    try:
                        d[key] = json.loads(d[key])
                    except (json.JSONDecodeError, TypeError):
                        d[key] = None
            rows.append(d)
        return rows
```

- [ ] **Step 4: 种子文件** `python/db/playbook_seed.py`(46 条逐条,值 = 3.0 §3.3 三张表逐行誊录):

```python
"""python/db/playbook_seed.py

Preset Playbook templates (类型 × 时期任务模板) for 3.0 时期任务模型.
Seeded on startup via Database.seed_playbook_templates() — INSERT OR REPLACE
idempotent, aligned with wiki_seed.py (3.0 §3.3 / §4.1).

字段口径(§3.3):
- due_offset_days: 进入该时期后 N 天截止(pending_activation 实例激活时换算)
- due_date_fixed: 固定日期(milestone 申报窗口类,铺入时抄入 due_date,用户可改)
- recurrence_rule: {"freq": monthly|quarterly|yearly, "due_day": 1-31|"end"|"MM-DD"}
- auto_complete: {"on": "file_parsed", "match": "k1|k2"}(资料/周期=文件名关键词)
                 {"on": "report_completed", "match": "title~k1|k2"}(报告=标题关键词)
"""
import json


def _T(tid, ptype, phase, ttype, title, *, due_offset_days=None, due_date_fixed=None,
       recurrence_rule=None, auto_complete=None, position=0):
    return {
        "id": tid, "project_type": ptype, "phase": phase, "task_type": ttype,
        "title": title, "description": "", "due_offset_days": due_offset_days,
        "due_date_fixed": due_date_fixed,
        "recurrence_rule": recurrence_rule, "auto_complete": auto_complete,
        "position": position,
    }


def _recruitment() -> list[dict]:
    return [
        _T("playbook_recruitment_before_bp", "recruitment", "before", "material",
           "上传企业 BP / 商业计划书", due_offset_days=14,
           auto_complete={"on": "file_parsed", "match": "BP|商业计划"}, position=1),
        _T("playbook_recruitment_before_license", "recruitment", "before", "material",
           "收集工商注册信息(执照/章程)", due_offset_days=7,
           auto_complete={"on": "file_parsed", "match": "营业执照|工商"}, position=2),
        _T("playbook_recruitment_before_credit", "recruitment", "before", "material",
           "收集企业征信/信用报告", due_offset_days=7,
           auto_complete={"on": "file_parsed", "match": "征信|信用报告"}, position=3),
        _T("playbook_recruitment_before_finance", "recruitment", "before", "material",
           "收集近三年财务报表", due_offset_days=14,
           auto_complete={"on": "file_parsed", "match": "财报|财务报表|审计"}, position=4),
        _T("playbook_recruitment_before_team", "recruitment", "before", "material",
           "收集核心团队背景资料", due_offset_days=7,
           auto_complete={"on": "file_parsed", "match": "团队|简历|高管"}, position=5),
        _T("playbook_recruitment_before_initial_report", "recruitment", "before", "report",
           "产出项目初评报告", due_offset_days=21,
           auto_complete={"on": "report_completed", "match": "title~初评|评估"}, position=6),
        _T("playbook_recruitment_before_pitch", "recruitment", "before", "report",
           "产出园区推介方案", due_offset_days=21,
           auto_complete={"on": "report_completed", "match": "title~推介"}, position=7),
        _T("playbook_recruitment_before_visit", "recruitment", "before", "milestone",
           "完成首次实地考察/接待", position=8),
        _T("playbook_recruitment_due_diligence", "recruitment", "during", "material",
           "收集尽调材料清单", due_offset_days=14,
           auto_complete={"on": "file_parsed", "match": "尽调"}, position=1),
        _T("playbook_recruitment_apply_material", "recruitment", "during", "material",
           "收集政策申报材料",
           auto_complete={"on": "file_parsed", "match": "申报"}, position=2),
        _T("playbook_recruitment_agreement", "recruitment", "during", "material",
           "收集投资/合作协议文本",
           auto_complete={"on": "file_parsed", "match": "协议|合同"}, position=3),
        _T("playbook_recruitment_dd_report", "recruitment", "during", "report",
           "产出项目尽调/评估报告", due_offset_days=21,
           auto_complete={"on": "report_completed", "match": "title~尽调|评估"}, position=4),
        _T("playbook_recruitment_landing_plan", "recruitment", "during", "report",
           "产出落地方案(选址/政策/时间表)", due_offset_days=21,
           auto_complete={"on": "report_completed", "match": "title~落地方案"}, position=5),
        _T("playbook_recruitment_signing", "recruitment", "during", "milestone",
           "完成项目签约", position=6),
        _T("playbook_recruitment_registration", "recruitment", "during", "milestone",
           "完成公司注册落地", position=7),
        _T("playbook_recruitment_land", "recruitment", "during", "milestone",
           "完成用地/厂房交付(拿地)", position=8),
        _T("playbook_recruitment_construction", "recruitment", "during", "milestone",
           "完成开工建设", position=9),
        _T("playbook_recruitment_production", "recruitment", "during", "milestone",
           "完成设备进场与投产", position=10),
        _T("playbook_recruitment_after_monthly_report", "recruitment", "after", "recurring",
           "企业月报", recurrence_rule={"freq": "monthly", "due_day": 15},
           auto_complete={"on": "file_parsed", "match": "月报|月度"}, position=1),
        _T("playbook_recruitment_after_quarterly_report", "recruitment", "after", "recurring",
           "企业季报", recurrence_rule={"freq": "quarterly", "due_day": "end"},
           auto_complete={"on": "file_parsed", "match": "季报|季度"}, position=2),
        _T("playbook_recruitment_after_yearly_data", "recruitment", "after", "recurring",
           "年度经营数据", recurrence_rule={"freq": "yearly", "due_day": "04-30"},
           auto_complete={"on": "file_parsed", "match": "年报|年度|经营数据"}, position=3),
        _T("playbook_recruitment_after_service_report", "recruitment", "after", "report",
           "产出年度项目服务报告", recurrence_rule={"freq": "yearly", "due_day": "12-31"},
           auto_complete={"on": "report_completed", "match": "title~服务报告|年度报告"},
           position=4),
        _T("playbook_recruitment_after_policy_window", "recruitment", "after", "milestone",
           "政策申报窗口(高企认定等,日期可改)", due_date_fixed="", position=5),
        _T("playbook_recruitment_after_annual_visit", "recruitment", "after", "milestone",
           "年度企业评估/走访", position=6),
    ]


def _investment() -> list[dict]:
    return [
        _T("playbook_investment_before_bp", "investment", "before", "material",
           "上传企业 BP", due_offset_days=7,
           auto_complete={"on": "file_parsed", "match": "BP|商业计划"}, position=1),
        _T("playbook_investment_before_finance", "investment", "before", "material",
           "收集近三年财务报表/审计报告", due_offset_days=14,
           auto_complete={"on": "file_parsed", "match": "财报|审计"}, position=2),
        _T("playbook_investment_before_legal", "investment", "before", "material",
           "收集工商/股权/涉诉信息", due_offset_days=7,
           auto_complete={"on": "file_parsed", "match": "工商|股权|涉诉"}, position=3),
        _T("playbook_investment_before_team", "investment", "before", "material",
           "收集核心团队背景", due_offset_days=7,
           auto_complete={"on": "file_parsed", "match": "团队|简历"}, position=4),
        _T("playbook_investment_before_industry", "investment", "before", "material",
           "收集行业研究报告", due_offset_days=14,
           auto_complete={"on": "file_parsed", "match": "行业|研报"}, position=5),
        _T("playbook_investment_before_dd_report", "investment", "before", "report",
           "产出尽职调查报告", due_offset_days=21,
           auto_complete={"on": "report_completed", "match": "title~尽调"}, position=6),
        _T("playbook_investment_before_ic_report", "investment", "before", "report",
           "产出投资分析报告(投决材料)", due_offset_days=21,
           auto_complete={"on": "report_completed", "match": "title~投资分析|投决"}, position=7),
        _T("playbook_investment_before_ic_review", "investment", "before", "milestone",
           "完成投决会评审", position=8),
        _T("playbook_investment_deal_docs", "investment", "during", "material",
           "收集投资协议/交易文件", due_offset_days=14,
           auto_complete={"on": "file_parsed", "match": "协议|交易|SPA"}, position=1),
        _T("playbook_investment_closing_conds", "investment", "during", "material",
           "收集交割先决条件材料",
           auto_complete={"on": "file_parsed", "match": "交割"}, position=2),
        _T("playbook_investment_closing_memo", "investment", "during", "report",
           "产出交割备忘", due_offset_days=14,
           auto_complete={"on": "report_completed", "match": "title~交割|备忘"}, position=3),
        _T("playbook_investment_payment", "investment", "during", "milestone",
           "完成打款交割", position=4),
        _T("playbook_investment_registration", "investment", "during", "milestone",
           "完成工商变更登记", position=5),
        _T("playbook_investment_after_monthly_report", "investment", "after", "recurring",
           "被投企业月报", recurrence_rule={"freq": "monthly", "due_day": 15},
           auto_complete={"on": "file_parsed", "match": "月报|月度"}, position=1),
        _T("playbook_investment_after_quarterly_report", "investment", "after", "recurring",
           "被投企业季报/季度财务", recurrence_rule={"freq": "quarterly", "due_day": "end"},
           auto_complete={"on": "file_parsed", "match": "季报|季度"}, position=2),
        _T("playbook_investment_after_audit", "investment", "after", "recurring",
           "年度审计报告", recurrence_rule={"freq": "yearly", "due_day": "04-30"},
           auto_complete={"on": "file_parsed", "match": "审计|年报"}, position=3),
        _T("playbook_investment_after_pm_report", "investment", "after", "report",
           "产出投后管理报告", recurrence_rule={"freq": "yearly", "due_day": "12-31"},
           auto_complete={"on": "report_completed", "match": "title~投后"}, position=4),
        _T("playbook_investment_after_exit_review", "investment", "after", "report",
           "退出路径评估",
           auto_complete={"on": "report_completed", "match": "title~退出"}, position=5),
        _T("playbook_investment_after_vam", "investment", "after", "milestone",
           "对赌/业绩承诺考核节点(按协议日期,可改)", due_date_fixed="", position=6),
        _T("playbook_investment_after_valuation", "investment", "after", "milestone",
           "年度投后回顾/估值更新", position=7),
    ]


def _general() -> list[dict]:
    return [
        _T("playbook_general_before_materials", "general", "before", "material",
           "上传项目相关资料", due_offset_days=14, position=1),
        _T("playbook_general_before_report", "general", "before", "report",
           "产出分析报告", due_offset_days=21,
           auto_complete={"on": "report_completed", "match": "title~分析|报告"}, position=2),
    ]


def get_all_playbook_templates() -> list[dict]:
    return _recruitment() + _investment() + _general()
```

注:`due_date_fixed=""` 的两条(申报窗口/对赌考核)表示「固定日期可调但无默认」——铺入时 due_date 留 NULL,用户在抽屉/编辑里自行定日。

- [ ] **Step 5: 启动序列**——`python/main.py` lifespan 中 `deps.db.seed_wiki_schemas(get_all_wiki_schemas())`(L96)之后插入:

```python
    # Seed Playbook templates on startup (idempotent, 3.0 §4.1):
    # 顺序裁决(§5.4): 建表迁移 → seed_wiki_schemas → seed_playbook_templates
    # → migrate_backfill_playbooks(Task 2 接入,依赖模板已就位) → 其余启动流程
    from db.playbook_seed import get_all_playbook_templates
    deps.db.seed_playbook_templates(get_all_playbook_templates())
```

- [ ] **Step 6: 跑测试确认通过**

Run: `C:/Users/Kelvin/.conda/envs/inkcube/python.exe -m pytest python/tests/test_affairs_seed.py -x -q`
Expected: PASS(4 用例)

- [ ] **Step 7: 回归**——`C:/Users/Kelvin/.conda/envs/inkcube/python.exe -m pytest python/tests -x -q` 确认存量无破坏。

- [ ] **Step 8: 提示用户提交**(建议信息:`feat(affairs): playbook/phase_tasks tables + 46 template seeds (3.0 phase2 task1)`)

---

### Task 2: DB 层任务实例方法 + 铺入 + 存量回填 + 创建同事务挂钩

**Files:**
- Modify: `python/db/database.py`(新方法区;`create_project` 加 `seed_playbook` 参数)
- Modify: `python/main.py`(lifespan 补 migrate_backfill_playbooks)
- Test: `python/tests/test_affairs_db.py`

**Interfaces:**
- Consumes: Task 1 的两表与 `list_playbook_templates`;`_uuid()/_now()`
- Produces(Task 3/4 依赖,签名精确):

```python
def insert_playbook_tasks(self, project_id: str, project_type: str, stage: str) -> int
    """按类型铺入全时期实例;当前时期 open(算 due),其余 pending_activation(due NULL);
    recurring 只铺「当期」一期实例(occurrence_key=当期,due=当期截止日);
    due_date_fixed 非空的 milestone 直接抄 due_date;返回铺入条数。幂等由 playbook_seeded 标记保证。"""

def migrate_backfill_playbooks(self) -> int
    """存量回填:kind='project' AND playbook_seeded=0 逐个铺入 + 置 1;
    幂等由标记列防重入(用户删光任务不重铺,§4.3);返回回填项目数。"""

def list_phase_tasks(self, project_id: str) -> list[dict]     # 全量含 pending/skipped,按 phase/task_type/due_date 排序
def get_phase_task(self, task_id: str) -> dict | None
def create_phase_task(self, project_id, phase, task_type, title, description="",
                      due_date=None, recurrence_rule=None, auto_complete=None,
                      template_id=None, status="open", occurrence_key=None) -> dict
    # 自建 recurring(template_id=None)生成共享 template_id='user_<uuid8>'(裁决 2)
def update_phase_task(self, task_id: str, fields: dict) -> dict | None
    # 白名单键: title/description/due_date/status/recurrence_rule/auto_complete/
    # linked_entity_type/linked_entity_id/phase(recurrence_rule/auto_complete 非 str 值自动 dumps)
    # status→'done': completed_at=now(或 fields.completed_at), completed_by='manual'(或 fields.completed_by)
    # status→'open'/'skipped': completed_at=NULL, completed_by=NULL
def delete_phase_task(self, task_id: str) -> bool
```

- `create_project(..., seed_playbook: bool = True)`:INSERT projects 与任务铺入同一 commit——铺入失败 rollback 并抛异常(创建不残留,§5.3 事务性);成功置 `playbook_seeded=1`;返回 dict 增加 `seeded_tasks_count` 键。

- [ ] **Step 1: 写失败测试** `python/tests/test_affairs_db.py`:

```python
"""python/tests/test_affairs_db.py — 3.0 二期 Task 2:铺入/回填/CRUD/创建同事务。"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import Database
from db.playbook_seed import get_all_playbook_templates


@pytest.fixture()
def db():
    with tempfile.TemporaryDirectory() as td:
        db = Database(os.path.join(td, "t.db"))
        db.initialize()
        db.seed_playbook_templates(get_all_playbook_templates())
        yield db
        db.conn.close()


def test_insert_playbook_tasks_current_open_others_pending(db):
    pid = db.create_project(name="P", project_type="recruitment", stage="during")["id"]
    tasks = db.list_phase_tasks(pid)
    assert len(tasks) == 24
    by = {}
    for t in tasks:
        by.setdefault(t["phase"], []).append(t)
    assert all(t["status"] == "open" and t["due_date"] for t in by["during"])
    assert all(t["status"] == "pending_activation" and not t["due_date"]
               for p in ("before", "after") for t in by[p])
    # recurring 只铺当前期一期(recruitment recurring 全在 after,不进 during)
    assert not any(t["task_type"] == "recurring" for t in by["during"])


def test_insert_playbook_tasks_seeds_current_occurrence_for_recurring(db):
    pid = db.create_project(name="P", project_type="recruitment", stage="after")["id"]
    monthly = next(t for t in db.list_phase_tasks(pid)
                   if t["template_id"] == "playbook_recruitment_after_monthly_report")
    assert monthly["status"] == "open"
    assert monthly["occurrence_key"]  # 非空,形如 2026-10
    assert monthly["due_date"]


def test_create_project_rolls_back_on_seed_failure(db):
    # 铺入失败 → 项目不残留(同事务,§5.3)
    db.conn.execute("DROP TABLE playbook_templates")
    with pytest.raises(Exception):
        db.create_project(name="X", project_type="recruitment", stage="before")
    row = db.conn.execute(
        "SELECT COUNT(*) c FROM projects WHERE name='X'").fetchone()
    assert row["c"] == 0


def test_migrate_backfill_idempotent(db):
    # 模拟存量:直接 SQL 建项目(绕过 create_project 的铺入)
    pid = db._uuid()
    db.conn.execute(
        "INSERT INTO projects (id, name, type, stage, kind, playbook_seeded) "
        "VALUES (?, 'old', 'general', 'before', 'project', 0)", (pid,))
    db.conn.commit()
    assert db.migrate_backfill_playbooks() == 1
    assert len(db.list_phase_tasks(pid)) == 2
    assert db.migrate_backfill_playbooks() == 0  # 标记防重入
    # 用户删光任务也不重铺(§4.3)
    for t in db.list_phase_tasks(pid):
        db.delete_phase_task(t["id"])
    assert db.migrate_backfill_playbooks() == 0


def test_update_phase_task_status_transitions(db):
    pid = db.create_project(name="P", project_type="general", stage="before")["id"]
    task = db.list_phase_tasks(pid)[0]
    up = db.update_phase_task(task["id"], {"status": "done"})
    assert up["status"] == "done" and up["completed_by"] == "manual" and up["completed_at"]
    up = db.update_phase_task(task["id"], {"status": "open"})
    assert up["completed_at"] is None and up["completed_by"] is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `C:/Users/Kelvin/.conda/envs/inkcube/python.exe -m pytest python/tests/test_affairs_db.py -x -q`
Expected: FAIL——`AttributeError: 'Database' object has no attribute 'insert_playbook_tasks'`

- [ ] **Step 3: 实现**——`database.py` Task 1 新方法之后继续追加「── Affairs: Phase Tasks ──」方法区(完整代码):

```python
    # ── Affairs: Phase Tasks (3.0 §4.2, phase 2) ──

    def _phase_of_stage(self, stage: str) -> str:
        # general 无时期(stage='none')→ 任务归 before 单时期(§3.3)
        return stage if stage in ("before", "during", "after") else "before"

    def _occurrence_for_now(self, rule: dict) -> tuple[str, str]:
        """铺入时的「当期」(key, due)。与服务层 affairs_service.current_occurrence
        同构;DB 层自持一份避免循环依赖,口径以服务层为准(Task 3 测试互证)。"""
        import calendar as _cal
        from datetime import date as _date
        today = _date.today()
        freq, day = rule.get("freq"), rule.get("due_day")
        if freq == "monthly":
            d = min(int(day), _cal.monthrange(today.year, today.month)[1])
            return (today.strftime("%Y-%m"),
                    f"{today.year:04d}-{today.month:02d}-{d:02d}")
        if freq == "quarterly":
            q = (today.month - 1) // 3 + 1
            last_month = q * 3
            due = _date(today.year, last_month, 1).replace(
                day=_cal.monthrange(today.year, last_month)[1])
            return f"{today.year}-Q{q}", due.isoformat()
        mmdd = str(day or "12-31")
        return today.strftime("%Y"), f"{today.year}-{mmdd}"

    def insert_playbook_tasks(self, project_id: str, project_type: str, stage: str) -> int:
        from datetime import date as _date, timedelta as _td
        templates = self.list_playbook_templates(project_type)
        current_phase = self._phase_of_stage(stage)
        today = _date.today()
        count = 0
        for t in templates:
            status = "open" if t["phase"] == current_phase else "pending_activation"
            due = None
            occ = None
            if status == "open":
                if t["task_type"] == "recurring" and t.get("recurrence_rule"):
                    occ, due = self._occurrence_for_now(t["recurrence_rule"])
                elif t.get("due_date_fixed"):
                    due = t["due_date_fixed"] or None
                elif t.get("due_offset_days"):
                    due = (today + _td(days=int(t["due_offset_days"]))).isoformat()
            self.conn.execute(
                """INSERT OR IGNORE INTO phase_tasks
                   (id, project_id, phase, task_type, title, description, template_id,
                    status, occurrence_key, due_date, recurrence_rule, auto_complete)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (self._uuid(), project_id, t["phase"], t["task_type"], t["title"],
                 t.get("description", ""), t["id"], status, occ, due,
                 json.dumps(t["recurrence_rule"], ensure_ascii=False) if t.get("recurrence_rule") else None,
                 json.dumps(t["auto_complete"], ensure_ascii=False) if t.get("auto_complete") else None,
                 ),
            )
            count += 1
        return count

    def migrate_backfill_playbooks(self) -> int:
        rows = self.conn.execute(
            "SELECT id, type, stage FROM projects WHERE kind='project' AND playbook_seeded = 0"
        ).fetchall()
        n = 0
        for r in rows:
            try:
                self.insert_playbook_tasks(r["id"], r["type"], r["stage"])
                self.conn.execute(
                    "UPDATE projects SET playbook_seeded = 1 WHERE id = ?", (r["id"],))
                self.conn.commit()
                n += 1
            except Exception:
                logger.warning("backfill playbooks failed for %s", r["id"], exc_info=True)
                self.conn.rollback()
        return n

    def list_phase_tasks(self, project_id: str) -> list[dict]:
        cursor = self.conn.execute(
            "SELECT * FROM phase_tasks WHERE project_id = ? "
            "ORDER BY phase ASC, task_type ASC, due_date ASC, created_at ASC",
            (project_id,),
        )
        rows = []
        for row in cursor.fetchall():
            d = dict(row)
            for key in ("recurrence_rule", "auto_complete"):
                if d.get(key):
                    try:
                        d[key] = json.loads(d[key])
                    except (json.JSONDecodeError, TypeError):
                        d[key] = None
            rows.append(d)
        return rows

    def get_phase_task(self, task_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM phase_tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row) if row else None

    def create_phase_task(self, project_id, phase, task_type, title, description="",
                          due_date=None, recurrence_rule=None, auto_complete=None,
                          template_id=None, status="open", occurrence_key=None) -> dict:
        # 自建 recurring 无模板:生成共享 template_id='user_<8位>' 供全部期次使用,
        # 否则 UNIQUE(project_id, template_id, occurrence_key) 对 NULL 不防重(裁决 2)
        if template_id is None and task_type == "recurring":
            template_id = "user_" + self._uuid().replace("-", "")[:8]
        task_id = self._uuid()
        self.conn.execute(
            """INSERT INTO phase_tasks
               (id, project_id, phase, task_type, title, description, template_id,
                status, occurrence_key, due_date, recurrence_rule, auto_complete)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (task_id, project_id, phase, task_type, title, description, template_id,
             status, occurrence_key, due_date,
             json.dumps(recurrence_rule, ensure_ascii=False) if recurrence_rule else None,
             json.dumps(auto_complete, ensure_ascii=False) if auto_complete else None,
             ),
        )
        self.conn.commit()
        return self.get_phase_task(task_id)

    _PHASE_TASK_FIELDS = ("title", "description", "due_date", "status",
                          "recurrence_rule", "auto_complete",
                          "linked_entity_type", "linked_entity_id", "phase")

    def update_phase_task(self, task_id: str, fields: dict) -> dict | None:
        allowed = {k: v for k, v in fields.items() if k in self._PHASE_TASK_FIELDS}
        if not allowed:
            return self.get_phase_task(task_id)
        for key in ("recurrence_rule", "auto_complete"):
            if key in allowed and allowed[key] is not None and not isinstance(allowed[key], str):
                allowed[key] = json.dumps(allowed[key], ensure_ascii=False)
        sets, params = [], []
        if "status" in allowed:
            status = allowed["status"]
            if status == "done":
                sets += ["completed_at = ?", "completed_by = ?"]
                params += [fields.get("completed_at") or self._now(),
                           fields.get("completed_by") or "manual"]
            else:
                sets += ["completed_at = NULL", "completed_by = NULL"]
        for k, v in allowed.items():
            sets.append(f"{k} = ?")
            params.append(v)
        params.append(task_id)
        self.conn.execute(
            f"UPDATE phase_tasks SET {', '.join(sets)} WHERE id = ?", params)
        self.conn.commit()
        return self.get_phase_task(task_id)

    def delete_phase_task(self, task_id: str) -> bool:
        cur = self.conn.execute("DELETE FROM phase_tasks WHERE id = ?", (task_id,))
        self.conn.commit()
        return cur.rowcount > 0
```

`create_project`(L668-695)替换为(同事务铺入版):

```python
    def create_project(self, name: str, description: str = "", project_type: str = "general", stage: str = "none",
                       company_name: str = "", credit_code: str = "", seed_playbook: bool = True) -> dict:
        now = self._now()
        proj_id = self._uuid()
        # Pre-fill Wiki purpose by project type (user-editable later, §4.6)
        default_purpose = self.DEFAULT_PURPOSES.get(project_type, self.DEFAULT_PURPOSES["general"])
        try:
            self.conn.execute(
                "INSERT INTO projects (id, name, description, type, stage, company_name, credit_code, kind, wiki_purpose, playbook_seeded, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'project', ?, 0, ?, ?)",
                (proj_id, name, description, project_type, stage, company_name, credit_code, default_purpose, now, now),
            )
            # 3.0 §5.3: 创建成功即铺入全时期任务,与创建同事务(铺入失败回滚创建)
            seeded = self.insert_playbook_tasks(proj_id, project_type, stage) if seed_playbook else 0
            if seed_playbook:
                self.conn.execute(
                    "UPDATE projects SET playbook_seeded = 1 WHERE id = ?", (proj_id,))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

        # Auto-link base libraries based on project type
        auto_libs = self.get_auto_linked_base_libs(project_type)
        for lib_id in auto_libs:
            self.link_project_to_base_lib(proj_id, lib_id, auto=True)

        return {
            "id": proj_id,
            "name": name,
            "description": description,
            "type": project_type,
            "stage": stage,
            "company_name": company_name,
            "credit_code": credit_code,
            "created_at": now,
            "updated_at": now,
            "seeded_tasks_count": seeded,
        }
```

`main.py` lifespan:Task 1 插入的 seed 调用之后补:

```python
    # 存量项目回填(3.0 §5.3):seed 之后、其余启动流程之前;幂等由 playbook_seeded 防重入
    _backfilled = deps.db.migrate_backfill_playbooks()
    if _backfilled:
        logging.getLogger(__name__).info(
            "Backfilled playbooks for %d existing projects", _backfilled)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `C:/Users/Kelvin/.conda/envs/inkcube/python.exe -m pytest python/tests/test_affairs_db.py python/tests/test_affairs_seed.py -x -q`
Expected: PASS

- [ ] **Step 5: 回归全量** `C:/Users/Kelvin/.conda/envs/inkcube/python.exe -m pytest python/tests -x -q`

- [ ] **Step 6: 提示用户提交**(建议信息:`feat(affairs): phase task CRUD + playbook seeding on create + backfill migration (3.0 phase2 task2)`)

---

### Task 3: 服务层 affairs_service.py——滚动生成/阶段激活/事件匹配

**Files:**
- Create: `python/affairs_service.py`
- Test: `python/tests/test_affairs_service.py`

**Interfaces:**
- Consumes: Task 2 全部 DB 方法;`db.conn` 直查(对齐 activity_events.py 风格)
- Produces(Task 4/5 依赖,签名精确):

```python
# python/affairs_service.py — 模块级函数;除纯函数外全部自带 try/except 兜底仅记日志
# (§5.4 异常语义:闭环是增强不是依赖,不阻塞解析/报告主流程)

def phase_of(stage: str) -> str
    # 'before'|'during'|'after' 直通;'none'/其它 → 'before'(general 单时期)

def current_occurrence(rule: dict, today: date) -> tuple[str, str]   # (key, due ISO)
def next_occurrence(rule: dict, key: str) -> tuple[str, str]
def prev_occurrence(rule: dict, key: str) -> tuple[str, str]
    # key 格式:2026-10(monthly)/ 2026-Q4(quarterly)/ 2026(yearly),同频率内字典序可比

def roll_recurring(db) -> int
    """滚动生成:对每个 (project_id, template_id) recurring 节奏组,从组内
    MIN(occurrence_key) 逐期补齐至「当期 + 下 1 期」,INSERT OR IGNORE 幂等
    (裁决 3,上限 120 期);节奏规则取组内最新期实例的 recurrence_rule(裁决 4)。
    历史期 due 按 rule 逐期回推计算。返回新生成实例数。失败仅记日志返回 0。"""

def maybe_roll_daily(db) -> bool
    """check-date 门:模块级 _last_roll_date,跨日即 roll_recurring 并置位;
    同日重复调用零开销。lifespan 与 /activities/status 共用(裁决,不新增轮询)。
    返回是否执行了 roll。"""

def activate_phase(db, project_id: str, new_stage: str) -> int
    """阶段切换激活(§5.3):new_stage 对应 phase 的 pending_activation → open;
    due = due_offset_days 换算(today+N)或 due_date_fixed 抄录(JOIN 模板取值);
    返回激活数。失败仅记日志返回 0。"""

def on_file_parsed(db, project_id: str, filename: str, file_id: str) -> int
    """material/recurring 自动完成:open 实例 auto_complete.on=='file_parsed'
    且 match 命中 filename → done(completed_by='auto', linked_entity_id=file_id)。
    material 全部命中实例完成;recurring 仅最早 open 期(裁决 5)。
    幂等:已 done/skipped/pending 不在候选。返回完成数。失败仅记日志返回 0。"""

def on_report_completed(db, report_id: str) -> int
    """report 自动完成:查 reports 得 project_id/title;open report 实例
    auto_complete.on=='report_completed' 且 match(去 'title~' 前缀)命中
    title → done(completed_by='auto')。未命中保留手动(§3.4)。
    报告不存在(已删)静默返回 0。失败仅记日志返回 0。"""
```

- [ ] **Step 1: 写失败测试** `python/tests/test_affairs_service.py`:

```python
"""python/tests/test_affairs_service.py — 3.0 二期 Task 3:滚动/激活/事件匹配。"""
import os
import sys
import tempfile
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import affairs_service
from db.database import Database
from db.playbook_seed import get_all_playbook_templates


@pytest.fixture()
def db():
    with tempfile.TemporaryDirectory() as td:
        db = Database(os.path.join(td, "t.db"))
        db.initialize()
        db.seed_playbook_templates(get_all_playbook_templates())
        yield db
        db.conn.close()


def _project(db, ptype="recruitment", stage="after"):
    return db.create_project(name="P", project_type=ptype, stage=stage)["id"]


def test_occurrence_math():
    rule = {"freq": "monthly", "due_day": 15}
    assert affairs_service.current_occurrence(rule, date(2026, 10, 9)) == ("2026-10", "2026-10-15")
    assert affairs_service.next_occurrence(rule, "2026-10") == ("2026-11", "2026-11-15")
    assert affairs_service.prev_occurrence(rule, "2026-10") == ("2026-09", "2026-09-15")
    q = {"freq": "quarterly", "due_day": "end"}
    assert affairs_service.current_occurrence(q, date(2026, 10, 9)) == ("2026-Q4", "2026-12-31")
    y = {"freq": "yearly", "due_day": "04-30"}
    assert affairs_service.current_occurrence(y, date(2026, 10, 9)) == ("2026", "2026-04-30")
    assert affairs_service.next_occurrence(y, "2026") == ("2027", "2027-04-30")


def test_roll_recurring_creates_next_once(db):
    pid = _project(db)
    created = affairs_service.roll_recurring(db)
    # 铺入已给 recruitment after 3 个节奏各当期一期;roll 补齐至当期+下期 → 恰各补 1
    assert created == 3
    again = affairs_service.roll_recurring(db)
    assert again == 0    # 幂等


def test_roll_recurring_fills_gap_after_delete(db):
    pid = _project(db)
    affairs_service.roll_recurring(db)
    # 模拟缺口:删掉月报当期实例 → roll 应重新补出
    monthly = next(t for t in db.list_phase_tasks(pid)
                   if t["template_id"] == "playbook_recruitment_after_monthly_report")
    db.delete_phase_task(monthly["id"])
    created = affairs_service.roll_recurring(db)
    assert created >= 1
    keys = [t["occurrence_key"] for t in db.list_phase_tasks(pid)
            if t["template_id"] == "playbook_recruitment_after_monthly_report"]
    assert len(keys) == 2  # 当期 + 下期


def test_activate_phase_opens_pending_and_computes_due(db):
    pid = _project(db, stage="before")
    assert not [t for t in db.list_phase_tasks(pid)
                if t["phase"] == "during" and t["status"] == "open"]
    n = affairs_service.activate_phase(db, pid, "during")
    assert n == 7  # recruitment during: 3 material + 2 report + 5 milestone - … 实为 10 条模板
    during = [t for t in db.list_phase_tasks(pid) if t["phase"] == "during"]
    assert all(t["status"] == "open" for t in during)
    assert all(t["due_date"] for t in during
               if t["task_type"] in ("material", "report"))


def test_on_file_parsed_completes_material_and_earliest_recurring(db):
    pid = _project(db)  # stage=after: recurring open,material 全 pending
    bp = next(t for t in db.list_phase_tasks(pid)
              if t["template_id"] == "playbook_recruitment_before_bp")
    db.update_phase_task(bp["id"], {"status": "open", "due_date": "2026-10-20"})
    done = affairs_service.on_file_parsed(db, pid, "XX科技商业计划书v3.pdf", "f1")
    assert done == 1
    assert db.get_phase_task(bp["id"])["status"] == "done"
    assert db.get_phase_task(bp["id"])["completed_by"] == "auto"
    # 幂等:已 done 不再作为候选
    assert affairs_service.on_file_parsed(db, pid, "BP.pdf", "f2") == 0
    # recurring:仅最早 open 期完成
    monthly_open = sorted(
        (t for t in db.list_phase_tasks(pid)
         if t["template_id"] == "playbook_recruitment_after_monthly_report"
         and t["status"] == "open"),
        key=lambda t: t["occurrence_key"])
    earliest = monthly_open[0]["occurrence_key"]
    assert affairs_service.on_file_parsed(db, pid, "9月月报.xlsx", "f3") == 1
    after = [t["occurrence_key"] for t in db.list_phase_tasks(pid)
             if t["template_id"] == "playbook_recruitment_after_monthly_report"
             and t["status"] == "done"]
    assert earliest in after


def test_on_report_completed_title_match_only(db):
    pid = _project(db, stage="before")
    initial = next(t for t in db.list_phase_tasks(pid)
                   if t["template_id"] == "playbook_recruitment_before_initial_report")
    db.update_phase_task(initial["id"], {"status": "open", "due_date": "2026-10-20"})

    def _report(title):
        rid = db._uuid()
        db.conn.execute(
            "INSERT INTO reports (id, project_id, template_id, title, output_format, status, created_at, updated_at) "
            "VALUES (?, ?, 't', ?, 'docx', 'done', '2026-10-09', '2026-10-09')",
            (rid, pid, title))
        db.conn.commit()
        return rid

    miss = _report("随便什么报告")
    hit = _report("企业初评报告")
    assert affairs_service.on_report_completed(db, miss) == 0  # 未命中保留手动
    assert affairs_service.on_report_completed(db, hit) == 1
    assert db.get_phase_task(initial["id"])["status"] == "done"


def test_on_report_completed_missing_report_silent(db):
    assert affairs_service.on_report_completed(db, "no-such-report") == 0


def test_on_file_parsed_swallows_db_errors(db):
    pid = _project(db)
    db.conn.execute("DROP TABLE phase_tasks")
    db.conn.commit()
    assert affairs_service.on_file_parsed(db, pid, "BP.pdf", "f1") == 0  # 不抛(§5.4)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `C:/Users/Kelvin/.conda/envs/inkcube/python.exe -m pytest python/tests/test_affairs_service.py -x -q`
Expected: FAIL——`ModuleNotFoundError: No module named 'affairs_service'`

- [ ] **Step 3: 实现** `python/affairs_service.py`(完整模块):

```python
"""python/affairs_service.py — 时期任务服务层(3.0 二期)

职责:周期节奏滚动生成 / 阶段切换激活 / 终态事件自动完成匹配。
惯例对齐 activity_events.py:模块级函数、自带 try/except 兜底仅记日志——
闭环是增强不是依赖(§5.4 异常语义),任何失败不阻塞解析/报告主流程。
不做 HTTP;路由层(routers/affairs.py)与终态声明点(pipeline/reports)直调。
"""
import calendar as _cal
import json
import logging
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


# ── 滚动生成器(裁决:lifespan 启动 + /activities/status check-date)──

_last_roll_date: str | None = None


def roll_recurring(db) -> int:
    try:
        return _roll_recurring_inner(db)
    except Exception:
        logger.warning("roll_recurring failed", exc_info=True)
        return 0


def _roll_recurring_inner(db) -> int:
    today = date.today()
    groups = db.conn.execute(
        """SELECT project_id, template_id FROM phase_tasks
           WHERE task_type='recurring' AND template_id IS NOT NULL
           GROUP BY project_id, template_id"""
    ).fetchall()
    created = 0
    for g in groups:
        rows = db.conn.execute(
            """SELECT id, occurrence_key, recurrence_rule FROM phase_tasks
               WHERE project_id=? AND template_id=? AND task_type='recurring'
               ORDER BY occurrence_key ASC""",
            (g["project_id"], g["template_id"]),
        ).fetchall()
        if not rows:
            continue
        # 裁决 4:节奏取最新期实例的 rule(改节奏仅影响未来期次)
        raw = rows[-1]["recurrence_rule"]
        try:
            rule = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            logger.warning("bad recurrence_rule on %s", rows[-1]["id"])
            continue
        if not isinstance(rule, dict) or not rule.get("freq"):
            continue
        cur_key, _ = current_occurrence(rule, today)
        nxt_key, _ = next_occurrence(rule, cur_key)
        existing = {r["occurrence_key"] for r in rows if r["occurrence_key"]}
        # 从组内最早期迭代到下期:补历史缺失 + 当期 + 下 1 期(裁决 3)
        key = rows[0]["occurrence_key"]
        proto = rows[-1]  # 新期次实例的列原型(同 project/phase/type/title/rule)
        for _ in range(_MAX_ROLL_PERIODS):
            if key > nxt_key:
                break
            if key not in existing:
                # 历史期 due 按 rule 逐期回推(各期 due 不同)
                w_key, w_due = cur_key, None
                _, w_due = current_occurrence(rule, today)
                while w_key > key:
                    w_key, w_due = prev_occurrence(rule, w_key)
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
        db.conn.commit()
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
            """SELECT pt.id, pt.task_type, tpl.due_offset_days, tpl.due_date_fixed
               FROM phase_tasks pt
               LEFT JOIN playbook_templates tpl ON tpl.id = pt.template_id
               WHERE pt.project_id = ? AND pt.phase = ? AND pt.status = 'pending_activation'""",
            (project_id, phase),
        ).fetchall()
        today = date.today()
        n = 0
        for r in rows:
            due = None
            if r["task_type"] != "recurring":
                if r["due_date_fixed"]:
                    due = r["due_date_fixed"]
                elif r["due_offset_days"]:
                    due = (today + timedelta(days=int(r["due_offset_days"]))).isoformat()
            db.conn.execute(
                "UPDATE phase_tasks SET status='open', due_date=? WHERE id=?",
                (due, r["id"]))
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
            earliest = min(recurring_hits, key=lambda r: r["occurrence_key"] or "")
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
```

注:`activate_phase` 激活数为「该时期全部 pending 实例数」(recruitment during = 10:3 material + 2 report + 5 milestone);测试断言 `n > 0` 亦可,上面按精确数断言,若模板后续迭代调整测试同步改。测试文件里 `test_activate_phase_opens_pending_and_computes_due` 断言写 `assert n > 0` 以免与种子条数强耦合(以实现时的实际为准,断言 during 全部 open + material/report 有 due 是强断言)。

- [ ] **Step 4: 跑测试确认通过**

Run: `C:/Users/Kelvin/.conda/envs/inkcube/python.exe -m pytest python/tests/test_affairs_service.py -x -q`
Expected: PASS(8 用例)

- [ ] **Step 5: 回归全量** `C:/Users/Kelvin/.conda/envs/inkcube/python.exe -m pytest python/tests -x -q`

- [ ] **Step 6: 提示用户提交**(建议信息:`feat(affairs): service layer — roll/activate/auto-complete matching (3.0 phase2 task3)`)

---

### Task 4: HTTP 路由 + 创建/激活/心跳挂钩 + IPC 通道

**Files:**
- Create: `python/routers/affairs.py`
- Modify: `python/main.py`(import + include_router)
- Modify: `python/routers/knowledge.py`(update_project 内 stage 激活挂钩)
- Modify: `python/routers/activities.py`(activities_status 入口 check-date roll)
- Modify: `electron/ipc-handlers.js`(affairs:* 4 通道)
- Test: `python/tests/test_affairs_api.py`

**Interfaces:**
- Consumes: Task 2 DB 方法、Task 3 `activate_phase`/`maybe_roll_daily`
- Produces(前端 Task 6+ 依赖):

```python
# python/routers/affairs.py — router = APIRouter(prefix="/projects", tags=["affairs"])
GET    /projects/{project_id}/tasks            → {"tasks": [...]}   # 全量(含 pending/skipped)
POST   /projects/{project_id}/tasks            body TaskIn  → {"task": {...}}
PATCH  /projects/{project_id}/tasks/{task_id}  body dict    → {"task": {...}}   # 404 if 不存在
DELETE /projects/{project_id}/tasks/{task_id}               → {"ok": True}
# TaskIn: title: str; task_type: 'material'|'report'|'milestone'|'recurring'
#         due_date: str|None; recurrence_rule: dict|None; auto_complete: dict|None; description: str=''
# 归时期:服务端读 project.stage(自建归当前时期,§6.1.7);recurring 自动铺当期实例
```

```js
// electron/ipc-handlers.js — registerIpcHandlers 内 wiki: 段之后新增(前缀裁决:affairs:*)
'affairs:listByProject' ({ projectId })            → GET  /projects/{projectId}/tasks
'affairs:createTask'    ({ projectId, ...fields })  → POST /projects/{projectId}/tasks
'affairs:updateTask'    ({ projectId, taskId, ...fields }) → PATCH /projects/{projectId}/tasks/{taskId}
'affairs:deleteTask'    ({ projectId, taskId })     → DELETE /projects/{projectId}/tasks/{taskId}
```

- [ ] **Step 1: 写失败测试** `python/tests/test_affairs_api.py`(Starlette TestClient 直挂 app;参考既有 test_reports_list_stage.py 的构造方式——若其使用 `from main import app` 则同款,否则用 `FastAPI()` 挂单 router + 伪造 `deps.db`):

```python
"""python/tests/test_affairs_api.py — 3.0 二期 Task 4:路由 + 挂钩 + check-date roll。"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import Database
from db.playbook_seed import get_all_playbook_templates


class _FakeDeps:
    db = None


@pytest.fixture()
def client_and_db(monkeypatch):
    import deps
    import main as main_mod
    from fastapi.testclient import TestClient
    with tempfile.TemporaryDirectory() as td:
        db = Database(os.path.join(td, "t.db"))
        db.initialize()
        db.seed_playbook_templates(get_all_playbook_templates())
        deps.db = db
        _FakeDeps.db = db
        # 单 router 挂载,避免拉起整个 main app 的 lifespan 依赖
        from fastapi import FastAPI
        from routers import affairs as affairs_router
        app = FastAPI()
        app.include_router(affairs_router.router)
        yield TestClient(app), db
        db.conn.close()


def test_list_and_crud(client_and_db):
    client, db = client_and_db
    pid = db.create_project(name="P", project_type="general", stage="before")["id"]
    r = client.get(f"/projects/{pid}/tasks")
    assert r.status_code == 200
    assert len(r.json()["tasks"]) == 2
    # create
    r = client.post(f"/projects/{pid}/tasks", json={
        "title": "拜访客户", "task_type": "milestone", "due_date": "2026-10-20"})
    assert r.status_code == 200
    tid = r.json()["task"]["id"]
    assert r.json()["task"]["phase"] == "before"  # 归当前时期
    # update(手动完成)
    r = client.patch(f"/projects/{pid}/tasks/{tid}", json={"status": "done"})
    assert r.json()["task"]["status"] == "done"
    # delete
    r = client.delete(f"/projects/{pid}/tasks/{tid}")
    assert r.json()["ok"] is True
    # 404
    assert client.patch(f"/projects/{pid}/tasks/{tid}", json={"status": "open"}).status_code == 404


def test_create_recurring_task_seeds_current_occurrence(client_and_db):
    client, db = client_and_db
    pid = db.create_project(name="P", project_type="general", stage="before")["id"]
    r = client.post(f"/projects/{pid}/tasks", json={
        "title": "周报", "task_type": "recurring",
        "recurrence_rule": {"freq": "monthly", "due_day": 20},
        "auto_complete": {"on": "file_parsed", "match": "周报"}})
    task = r.json()["task"]
    assert task["occurrence_key"] and task["due_date"]
    assert task["template_id"].startswith("user_")  # 裁决 2:自建节奏共享 id


def test_stage_change_activates_pending(client_and_db, monkeypatch):
    client, db = client_and_db
    pid = db.create_project(name="P", project_type="recruitment", stage="before")["id"]
    # 直接调服务(激活挂钩本体在 knowledge.py,路由级测厚覆盖度有限——挂钩行以
    # 「update_project 内 stage 变更时调用」落地,人工核验 + 本处验服务行为)
    import affairs_service
    n = affairs_service.activate_phase(db, pid, "during")
    assert n == 10  # recruitment during 模板数


def test_maybe_roll_daily_gate(monkeypatch):
    import affairs_service
    calls = []
    monkeypatch.setattr(affairs_service, "roll_recurring", lambda db: calls.append(1) or 0)
    affairs_service._last_roll_date = None
    assert affairs_service.maybe_roll_daily(None) is True
    assert affairs_service.maybe_roll_daily(None) is False  # 同日零开销
    assert len(calls) == 1
```

- [ ] **Step 2: 跑测试确认失败**(ModuleNotFoundError: routers.affairs)

- [ ] **Step 3: 实现 `python/routers/affairs.py`**:

```python
"""python/routers/affairs.py — 时期任务路由(3.0 二期)

挂 /projects/{project_id}/tasks(与 knowledge.py 的 /knowledge/projects 无冲突)。
IPC 通道前缀 affairs:*(裁决:tasks:* 已被 TaskAgent 长任务占用)。
"""
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import deps
import affairs_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/projects", tags=["affairs"])


def _db():
    return deps.db


class TaskIn(BaseModel):
    title: str
    task_type: str                      # material | report | milestone | recurring
    description: str = ""
    due_date: str | None = None
    recurrence_rule: dict | None = None
    auto_complete: dict | None = None


_TASK_TYPES = {"material", "report", "milestone", "recurring"}


@router.get("/{project_id}/tasks")
async def list_phase_tasks(project_id: str):
    return {"tasks": _db().list_phase_tasks(project_id)}


@router.post("/{project_id}/tasks")
async def create_phase_task(project_id: str, body: TaskIn):
    db = _db()
    if body.task_type not in _TASK_TYPES:
        raise HTTPException(status_code=422, detail="invalid task_type")
    if not body.title.strip():
        raise HTTPException(status_code=422, detail="title required")
    project = db.get_project(project_id)
    if not project or project.get("kind") != "project":
        raise HTTPException(status_code=404, detail="project not found")
    phase = affairs_service.phase_of(project.get("stage") or "none")
    task = db.create_phase_task(
        project_id=project_id, phase=phase, task_type=body.task_type,
        title=body.title.strip(), description=body.description,
        due_date=body.due_date, recurrence_rule=body.recurrence_rule,
        auto_complete=body.auto_complete,
    )
    return {"task": task}


@router.patch("/{project_id}/tasks/{task_id}")
async def update_phase_task(project_id: str, task_id: str, body: dict):
    db = _db()
    task = db.get_phase_task(task_id)
    if not task or task["project_id"] != project_id:
        raise HTTPException(status_code=404, detail="task not found")
    updated = db.update_phase_task(task_id, body or {})
    return {"task": updated}


@router.delete("/{project_id}/tasks/{task_id}")
async def delete_phase_task(project_id: str, task_id: str):
    db = _db()
    task = db.get_phase_task(task_id)
    if not task or task["project_id"] != project_id:
        raise HTTPException(status_code=404, detail="task not found")
    db.delete_phase_task(task_id)
    return {"ok": True}
```

- [ ] **Step 4: 挂钩三处**:

`main.py` L81 import 行加 `affairs`:

```python
from routers import settings, models, knowledge, activities, health, system, tasks, skills, reports, wiki, affairs
```

L276 `app.include_router(wiki.router)` 之后加 `app.include_router(affairs.router)`。

`knowledge.py` `update_project`(L660-678):`db.update_project(...)` 之前读旧 stage,变更时激活:

```python
        db = get_db()
        if body.name is None and body.description is None and body.type is None and body.stage is None:
            raise HTTPException(status_code=400, detail="No fields to update")
        # 3.0 §5.3: 阶段切换 → 激活该时期 pending_activation 实例(服务层自行兜底异常)
        if body.stage:
            _old = db.get_project(project_id)
            _stage_changed = bool(_old) and (_old.get("stage") != body.stage)
        db.update_project(
            project_id,
            name=body.name,
            description=body.description,
            type=body.type,
            stage=body.stage,
        )
        if body.stage and _stage_changed:
            import affairs_service
            affairs_service.activate_phase(db, project_id, body.stage)
        return {"success": True}
```

`activities.py` `activities_status`(L165)入口第一行后加:

```python
    # 3.0 §5.4 滚动生成器裁决:心跳入口 check-date roll(跨日即滚,同日零开销;
    # 复用既有心跳,不新增轮询端点;服务层自行兜底异常)
    import affairs_service
    affairs_service.maybe_roll_daily(db)
```

- [ ] **Step 5: IPC 通道**——`electron/ipc-handlers.js` wiki: 段之后(L1432 `wiki:getGraph` 之后任意位置,与 wiki 段相邻)追加:

```js
  // ── Affairs: 时期任务(3.0 二期;前缀裁决 tasks:* 已被 TaskAgent 占用)──
  ipcMain.handle('affairs:listByProject', async (_event, { projectId }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/projects/${projectId}/tasks`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('affairs:createTask', async (_event, { projectId, ...fields }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/projects/${projectId}/tasks`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title: fields.title,
        task_type: fields.taskType,
        description: fields.description || '',
        due_date: fields.dueDate || null,
        recurrence_rule: fields.recurrenceRule || null,
        auto_complete: fields.autoComplete || null,
      }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('affairs:updateTask', async (_event, { projectId, taskId, ...fields }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/projects/${projectId}/tasks/${taskId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title: fields.title,
        description: fields.description,
        due_date: fields.dueDate,
        status: fields.status,
        recurrence_rule: fields.recurrenceRule,
        auto_complete: fields.autoComplete,
      }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('affairs:deleteTask', async (_event, { projectId, taskId }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/projects/${projectId}/tasks/${taskId}`, {
      method: 'DELETE',
    })
    return handlePythonResponse(res)
  })
```

- [ ] **Step 6: 跑测试确认通过**

Run: `C:/Users/Kelvin/.conda/envs/inkcube/python.exe -m pytest python/tests/test_affairs_api.py python/tests/test_affairs_service.py -x -q`
Expected: PASS

- [ ] **Step 7: 回归全量**(Python pytest + `npx tsc --noEmit` 确认 JS 改动无类型影响)

- [ ] **Step 8: 提示用户提交**(建议信息:`feat(affairs): HTTP routes + IPC channels + stage/heartbeat hooks (3.0 phase2 task4)`)

---

### Task 5: 完成闭环直调接线(pipeline.py + reports.py)

**Files:**
- Modify: `python/knowledge/pipeline.py`(L134 complete_file 之后)
- Modify: `python/routers/reports.py`(L1063 complete_report 之后)
- Test: `python/tests/test_affairs_hooks.py`

**Interfaces:**
- Consumes: Task 3 `on_file_parsed(db, project_id, filename, file_id)` / `on_report_completed(db, report_id)`
- Produces: 行为闭环——上传命中文件名的文件解析成功 → material/recurring 任务自动 done;报告标题命中 → report 任务自动 done(均为 try/except 包裹仅记日志,不阻塞主流程)

- [ ] **Step 1: 写失败测试** `python/tests/test_affairs_hooks.py`:

```python
"""python/tests/test_affairs_hooks.py — 3.0 二期 Task 5:终态声明点直调接线。

验证方式:monkeypatch affairs_service.on_file_parsed / on_report_completed 捕获调用,
并直调真实服务函数验证完成行为(服务本体已在 Task 3 测过,此处验「挂钩存在且传参正确」)。
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import Database
from db.playbook_seed import get_all_playbook_templates


@pytest.fixture()
def db():
    with tempfile.TemporaryDirectory() as td:
        db = Database(os.path.join(td, "t.db"))
        db.initialize()
        db.seed_playbook_templates(get_all_playbook_templates())
        yield db
        db.conn.close()


def test_service_completes_task_on_file_parsed(db):
    """真实链路冒烟:铺入 → 激活 → 解析完成事件 → 任务 done。"""
    import affairs_service
    pid = db.create_project(name="P", project_type="recruitment", stage="before")["id"]
    done = affairs_service.on_file_parsed(db, pid, "企业BP最终版.pdf", "f1")
    assert done == 1
    bp = next(t for t in db.list_phase_tasks(pid)
              if t["template_id"] == "playbook_recruitment_before_bp")
    assert bp["status"] == "done" and bp["completed_by"] == "auto"
    assert bp["linked_entity_id"] == "f1"


def test_service_completes_task_on_report_completed(db):
    import affairs_service
    pid = db.create_project(name="P", project_type="general", stage="before")["id"]
    rid = db._uuid()
    db.conn.execute(
        "INSERT INTO reports (id, project_id, template_id, title, output_format, status, created_at, updated_at) "
        "VALUES (?, ?, 't', '项目分析报告', 'docx', 'done', '2026-10-09', '2026-10-09')",
        (rid, pid))
    db.conn.commit()
    assert affairs_service.on_report_completed(db, rid) == 1
    report_task = next(t for t in db.list_phase_tasks(pid) if t["task_type"] == "report")
    assert report_task["status"] == "done"


def test_hook_wiring_static():
    """静态断言:两个终态声明点文件含直调行(防实现者漏接线)。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pipeline = open(os.path.join(root, "knowledge", "pipeline.py"), encoding="utf-8").read()
    reports = open(os.path.join(root, "routers", "reports.py"), encoding="utf-8").read()
    assert "affairs_service.on_file_parsed" in pipeline
    assert "affairs_service.on_report_completed" in reports
```

- [ ] **Step 2: 跑测试确认失败**(`test_hook_wiring_static` FAIL——直调行不存在)

- [ ] **Step 3: 接线**——`pipeline.py` L134 `activity_events.complete_file(self.db, file_id, ...)` 之后插入:

```python
            # 3.0 二期完成闭环(§5.4 裁决:终态声明点旁直调;失败仅记日志不阻塞解析,
            # 任务保持 open 由手动兜底——闭环是增强不是依赖)
            try:
                import affairs_service
                affairs_service.on_file_parsed(self.db, project_id, filename, file_id)
            except Exception:
                self.logger.warning(
                    "affairs on_file_parsed failed for %s", file_id, exc_info=True)
```

(实现时确认 pipeline.py 的 logger 属性名——若为模块级 `logger` 则用 `logger.warning`;若类内无 logger,取 `logging.getLogger(__name__)`。)`

`reports.py` L1063 `activity_events.complete_report(db, report_id)` 之后插入:

```python
                # 3.0 二期完成闭环(§5.4 裁决:终态声明点旁直调;失败仅记日志)
                try:
                    import affairs_service
                    affairs_service.on_report_completed(db, report_id)
                except Exception:
                    logger.warning(
                        "affairs on_report_completed failed for %s", report_id,
                        exc_info=True)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `C:/Users/Kelvin/.conda/envs/inkcube/python.exe -m pytest python/tests/test_affairs_hooks.py python/tests/test_affairs_service.py -x -q`
Expected: PASS

- [ ] **Step 5: 回归全量** `C:/Users/Kelvin/.conda/envs/inkcube/python.exe -m pytest python/tests -x -q`(重点 test_degradation_warnings/test_activity_events——同文件改动区)

- [ ] **Step 6: 提示用户提交**(建议信息:`feat(affairs): wire auto-complete into file/report terminal events (3.0 phase2 task5)`)

---

### Task 6: 前端类型 + AffairsTab 容器 + 详情页接入(Tab/信号)

**Files:**
- Create: `src/features/project/affairs/types.ts`
- Create: `src/features/project/affairs/AffairsTab.tsx`
- Create: `src/features/project/affairs/AffairsTab.module.css`
- Modify: `src/features/project/ProjectDetailPage.tsx`(TABS 加 affairs、tabSignal 白名单、渲染 AffairsTab、边沿刷新信号)
- Modify: `src/i18n/locales/zh-CN.json` / `en-US.json`(`project.tab.affairs` + `affairs.*` 基础键)
- Test: `tests/AffairsTab.test.tsx`

**Interfaces:**
- Consumes: IPC `affairs:listByProject`;`useActivity` byProject(边沿检测)
- Produces(Task 7-11 依赖):

```ts
// src/features/project/affairs/types.ts
import type { ProjectStage } from '../types'

export type PhaseTaskType = 'material' | 'report' | 'milestone' | 'recurring'
export type PhaseTaskStatus = 'open' | 'done' | 'skipped' | 'pending_activation'
export type PhaseKey = 'before' | 'during' | 'after'

export interface RecurrenceRule { freq: 'monthly' | 'quarterly' | 'yearly'; due_day: number | 'end' | string }

export interface PhaseTask {
  id: string
  project_id: string
  phase: PhaseKey
  task_type: PhaseTaskType
  title: string
  description: string
  template_id: string | null
  status: PhaseTaskStatus
  occurrence_key: string | null
  due_date: string | null
  recurrence_rule: RecurrenceRule | null
  auto_complete: { on: string; match?: string } | null
  linked_entity_type: string | null
  linked_entity_id: string | null
  completed_at: string | null
  completed_by: string | null
  created_at: string
}

// 逾期/到期工具(全组件共用,纯函数便于测试)
export function isOverdue(task: PhaseTask, now?: Date): boolean
  // status==='open' && due_date && due_date.slice(0,10) < 当日 ISO
export function daysOverdue(task: PhaseTask, now?: Date): number   // 逾期天数(≥1)
export function dueWithin(task: PhaseTask, days: number, now?: Date): boolean  // 含今日起 7 日内到期

export type AffairsView = 'board' | 'rhythm' | 'calendar'
```

```tsx
// src/features/project/affairs/AffairsTab.tsx
export interface AffairsTabProps {
  projectId: string
  projectType: ProjectType
  stage: ProjectStage                 // 当前时期(阶段 pill 切换后父级下发)
  /** 父级边沿信号(解析/编译终态 useEdgeRefetch 时 +1):变化即重拉,零新增轮询 */
  refreshSignal?: number
  /** 执行抽屉打开 → 父级关 Chat(互斥,§6.4-7) */
  onPanelOpenChange?: (open: boolean) => void
  /** 抽屉「发起报告」→ 父级切 reports Tab(裁决 7) */
  onOpenReports?: () => void
}
export function AffairsTab(props: AffairsTabProps): JSX.Element
// 内部:tasks state + latestPidRef 快切守卫(同 WikiTab 模式)+ 挂载/refreshSignal/操作后拉取
// 三视图切换(view state,默认 board):PhaseBoard / RhythmCards / CalendarView(Task 7-9)
// TaskDrawer/TaskFormModal 状态提升到此(Task 10-11 接入)
```

- [ ] **Step 1: 写失败测试** `tests/AffairsTab.test.tsx`:

```tsx
/** tests/AffairsTab.test.tsx — 3.0 二期 Task 6:容器拉取/三视图切换/快切守卫 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

const invokeMock = vi.fn()
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, o?: { defaultValue?: string }) => o?.defaultValue ?? k,
    i18n: { language: 'zh-CN' },
  }),
}))
vi.mock('../src/features/activity/ActivityContext', () => ({
  useActivity: () => ({ byProject: new Map(), items: [] }),
}))
vi.mock('../src/features/project/affairs/AffairsTab.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, fireEvent, cleanup, act } from '@testing-library/react'
import { AffairsTab } from '../src/features/project/affairs/AffairsTab'
import type { PhaseTask } from '../src/features/project/affairs/types'

const task = (over: Partial<PhaseTask>): PhaseTask => ({
  id: 't1', project_id: 'p1', phase: 'before', task_type: 'material',
  title: '上传BP', description: '', template_id: 'tpl1', status: 'open',
  occurrence_key: null, due_date: null, recurrence_rule: null, auto_complete: null,
  linked_entity_type: null, linked_entity_id: null, completed_at: null,
  completed_by: null, created_at: '2026-10-09T00:00:00', ...over,
})

const flush = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await Promise.resolve() })
  }
}

beforeEach(() => {
  invokeMock.mockReset()
  invokeMock.mockImplementation(async (ch: string) =>
    ch === 'affairs:listByProject'
      ? { tasks: [task({}), task({ id: 't2', task_type: 'recurring', occurrence_key: '2026-10' })] }
      : {})
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})
afterEach(() => {
  cleanup()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

const base = { projectId: 'p1', projectType: 'recruitment' as const, stage: 'before' as const }

describe('AffairsTab container', () => {
  it('loads tasks on mount and switches three views', async () => {
    const { container, getByText } = render(<AffairsTab {...base} />)
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('affairs:listByProject', { projectId: 'p1' })
    expect(container.textContent).toContain('上传BP')
    fireEvent.click(getByText('affairs.viewRhythm'))
    expect(container.textContent).toContain('affairs.rhythmEmpty') // 无已收节奏时的空态(月报卡片在 Task 8)
    fireEvent.click(getByText('affairs.viewCalendar'))
    expect(container.querySelector('.calGrid')).toBeTruthy()
  })

  it('refetches when refreshSignal bumps', async () => {
    const { rerender } = render(<AffairsTab {...base} refreshSignal={0} />)
    await flush()
    expect(invokeMock).toHaveBeenCalledTimes(1)
    rerender(<AffairsTab {...base} refreshSignal={1} />)
    await flush()
    expect(invokeMock).toHaveBeenCalledTimes(2)
  })

  it('drops stale responses when switching projects fast', async () => {
    let resolveA: (v: unknown) => void
    invokeMock.mockImplementationOnce(() => new Promise((r) => { resolveA = r }))
    const { rerender, container } = render(<AffairsTab {...base} />)
    rerender(<AffairsTab {...base} projectId="p2" />)
    await flush()
    resolveA!({ tasks: [task({ title: 'STALE-A' })] })
    await flush()
    expect(container.textContent).not.toContain('STALE-A')
  })
})
```

注:第三个用例依赖组件对旧响应丢弃后按新 projectId 重拉的实现——若 p2 的重拉 mock 未配置返回,断言「不含 STALE-A」即守卫生效的充分条件。

- [ ] **Step 2: 跑测试确认失败**(模块不存在)

Run: `npx vitest run tests/AffairsTab.test.tsx`

- [ ] **Step 3: 实现 types.ts(Step「Interfaces」中的完整代码)+ AffairsTab 容器**:

```tsx
/** src/features/project/affairs/AffairsTab.tsx — 事务 Tab 容器(3.0 §6.1.3-6.1.5)
 *  三视图切换:时期看板(默认)/ 周期节奏 / 日历;数据 affairs:listByProject 全量拉取,
 *  刷新时机 = 挂载 + refreshSignal 边沿 + 操作后回调(0.4 §3.1 零新增轮询);
 *  快切项目守卫 latestPidRef(同 WikiTab 模式,3.0 一期 T3+4 裁决复用)。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { CalendarDays, LayoutGrid, Repeat } from 'lucide-react'
import type { ProjectStage, ProjectType } from '../types'
import { isOverdue, dueWithin } from './types'
import type { AffairsView, PhaseTask } from './types'
import { PhaseBoard } from './PhaseBoard'
import { RhythmCards } from './RhythmCards'
import { CalendarView } from './CalendarView'
import { TaskDrawer } from './TaskDrawer'
import { TaskFormModal } from './TaskFormModal'
import s from './AffairsTab.module.css'

export interface AffairsTabProps {
  projectId: string
  projectType: ProjectType
  stage: ProjectStage
  refreshSignal?: number
  onPanelOpenChange?: (open: boolean) => void
  onOpenReports?: () => void
}

export function AffairsTab({ projectId, projectType, stage, refreshSignal, onPanelOpenChange, onOpenReports }: AffairsTabProps) {
  const { t } = useTranslation()
  const [tasks, setTasks] = useState<PhaseTask[]>([])
  const [view, setView] = useState<AffairsView>('board')
  const [drawerTask, setDrawerTask] = useState<PhaseTask | null>(null)
  const [formTask, setFormTask] = useState<PhaseTask | null | undefined>(undefined)
  // undefined=关闭;null=新建;PhaseTask=编辑预填
  const latestPidRef = useRef(projectId)
  useEffect(() => { latestPidRef.current = projectId }, [projectId])

  const load = useCallback(async () => {
    const pid = projectId
    try {
      const r: any = await window.electronAPI.invoke('affairs:listByProject', { projectId: pid })
      if (latestPidRef.current !== pid) return
      setTasks(r?.tasks ?? [])
    } catch (err) {
      console.error('Failed to load affairs:', err)
    }
  }, [projectId])

  useEffect(() => { void load() }, [load])
  useEffect(() => { if (refreshSignal !== undefined && refreshSignal > 0) void load() }, [refreshSignal, load])

  const handleChanged = useCallback(() => { void load() }, [load])
  const openDrawer = useCallback((task: PhaseTask) => {
    setDrawerTask(task)
    onPanelOpenChange?.(true)
  }, [onPanelOpenChange])
  const closeDrawer = useCallback(() => {
    setDrawerTask(null)
    onPanelOpenChange?.(false)
  }, [onPanelOpenChange])

  const VIEWS: { key: AffairsView; labelKey: string; Icon: typeof CalendarDays }[] = [
    { key: 'board', labelKey: 'affairs.viewBoard', Icon: LayoutGrid },
    { key: 'rhythm', labelKey: 'affairs.viewRhythm', Icon: Repeat },
    { key: 'calendar', labelKey: 'affairs.viewCalendar', Icon: CalendarDays },
  ]

  return (
    <div className={s.container}>
      <div className={s.viewSwitch}>
        {VIEWS.map(({ key, labelKey, Icon }) => (
          <button key={key}
            className={`${s.viewBtn} ${view === key ? s.viewBtnActive : ''}`}
            onClick={() => setView(key)}>
            <Icon size={14} /><span>{t(labelKey)}</span>
          </button>
        ))}
      </div>
      {view === 'board' && (
        <PhaseBoard tasks={tasks} stage={stage} projectType={projectType}
          onCreateTask={() => setFormTask(null)}
          onOpenDrawer={openDrawer} />
      )}
      {view === 'rhythm' && (
        <RhythmCards tasks={tasks} onOpenDrawer={openDrawer}
          onEditRhythm={(task) => setFormTask(task)} />
      )}
      {view === 'calendar' && (
        <CalendarView tasks={tasks} onOpenDrawer={openDrawer}
          onCreateTask={(prefill) => setFormTask(null)} />
      )}
      {drawerTask && (
        <TaskDrawer task={drawerTask} projectId={projectId}
          onClose={closeDrawer} onChanged={handleChanged}
          onEdit={() => setFormTask(drawerTask)}
          onOpenReports={() => { closeDrawer(); onOpenReports?.() }} />
      )}
      {formTask !== undefined && (
        <TaskFormModal projectId={projectId}
          initial={formTask}
          onClose={() => setFormTask(undefined)}
          onSaved={handleChanged} />
      )}
    </div>
  )
}
```

注:PhaseBoard/RhythmCards/CalendarView/TaskDrawer/TaskFormModal 在 Task 7-11 实现——本任务先建**最小占位实现**(各组件返回 `null` 或空容器 + 导出名),保证本任务可独立编译测试;Task 7-11 逐一替换为真实现并各自带测试。(占位组件不写逻辑,不算占位符计划——真实现在后续任务的完整代码块中。)

CSS(`AffairsTab.module.css`)按 0.3 §4.3 token 写:`.container`(纵向布局)、`.viewSwitch`(横排按钮组)、`.viewBtn`/`.viewBtnActive`(accent 高亮,沿用 ProjectDetailPage `.tab`/`.tabActive` 视觉口径)。

- [ ] **Step 4: 详情页接入**——`ProjectDetailPage.tsx`:

```tsx
// L4 import 行(图标):
import { FolderOpen, Library, FileText, ClipboardList, CalendarDays } from 'lucide-react'
// L9 后新增:
import { AffairsTab } from './affairs/AffairsTab'
// L29 TabKey:
type TabKey = 'materials' | 'wiki' | 'affairs' | 'reports' | 'forms'
// L31 TABS(事务第二位,§2.1):
const TABS: { key: TabKey; labelKey: string; Icon: typeof FolderOpen }[] = [
  { key: 'wiki', labelKey: 'project.tab.wiki', Icon: Library },
  { key: 'affairs', labelKey: 'project.tab.affairs', Icon: CalendarDays },
  { key: 'materials', labelKey: 'project.tab.materials', Icon: FolderOpen },
  { key: 'reports', labelKey: 'project.tab.reports', Icon: FileText },
  { key: 'forms', labelKey: 'project.tab.forms', Icon: ClipboardList },
]
// L101 tabSignal 白名单:
if (tabSignal?.tab && ['materials', 'wiki', 'affairs', 'reports', 'forms'].includes(tabSignal.tab)) {
// L68 区域新增(解析边沿信号,0.4 心跳派生,零新增轮询):
const filesActive = (byProject.get(project.id)?.files ?? 0) > 0
const [affairsRefresh, setAffairsRefresh] = useState(0)
useEdgeRefetch(filesActive, () => { setAffairsRefresh((x) => x + 1) })
// 渲染区(L351 reports 分支之前)新增:
{activeTab === 'affairs' && (
  <AffairsTab
    projectId={project.id}
    projectType={projectType}
    stage={currentStage}
    refreshSignal={affairsRefresh}
    onPanelOpenChange={(open) => { if (open) setChatOpen(false) }}
    onOpenReports={() => setActiveTab('reports')}
  />
)}
```

i18n(zh-CN / en-US 同步,`project.tab` 段与新增 `affairs` 顶层段):

```json
"tab": { ..., "affairs": "事务" }        // en: "Affairs"
"affairs": {
  "viewBoard": "时期看板", "viewRhythm": "周期节奏", "viewCalendar": "日历",
  "rhythmEmpty": "暂无周期事务",
  "calendarEmpty": "本月无安排"
}
// en: "Board" / "Rhythm" / "Calendar" / "No recurring affairs" / "Nothing scheduled this month"
```

- [ ] **Step 5: 跑测试确认通过**

Run: `npx vitest run tests/AffairsTab.test.tsx tests/ProjectDetailPage.test.tsx` 与 `npx tsc --noEmit`
Expected: PASS(既有 ProjectDetailPage 用例不受影响——默认 tab 仍 wiki、tabPulse 断言按 className)

- [ ] **Step 6: 提示用户提交**(建议信息:`feat(affairs): tab container + detail page integration (3.0 phase2 task6)`)

### Task 7: PhaseBoard 时期看板

**Files:**
- Create: `src/features/project/affairs/PhaseBoard.tsx`
- Create: `src/features/project/affairs/PhaseBoard.module.css`
- Modify: `src/i18n/locales/zh-CN.json` / `en-US.json`(`affairs.*` 看板键)
- Test: `tests/PhaseBoard.test.tsx`

**Interfaces:**
- Consumes: `PhaseTask`/`isOverdue`/`daysOverdue`/`dueWithin`(affairs/types.ts,Task 6);`STAGE_MAP`/`ProjectType`/`ProjectStage`(../types);替换 Task 6 建的占位组件
- Produces(AffairsTab 容器消费,签名与 Task 6 接线一致):

```ts
export interface PhaseBoardProps {
  tasks: PhaseTask[]
  stage: ProjectStage            // 当前时期:进度条高亮;变更时自动展开该组(§6.4-2 仅展开不激活)
  projectType: ProjectType
  onCreateTask: () => void
  onOpenDrawer: (task: PhaseTask) => void
}
export function PhaseBoard(props: PhaseBoardProps): JSX.Element
```

- [ ] **Step 1: 写失败测试** `tests/PhaseBoard.test.tsx`:

```tsx
/** tests/PhaseBoard.test.tsx — 3.0 二期 Task 7:时期分组/折叠/状态色/pending 灰态/general 单组 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, o?: { defaultValue?: string }) => o?.defaultValue ?? k }),
}))
vi.mock('../src/features/project/affairs/PhaseBoard.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, fireEvent, cleanup } from '@testing-library/react'
import { PhaseBoard } from '../src/features/project/affairs/PhaseBoard'
import type { PhaseTask } from '../src/features/project/affairs/types'

const task = (over: Partial<PhaseTask>): PhaseTask => ({
  id: 't1', project_id: 'p1', phase: 'before', task_type: 'material',
  title: '上传BP', description: '', template_id: 'tpl1', status: 'open',
  occurrence_key: null, due_date: null, recurrence_rule: null, auto_complete: null,
  linked_entity_type: null, linked_entity_id: null, completed_at: null,
  completed_by: null, created_at: '2026-10-09T00:00:00', ...over,
})

const base = { stage: 'before' as const, projectType: 'recruitment' as const,
  onCreateTask: vi.fn(), onOpenDrawer: vi.fn() }

beforeEach(() => vi.clearAllMocks())
afterEach(() => cleanup())

describe('PhaseBoard', () => {
  it('groups by phase, expands current stage, collapses others to header counts', () => {
    const tasks = [
      task({}),
      task({ id: 't2', phase: 'during', title: '尽调材料' }),
      task({ id: 't3', phase: 'after', title: '企业月报', task_type: 'recurring', occurrence_key: '2026-10' }),
    ]
    const { container } = render(<PhaseBoard {...base} tasks={tasks} />)
    const text = container.textContent!
    expect(text).toContain('上传BP')                              // 当前期展开
    expect(text).not.toContain('尽调材料')                          // 其他时期折叠为计数
    expect(text).toContain('project.stage.recruitmentDuring')     // 折叠组头仍可见
  })

  it('marks overdue row with error class and days text', () => {
    const { container } = render(<PhaseBoard {...base} tasks={[task({ due_date: '2026-10-01' })]} />)
    expect(container.querySelector('.rowErr')).toBeTruthy()
    expect(container.textContent).toContain('affairs.overdueDays')
  })

  it('renders general type as flat list without phase bar', () => {
    const { container } = render(
      <PhaseBoard {...base} projectType="general" stage="none"
        tasks={[task({ title: '通用资料' }), task({ id: 't9', title: '通用报告', task_type: 'report' })]} />
    )
    expect(container.textContent).toContain('通用资料')
    expect(container.textContent).toContain('通用报告')
    expect(container.querySelector('.phaseBar')).toBeNull()
  })

  it('renders pending_activation dimmed with hint text', () => {
    const { container } = render(
      <PhaseBoard {...base} tasks={[task({ status: 'pending_activation', title: '推介方案' })]} />
    )
    expect(container.querySelector('.rowPending')).toBeTruthy()
    expect(container.textContent).toContain('affairs.pendingHint')
  })

  it('expands collapsed group on header click (view-only, §6.4-2)', () => {
    const tasks = [task({}), task({ id: 't2', phase: 'during', title: '尽调材料' })]
    const { container, getByText } = render(<PhaseBoard {...base} tasks={tasks} />)
    expect(container.textContent).not.toContain('尽调材料')
    fireEvent.click(getByText('project.stage.recruitmentDuring'))
    expect(container.textContent).toContain('尽调材料')   // 仅展开视图,不激活任务
  })

  it('fires onCreateTask and onOpenDrawer', () => {
    const { getByTitle, container } = render(<PhaseBoard {...base} tasks={[task()]} />)
    fireEvent.click(getByTitle('affairs.addTask'))
    expect(base.onCreateTask).toHaveBeenCalled()
    fireEvent.click(container.querySelector('.row')!)
    expect(base.onOpenDrawer).toHaveBeenCalledWith(expect.objectContaining({ id: 't1' }))
  })
})
```

- [ ] **Step 2: 跑测试确认失败**(占位组件返回 null)

Run: `npx vitest run tests/PhaseBoard.test.tsx`

- [ ] **Step 3: 实现** `src/features/project/affairs/PhaseBoard.tsx`:

```tsx
/** src/features/project/affairs/PhaseBoard.tsx — 时期看板(3.0 §6.1.3)
 *  顶部时期进度条仅展示当前期高亮(general 无时期则不渲染);任务按时期分组,
 *  当前期默认展开、其余折叠为计数;阶段 pill 切换仅自动展开对应组,不激活任务(§6.4-2)。
 *  行状态色(§6.3):逾期 --error / 7 日内 --warning / 进行中 --accent /
 *  done 灰 / skipped 灰划线 / pending_activation 灰斜体「等待进入时期」。
 *  已跳过默认隐藏,开关切换;组内 due_date 升序、无 due 殿后。
 */
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus } from 'lucide-react'
import type { ProjectStage, ProjectType } from '../types'
import { STAGE_MAP } from '../types'
import { daysOverdue, dueWithin, isOverdue } from './types'
import type { PhaseKey, PhaseTask } from './types'
import s from './PhaseBoard.module.css'

const PHASES: PhaseKey[] = ['before', 'during', 'after']

export interface PhaseBoardProps {
  tasks: PhaseTask[]
  stage: ProjectStage
  projectType: ProjectType
  onCreateTask: () => void
  onOpenDrawer: (task: PhaseTask) => void
}

export function PhaseBoard({ tasks, stage, projectType, onCreateTask, onOpenDrawer }: PhaseBoardProps) {
  const { t } = useTranslation()
  const [openPhases, setOpenPhases] = useState<Set<PhaseKey>>(
    () => new Set(stage !== 'none' ? [stage as PhaseKey] : PHASES),
  )
  const [showSkipped, setShowSkipped] = useState(false)

  // 阶段 pill 切换 → 自动展开对应组(仅展开视图,不激活任务,§6.4-2;不收缩已展开组)
  useEffect(() => {
    if (stage !== 'none') setOpenPhases((prev) => new Set([...prev, stage as PhaseKey]))
  }, [stage])

  const visible = useMemo(
    () => tasks.filter((x) => showSkipped || x.status !== 'skipped'),
    [tasks, showSkipped],
  )
  const phaseLabelKey = (p: PhaseKey): string =>
    STAGE_MAP[projectType].find((x) => x.key === p)?.labelKey ?? `project.stage.${p}`

  const rowClass = (task: PhaseTask): string => {
    if (task.status === 'done') return s.rowDone
    if (task.status === 'skipped') return s.rowSkipped
    if (task.status === 'pending_activation') return s.rowPending
    if (isOverdue(task)) return s.rowErr
    if (dueWithin(task, 7)) return s.rowWarn
    return s.rowOpen
  }
  const dueText = (task: PhaseTask): string => {
    if (task.status === 'pending_activation') return t('affairs.pendingHint')
    if (!task.due_date) return '—'
    if (isOverdue(task)) return t('affairs.overdueDays', { count: daysOverdue(task) })
    return task.due_date.slice(5, 10)
  }

  const togglePhase = (p: PhaseKey) => {
    setOpenPhases((prev) => {
      const next = new Set(prev)
      if (next.has(p)) next.delete(p)
      else next.add(p)
      return next
    })
  }

  return (
    <div className={s.container}>
      <div className={s.head}>
        {STAGE_MAP[projectType].length > 0 && (
          <div className={s.phaseBar}>
            {PHASES.map((p) => (
              <span key={p} className={`${s.phaseSeg} ${stage === p ? s.phaseSegActive : ''}`}>
                {t(phaseLabelKey(p))}
              </span>
            ))}
          </div>
        )}
        <div className={s.headActions}>
          <label className={s.skippedToggle}>
            <input type="checkbox" checked={showSkipped} onChange={(e) => setShowSkipped(e.target.checked)} />
            <span>{t('affairs.showSkipped')}</span>
          </label>
          <button className={s.addBtn} onClick={onCreateTask} title={t('affairs.addTask')}>
            <Plus size={14} /><span>{t('affairs.addTask')}</span>
          </button>
        </div>
      </div>
      {visible.length === 0 && <div className={s.empty}>{t('affairs.boardEmpty')}</div>}
      {PHASES.map((p) => {
        const group = visible
          .filter((x) => x.phase === p)
          .sort((a, b) => (a.due_date ?? '9999').localeCompare(b.due_date ?? '9999'))
        if (group.length === 0) return null
        const hasPhases = STAGE_MAP[projectType].length > 0
        const open = !hasPhases || openPhases.has(p)
        return (
          <div key={p} className={s.phaseGroup}>
            {hasPhases && (
              <button className={s.groupHead} onClick={() => togglePhase(p)}>
                <span>{t(phaseLabelKey(p))}</span>
                <span className={s.groupCount}>{group.length}</span>
                <span className={s.caret}>{open ? '▾' : '▸'}</span>
              </button>
            )}
            {open && (
              <div className={s.rows}>
                {group.map((task) => (
                  <button key={task.id}
                    className={`${s.row} ${rowClass(task)}`}
                    onClick={() => onOpenDrawer(task)}>
                    <span className={s.dot} />
                    <span className={s.rowTitle}>{task.title}</span>
                    {task.task_type === 'recurring' && task.occurrence_key && (
                      <span className={s.occ}>{task.occurrence_key}</span>
                    )}
                    <span className={s.typeBadge}>{t(`affairs.type.${task.task_type}`)}</span>
                    <span className={s.due}>{dueText(task)}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
```

CSS(`PhaseBoard.module.css`)按 0.3 §4.3 token:`.phaseBar` 横排三段(`.phaseSegActive` 用 `--accent` 底/白字),`.rowErr` 左边框 `var(--error)`,`.rowWarn` `var(--warning)`,`.rowOpen` 点用 `var(--accent)`,`.rowDone`/`.rowSkipped` 标题 `var(--text-muted)` + line-through(仅 skipped),`.rowPending` 斜体 + `var(--text-muted)`,行 hover `var(--bg-hover)`。

- [ ] **Step 4: i18n**——`affairs` 段增量(zh-CN / en-US 同步):

```json
"boardEmpty": "该项目暂无任务",          // en: "No tasks yet"
"showSkipped": "显示已跳过",            // en: "Show skipped"
"addTask": "添加任务",                 // en: "Add task"
"pendingHint": "等待进入时期",          // en: "Pending phase"
"overdueDays": "已逾期 {{count}} 天",    // en: "{{count}} days overdue"
"type": {
  "material": "资料", "report": "报告", "milestone": "里程碑", "recurring": "周期"
  // en: "Material" / "Report" / "Milestone" / "Recurring"
}
```

- [ ] **Step 5: 跑测试确认通过**

Run: `npx vitest run tests/PhaseBoard.test.tsx tests/AffairsTab.test.tsx` 与 `npx tsc --noEmit`
Expected: PASS(AffairsTab 容器用例不回归)

- [ ] **Step 6: 提示用户提交**(建议信息:`feat(affairs): phase board view (3.0 phase2 task7)`)

---

### Task 8: RhythmCards 周期节奏卡

**Files:**
- Create: `src/features/project/affairs/RhythmCards.tsx`
- Create: `src/features/project/affairs/RhythmCards.module.css`
- Modify: `src/i18n/locales/zh-CN.json` / `en-US.json`(`affairs.*` 节奏键)
- Test: `tests/RhythmCards.test.tsx`

**Interfaces:**
- Consumes: `PhaseTask`/`RecurrenceRule`/`isOverdue`/`dueWithin`(affairs/types.ts);替换 Task 6 占位组件
- Produces:

```ts
export interface RhythmCardsProps {
  tasks: PhaseTask[]
  onOpenDrawer: (task: PhaseTask) => void      // 点期次格 → 该期实例抽屉
  onEditRhythm: (task: PhaseTask) => void      // 「调整节奏」→ 编辑最新期实例(裁决 4:改节奏仅影响未来期)
}
export function RhythmCards(props: RhythmCardsProps): JSX.Element
```

- [ ] **Step 1: 写失败测试** `tests/RhythmCards.test.tsx`:

```tsx
/** tests/RhythmCards.test.tsx — 3.0 二期 Task 8:聚合/期次横排/空态/回调 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, o?: { defaultValue?: string }) => o?.defaultValue ?? k }),
}))
vi.mock('../src/features/project/affairs/RhythmCards.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, fireEvent, cleanup } from '@testing-library/react'
import { RhythmCards } from '../src/features/project/affairs/RhythmCards'
import type { PhaseTask } from '../src/features/project/affairs/types'

const task = (over: Partial<PhaseTask>): PhaseTask => ({
  id: 't1', project_id: 'p1', phase: 'after', task_type: 'recurring',
  title: '企业月报', description: '', template_id: 'tpl_monthly', status: 'open',
  occurrence_key: '2026-10', due_date: '2026-10-15',
  recurrence_rule: { freq: 'monthly', due_day: 15 }, auto_complete: null,
  linked_entity_type: null, linked_entity_id: null, completed_at: null,
  completed_by: null, created_at: '2026-10-09T00:00:00', ...over,
})

const base = { onOpenDrawer: vi.fn(), onEditRhythm: vi.fn() }

beforeEach(() => vi.clearAllMocks())
afterEach(() => cleanup())

describe('RhythmCards', () => {
  it('groups by template_id and renders one card per rhythm', () => {
    const tasks = [
      task({ id: 'a1', occurrence_key: '2026-09', status: 'done' }),
      task({ id: 'a2', occurrence_key: '2026-10' }),
      task({ id: 'b1', title: '企业季报', template_id: 'tpl_q', occurrence_key: '2026-Q4',
        recurrence_rule: { freq: 'quarterly', due_day: 'end' } }),
    ]
    const { container } = render(<RhythmCards {...base} tasks={tasks} />)
    expect(container.querySelectorAll('.card').length).toBe(2)
    expect(container.querySelectorAll('.occCell').length).toBe(3)   // 2 期 + 1 期
    expect(container.textContent).toContain('affairs.freqMonthly')
    expect(container.textContent).toContain('affairs.freqQuarterly')
  })

  it('shows empty state without recurring tasks', () => {
    const { container } = render(<RhythmCards {...base} tasks={[task({ recurrence_rule: null, task_type: 'material' })]} />)
    expect(container.textContent).toContain('affairs.rhythmEmpty')
    expect(container.querySelector('.card')).toBeNull()
  })

  it('fires onOpenDrawer per occurrence and onEditRhythm with latest', () => {
    const tasks = [
      task({ id: 'a1', occurrence_key: '2026-09', status: 'done' }),
      task({ id: 'a2', occurrence_key: '2026-10' }),
    ]
    const { container } = render(<RhythmCards {...base} tasks={tasks} />)
    const cells = container.querySelectorAll('.occCell')
    fireEvent.click(cells[0])
    expect(base.onOpenDrawer).toHaveBeenCalledWith(expect.objectContaining({ id: 'a1' }))
    fireEvent.click(container.querySelector('.editBtn')!)
    expect(base.onEditRhythm).toHaveBeenCalledWith(expect.objectContaining({ id: 'a2' }))  // 最新期
  })

  it('marks overdue occurrence with error class', () => {
    const { container } = render(
      <RhythmCards {...base} tasks={[task({ id: 'x1', occurrence_key: '2026-09', due_date: '2026-09-15', status: 'open' })]} />
    )
    expect(container.querySelector('.occErr')).toBeTruthy()
  })
})
```

- [ ] **Step 2: 跑测试确认失败**(占位组件返回 null)

Run: `npx vitest run tests/RhythmCards.test.tsx`

- [ ] **Step 3: 实现** `src/features/project/affairs/RhythmCards.tsx`:

```tsx
/** src/features/project/affairs/RhythmCards.tsx — 周期节奏卡(3.0 §6.1.4)
 *  按 template_id 聚合周期任务(自建 recurring 共享 user_* 模板键,同样聚合);
 *  卡头节奏文案取组内最新期(MAX occurrence_key)实例 —— 后端滚动生成器已保证
 *  未来期次携带最新 rule(裁决 4);卡内横排近 6 期状态,点期次开抽屉,「调整节奏」进编辑。
 */
import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Repeat, Settings2 } from 'lucide-react'
import { isOverdue, dueWithin } from './types'
import type { PhaseTask, RecurrenceRule } from './types'
import s from './RhythmCards.module.css'

export interface RhythmCardsProps {
  tasks: PhaseTask[]
  onOpenDrawer: (task: PhaseTask) => void
  onEditRhythm: (task: PhaseTask) => void
}

interface RhythmGroup {
  key: string
  title: string
  rule: RecurrenceRule
  latest: PhaseTask
  recent: PhaseTask[]                       // occurrence_key 升序最后 6 期
}

export function RhythmCards({ tasks, onOpenDrawer, onEditRhythm }: RhythmCardsProps) {
  const { t } = useTranslation()
  const groups = useMemo<RhythmGroup[]>(() => {
    const byKey = new Map<string, PhaseTask[]>()
    for (const task of tasks) {
      if (!task.recurrence_rule) continue
      const key = task.template_id ?? task.id
      const list = byKey.get(key) ?? []
      list.push(task)
      byKey.set(key, list)
    }
    return [...byKey.entries()].map(([key, list]) => {
      const sorted = [...list].sort((a, b) =>
        (a.occurrence_key ?? '').localeCompare(b.occurrence_key ?? ''))  // 字典序即时间序
      const latest = sorted[sorted.length - 1]
      return { key, title: latest.title, rule: latest.recurrence_rule!, latest, recent: sorted.slice(-6) }
    })
  }, [tasks])

  if (groups.length === 0) return <div className={s.empty}>{t('affairs.rhythmEmpty')}</div>

  const freqLabel = (rule: RecurrenceRule): string =>
    rule.freq === 'monthly' ? t('affairs.freqMonthly')
      : rule.freq === 'quarterly' ? t('affairs.freqQuarterly')
        : t('affairs.freqYearly')

  const dueLabel = (rule: RecurrenceRule): string => {
    if (rule.freq === 'monthly') return t('affairs.dueDayOfMonth', { day: String(rule.due_day) })
    if (rule.freq === 'quarterly') {
      return rule.due_day === 'end'
        ? t('affairs.dueDayQuarterEnd')
        : t('affairs.dueDayOfQuarter', { day: String(rule.due_day) })
    }
    return t('affairs.dueDayOfYear', { date: String(rule.due_day) })
  }

  const occCellClass = (task: PhaseTask): string => {
    if (task.status === 'done') return s.occDone
    if (task.status === 'skipped') return s.occSkipped
    if (isOverdue(task)) return s.occErr
    if (dueWithin(task, 7)) return s.occWarn
    return s.occOpen
  }
  const occShort = (key: string | null): string => {
    if (!key) return '—'
    if (/^\d{4}-\d{2}$/.test(key)) return key.slice(5) + t('affairs.occMonthSuffix')  // 10月 / 10
    if (/^\d{4}-Q\d$/.test(key)) return key.slice(5)                                   // Q4
    return key
  }

  return (
    <div className={s.container}>
      {groups.map((g) => (
        <div key={g.key} className={s.card}>
          <div className={s.cardHead}>
            <Repeat size={14} />
            <span className={s.cardTitle}>{g.title}</span>
            <span className={s.freqBadge}>{freqLabel(g.rule)} · {dueLabel(g.rule)}</span>
            <button className={s.editBtn} onClick={() => onEditRhythm(g.latest)} title={t('affairs.editRhythm')}>
              <Settings2 size={13} /><span>{t('affairs.editRhythm')}</span>
            </button>
          </div>
          <div className={s.occRow}>
            {g.recent.map((task) => (
              <button key={task.id}
                className={`${s.occCell} ${occCellClass(task)}`}
                onClick={() => onOpenDrawer(task)}
                title={task.occurrence_key ?? undefined}>
                <span className={s.occKey}>{occShort(task.occurrence_key)}</span>
                <span className={s.occDot} />
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
```

CSS token 口径:`.occErr` 点 `var(--error)`、`.occWarn` `var(--warning)`、`.occOpen` `var(--accent)`、`.occDone` `var(--text-muted)`、`.occSkipped` 灰 + opacity 0.5;`.freqBadge` 用 `var(--bg-secondary)` 底圆角。

- [ ] **Step 4: i18n**——`affairs` 段增量(zh-CN / en-US 同步):

```json
"freqMonthly": "每月",                    // en: "Monthly"
"freqQuarterly": "每季",                  // en: "Quarterly"
"freqYearly": "每年",                    // en: "Yearly"
"dueDayOfMonth": "每月 {{day}} 日前",      // en: "by day {{day}}"
"dueDayQuarterEnd": "每季度末前",          // en: "by quarter end"
"dueDayOfQuarter": "季度 {{day}} 日前",    // en: "by day {{day}} of quarter"
"dueDayOfYear": "每年 {{date}} 前",        // en: "by {{date}} yearly"
"editRhythm": "调整节奏",                 // en: "Adjust rhythm"
"occMonthSuffix": "月"                   // en: ""(期次短标签后缀)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `npx vitest run tests/RhythmCards.test.tsx tests/AffairsTab.test.tsx` 与 `npx tsc --noEmit`
Expected: PASS(AffairsTab 容器用例不回归——其 mock 数据无 recurrence_rule,rhythmEmpty 空态断言仍成立)

- [ ] **Step 6: 提示用户提交**(建议信息:`feat(affairs): rhythm cards view (3.0 phase2 task8)`)

### Task 9: CalendarView 日历视图

**Files:**
- Create: `src/features/project/affairs/CalendarView.tsx`
- Create: `src/features/project/affairs/CalendarView.module.css`
- Modify: `src/i18n/locales/zh-CN.json` / `en-US.json`(`affairs.*` 日历键)
- Test: `tests/CalendarView.test.tsx`

**Interfaces:**
- Consumes: `PhaseTask`/`isOverdue`(affairs/types.ts);替换 Task 6 占位组件(AffairsTab 测试已断言 `.calGrid` 类名,本组件网格类名必须叫 `calGrid`)
- Produces:

```ts
export interface CalendarViewProps {
  tasks: PhaseTask[]
  onOpenDrawer: (task: PhaseTask) => void
  /** 点格空白:携带该日 YYYY-MM-DD 供表单预填(Task 10 经 AffairsTab 接线) */
  onCreateTask: (prefillDate: string) => void
}
export function CalendarView(props: CalendarViewProps): JSX.Element
```

- [ ] **Step 1: 写失败测试** `tests/CalendarView.test.tsx`:

```tsx
/** tests/CalendarView.test.tsx — 3.0 二期 Task 9:网格/任务条/点格预填/月切换 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, o?: { defaultValue?: string }) => o?.defaultValue ?? k }),
}))
vi.mock('../src/features/project/affairs/CalendarView.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, fireEvent, cleanup } from '@testing-library/react'
import { CalendarView } from '../src/features/project/affairs/CalendarView'
import type { PhaseTask } from '../src/features/project/affairs/types'

const pad2 = (n: number): string => String(n).padStart(2, '0')
const now = new Date()
const month15 = `${now.getFullYear()}-${pad2(now.getMonth() + 1)}-15`

const task = (over: Partial<PhaseTask>): PhaseTask => ({
  id: 't1', project_id: 'p1', phase: 'before', task_type: 'material',
  title: '上传BP', description: '', template_id: 'tpl1', status: 'open',
  occurrence_key: null, due_date: null, recurrence_rule: null, auto_complete: null,
  linked_entity_type: null, linked_entity_id: null, completed_at: null,
  completed_by: null, created_at: '2026-10-09T00:00:00', ...over,
})

const base = { onOpenDrawer: vi.fn(), onCreateTask: vi.fn() }

beforeEach(() => vi.clearAllMocks())
afterEach(() => cleanup())

describe('CalendarView', () => {
  it('renders 42-cell grid with 7 dow headers and today highlight', () => {
    const { container } = render(<CalendarView {...base} tasks={[]} />)
    expect(container.querySelector('.calGrid')).toBeTruthy()
    expect(container.querySelectorAll('.dow').length).toBe(7)
    expect(container.querySelectorAll('.cell').length).toBe(42)
    expect(container.querySelector('.cellToday')).toBeTruthy()
  })

  it('renders due task in its day cell and opens drawer on click', () => {
    const { container, getByText } = render(
      <CalendarView {...base} tasks={[task({ due_date: month15 })]} />
    )
    expect(container.textContent).toContain('上传BP')
    fireEvent.click(getByText('上传BP').closest('button')!)
    expect(base.onOpenDrawer).toHaveBeenCalledWith(expect.objectContaining({ id: 't1' }))
  })

  it('fires onCreateTask with the clicked date on empty cell', () => {
    const { container } = render(<CalendarView {...base} tasks={[]} />)
    fireEvent.click(container.querySelectorAll('.cell')[10])
    expect(base.onCreateTask).toHaveBeenCalledWith(
      expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/))
  })

  it('caps entries per cell at 3 with +N overflow, done entries dimmed', () => {
    const tasks = [1, 2, 3, 4].map((i) =>
      task({ id: `m${i}`, title: `任务${i}`, due_date: month15 }))
    tasks.push(task({ id: 'd1', title: '已完成项', due_date: month15, status: 'done' }))
    const { container } = render(<CalendarView {...base} tasks={tasks} />)
    expect(container.textContent!).toContain('+2')     // 5 条 → 显 3 + +2
    expect(container.querySelector('.evDone')).toBeTruthy()
  })

  it('navigates months and back to today', () => {
    const { container, getByText } = render(<CalendarView {...base} tasks={[]} />)
    const before = container.querySelector('.calTitle')!.textContent
    fireEvent.click(container.querySelector('.navBtn')!)
    expect(container.querySelector('.calTitle')!.textContent).not.toBe(before)
    fireEvent.click(getByText('affairs.calToday'))
    expect(container.querySelector('.calTitle')!.textContent).toBe(before)
  })
})
```

注:第 4 用例的今日格定位兜底不可靠也无妨——「+2 与 .evDone 存在」的断言在整月视图中即充分;若当月 15 日非当月(不可能——month15 恒取当月),无需再处理。

- [ ] **Step 2: 跑测试确认失败**(占位组件返回 null)

Run: `npx vitest run tests/CalendarView.test.tsx`

- [ ] **Step 3: 实现** `src/features/project/affairs/CalendarView.tsx`:

```tsx
/** src/features/project/affairs/CalendarView.tsx — 日历视图(3.0 §6.1.5)
 *  月历网格(固定 6 行 42 格,前后月补灰):格内按 due_date 分组显示任务条
 *  (≤3 条 +N,done 灰、逾期红);点任务条开抽屉(stopPropagation 防冒泡),
 *  点格空白以该日预填新建(经父级 AffairsTab → TaskFormModal);今日高亮,可回今天。
 */
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { isOverdue } from './types'
import type { PhaseTask } from './types'
import s from './CalendarView.module.css'

export interface CalendarViewProps {
  tasks: PhaseTask[]
  onOpenDrawer: (task: PhaseTask) => void
  onCreateTask: (prefillDate: string) => void
}

const pad2 = (n: number): string => String(n).padStart(2, '0')
const dsOf = (d: Date): string => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`

export function CalendarView({ tasks, onOpenDrawer, onCreateTask }: CalendarViewProps) {
  const { t } = useTranslation()
  const today = new Date()
  const [cur, setCur] = useState({ y: today.getFullYear(), m: today.getMonth() })

  // 42 格:当月首日所在周的周日起,含前后月补位
  const cells = useMemo(() => {
    const first = new Date(cur.y, cur.m, 1)
    const start = new Date(cur.y, cur.m, 1 - first.getDay())
    const out: { ds: string; inMonth: boolean; day: number }[] = []
    for (let i = 0; i < 42; i++) {
      const d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i)
      out.push({ ds: dsOf(d), inMonth: d.getMonth() === cur.m, day: d.getDate() })
    }
    return out
  }, [cur])

  const byDate = useMemo(() => {
    const map = new Map<string, PhaseTask[]>()
    for (const task of tasks) {
      if (!task.due_date || task.status === 'pending_activation') continue
      const key = task.due_date.slice(0, 10)
      const list = map.get(key) ?? []
      list.push(task)
      map.set(key, list)
    }
    return map
  }, [tasks])

  const todayStr = dsOf(today)

  return (
    <div className={s.container}>
      <div className={s.calHead}>
        <button className={s.navBtn}
          onClick={() => setCur(({ y, m }) => (m === 0 ? { y: y - 1, m: 11 } : { y, m: m - 1 }))}>
          <ChevronLeft size={14} />
        </button>
        <span className={s.calTitle}>{t('affairs.calMonth', { year: cur.y, month: cur.m + 1 })}</span>
        <button className={s.navBtn}
          onClick={() => setCur(({ y, m }) => (m === 11 ? { y: y + 1, m: 0 } : { y, m: m + 1 }))}>
          <ChevronRight size={14} />
        </button>
        <button className={s.todayBtn} onClick={() => setCur({ y: today.getFullYear(), m: today.getMonth() })}>
          {t('affairs.calToday')}
        </button>
      </div>
      <div className={s.calGrid}>
        {[0, 1, 2, 3, 4, 5, 6].map((i) => (
          <div key={i} className={s.dow}>{t(`affairs.dow${i}`)}</div>
        ))}
        {cells.map(({ ds, inMonth, day }) => {
          const items = byDate.get(ds) ?? []
          const shown = items.slice(0, 3)
          return (
            <div key={ds}
              className={`${s.cell} ${inMonth ? '' : s.cellOut} ${ds === todayStr ? s.cellToday : ''}`}
              onClick={() => onCreateTask(ds)}>
              <span className={s.dayNum}>{day}</span>
              {shown.map((task) => (
                <button key={task.id}
                  className={`${s.evBtn} ${task.status === 'done' ? s.evDone : isOverdue(task) ? s.evErr : ''}`}
                  onClick={(e) => { e.stopPropagation(); onOpenDrawer(task) }}>
                  {task.title}
                </button>
              ))}
              {items.length > 3 && <span className={s.evMore}>+{items.length - 3}</span>}
            </div>
          )
        })}
      </div>
    </div>
  )
}
```

CSS token 口径:`.cellToday` 外框 `var(--accent)`,`.cellOut` 文字 `var(--text-muted)` + opacity 0.5,`.evErr` 底 `var(--error)` 浅底/红字,`.evDone` `var(--text-muted)` + line-through;格 hover `var(--bg-hover)` 提示可点。

- [ ] **Step 4: i18n**——`affairs` 段增量(zh-CN / en-US 同步):

```json
"calMonth": "{{year}} 年 {{month}} 月",      // en: "{{month}}/{{year}}"
"calToday": "今天",                        // en: "Today"
"dow0": "日", "dow1": "一", "dow2": "二", "dow3": "三",
"dow4": "四", "dow5": "五", "dow6": "六"     // en: Sun/Mon/Tue/Wed/Thu/Fri/Sat
```

- [ ] **Step 5: 跑测试确认通过**

Run: `npx vitest run tests/CalendarView.test.tsx tests/AffairsTab.test.tsx` 与 `npx tsc --noEmit`
Expected: PASS(AffairsTab「切到日历→.calGrid」用例即真实现,不回归)

- [ ] **Step 6: 提示用户提交**(建议信息:`feat(affairs): calendar view (3.0 phase2 task9)`)

---

### Task 10: TaskFormModal 新建/编辑表单

**Files:**
- Create: `src/features/project/affairs/TaskFormModal.tsx`
- Create: `src/features/project/affairs/TaskFormModal.module.css`
- Modify: `src/features/project/affairs/AffairsTab.tsx`(接线:stage 传参 + 日历预填 state)
- Modify: `electron/ipc-handlers.js`(`affairs:updateTask` 通道补 `phase` 字段——编辑改时期)
- Modify: `src/i18n/locales/zh-CN.json` / `en-US.json`(`affairs.form.*`)
- Test: `tests/TaskFormModal.test.tsx`

**Interfaces:**
- Consumes: `PhaseTask`/`RecurrenceRule`/`PhaseTaskType`/`PhaseKey`(affairs/types.ts);IPC `affairs:createTask`/`affairs:updateTask`(Task 4);替换 Task 6 占位组件
- Produces:

```ts
export interface TaskFormModalProps {
  projectId: string
  /** 新建默认时期来源(=详情页当前 stage;POST 服务端亦按项目 stage 推导归期,二者一致) */
  stage: ProjectStage
  /** null=新建;PhaseTask=编辑预填(父级 AffairsTab 以 undefined 控制不渲染) */
  initial: PhaseTask | null
  /** 日历点格预填截止日(Task 9 → AffairsTab → 本组件) */
  prefillDueDate?: string | null
  onClose: () => void
  onSaved: () => void
}
export function TaskFormModal(props: TaskFormModalProps): JSX.Element
```

- 字段裁决(§6.1.6):**新建不显示时期字段**——POST 由服务端按项目当前 stage 推导归期(Task 4 路由已定,归当前时期);**编辑显示时期下拉**——PATCH `update_phase_task` 白名单含 phase。编辑态**类型下拉禁用**——`update_phase_task` 白名单不含 task_type(实例与模板解耦,类型不可变)。recurring 类型切为节奏字段(频率 + due_day:1-31 数字 | `end` | `MM-DD`),隐藏截止日。

- [ ] **Step 1: 写失败测试** `tests/TaskFormModal.test.tsx`:

```tsx
/** tests/TaskFormModal.test.tsx — 3.0 二期 Task 10:新建/节奏字段/编辑预填/预填截止日 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const invokeMock = vi.fn()
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, o?: { defaultValue?: string }) => o?.defaultValue ?? k }),
}))
vi.mock('../src/features/project/affairs/TaskFormModal.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, fireEvent, cleanup, act } from '@testing-library/react'
import { TaskFormModal } from '../src/features/project/affairs/TaskFormModal'
import type { PhaseTask } from '../src/features/project/affairs/types'

const task = (over: Partial<PhaseTask>): PhaseTask => ({
  id: 't1', project_id: 'p1', phase: 'before', task_type: 'material',
  title: '上传BP', description: '', template_id: 'tpl1', status: 'open',
  occurrence_key: null, due_date: null, recurrence_rule: null, auto_complete: null,
  linked_entity_type: null, linked_entity_id: null, completed_at: null,
  completed_by: null, created_at: '2026-10-09T00:00:00', ...over,
})

const flush = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await Promise.resolve() })
  }
}

const base = { projectId: 'p1', stage: 'before' as const,
  onClose: vi.fn(), onSaved: vi.fn() }

beforeEach(() => {
  vi.clearAllMocks()
  invokeMock.mockResolvedValue({ task: {} })
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})
afterEach(() => {
  cleanup()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

describe('TaskFormModal', () => {
  it('creates milestone with title and optional due date', async () => {
    const { getByPlaceholderText, getByText, container } = render(
      <TaskFormModal {...base} initial={null} />)
    fireEvent.change(getByPlaceholderText('affairs.form.titleLabel'),
      { target: { value: '拜访客户' } })
    fireEvent.change(container.querySelector('input[type="date"]')!,
      { target: { value: '2026-10-20' } })
    fireEvent.click(getByText('affairs.form.save'))
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('affairs:createTask', expect.objectContaining({
      projectId: 'p1', title: '拜访客户', taskType: 'material', dueDate: '2026-10-20',
    }))
    expect(base.onSaved).toHaveBeenCalled()
    expect(base.onClose).toHaveBeenCalled()
  })

  it('hides phase select on create (server derives from project stage)', () => {
    const { container } = render(<TaskFormModal {...base} initial={null} />)
    expect(container.textContent).not.toContain('affairs.form.phaseLabel')
  })

  it('rejects invalid due_day and submits valid recurrence on create', async () => {
    const { getByPlaceholderText, getByText, container } = render(
      <TaskFormModal {...base} initial={null} />)
    fireEvent.change(getByPlaceholderText('affairs.form.titleLabel'),
      { target: { value: '周报' } })
    fireEvent.change(getByText('affairs.type.material').closest('select')!,
      { target: { value: 'recurring' } })
    fireEvent.change(getByPlaceholderText('affairs.form.dueDayPlaceholder'),
      { target: { value: 'abc' } })
    fireEvent.click(getByText('affairs.form.save'))
    await flush()
    expect(container.textContent).toContain('affairs.form.dueDayInvalid')
    expect(invokeMock).not.toHaveBeenCalled()

    fireEvent.change(getByPlaceholderText('affairs.form.dueDayPlaceholder'),
      { target: { value: '20' } })
    fireEvent.click(getByText('affairs.form.save'))
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('affairs:createTask', expect.objectContaining({
      recurrenceRule: { freq: 'monthly', due_day: 20 },
    }))
  })

  it('prefills from initial, disables type select, sends phase on edit', async () => {
    const { getByDisplayValue, getByText } = render(
      <TaskFormModal {...base} initial={task({ title: '尽调材料', phase: 'during', due_date: '2026-10-18' })} />)
    expect(getByDisplayValue('尽调材料')).toBeTruthy()
    const typeSelect = getByText('affairs.type.material').closest('select')!
    expect(typeSelect).toBeDisabled()
    fireEvent.change(getByText('affairs.form.phaseLabel').closest('.fieldRow')!
      .querySelector('select')!, { target: { value: 'after' } })
    fireEvent.click(getByText('affairs.form.save'))
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('affairs:updateTask', expect.objectContaining({
      taskId: 't1', phase: 'after', dueDate: '2026-10-18',
    }))
  })

  it('prefills due date from calendar prefill prop', () => {
    const { container } = render(
      <TaskFormModal {...base} initial={null} prefillDueDate="2026-11-05" />)
    expect((container.querySelector('input[type="date"]') as HTMLInputElement).value).toBe('2026-11-05')
  })
})
```

- [ ] **Step 2: 跑测试确认失败**(占位组件返回 null)

Run: `npx vitest run tests/TaskFormModal.test.tsx`

- [ ] **Step 3: 实现** `src/features/project/affairs/TaskFormModal.tsx`:

```tsx
/** src/features/project/affairs/TaskFormModal.tsx — 任务新建/编辑表单(3.0 §6.1.6)
 *  新建:归当前时期(服务端 POST 按项目 stage 推导,表单不出现时期字段);
 *  编辑:时期可改(PATCH 白名单 phase),类型禁改(白名单无 task_type)。
 *  recurring 类型切换为节奏字段(频率 + due_day:1-31|end|MM-DD),隐藏截止日;
 *  模板带入的 auto_complete 原样保留回传(实例与模板解耦,§6.4-1)。
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { ProjectStage } from '../types'
import type { PhaseKey, PhaseTask, PhaseTaskType, RecurrenceRule } from './types'
import s from './TaskFormModal.module.css'

export interface TaskFormModalProps {
  projectId: string
  stage: ProjectStage
  initial: PhaseTask | null
  prefillDueDate?: string | null
  onClose: () => void
  onSaved: () => void
}

const TASK_TYPES: PhaseTaskType[] = ['material', 'report', 'milestone', 'recurring']
const FREQS: RecurrenceRule['freq'][] = ['monthly', 'quarterly', 'yearly']
const PHASES: PhaseKey[] = ['before', 'during', 'after']

export function TaskFormModal({ projectId, stage, initial, prefillDueDate, onClose, onSaved }: TaskFormModalProps) {
  const { t } = useTranslation()
  const [title, setTitle] = useState(initial?.title ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [taskType, setTaskType] = useState<PhaseTaskType>(initial?.task_type ?? 'material')
  const [phase, setPhase] = useState<PhaseKey>(initial?.phase ?? (stage !== 'none' ? stage : 'before'))
  const [dueDate, setDueDate] = useState(initial?.due_date?.slice(0, 10) ?? prefillDueDate ?? '')
  const isRecurring = taskType === 'recurring'
  const [freq, setFreq] = useState<RecurrenceRule['freq']>(initial?.recurrence_rule?.freq ?? 'monthly')
  const [dueDay, setDueDay] = useState<string>(
    initial?.recurrence_rule ? String(initial.recurrence_rule.due_day) : '15')
  const [dueDayError, setDueDayError] = useState(false)
  const [saving, setSaving] = useState(false)

  const parseDueDay = (raw: string): number | 'end' | string | null => {
    const v = raw.trim()
    if (v === 'end') return 'end'
    if (/^\d{1,2}$/.test(v)) {
      const n = Number(v)
      return n >= 1 && n <= 31 ? n : null
    }
    if (/^\d{2}-\d{2}$/.test(v)) return v
    return null
  }

  const submit = async () => {
    if (!title.trim() || saving) return
    let recurrenceRule: RecurrenceRule | null = null
    let due: string | null = dueDate || null
    if (isRecurring) {
      const day = parseDueDay(dueDay)
      if (day == null) { setDueDayError(true); return }
      setDueDayError(false)
      recurrenceRule = { freq, due_day: day }
      due = null
    }
    setSaving(true)
    try {
      if (initial) {
        await window.electronAPI.invoke('affairs:updateTask', {
          projectId, taskId: initial.id,
          title: title.trim(), description,
          dueDate: due, phase,
          recurrenceRule,
          autoComplete: initial.auto_complete,   // 模板带入的完成规则原样保留(§6.4-1)
        })
      } else {
        await window.electronAPI.invoke('affairs:createTask', {
          projectId, title: title.trim(), taskType, description,
          dueDate: due, recurrenceRule,
        })
      }
      onSaved()
      onClose()
    } catch (err) {
      console.error('Failed to save task:', err)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className={s.overlay} onClick={onClose}>
      <div className={s.modal} onClick={(e) => e.stopPropagation()}>
        <h3 className={s.title}>{initial ? t('affairs.form.editTitle') : t('affairs.form.newTitle')}</h3>
        <input className={s.input} placeholder={t('affairs.form.titleLabel')}
          value={title} onChange={(e) => setTitle(e.target.value)} autoFocus />
        <textarea className={s.textarea} placeholder={t('affairs.form.descLabel')}
          value={description} onChange={(e) => setDescription(e.target.value)} />
        <div className={s.fieldRow}>
          <label className={s.label}>{t('affairs.form.typeLabel')}</label>
          <select className={s.input} value={taskType} disabled={!!initial}
            onChange={(e) => setTaskType(e.target.value as PhaseTaskType)}>
            {TASK_TYPES.map((tt) => <option key={tt} value={tt}>{t(`affairs.type.${tt}`)}</option>)}
          </select>
        </div>
        {initial && (
          <div className={s.fieldRow}>
            <label className={s.label}>{t('affairs.form.phaseLabel')}</label>
            <select className={s.input} value={phase}
              onChange={(e) => setPhase(e.target.value as PhaseKey)}>
              {PHASES.map((p) => <option key={p} value={p}>{t(`project.stage.${p}`)}</option>)}
            </select>
          </div>
        )}
        {isRecurring ? (
          <div className={s.fieldRow}>
            <label className={s.label}>{t('affairs.form.rhythmLabel')}</label>
            <select className={s.freqSelect} value={freq}
              onChange={(e) => setFreq(e.target.value as RecurrenceRule['freq'])}>
              {FREQS.map((f) => <option key={f} value={f}>{t(`affairs.freq${f[0].toUpperCase()}${f.slice(1)}`)}</option>)}
            </select>
            <input className={s.dayInput} value={dueDay}
              onChange={(e) => setDueDay(e.target.value)}
              placeholder={t('affairs.form.dueDayPlaceholder')} />
          </div>
        ) : (
          <div className={s.fieldRow}>
            <label className={s.label}>{t('affairs.form.dueLabel')}</label>
            <input type="date" className={s.input} value={dueDate}
              onChange={(e) => setDueDate(e.target.value)} />
          </div>
        )}
        {dueDayError && <p className={s.error}>{t('affairs.form.dueDayInvalid')}</p>}
        <div className={s.actions}>
          <button className={s.btnCancel} onClick={onClose}>{t('affairs.form.cancel')}</button>
          <button className={s.btnCreate} disabled={!title.trim() || saving}
            onClick={() => void submit()}>
            {saving ? t('affairs.form.saving') : t('affairs.form.save')}
          </button>
        </div>
      </div>
    </div>
  )
}
```

注意频率选项文案键构造:`freq${首字母大写}`(monthly→`affairs.freqMonthly`),与 Task 8 徽标共用同一组键。

- [ ] **Step 4: 接线三处**

`AffairsTab.tsx`(Task 6 代码,三处精确修改):

```tsx
// ① formTask state 之后新增:
const [formPrefill, setFormPrefill] = useState<string | null>(null)
// ② board 视图回调:
onCreateTask={() => { setFormPrefill(null); setFormTask(null) }}
// ③ calendar 视图回调(prefill 接住,不再丢弃):
onCreateTask={(d) => { setFormPrefill(d); setFormTask(null) }}
// ④ TaskFormModal 渲染改为:
<TaskFormModal projectId={projectId} stage={stage} initial={formTask}
  prefillDueDate={formPrefill}
  onClose={() => { setFormPrefill(null); setFormTask(undefined) }}
  onSaved={handleChanged} />
```

`electron/ipc-handlers.js` `affairs:updateTask` body 增加 `phase: fields.phase,`(与 title/description 平级)。

- [ ] **Step 5: i18n**——`affairs` 段新增 `form` 子对象(zh-CN / en-US 同步):

```json
"form": {
  "newTitle": "新建任务", "editTitle": "编辑任务",       // en: "New Task" / "Edit Task"
  "titleLabel": "标题", "descLabel": "描述(可选)",      // en: "Title" / "Description (optional)"
  "typeLabel": "类型", "phaseLabel": "时期",            // en: "Type" / "Phase"
  "rhythmLabel": "节奏", "dueLabel": "截止日",          // en: "Rhythm" / "Due date"
  "dueDayPlaceholder": "15 / end / 04-30",
  "dueDayInvalid": "格式:1-31 数字、end 或 MM-DD",      // en: "1-31, end, or MM-DD"
  "cancel": "取消", "save": "保存", "saving": "保存中..." // en: "Cancel" / "Save" / "Saving..."
}
```

- [ ] **Step 6: 跑测试确认通过**

Run: `npx vitest run tests/TaskFormModal.test.tsx tests/AffairsTab.test.tsx` 与 `npx tsc --noEmit`
Expected: PASS(AffairsTab 容器用例不回归——新增接线不影响挂载/切换/守卫行为)

- [ ] **Step 7: 提示用户提交**(建议信息:`feat(affairs): task form modal with rhythm editor (3.0 phase2 task10)`)

### Task 11: TaskDrawer 执行抽屉

**Files:**
- Create: `src/features/project/affairs/TaskDrawer.tsx`
- Create: `src/features/project/affairs/TaskDrawer.module.css`
- Modify: `src/i18n/locales/zh-CN.json` / `en-US.json`(`affairs.status.*` + `affairs.drawer.*`)
- Test: `tests/TaskDrawer.test.tsx`

**Interfaces:**
- Consumes: `PhaseTask`/`isOverdue`/`daysOverdue`(affairs/types.ts);`useConfirm`(`src/components/ConfirmDialog`,与 App/BaseLibFooter 同源,全局唯一确认层);IPC `affairs:updateTask`/`affairs:deleteTask`(Task 4);替换 Task 6 占位组件
- Produces(签名与 Task 6 接线一致):

```ts
export interface TaskDrawerProps {
  task: PhaseTask
  projectId: string
  onClose: () => void
  /** 任一写操作成功后回调(父级重拉列表) */
  onChanged: () => void
  onEdit: () => void
  onOpenReports: () => void
}
export function TaskDrawer(props: TaskDrawerProps): JSX.Element
```

- 交互裁决(§6.1.7 + 裁决 7/9):右侧滑出面板复用 WikiTab 预览面板模式(拖宽 260-720px、遮罩点击收起);打开时关 Chat 由父级 onPanelOpenChange 处理(§6.4-7,Task 6 已接);report 类显示「发起报告」→ 切 reports Tab(裁决 7);material 类 v1 显示上传引导文案不内置文件选择(裁决 9 延伸:闭环挂在解析终态,从素材 Tab 上传同样触发自动完成,无需重复建设上传链路);关联文件名不显示(裁决 9)。

- [ ] **Step 1: 写失败测试** `tests/TaskDrawer.test.tsx`:

```tsx
/** tests/TaskDrawer.test.tsx — 3.0 二期 Task 11:完成/跳过/恢复/删除确认/发起报告 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const invokeMock = vi.fn()
const confirmMock = vi.fn()
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, o?: { defaultValue?: string }) => o?.defaultValue ?? k }),
}))
vi.mock('../src/components/ConfirmDialog', () => ({
  useConfirm: () => confirmMock,
}))
vi.mock('../src/features/project/affairs/TaskDrawer.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, fireEvent, cleanup, act } from '@testing-library/react'
import { TaskDrawer } from '../src/features/project/affairs/TaskDrawer'
import type { PhaseTask } from '../src/features/project/affairs/types'

const task = (over: Partial<PhaseTask>): PhaseTask => ({
  id: 't1', project_id: 'p1', phase: 'before', task_type: 'material',
  title: '上传BP', description: '', template_id: 'tpl1', status: 'open',
  occurrence_key: null, due_date: null, recurrence_rule: null, auto_complete: null,
  linked_entity_type: null, linked_entity_id: null, completed_at: null,
  completed_by: null, created_at: '2026-10-09T00:00:00', ...over,
})

const flush = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await Promise.resolve() })
  }
}

const base = { projectId: 'p1', onClose: vi.fn(), onChanged: vi.fn(),
  onEdit: vi.fn(), onOpenReports: vi.fn() }

beforeEach(() => {
  vi.clearAllMocks()
  invokeMock.mockResolvedValue({ task: {} })
  confirmMock.mockResolvedValue(true)
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})
afterEach(() => {
  cleanup()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

describe('TaskDrawer', () => {
  it('marks open task done via updateTask and notifies parent', async () => {
    const { getByText } = render(<TaskDrawer {...base} task={task()} />)
    fireEvent.click(getByText('affairs.drawer.done').closest('button')!)
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('affairs:updateTask',
      expect.objectContaining({ projectId: 'p1', taskId: 't1', status: 'done' }))
    expect(base.onChanged).toHaveBeenCalled()
  })

  it('skips open task and offers reopen for done', async () => {
    const { getByText } = render(<TaskDrawer {...base} task={task()} />)
    fireEvent.click(getByText('affairs.drawer.skip').closest('button')!)
    await flush()
    expect(invokeMock).toHaveBeenCalledWith('affairs:updateTask',
      expect.objectContaining({ status: 'skipped' }))
    const { getByText: get2 } = render(<TaskDrawer {...base} task={task({ status: 'done' })} />)
    expect(get2('affairs.drawer.reopen')).toBeTruthy()
  })

  it('deletes via confirm dialog (danger) and closes', async () => {
    const { getByText } = render(<TaskDrawer {...base} task={task()} />)
    fireEvent.click(getByText('affairs.drawer.delete').closest('button')!)
    await flush()
    expect(confirmMock).toHaveBeenCalledWith(expect.objectContaining({ danger: true }))
    expect(invokeMock).toHaveBeenCalledWith('affairs:deleteTask',
      expect.objectContaining({ projectId: 'p1', taskId: 't1' }))
    expect(base.onClose).toHaveBeenCalled()
  })

  it('shows start-report for report tasks and fires onOpenReports', () => {
    const { getByText, queryByText } = render(
      <TaskDrawer {...base} task={task({ task_type: 'report', title: '初评报告' })} />)
    expect(queryByText('affairs.drawer.uploadHint')).toBeNull()   // 仅 material 显示
    fireEvent.click(getByText('affairs.drawer.startReport').closest('button')!)
    expect(base.onOpenReports).toHaveBeenCalled()
  })

  it('shows upload hint and auto-complete rule text for material', () => {
    const { container } = render(
      <TaskDrawer {...base} task={task({
        auto_complete: { on: 'file_parsed', match: 'BP|商业计划' } })} />)
    const text = container.textContent!
    expect(text).toContain('affairs.drawer.uploadHint')
    expect(text).toContain('affairs.drawer.ruleFile')
  })
})
```

- [ ] **Step 2: 跑测试确认失败**(占位组件返回 null)

Run: `npx vitest run tests/TaskDrawer.test.tsx`

- [ ] **Step 3: 实现** `src/features/project/affairs/TaskDrawer.tsx`:

```tsx
/** src/features/project/affairs/TaskDrawer.tsx — 执行抽屉(3.0 §6.1.7)
 *  右侧滑出面板,复用 WikiTab 预览面板模式:拖宽 260-720px、遮罩点击收起;
 *  与 Chat 互斥由父级 onPanelOpenChange 处理(§6.4-7)。
 *  动作:完成/跳过/恢复/编辑/删除(useConfirm danger)/发起报告(report 类,裁决 7);
 *  material 类 v1 显示上传引导文案(裁决 9 延伸:闭环挂解析终态,不内置上传);
 *  关联文件名不显示(裁决 9);完成规则文案含 auto_done 来源标注。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Check, SkipForward, RotateCcw, Pencil, Trash2, FileText } from 'lucide-react'
import { useConfirm } from '../../../components/ConfirmDialog'
import { daysOverdue, isOverdue } from './types'
import type { PhaseTask } from './types'
import s from './TaskDrawer.module.css'

const MIN_W = 260
const MAX_W = 720
const DEFAULT_W = 420

export interface TaskDrawerProps {
  task: PhaseTask
  projectId: string
  onClose: () => void
  onChanged: () => void
  onEdit: () => void
  onOpenReports: () => void
}

export function TaskDrawer({ task, projectId, onClose, onChanged, onEdit, onOpenReports }: TaskDrawerProps) {
  const { t } = useTranslation()
  const confirmDialog = useConfirm()
  const [width, setWidth] = useState(DEFAULT_W)
  const [busy, setBusy] = useState(false)
  const dragging = useRef(false)
  const startX = useRef(0)
  const startW = useRef(DEFAULT_W)

  const onDragDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    dragging.current = true
    startX.current = e.clientX
    startW.current = width
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }, [width])

  useEffect(() => {
    const move = (e: MouseEvent) => {
      if (!dragging.current) return
      const delta = startX.current - e.clientX
      setWidth(Math.min(MAX_W, Math.max(MIN_W, startW.current + delta)))
    }
    const up = () => {
      if (!dragging.current) return
      dragging.current = false
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
    document.addEventListener('mousemove', move)
    document.addEventListener('mouseup', up)
    return () => {
      document.removeEventListener('mousemove', move)
      document.removeEventListener('mouseup', up)
    }
  }, [])

  const patch = async (fields: Record<string, unknown>) => {
    setBusy(true)
    try {
      await window.electronAPI.invoke('affairs:updateTask', { projectId, taskId: task.id, ...fields })
      onChanged()
    } catch (err) {
      console.error('Failed to update task:', err)
    } finally {
      setBusy(false)
    }
  }

  const handleDelete = async () => {
    if (!(await confirmDialog({
      title: t('affairs.drawer.deleteTitle'),
      message: t('affairs.drawer.deleteConfirm', { title: task.title }),
      danger: true,
    }))) return
    try {
      await window.electronAPI.invoke('affairs:deleteTask', { projectId, taskId: task.id })
      onChanged()
      onClose()
    } catch (err) {
      console.error('Failed to delete task:', err)
    }
  }

  const ruleText = (): string => {
    const ac = task.auto_complete
    if (ac?.on === 'file_parsed') return t('affairs.drawer.ruleFile', { match: ac.match ?? '' })
    if (ac?.on === 'report_completed') {
      return t('affairs.drawer.ruleReport', { match: (ac.match ?? '').replace(/^title~/, '') })
    }
    if (task.recurrence_rule) return t('affairs.drawer.ruleRecurring')
    return t('affairs.drawer.ruleManual')
  }

  return (
    <div className={s.overlay} onClick={onClose}>
      <div className={s.panel} style={{ width }} onClick={(e) => e.stopPropagation()}>
        <div className={s.dragHandle} onMouseDown={onDragDown} />
        <div className={s.body}>
          <div className={s.headRow}>
            <span className={`${s.statusBadge} ${task.status === 'done' ? s.badgeDone
              : task.status === 'skipped' ? s.badgeMuted
              : isOverdue(task) ? s.badgeErr : ''}`}>
              {t(`affairs.status.${task.status}`)}
            </span>
            <span className={s.typeBadge}>{t(`affairs.type.${task.task_type}`)}</span>
            {task.occurrence_key && <span className={s.occ}>{task.occurrence_key}</span>}
          </div>
          <h3 className={s.title}>{task.title}</h3>
          {task.description && <p className={s.desc}>{task.description}</p>}
          <div className={s.meta}>
            <span className={isOverdue(task) ? s.overdue : ''}>
              {task.due_date
                ? isOverdue(task)
                  ? t('affairs.overdueDays', { count: daysOverdue(task) })
                  : t('affairs.drawer.dueAt', { date: task.due_date.slice(0, 10) })
                : t('affairs.drawer.noDue')}
            </span>
          </div>
          <div className={s.rule}>
            <span className={s.ruleLabel}>{t('affairs.drawer.ruleLabel')}</span>
            <span>{ruleText()}</span>
            {task.completed_by === 'auto' && task.completed_at && (
              <span className={s.autoDone}>
                {t('affairs.drawer.autoDone', { date: task.completed_at.slice(0, 10) })}
              </span>
            )}
          </div>
          {task.task_type === 'material' && task.status === 'open' && (
            <p className={s.uploadHint}>{t('affairs.drawer.uploadHint')}</p>
          )}
          <div className={s.actions}>
            {task.status === 'open' && (
              <>
                <button className={s.primaryBtn} disabled={busy} onClick={() => void patch({ status: 'done' })}>
                  <Check size={14} />{t('affairs.drawer.done')}
                </button>
                <button className={s.btn} disabled={busy} onClick={() => void patch({ status: 'skipped' })}>
                  <SkipForward size={14} />{t('affairs.drawer.skip')}
                </button>
              </>
            )}
            {(task.status === 'done' || task.status === 'skipped') && (
              <button className={s.btn} disabled={busy} onClick={() => void patch({ status: 'open' })}>
                <RotateCcw size={14} />{t('affairs.drawer.reopen')}
              </button>
            )}
            <button className={s.btn} onClick={onEdit}>
              <Pencil size={14} />{t('affairs.drawer.edit')}
            </button>
            {task.task_type === 'report' && task.status !== 'done' && (
              <button className={s.btn} onClick={onOpenReports}>
                <FileText size={14} />{t('affairs.drawer.startReport')}
              </button>
            )}
            <button className={s.dangerBtn} onClick={() => void handleDelete()}>
              <Trash2 size={14} />{t('affairs.drawer.delete')}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
```

CSS:`.overlay` 半透明黑遮罩(`rgba(0,0,0,0.4)`),`.panel` 右侧固定全高白底(`var(--bg-primary)`)+左侧 `var(--border-color)` 分隔,`.dragHandle` 左缘 6px 热区 hover `col-resize`,`.badgeErr` `var(--error)`、`.badgeDone`/`.badgeMuted` `var(--text-muted)`,`.dangerBtn` `var(--error)` 描边,`.primaryBtn` `var(--accent)` 底白字。

- [ ] **Step 4: i18n**——`affairs` 段增量(zh-CN / en-US 同步):

```json
"status": {
  "open": "待办", "done": "已完成", "skipped": "已跳过", "pending_activation": "等待时期"
  // en: "Open" / "Done" / "Skipped" / "Pending"
},
"drawer": {
  "dueAt": "截止 {{date}}",                       // en: "Due {{date}}"
  "noDue": "未定截止日",                           // en: "No due date"
  "ruleLabel": "完成规则",                         // en: "Completion rule"
  "ruleFile": "上传文件名含「{{match}}」的文件并解析成功后自动完成",   // en: "Auto-completes when a parsed file name contains \"{{match}}\""
  "ruleReport": "产出标题含「{{match}}」的报告后自动完成",           // en: "Auto-completes when a report title contains \"{{match}}\""
  "ruleRecurring": "按节奏周期性推进,上传对应材料自动完成",           // en: "Recurring — upload the material to auto-complete"
  "ruleManual": "手动标记完成",                     // en: "Mark done manually"
  "autoDone": "{{date}} 自动完成",                 // en: "Auto-completed {{date}}"
  "uploadHint": "到「素材」Tab 上传命中关键名的文件,解析完成后本任务自动完成",  // en: "Upload a matching file in Materials; this task auto-completes after parsing"
  "done": "标记完成", "skip": "跳过", "reopen": "恢复为待办",      // en: "Mark done" / "Skip" / "Reopen"
  "edit": "编辑", "delete": "删除", "startReport": "发起报告",     // en: "Edit" / "Delete" / "Start report"
  "deleteTitle": "删除任务",
  "deleteConfirm": "确认删除「{{title}}」?该操作不可撤销。"        // en: "Delete \"{{title}}\"? This cannot be undone."
}
```

- [ ] **Step 5: 跑测试确认通过**

Run: `npx vitest run tests/TaskDrawer.test.tsx tests/AffairsTab.test.tsx` 与 `npx tsc --noEmit`
Expected: PASS

- [ ] **Step 6: 提示用户提交**(建议信息:`feat(affairs): task drawer with actions (3.0 phase2 task11)`)

---

### Task 12: PulseBar 应办 chip + 逾期状态句 + ProjectDetailPage 三路拉取

**Files:**
- Modify: `src/features/project/affairs/types.ts`(新增 `PulseDueSummary` + `summarizeDueTasks`)
- Modify: `src/features/project/PulseBar.tsx`(props 扩展 + chips/句子/折叠徽记)
- Modify: `src/features/project/ProjectDetailPage.tsx`(loadPulseData 三路 allSettled + PulseBar 接线)
- Modify: `src/i18n/locales/zh-CN.json` / `en-US.json`(`pulse.*` 增量键)
- Test: `tests/PulseBar.test.tsx`(追加用例)、`tests/AffairsTab.test.tsx`(追加 summarizeDueTasks 纯函数用例)

**Interfaces:**
- Consumes: `PhaseTask`/`isOverdue`/`dueWithin`/`daysOverdue`(affairs/types.ts);IPC `affairs:listByProject`(Task 4)
- Produces:

```ts
// affairs/types.ts 增量
export interface PulseDueSummary {
  overdue: number          // 逾期 open 任务数
  weekDue: number          // 7 日内到期(含今日)且未逾期的 open 数
  nearestOverdueTitle: string | null
  nearestOverdueDays: number
}
export function summarizeDueTasks(tasks: PhaseTask[], now?: Date): PulseDueSummary

// PulseBar.tsx props 增量(全部 optional,既有调用不破坏)
dueTasks?: PulseDueSummary | null                 // null/undefined = 未加载或拉取失败,chip 隐藏
// 注:onOpenWiki 签名扩展('lint' | 'compile')属 Task 13(compile 消费端就绪后才接线,
// 避免中间态把 WikiTab focusSignal 切到未支持的视图)
onOpenAffairs?: () => void                        // 应办 chip 点击 → 事务 Tab
```

- 裁决落点:chip 计数 = `overdue + weekDue`(总应办);状态句按 `overdue`;句子优先级 = loadFailed > 逾期 > 矛盾 > 缺口 > 报告生成中 > 一切正常(行动项比知识问题更即时);**affairs 拉取失败不影响知识侧失败态**(两个数据源独立兜底,`Promise.allSettled` 三路并行,零新增轮询——仍挂在载/边沿补拉)。

- [ ] **Step 1: 写失败测试**——`tests/PulseBar.test.tsx` 末尾 `describe('PulseBar v1', ...)` 之后追加:

```tsx
describe('PulseBar due tasks (3.0 二期 Task 12)', () => {
  it('shows due chip with combined overdue+week count and fires onOpenAffairs', () => {
    const onOpenAffairs = vi.fn()
    const { getByText } = render(
      <PulseBar {...base} onOpenAffairs={onOpenAffairs} reviews={{ contradiction: 0, gap: 0 }}
        dueTasks={{ overdue: 2, weekDue: 3, nearestOverdueTitle: '上传BP', nearestOverdueDays: 3 }} />
    )
    const chip = getByText(/pulse\.chipDue/).closest('button')!
    expect(chip.textContent).toContain('5')
    fireEvent.click(chip)
    expect(onOpenAffairs).toHaveBeenCalled()
  })

  it('elevates overdue sentence above contradiction/gap', () => {
    const { container } = render(
      <PulseBar {...base} reviews={{ contradiction: 1, gap: 1 }}
        dueTasks={{ overdue: 2, weekDue: 0, nearestOverdueTitle: null, nearestOverdueDays: 0 }} />
    )
    const text = container.textContent!
    expect(text).toContain('pulse.sentenceOverdue')
    expect(text).not.toContain('pulse.sentenceContradiction')
  })

  it('shows overdue badge in collapsed row', () => {
    const { container } = render(
      <PulseBar {...base} reviews={{ contradiction: 0, gap: 0 }}
        dueTasks={{ overdue: 1, weekDue: 0, nearestOverdueTitle: null, nearestOverdueDays: 1 }} />
    )
    fireEvent.click(container.querySelector('.collapseBtn')!)
    const folded = container.querySelector('.foldedRow')!
    expect(folded.textContent).toContain('1')
  })

})

// 待编译 chip 的 compile 视图用例在 Task 13(消费端就绪后接线)
```

`tests/AffairsTab.test.tsx` 末尾追加纯函数用例:

```tsx
describe('summarizeDueTasks (Task 12)', () => {
  it('counts overdue and week-due, finds nearest overdue', () => {
    const now = new Date('2026-10-10T12:00:00')
    const mk = (over: Partial<PhaseTask>): PhaseTask => ({
      id: 'x', project_id: 'p1', phase: 'before', task_type: 'material',
      title: 'X', description: '', template_id: null, status: 'open',
      occurrence_key: null, due_date: null, recurrence_rule: null, auto_complete: null,
      linked_entity_type: null, linked_entity_id: null, completed_at: null,
      completed_by: null, created_at: '2026-10-09T00:00:00', ...over,
    })
    const s = summarizeDueTasks([
      mk({ id: 'a', title: '过期项', due_date: '2026-10-08' }),
      mk({ id: 'b', title: '更早项', due_date: '2026-10-01' }),
      mk({ id: 'c', due_date: '2026-10-12' }),
      mk({ id: 'd', due_date: '2026-10-20' }),
      mk({ id: 'e', due_date: '2026-10-08', status: 'done' }),
    ], now)
    expect(s.overdue).toBe(2)
    expect(s.weekDue).toBe(1)
    expect(s.nearestOverdueTitle).toBe('更早项')
    expect(s.nearestOverdueDays).toBe(9)
  })
})
```

(顶部 import 行补 `summarizeDueTasks`。)

- [ ] **Step 2: 跑测试确认失败**(dueTasks props 未定义→用例 FAIL)

- [ ] **Step 3: 实现 types 增量**——`src/features/project/affairs/types.ts` 尾部追加:

```ts
/** 脉搏带应办概要(3.0 §2 二期):由任务列表派生的纯数据 */
export interface PulseDueSummary {
  overdue: number
  weekDue: number
  nearestOverdueTitle: string | null
  nearestOverdueDays: number
}

export function summarizeDueTasks(tasks: PhaseTask[], now: Date = new Date()): PulseDueSummary {
  const open = tasks.filter((x) => x.status === 'open')
  const overdueList = open.filter((x) => isOverdue(x, now))
  const nearest = overdueList.reduce<PhaseTask | null>((acc, x) => {
    if (!acc || (x.due_date ?? '') < (acc.due_date ?? '')) return x
    return acc
  }, null)
  return {
    overdue: overdueList.length,
    weekDue: open.filter((x) => dueWithin(x, 7, now) && !isOverdue(x, now)).length,
    nearestOverdueTitle: nearest?.title ?? null,
    nearestOverdueDays: nearest ? daysOverdue(nearest, now) : 0,
  }
}
```

- [ ] **Step 4: 实现 PulseBar**——`src/features/project/PulseBar.tsx` 五处修改:

```tsx
// ① import 增量(类型):
import type { PulseDueSummary } from './affairs/types'
// ② PulseBarProps 两个新 prop(onOpenWiki 签名不动,Task 13 再扩):
  /** 应办概要(3.0 二期):overdue+7 日内;null/undefined = 未加载或失败,chip 隐藏 */
  dueTasks?: PulseDueSummary | null
  /** 应办 chip 点击 → 事务 Tab */
  onOpenAffairs?: () => void
// ③ 函数签名解构补 dueTasks, onOpenAffairs;chips 构建前派生:
  const dueTotal = (dueTasks?.overdue ?? 0) + (dueTasks?.weekDue ?? 0)
// ④ chips 构建(应办最前;pending chip 保持原样——compile 视图 Task 13 接线):
  const chips: Array<{ key: string; label: string; count?: number; cls: string; view?: 'lint' }> = []
  if (dueTotal > 0) chips.push({ key: 'due', label: t('pulse.chipDue'), count: dueTotal, cls: s.chipErr })
  if (reviews.contradiction > 0) chips.push({ key: 'contradiction', ...原样... })
  if (reviews.gap > 0) chips.push({ key: 'gap', ...原样... })
  if (pendingCompile > 0) chips.push({ key: 'pending', ...原样... })
  if (reportsActive) chips.push({ key: 'reports', ...原样... })
// ⑤ 状态句优先级(逾期插在 loadFailed 之后):
  const sentence = loadFailed
    ? t('pulse.loadFailed')
    : dueTasks != null && dueTasks.overdue > 0
      ? t('pulse.sentenceOverdue', { count: dueTasks.overdue })
      : reviews.contradiction > 0
        ? ...原样链不动...
// 折叠行:矛盾徽记旁加逾期徽记:
          {dueTasks != null && dueTasks.overdue > 0 && (
            <span className={`${s.chip} ${s.chipErr}`}>
              <AlertTriangle size={12} />{dueTasks.overdue}
            </span>
          )}
// chips 行点击分发(应办走 onOpenAffairs,其余走 onOpenWiki):
            onClick={() => { if (c.key === 'due') onOpenAffairs?.(); else onOpenWiki(c.view) }}
```

- [ ] **Step 5: 实现 ProjectDetailPage 三路拉取**——`loadPulseData` 整体替换为:

```tsx
  const [dueTasks, setDueTasks] = useState<PulseDueSummary | null>(null)
  const loadPulseData = useCallback(async () => {
    const pid = project.id
    // 三路并行独立兑底(allSettled):知识侧失败 → 脉搏带失败态;应办失败 → chip 隐藏,
    // 两数据源互不拖垮;仍只挂在载 + 终态边沿各一次,零新增轮询(0.4 §3.1)
    const [rvR, pgR, afR] = await Promise.allSettled([
      window.electronAPI.invoke('wiki:listReviews', { projectId: pid, resolved: false }),
      window.electronAPI.invoke('wiki:listPages', { projectId: pid }),
      window.electronAPI.invoke('affairs:listByProject', { projectId: pid }),
    ])
    if (latestPidRef.current !== pid) return
    if (rvR.status === 'rejected' || pgR.status === 'rejected') {
      console.error('Failed to load pulse data:',
        rvR.status === 'rejected' ? rvR.reason : pgR.reason)
      setPulseLoadFailed(true)
    } else {
      setPulseLoadFailed(false)
      const rv = rvR.value as any
      const pg = pgR.value as any
      const items: Array<{ type: string }> = rv?.reviews ?? rv?.items ?? []
      setReviewsCounts({
        contradiction: items.filter((r) => r.type === 'contradiction').length,
        gap: items.filter((r) => r.type === 'data-gap' || r.type === 'missing-page' || r.type === 'broken-link').length,
      })
      setPendingCompile(pg?.pending_compile_count ?? 0)
    }
    if (afR.status === 'fulfilled') {
      const af = afR.value as any
      setDueTasks(summarizeDueTasks(af?.tasks ?? []))
    } else {
      console.error('Failed to load due tasks:', afR.reason)
      setDueTasks(null)
    }
  }, [project.id])
```

顶部 import 补:`import { summarizeDueTasks } from './affairs/types'` 与 `import type { PulseDueSummary } from './affairs/types'`。PulseBar 接线补两个 prop:

```tsx
      <PulseBar
        projectId={project.id}
        stageLabel={stageLabel}
        reviews={reviewsCounts}
        pendingCompile={pendingCompile}
        loadFailed={pulseLoadFailed}
        dueTasks={dueTasks}
        onOpenAffairs={() => setActiveTab('affairs')}
        onOpenWiki={(view) => {
          setActiveTab('wiki')
          if (view === 'lint') setWikiFocus({ view, ts: Date.now() })
        }}
        // onOpenWiki 改为透传任意 view 在 Task 13(compile 消费端就绪后)
      />
```

- [ ] **Step 6: i18n**——`pulse` 段增量(zh-CN L676 区 / en-US 同位):

```json
"chipDue": "应办",                          // en: "Due"
"sentenceOverdue": "{{count}} 项任务已逾期",   // en: "{{count}} tasks overdue"
```

- [ ] **Step 7: 跑测试确认通过**

Run: `npx vitest run tests/PulseBar.test.tsx tests/AffairsTab.test.tsx tests/ProjectDetailPage.test.tsx` 与 `npx tsc --noEmit`
Expected: PASS(既有 PulseBar v1 用例不回归——新 props 全 optional;ProjectDetailPage 用例 mock PulseBar 不受影响)

- [ ] **Step 8: 提示用户提交**(建议信息:`feat(affairs): pulse due chip + three-way settled fetch (3.0 phase2 task12)`)

### Task 13: 待编译通知合并 + 创建向导确认态(裁决 6/8)

**Files:**
- Modify: `src/features/project/PulseBar.tsx`(onOpenWiki 签名扩 `'lint' | 'compile'` + 待编译 chip 携 compile 视图)
- Modify: `src/features/project/ProjectDetailPage.tsx`(wikiFocus state 类型扩 + onOpenWiki 回调透传)
- Modify: `src/features/project/tabs/WikiTab.tsx`(focusSignal 类型扩 + consume effect 处理 compile + 删 pendingNotice 块)
- Modify: `src/features/project/tabs/WikiTab.module.css`(删 `.pendingNotice`/`.compileBtn` 死类)
- Modify: `src/App.tsx`(handleCreateProject 返回 result)
- Modify: `src/features/knowledge/ProjectList.tsx` + `ProjectList.module.css`(创建确认态,裁决 8)
- Modify: `src/i18n/locales/zh-CN.json` / `en-US.json`(删 2 键、改 1 键值、增 4 键)
- Test: `tests/ProjectList.test.tsx`(新建)、`tests/PulseBar.test.tsx`(追加 compile chip 用例)

**Interfaces:**
- Consumes: Task 12 的应办 chip 骨架(chips 点击分发已就位);WikiTab `handleCompile()`(既有);`app:navigate-project-tab` 事件(App.tsx L270 既有监听,tab 值无白名单直接透传;ProjectDetailPage tabSignal 白名单 Task 6 已含 `'affairs'`)
- Produces: 行为闭环——顶栏待编译通知删除,触发编译入口统一为脉搏带待编译 chip(裁决 6);创建项目后 modal 切确认态展示铺入结果(裁决 8)

- [ ] **Step 1: 写失败测试**

`tests/PulseBar.test.tsx` 的 `describe('PulseBar due tasks ...')` 内末尾追加:

```tsx
  it('adds compile view to pending chip click (裁决 6,Task 13)', () => {
    const onOpenWiki = vi.fn()
    const { getByText } = render(
      <PulseBar {...base} onOpenWiki={onOpenWiki} reviews={{ contradiction: 0, gap: 0 }}
        pendingCompile={4} />
    )
    fireEvent.click(getByText(/pulse\.chipPendingCompile/).closest('button')!)
    expect(onOpenWiki).toHaveBeenCalledWith('compile')
  })
```

新建 `tests/ProjectList.test.tsx`:

```tsx
/** tests/ProjectList.test.tsx — 3.0 二期 Task 13:创建确认态(裁决 8) */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, o?: { defaultValue?: string }) => o?.defaultValue ?? k }),
}))
vi.mock('../src/features/activity/ActivityContext', () => ({
  useActivity: () => ({ byProject: new Map(), items: [] }),
}))
vi.mock('../src/features/knowledge/ProjectList.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, fireEvent, cleanup, act } from '@testing-library/react'
import { ProjectList } from '../src/features/knowledge/ProjectList'

const flush = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await Promise.resolve() })
  }
}

const base = { projects: [], selectedProject: null, onSelect: vi.fn(), onDelete: vi.fn() }

beforeEach(() => vi.clearAllMocks())
afterEach(() => cleanup())

const openAndFill = (getByPlaceholderText: (s: string) => HTMLElement, getByText: (s: string | RegExp) => HTMLElement, name = '新园区') => {
  fireEvent.click(getByText('knowledge.newProject'))
  fireEvent.change(getByPlaceholderText('knowledge.projectName'), { target: { value: name } })
}

describe('ProjectList create confirm (裁决 8)', () => {
  it('switches to confirm state with seeded count after create resolves', async () => {
    const onCreate = vi.fn().mockResolvedValue({ id: 'p9', seeded_tasks_count: 24 })
    const { getByPlaceholderText, getByText, container, queryByPlaceholderText } = render(
      <ProjectList {...base} onCreate={onCreate} />)
    openAndFill(getByPlaceholderText, getByText)
    fireEvent.click(getByText('knowledge.create'))
    await flush()
    const text = container.textContent!
    expect(text).toContain('knowledge.wizardConfirmDesc')
    expect(text).toContain('24')
    expect(queryByPlaceholderText('knowledge.projectName')).toBeNull()   // 表单已切走,modal 未关
  })

  it('adjust button dispatches navigate-project-tab with affairs tab and closes', async () => {
    const onCreate = vi.fn().mockResolvedValue({ id: 'p9', seeded_tasks_count: 24 })
    const dispatchSpy = vi.spyOn(window, 'dispatchEvent')
    const { getByPlaceholderText, getByText, queryByText } = render(
      <ProjectList {...base} onCreate={onCreate} />)
    openAndFill(getByPlaceholderText, getByText)
    fireEvent.click(getByText('knowledge.create'))
    await flush()
    dispatchSpy.mockClear()
    fireEvent.click(getByText('knowledge.wizardConfirmAdjust'))
    const ev = dispatchSpy.mock.calls.map(([e]) => e as CustomEvent)
      .find((e) => e.type === 'app:navigate-project-tab')
    expect(ev).toBeTruthy()
    expect((ev as CustomEvent).detail).toEqual({ projectId: 'p9', tab: 'affairs' })
    expect(queryByText('knowledge.wizardConfirmAdjust')).toBeNull()      // modal 已关
  })

  it('falls back to legacy close when onCreate resolves without result', async () => {
    const onCreate = vi.fn().mockResolvedValue(undefined)
    const { getByPlaceholderText, getByText, queryByPlaceholderText } = render(
      <ProjectList {...base} onCreate={onCreate} />)
    openAndFill(getByPlaceholderText, getByText)
    fireEvent.click(getByText('knowledge.create'))
    await flush()
    expect(queryByPlaceholderText('knowledge.projectName')).toBeNull()   // 直接关(原行为)
  })
})
```

- [ ] **Step 2: 跑测试确认失败**(wizardConfirm 键文案/compile 视图未实现)

Run: `npx vitest run tests/ProjectList.test.tsx tests/PulseBar.test.tsx`

- [ ] **Step 3: compile 信号链**(裁决 6)

`PulseBar.tsx` 两处:

```tsx
// onOpenWiki 签名:
  /** chip 点击;lint=治理视图 / compile=触发编译(裁决 6) */
  onOpenWiki: (view?: 'lint' | 'compile') => void
// chips 数组 view 类型扩 + 待编译 chip:
  const chips: Array<{ key: string; label: string; count?: number; cls: string; view?: 'lint' | 'compile' }> = []
  ...
  if (pendingCompile > 0) chips.push({ key: 'pending', label: t('pulse.chipPendingCompile'), count: pendingCompile, cls: s.chipWarn, view: 'compile' })
```

`ProjectDetailPage.tsx` 两处:

```tsx
// wikiFocus state(L72):
  const [wikiFocus, setWikiFocus] = useState<{ view: 'lint' | 'compile'; ts: number } | null>(null)
// PulseBar onOpenWiki 回调(Task 12 的 `if (view === 'lint')` 改为透传):
        onOpenWiki={(view) => {
          setActiveTab('wiki')
          if (view) setWikiFocus({ view, ts: Date.now() })
        }}
```

`WikiTab.tsx` 三处:

```tsx
// ① props 类型(L39):
  /** 脉搏带/健康卡直达信号(一期 lint 直达;二期扩 compile 直达编译,裁决 6) */
  focusSignal?: { view: 'lint' | 'compile'; ts: number } | null
// ② consume effect(L218-223)替换:
  useEffect(() => {
    if (!focusSignal?.view) return
    if (focusSignal.view === 'compile') {
      handleCompile()   // 与点「编译」按钮等价(裁决 6):handleCompile() 内部走 entry guard,无新风险
    } else {
      setViewMode(focusSignal.view)
    }
    onFocusHandled?.()
  }, [focusSignal])
// ③ 删除顶栏待编译通知块(L465-474,含内嵌编译按钮——顶栏唯一编译入口):
//    {pendingCount > 0 && !isCompiling && !isResumed && (
//      <div className={s.pendingNotice}> ... </div>
//    )}
//    pendingCount state 保留(首页 pendingCta/startCompile 仍消费)
```

`WikiTab.module.css`:删除 `.pendingNotice` 与 `.compileBtn` 两个类(块删除后成死代码,§2.2 清理)。

- [ ] **Step 4: 创建向导确认态**(裁决 8)

`App.tsx` `handleCreateProject`:

```tsx
      await loadProjects()
      // Auto-select the newly created project
      if (result?.id) {
        setSelectedProjectId(result.id)
      }
      return result as { id: string; seeded_tasks_count?: number } | undefined   // 裁决 8:回传向导确认态
    } catch (err) {
      console.error('Failed to create project:', err)
      showToast(t('knowledge.createProjectFailed'), 'error')
      return undefined
    }
```

`ProjectList.tsx`:

```tsx
// ① onCreate prop 类型:
  onCreate?: (name: string, description?: string, type?: ProjectType, companyName?: string, creditCode?: string)
    => Promise<{ id: string; seeded_tasks_count?: number } | undefined | void> | void
// ② 新 state(formState 区):
  const [confirmInfo, setConfirmInfo] = useState<{ projectId: string; name: string; type: ProjectType; count: number } | null>(null)
// ③ handleCreate 替换:
  const resetAndClose = () => {
    setNewProjectName('')
    setNewProjectDesc('')
    setNewProjectType('general')
    setNewCompanyName('')
    setNewCreditCode('')
    setConfirmInfo(null)
    setShowCreateModal(false)
  }
  const handleCreate = async () => {
    if (!newProjectName.trim() || !onCreate || creating) return
    setCreating(true)
    try {
      const result = await onCreate(newProjectName.trim(), newProjectDesc.trim() || undefined, newProjectType, newCompanyName.trim() || undefined, newCreditCode.trim() || undefined)
      if (result?.id) {
        // 裁决 8:创建成功不关 modal,切确认态(App 已 auto-select 项目)
        setConfirmInfo({ projectId: result.id, name: newProjectName.trim(), type: newProjectType, count: result.seeded_tasks_count ?? 0 })
      } else {
        resetAndClose()
      }
    } finally {
      setCreating(false)
    }
  }
// ④ modal 内容改二分支:confirmInfo ? 确认面板 : 原表单。确认面板:
          <>
            <h3 className={s.modalTitle}>{t('knowledge.wizardConfirmTitle')}</h3>
            <p className={s.confirmDesc}>
              {t('knowledge.wizardConfirmDesc', {
                name: confirmInfo.name,
                count: confirmInfo.count,
                type: t(PROJECT_TYPE_CONFIG[confirmInfo.type].labelKey),
              })}
            </p>
            <div className={s.modalActions}>
              <button className={s.btnCancel} onClick={resetAndClose}>
                {t('knowledge.wizardConfirmEnter')}
              </button>
              <button className={s.btnCreate} onClick={() => {
                const { projectId } = confirmInfo
                resetAndClose()
                window.dispatchEvent(new CustomEvent('app:navigate-project-tab', { detail: { projectId, tab: 'affairs' } }))
              }}>
                {t('knowledge.wizardConfirmAdjust')}
              </button>
            </div>
          </>
// ⑤ overlay onClick 与 Escape(handleKeyDown)改调 resetAndClose(确认态下关闭也清残留)
```

`ProjectList.module.css`:新增 `.confirmDesc`(`var(--text-secondary)`,行高 1.6,margin-bottom 16px)。

- [ ] **Step 5: i18n 三改**(zh-CN / en-US 同步)

删 2 键(块删除后无引用,§2.2):`project.wiki.compileBtnTip`(zh L811/en L811)、`project.wiki.pendingCompile`(zh L896/en L896)。

改 1 键值(`project.wiki.homePage.pendingCta`,顶栏编译按钮已删,引导语改指首页「开始编译」按钮):

```json
"pendingCta": "有 {{count}} 个新文件待编译,点击「开始编译」生成 Wiki 页面。"
// en: "{{count}} new files pending. Click Start Compile to generate Wiki pages."
```

增 4 键(`knowledge` 段,裁决 8 确认态):

```json
"wizardConfirmTitle": "项目已创建",
"wizardConfirmDesc": "已按{{type}}流程为「{{name}}」带入 {{count}} 项任务与后续节奏",
"wizardConfirmEnter": "进入项目",
"wizardConfirmAdjust": "调整任务 →"
// en: "Project created" / "Seeded {{count}} tasks and recurring rhythm for \"{{name}}\" ({{type}})"
//     "Enter project" / "Adjust tasks →"
```

- [ ] **Step 6: 跑测试确认通过**

Run: `npx vitest run tests/ProjectList.test.tsx tests/PulseBar.test.tsx tests/WikiTab.test.tsx tests/ProjectDetailPage.test.tsx` 与 `npx tsc --noEmit`
Expected: PASS(WikiTab 既有用例不断言 pendingNotice,不回归;ProjectDetailPage mock PulseBar 不受签名扩展影响)

- [ ] **Step 7: 提示用户提交**(建议信息:`feat(affairs): compile direct signal + create wizard confirm (3.0 phase2 task13)`)

---

### Task 14: 收尾——设计文档回改 + 全量验证

**Files:**
- Modify: `docs/3.0project-lifecycle-design.md`(§5.1 命名裁决注记 + §9 对齐表,§2.1 文档↔代码双向对齐)

**Interfaces:**
- Consumes: 全部前序任务的落地事实
- Produces: 文档与代码一致 + 全量验证绿

- [ ] **Step 1: §5.1 命名裁决回改**——数据流/组件描述中「routers/tasks.py(新增)」改「routers/affairs.py(新增;裁决:tasks 命名已被 TaskAgent 长任务路由占用,见 §5.4 实施裁决表)」;IPC 通道名「tasks:* 通道」改「affairs:* 通道(4 通道:listByProject/createTask/updateTask/deleteTask)」。

- [ ] **Step 2: §9 对齐表更新**——核对 2.1 行:二期交付后项目详情页为五 Tab(知识/事务/素材/报告/填表);若一期收尾标为「四 Tab(事务 Tab 二期补)」,更新为「五 Tab(二期交付)」;补二期范围行(§8 表)交付状态。

- [ ] **Step 3: 全量验证**

Run:
- `C:/Users/Kelvin/.conda/envs/inkcube/python.exe -m pytest python/tests -q`(全量,预期全绿)
- `npx vitest run`(全量,预期全绿)
- `npx tsc --noEmit`

Expected: 全部 PASS;任何红项当场修复(禁遗留)。

- [ ] **Step 4: 交付核对**(对照 Global Constraints 逐条自检:轮询清单未增长/零硬编码文案/图标经中央注册/不变量遵守/测试落位与查重)→ 提示用户提交收尾改动(建议信息:`docs(affairs): align 3.0 design with phase2 delivery (3.0 phase2 task14)`)。
