# 首页今日工作台 + 侧栏色点(3.0 三期)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 首页升级为「今日工作台」——今日待办(跨项目,逾期置顶)+ 近期日历(未来 7 天)+ 项目卡片墙(替代最近项目列表,含健康色点),并在侧栏项目列表追加同口径健康色点。

**Architecture:** 后端新增跨项目聚合端点 `GET /projects/due`(一次返回 tasks + per 项目 reviews 计数,避免 N+1);前端 App 持单一 `dueData` 快照(mount / nav 边沿 / `app:tasks-changed` 事件驱动重拉,零新增轮询),下发 HomePage(待办/日历/卡片墙)与 ProjectSidebar→ProjectList(色点);色点档位与 per 项目汇总为纯函数,与二期 PulseBar 同口径复用 `isOverdue`/`dueWithin`。

**Tech Stack:** FastAPI + SQLite、Electron IPC(透传)、React + TypeScript + CSS Modules、vitest + pytest。

**Spec:** [docs/3.0project-lifecycle-design.md](../../3.0project-lifecycle-design.md) §6.1.8(首页布局)、§7.1(今日待办/卡片墙)、§7.2(listDue/色点)、§8(三期范围)、§10 裁决 10(首页只读 + 直达,仅打勾为快捷完成,不做字段编辑)。

## Global Constraints

- **禁止 git 操作**——提交由用户手动完成;每任务末尾给出「建议提交信息」。
- Python:`C:/Users/Kelvin/.conda/envs/inkcube/python.exe -m pytest python/tests -q`(全量)/`...test_affairs_api.py -q`(定向)。
- 前端测试:`node node_modules/vitest/vitest.mjs run <file>`(npx 被拦截);类型检查:`node node_modules/typescript/lib/tsc.js --noEmit`。
- i18n 双语同步:`src/i18n/locales/zh-CN.json` 与 `en-US.json` 同步落键。
- IPC 命名:`affairs:listDue`(§5.1;`tasks:*` 前缀已被 TaskAgent 占用,禁用)。
- **零新增轮询**(0.4 §3.1):dueData 只在 App mount、activeNav 变化、`app:tasks-changed` 事件三个时机重拉。
- 前端日期一律本地时区 ISO(字典序即时间序);**禁止 `toISOString()`**(UTC 偏移致边界差一天,affairs/types.ts L34 项目既定裁决)。
- 色点口径(§7.2):红=有逾期;橙=有本周到期或待处理发现项;绿=两无。
- 日志纪律:前端禁止新增 `console.log`;后端读端点无需新日志。
- 同类一次清理:改 CSS 删除不再引用的死样式;改 i18n 同步删两份 locale 死键。

## 现状锚点(执行者必读,探索于 2026-10-10)

| 锚点 | 事实 |
|------|------|
| `src/features/home/HomePage.tsx`(291 行) | 顺序:状态卡 L135-182 →进行中 L184-201 →快捷入口 L203-220 →`recentGrid` 双列 L222-287(左=最近项目列表 created_at top5 L226-259,右=活动流 L261-286)。props:`projects/onNavigate/onOpenSettings/onSelectProject` |
| `src/App.tsx` | `projects` state L96;`loadProjects` L101;L152-156 activeNav==='projects' 时重拉;**App 渲染 ActivityProvider,自身不能用 useActivity**(L166 注释);`ProjectSidebar` L415-422;`HomePage` L467-474;App 已监听 `app:navigate-project-tab`(L271-295,detail 含 projectId/tab/container,tab 可选) |
| `src/features/project/ProjectSidebar.tsx`(37 行) | 纯包装,透传 props 给 ProjectList |
| `src/features/knowledge/ProjectList.tsx` | 项目 item name h4 内有 `activityDot` 脉动点(byProject files/compiles/reports>0,瞬态)——与三期健康色点语义不同,**保留并存** |
| `src/features/project/affairs/types.ts`(91 行) | `PhaseTask` L12;`localISODate` L35(私有);`isOverdue` L43(open 且 due<当日);`daysOverdue` L48;`dueWithin(task,days)` L56(含今日,已逾期归 isOverdue);`summarizeDueTasks` L77(单项目) |
| `src/features/project/affairs/AffairsTab.tsx` | 写操作汇聚点 `handleChanged`(L74)=`{ void load() }`;TaskDrawer `onChanged`(L117)+ TaskFormModal `onSaved`(L125)都走它 |
| `python/routers/affairs.py`(101 行) | `APIRouter(prefix="/projects")`;4 端点:`GET/POST /{project_id}/tasks`、`PATCH/DELETE /{project_id}/tasks/{task_id}` |
| `python/db/database.py` | `list_phase_tasks` L2287(per 项目 + JSON 解码);`create/get/update/delete_phase_task` L2305-2377;`wiki_reviews` DDL L420 含 `project_id/type/title/resolved INTEGER DEFAULT 0/created_at`;`self._now()` 可用 |
| `electron/ipc-handlers.js` | affairs 四通道 L1509-1553:`ipcMain.handle` → `fetchWithTimeout(\`${pythonManager.getBaseUrl()}/...\`)` → `handlePythonResponse` |
| `src/styles/global.css` | `--success: #22c55e`、`--error: #ef4444`、`--text-warning: #d97706`(无独立 --warning) |
| 阶段 label | `STAGE_MAP`(src/features/project/types.ts):`STAGE_MAP[p.type]?.find(x => x.key === p.stage)?.labelKey ?? \`project.stage.${p.stage}\``(PhaseBoard 同款);general 类型 STAGE_MAP 为空数组→走 fallback;`stage==='none'` 不渲染 pill |
| 周几缩写 | `affairs.dow0`…`dow6` 已存在,首页日历复用 |
| `python/tests/test_affairs_api.py` | fixture `client_and_db`:tmp db + `ensure_preset_base_libs` + `seed_playbook_templates` + 单 router 挂载;建项目 `db.create_project(name, project_type, stage)["id"]` |
| `tests/ProjectList.test.tsx` | mock 口径:`t: (k, o) => o ? \`${k}:${JSON.stringify(o)}\` : k`;css `Proxy({ get: (_t, k) => String(k) })`(类名=键名);`flush()` 多轮 act |
| 排序保证 | `list_due_phase_tasks` SQL `ORDER BY due_date ASC`——逾期日期序天然在前,前端不再排序 |

## File Structure

| 文件 | 动作 |
|------|------|
| `python/db/database.py` / `python/routers/affairs.py` / `electron/ipc-handlers.js` | 修改(+due 聚合) |
| `python/tests/test_affairs_api.py` | 修改(+2 用例) |
| `src/features/project/affairs/types.ts` | 修改(+DueTask/ProjectHealthSummary/HealthLevel/healthLevel/summarizeDueByProject) |
| `src/features/project/affairs/HealthDot.tsx` + css | 新建(色点组件) |
| `src/features/knowledge/ProjectList.tsx` + css / `ProjectSidebar.tsx` | 修改(+healthData 透传/渲染) |
| `src/features/home/TodayTodo.tsx` + css、`UpcomingCalendar.tsx` + css、`ProjectCardWall.tsx` + css | 新建 |
| `src/features/home/HomePage.tsx` + css | 修改(装配四区,替换最近项目列表,清理死样式) |
| `src/App.tsx` | 修改(dueData 快照+事件监听+下发) |
| `src/features/project/affairs/AffairsTab.tsx` | 修改(handleChanged 追加 dispatch) |
| `src/i18n/locales/{zh-CN,en-US}.json` | 修改(+homeTodo.*) |
| `tests/TodayTodo.test.tsx`、`UpcomingCalendar.test.tsx`、`ProjectCardWall.test.tsx`、`HomePage.test.tsx` | 新建 |
| `tests/AffairsTab.test.tsx`、`tests/ProjectList.test.tsx` | 修改(追加用例) |
| `docs/3.0project-lifecycle-design.md` | 修改(§8/§9 交付标注) |

---

### Task 1: 后端 due 聚合端点 + IPC 通道

**Files:**
- Modify: `python/db/database.py`(L2377 `delete_phase_task` 之后插入)
- Modify: `python/routers/affairs.py`(L39 `list_phase_tasks` 端点**之前**声明)
- Modify: `electron/ipc-handlers.js`(L1513 `affairs:listByProject` 之后)
- Test: `python/tests/test_affairs_api.py`(末尾追加)

**Interfaces:**
- Consumes: 既有 `phase_tasks`/`wiki_reviews` 表、`self._now()`
- Produces: `db.list_due_phase_tasks(horizon_days: int = 7) -> list[dict]`(字段=phase_tasks.* + `project_name` + `project_stage`);`db.count_pending_reviews_by_project() -> dict[str, int]`;HTTP `GET /projects/due?horizon_days=7` → `{"tasks": [...], "reviews_counts": {"<pid>": n}}`;IPC `affairs:listDue`

- [ ] **Step 1: 写失败测试(test_affairs_api.py 末尾追加;头部 `from datetime import date` 若无 timedelta 则扩展为 `from datetime import date, timedelta`)**

```python
# ── 3.0 三期 Task 1:GET /projects/due(首页今日工作台 + 色点数据源)──

def _d(offset: int) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


def test_list_due_orders_horizon_and_review_counts(client_and_db):
    """跨项目 open+有截止任务;due ASC;horizon 过滤;reviews 计数内嵌(§7.2 防 N+1)。"""
    client, db = client_and_db
    pa = db.create_project(name="A", project_type="general", stage="before")["id"]
    pb = db.create_project(name="B", project_type="general", stage="before")["id"]
    db.create_phase_task(pa, "before", "milestone", "逾期A", due_date=_d(-2))
    db.create_phase_task(pa, "before", "material", "今日A", due_date=_d(0))
    db.create_phase_task(pb, "before", "milestone", "明日B", due_date=_d(1))
    db.create_phase_task(pb, "before", "report", "八天后B", due_date=_d(8))      # 界外,不返回
    db.create_phase_task(pa, "before", "milestone", "已完成A", due_date=_d(-5),
                         status="done")                                          # 终态,不返回
    db.create_phase_task(pa, "before", "milestone", "无截止A")                   # due NULL,不返回
    now = db._now()
    for rid, pid, resolved in (("rv1", pa, 0), ("rv2", pa, 0), ("rv3", pa, 1)):
        db.conn.execute(
            "INSERT INTO wiki_reviews (id, project_id, type, title, resolved, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)", (rid, pid, "gap", rid, resolved, now))
    db.conn.commit()

    r = client.get("/projects/due")
    assert r.status_code == 200
    body = r.json()
    assert [t["title"] for t in body["tasks"]] == ["逾期A", "今日A", "明日B"]     # due ASC,逾期天然在前
    assert all(t["project_name"] for t in body["tasks"])                          # JOIN 出项目名
    assert all(t["status"] == "open" for t in body["tasks"])
    assert body["reviews_counts"] == {pa: 2}                                      # resolved=1 不计;pb 无发现项不出现


def test_list_due_horizon_validation(client_and_db):
    client, _ = client_and_db
    assert client.get("/projects/due", params={"horizon_days": 0}).status_code == 422
    assert client.get("/projects/due", params={"horizon_days": 32}).status_code == 422
    assert client.get("/projects/due", params={"horizon_days": 1}).status_code == 200
```

- [ ] **Step 2: 跑测试确认失败**

Run: `C:/Users/Kelvin/.conda/envs/inkcube/python.exe -m pytest python/tests/test_affairs_api.py -q`
Expected: 新增 2 用例 FAIL(404,`/projects/due` 不存在)

- [ ] **Step 3: 实现 db 两方法(database.py `delete_phase_task` 之后;若无 timedelta 导入则并入既有 datetime import 行)**

```python
    def list_due_phase_tasks(self, horizon_days: int = 7) -> list[dict]:
        """跨项目 open 且有截止日的任务(逾期 + horizon 日内),due ASC(3.0 三期 §7.2)。

        逾期判定(due_date < today)交由前端纯函数 isOverdue;后端只按
        due_date <= today+horizon 过滤(含今日与逾期),用本地日期避免 UTC 分叉。
        """
        limit = (date.today() + timedelta(days=horizon_days)).isoformat()
        cursor = self.conn.execute(
            """SELECT pt.*, p.name AS project_name, p.stage AS project_stage
               FROM phase_tasks pt
               JOIN projects p ON p.id = pt.project_id
               WHERE p.kind = 'project' AND pt.status = 'open'
                 AND pt.due_date IS NOT NULL AND pt.due_date <= ?
               ORDER BY pt.due_date ASC, pt.created_at ASC""",
            (limit,),
        )
        rows = []
        for row in cursor.fetchall():
            d = dict(row)
            # JSON 解码与 list_phase_tasks 对齐(消费方统一拿到 dict 而非原始字符串)
            for key in ("recurrence_rule", "auto_complete"):
                if d.get(key):
                    try:
                        d[key] = json.loads(d[key])
                    except (json.JSONDecodeError, TypeError):
                        d[key] = None
            rows.append(d)
        return rows

    def count_pending_reviews_by_project(self) -> dict[str, int]:
        """per 项目待处理发现项计数(resolved=0),供卡片墙/侧栏健康色点(3.0 §7.2)。"""
        cursor = self.conn.execute(
            "SELECT project_id, COUNT(*) AS cnt FROM wiki_reviews "
            "WHERE resolved = 0 GROUP BY project_id")
        return {row["project_id"]: row["cnt"] for row in cursor.fetchall()}
```

- [ ] **Step 4: 实现路由(affairs.py,`list_phase_tasks` 端点之前声明)**

```python
@router.get("/due")
async def list_due_tasks(horizon_days: int = 7):
    """首页今日工作台 + 侧栏色点数据源(3.0 三期 §7.2):跨项目一次返回,防 N+1。

    声明在 /{project_id}/tasks 之前,防未来新增单段项目路由遮蔽 /due。
    """
    if not 1 <= horizon_days <= 31:
        raise HTTPException(status_code=422, detail="horizon_days must be 1-31")
    db = _db()
    return {
        "tasks": db.list_due_phase_tasks(horizon_days),
        "reviews_counts": db.count_pending_reviews_by_project(),
    }
```

- [ ] **Step 5: 跑测试确认通过**

Run: `C:/Users/Kelvin/.conda/envs/inkcube/python.exe -m pytest python/tests/test_affairs_api.py -q`
Expected: 全部 PASS(含既有用例)

- [ ] **Step 6: IPC 通道(ipc-handlers.js,L1513 `affairs:listByProject` 之后插入)**

```javascript
  ipcMain.handle('affairs:listDue', async (_event, { horizonDays } = {}) => {
    const res = await fetchWithTimeout(
      `${pythonManager.getBaseUrl()}/projects/due?horizon_days=${Number(horizonDays) || 7}`)
    return handlePythonResponse(res)
  })
```

- [ ] **Step 7: 语法检查 + 后端全量回归**

Run: `node --check electron/ipc-handlers.js`(Expected: exit 0)
Run: `C:/Users/Kelvin/.conda/envs/inkcube/python.exe -m pytest python/tests -q`(Expected: 全量 PASS)

- [ ] **Step 8: 用户手动提交(建议)**`feat(affairs): cross-project due endpoint + reviews counts (3.0 三期 T1)`

---

### Task 2: 前端类型与纯函数(色点档位 + per 项目汇总)

**Files:**
- Modify: `src/features/project/affairs/types.ts`(末尾追加)
- Test: `tests/AffairsTab.test.tsx`(末尾追加 describe;新符号并入文件既有 types import)

**Interfaces:**
- Consumes: 同文件既有 `PhaseTask`、`isOverdue(task, now?)`、`dueWithin(task, days, now?)`
- Produces(后续任务依赖的精确签名):
  - `interface DueTask extends PhaseTask { project_name: string; project_stage?: string | null }`
  - `interface ProjectHealthSummary { overdue: number; weekDue: number; reviewsPending: number }`
  - `type HealthLevel = 'green' | 'orange' | 'red'`
  - `healthLevel(s: ProjectHealthSummary): HealthLevel`
  - `summarizeDueByProject(tasks: DueTask[], reviewsCounts?: Record<string, number>, now?: Date): Record<string, ProjectHealthSummary>`

- [ ] **Step 1: 写失败测试(tests/AffairsTab.test.tsx 末尾追加)**

```tsx
describe('healthLevel / summarizeDueByProject (3.0 三期 Task 2)', () => {
  const now = new Date('2026-10-10T10:00:00')
  const mk = (id: string, project_id: string, due_date: string | null): DueTask => ({
    id, project_id, phase: 'before', task_type: 'milestone', title: id, description: '',
    template_id: null, status: 'open', occurrence_key: null, due_date,
    recurrence_rule: null, auto_complete: null, linked_entity_type: null,
    linked_entity_id: null, completed_at: null, completed_by: null,
    created_at: '2026-10-01T00:00:00', project_name: 'P-' + project_id,
  } as DueTask)

  it('healthLevel: red > orange(weekDue/reviews) > green', () => {
    expect(healthLevel({ overdue: 1, weekDue: 0, reviewsPending: 5 })).toBe('red')
    expect(healthLevel({ overdue: 0, weekDue: 2, reviewsPending: 0 })).toBe('orange')
    expect(healthLevel({ overdue: 0, weekDue: 0, reviewsPending: 3 })).toBe('orange')
    expect(healthLevel({ overdue: 0, weekDue: 0, reviewsPending: 0 })).toBe('green')
  })

  it('summarizeDueByProject: groups with same overdue/weekDue semantics as summarizeDueTasks', () => {
    const tasks = [
      mk('t1', 'pa', '2026-10-08'),   // 逾期(now=10-10)
      mk('t2', 'pa', '2026-10-10'),   // 今日:非逾期、dueWithin
      mk('t3', 'pb', '2026-10-12'),   // 周内
      mk('t4', 'pb', '2026-10-20'),   // 界外不计数
    ]
    const sum = summarizeDueByProject(tasks, { pc: 4 }, now)
    expect(sum.pa).toEqual({ overdue: 1, weekDue: 1, reviewsPending: 0 })
    expect(sum.pb).toEqual({ overdue: 0, weekDue: 1, reviewsPending: 0 })
    expect(sum.pc).toEqual({ overdue: 0, weekDue: 0, reviewsPending: 4 })   // 纯发现项项目也出现
  })
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `node node_modules/vitest/vitest.mjs run tests/AffairsTab.test.tsx`
Expected: 新 describe FAIL(符号未导出)

- [ ] **Step 3: 实现(types.ts 末尾追加)**

```ts

// ── 3.0 三期:首页今日工作台 + 健康色点(§7.2;口径复用上方 isOverdue/dueWithin)──

/** affairs:listDue 返回的任务行(PhaseTask + JOIN 出的项目名/阶段) */
export interface DueTask extends PhaseTask {
  project_name: string
  project_stage?: string | null
}

/** per 项目健康概要(色点与卡片墙派生的纯数据) */
export interface ProjectHealthSummary {
  overdue: number
  weekDue: number
  reviewsPending: number
}

export type HealthLevel = 'green' | 'orange' | 'red'

/** 色点档位(§7.2 口径):红=有逾期;橙=本周待办或待处理发现项;绿=两无 */
export function healthLevel(s: ProjectHealthSummary): HealthLevel {
  if (s.overdue > 0) return 'red'
  if (s.weekDue > 0 || s.reviewsPending > 0) return 'orange'
  return 'green'
}

/** 跨项目按项目分组计数(weekDue 口径与 summarizeDueTasks 一致:dueWithin 且非逾期);
 *  reviewsCounts 中的项目即使无任务也出现(纯发现项项目=橙,侧栏色点可见)。 */
export function summarizeDueByProject(
  tasks: DueTask[],
  reviewsCounts?: Record<string, number>,
  now: Date = new Date(),
): Record<string, ProjectHealthSummary> {
  const acc: Record<string, ProjectHealthSummary> = {}
  for (const pid of Object.keys(reviewsCounts ?? {})) {
    acc[pid] = { overdue: 0, weekDue: 0, reviewsPending: reviewsCounts![pid] ?? 0 }
  }
  for (const task of tasks) {
    const entry = acc[task.project_id]
      ?? (acc[task.project_id] = { overdue: 0, weekDue: 0, reviewsPending: reviewsCounts?.[task.project_id] ?? 0 })
    if (isOverdue(task, now)) entry.overdue += 1
    else if (dueWithin(task, 7, now)) entry.weekDue += 1
  }
  return acc
}
```

- [ ] **Step 4: 验证**

Run: `node node_modules/vitest/vitest.mjs run tests/AffairsTab.test.tsx`(Expected: 全 PASS)
Run: `node node_modules/typescript/lib/tsc.js --noEmit`(Expected: exit 0)

- [ ] **Step 5: 用户手动提交(建议)**`feat(affairs): healthLevel + summarizeDueByProject pure fns (3.0 三期 T2)`

---

### Task 3: HealthDot 组件 + 侧栏色点

**Files:**
- Create: `src/features/project/affairs/HealthDot.tsx`、`HealthDot.module.css`
- Modify: `src/features/knowledge/ProjectList.tsx` + `.module.css`、`src/features/project/ProjectSidebar.tsx`
- Modify: `src/i18n/locales/{zh-CN,en-US}.json`(新建 `homeTodo` 命名空间,落色点三键)
- Test: `tests/ProjectList.test.tsx`(末尾追加 describe)

**Interfaces:**
- Consumes: Task 2 `ProjectHealthSummary`/`healthLevel`
- Produces: `HealthDot({ summary, className? }: { summary: ProjectHealthSummary; className?: string })`——props 传 summary(非 level),组件内部算档位与 `title` 文案(i18n `homeTodo.healthRed/healthOrange/healthGreen`),消费方零重复;`ProjectList`/`ProjectSidebar` 新增可选 prop `healthData?: Record<string, ProjectHealthSummary>`

- [ ] **Step 1: 写失败测试(tests/ProjectList.test.tsx 末尾追加;沿用文件既有 mock 与 base;若既有用例 Project 字面量字段不足,按组件渲染所需补 description/stage/type)**

```tsx
describe('ProjectList health dot (3.0 三期 Task 3)', () => {
  const proj = { id: 'p1', name: 'P1', file_count: 0, description: '', type: 'general', stage: 'before' }

  it('renders health dot with level class and i18n title when healthData provided', () => {
    const { container } = render(
      <ProjectList {...base} projects={[proj]}
        healthData={{ p1: { overdue: 2, weekDue: 1, reviewsPending: 0 } }} />)
    const dot = container.querySelector('span[class*="red"]')   // css mock: 类名=键名
    expect(dot).toBeTruthy()
    expect(dot!.getAttribute('title')).toContain('homeTodo.healthRed')
  })

  it('omits health dot without healthData (未接线/降级态)', () => {
    const { container } = render(<ProjectList {...base} projects={[proj]} />)
    expect(container.querySelector('span[title]')).toBeNull()
  })
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `node node_modules/vitest/vitest.mjs run tests/ProjectList.test.tsx`
Expected: 新 2 用例 FAIL(无 title span)

- [ ] **Step 3: 实现 HealthDot**

`src/features/project/affairs/HealthDot.tsx`:

```tsx
/** src/features/project/affairs/HealthDot.tsx — 项目健康色点(3.0 §7.2)
 *  口径:红=有逾期;橙=本周待办或待处理发现项;绿=两无。props 传 summary,
 *  组件内部算档位与 title 文案,消费方(侧栏/卡片墙)零重复。 */
import { useTranslation } from 'react-i18next'
import { healthLevel, type ProjectHealthSummary } from './types'
import s from './HealthDot.module.css'

export function HealthDot({ summary, className }: { summary: ProjectHealthSummary; className?: string }) {
  const { t } = useTranslation()
  const level = healthLevel(summary)
  const title = summary.overdue > 0
    ? t('homeTodo.healthRed', { count: summary.overdue })
    : summary.weekDue > 0 || summary.reviewsPending > 0
      ? t('homeTodo.healthOrange', { count: summary.weekDue, reviews: summary.reviewsPending })
      : t('homeTodo.healthGreen')
  return <span className={`${s.dot} ${s[level]}${className ? ` ${className}` : ''}`} title={title} aria-label={title} />
}
```

`src/features/project/affairs/HealthDot.module.css`:

```css
/* 3.0 三期 §7.2:项目健康色点(绿/橙/红) */
.dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.green { background: var(--success); }
.orange { background: var(--text-warning); }
.red { background: var(--error); }
```

- [ ] **Step 4: ProjectList 接色点**

1. import 区追加:

```tsx
import { HealthDot } from '../project/affairs/HealthDot'
import type { ProjectHealthSummary } from '../project/affairs/types'
```

2. props 接口加可选字段 `healthData?: Record<string, ProjectHealthSummary>`(解构参数同步加);
3. 项目 item 渲染:在项目名元素**之前**插入(item 容器最左;若 item 为 flex 布局直接作为首个子元素):

```tsx
{healthData?.[p.id] && <HealthDot summary={healthData[p.id]} className={s.healthDotGap} />}
```

`ProjectList.module.css` 追加:

```css
.healthDotGap {
  margin-right: 6px;
}
```

- [ ] **Step 5: ProjectSidebar 透传**

`ProjectSidebarProps` 加 `healthData?: Record<string, ProjectHealthSummary>`(import 同 HealthDot 来源),解构并透传给 `<ProjectList healthData={healthData} />`。App 接线在 Task 6。

- [ ] **Step 6: i18n 落键(两份 locale 顶层各加 `homeTodo`,后续任务往内追加)**

zh-CN:

```json
"homeTodo": {
  "healthRed": "有 {{count}} 项逾期",
  "healthOrange": "本周 {{count}} 项待办,发现项 {{reviews}} 项",
  "healthGreen": "无待办,无发现项"
}
```

en-US:

```json
"homeTodo": {
  "healthRed": "{{count}} overdue",
  "healthOrange": "{{count}} due this week, {{reviews}} findings",
  "healthGreen": "No due tasks or findings"
}
```

- [ ] **Step 7: 验证**

Run: `node node_modules/vitest/vitest.mjs run tests/ProjectList.test.tsx`(Expected: 全 PASS)
Run: `node node_modules/typescript/lib/tsc.js --noEmit`(Expected: exit 0)

- [ ] **Step 8: 用户手动提交(建议)**`feat(project): HealthDot + sidebar health dots (3.0 三期 T3)`

---

### Task 4: 今日待办 + 近期日历组件

**Files:**
- Create: `src/features/home/TodayTodo.tsx` + `.module.css`、`src/features/home/UpcomingCalendar.tsx` + `.module.css`
- Test: `tests/TodayTodo.test.tsx`、`tests/UpcomingCalendar.test.tsx`(新建)
- Modify: `src/i18n/locales/{zh-CN,en-US}.json`(homeTodo 追加 9 键)

**Interfaces:**
- Consumes: Task 2 `DueTask`/`isOverdue`/`daysOverdue`;既有 IPC `affairs:updateTask`(projectId/taskId/status);`useToast`(src/features/knowledge/Toast,App 全局已挂 Provider)
- Produces:
  - `export type HomeTab = 'affairs' | 'materials' | 'reports'`(从 TodayTodo 导出,后续任务复用)
  - `TodayTodo({ tasks, onOpenProject }: { tasks: DueTask[]; onOpenProject: (projectId: string, tab: HomeTab) => void })`——空列表返回 `null`;打勾成功 `window.dispatchEvent(new CustomEvent('app:tasks-changed'))`(Task 6 App 监听)
  - `UpcomingCalendar({ tasks, onOpenProject }: 同形)`——内部过滤 today..today+7,空返回 `null`

- [ ] **Step 1: 写失败测试 tests/TodayTodo.test.tsx(新建)**

```tsx
/** tests/TodayTodo.test.tsx — 3.0 三期 Task 4:首页今日待办 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, o?: Record<string, unknown>) =>
    (o ? `${k}:${JSON.stringify(o)}` : k) as string }),
}))
vi.mock('../src/features/home/TodayTodo.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))
vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

import { render, fireEvent, cleanup, act } from '@testing-library/react'
import { TodayTodo } from '../src/features/home/TodayTodo'
import type { DueTask } from '../src/features/project/affairs/types'

const flush = async () => { for (let i = 0; i < 6; i++) await act(async () => { await Promise.resolve() }) }

const mk = (over: Partial<DueTask>): DueTask => ({
  id: over.id ?? 't1', project_id: 'pa', phase: 'before', task_type: 'milestone',
  title: over.title ?? '拜访客户', description: '', template_id: null, status: 'open',
  occurrence_key: null, due_date: null, recurrence_rule: null, auto_complete: null,
  linked_entity_type: null, linked_entity_id: null, completed_at: null,
  completed_by: null, created_at: '2026-10-01T00:00:00', project_name: '招聘项目',
  ...over,
} as DueTask)

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date('2026-10-10T10:00:00'))
  ;(window as any).electronAPI = { invoke: vi.fn(async () => ({})) }
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.clearAllMocks() })

describe('TodayTodo (3.0 三期 Task 4)', () => {
  it('renders due text per row: overdue / today / days-left, with cross-project names', () => {
    const tasks = [
      mk({ id: 't1', title: '逾期任务', due_date: '2026-10-08' }),
      mk({ id: 't2', title: '今日任务', due_date: '2026-10-10' }),
      mk({ id: 't3', title: '未来任务', due_date: '2026-10-13', task_type: 'material', project_name: 'B 项目' }),
    ]
    const { container, getByTitle } = render(<TodayTodo tasks={tasks} onOpenProject={vi.fn()} />)
    const text = container.textContent!
    expect(text).toContain('homeTodo.title')
    expect(text).toContain('逾期任务')
    expect(text).toContain('homeTodo.overdueDays')
    expect(text).toContain('homeTodo.dueToday')
    expect(text).toContain('homeTodo.dueInDays')
    expect(getByTitle('homeTodo.upload')).toBeTruthy()          // material 行动作
    expect(text).toContain('B 项目')                            // 跨项目名称
  })

  it('upload button navigates to materials without triggering row nav (stopPropagation)', () => {
    const onOpenProject = vi.fn()
    const { getByTitle } = render(
      <TodayTodo tasks={[mk({ task_type: 'material', due_date: '2026-10-10' })]} onOpenProject={onOpenProject} />)
    fireEvent.click(getByTitle('homeTodo.upload'))
    expect(onOpenProject).toHaveBeenCalledWith('pa', 'materials')
    expect(onOpenProject).toHaveBeenCalledTimes(1)
  })

  it('check button invokes affairs:updateTask and dispatches app:tasks-changed', async () => {
    const dispatchSpy = vi.spyOn(window, 'dispatchEvent')
    const { getByTitle } = render(
      <TodayTodo tasks={[mk({ id: 't9', due_date: '2026-10-10' })]} onOpenProject={vi.fn()} />)
    fireEvent.click(getByTitle('homeTodo.done'))
    await flush()
    expect((window as any).electronAPI.invoke).toHaveBeenCalledWith(
      'affairs:updateTask', { projectId: 'pa', taskId: 't9', status: 'done' })
    const ev = dispatchSpy.mock.calls.map(([e]) => e as CustomEvent).find((e) => e.type === 'app:tasks-changed')
    expect(ev).toBeTruthy()
  })

  it('renders nothing when tasks empty', () => {
    const { container } = render(<TodayTodo tasks={[]} onOpenProject={vi.fn()} />)
    expect(container.querySelector('section')).toBeNull()
  })
})
```

- [ ] **Step 2: 写失败测试 tests/UpcomingCalendar.test.tsx(新建)**

```tsx
/** tests/UpcomingCalendar.test.tsx — 3.0 三期 Task 4:首页近期日历 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, o?: Record<string, unknown>) =>
      (o ? `${k}:${JSON.stringify(o)}` : k) as string,
    i18n: { language: 'zh-CN' },
  }),
}))
vi.mock('../src/features/home/UpcomingCalendar.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, fireEvent, cleanup } from '@testing-library/react'
import { UpcomingCalendar } from '../src/features/home/UpcomingCalendar'
import type { DueTask } from '../src/features/project/affairs/types'

const mk = (id: string, due_date: string, project_name = 'A 项目'): DueTask => ({
  id, project_id: 'pa', phase: 'before', task_type: 'milestone', title: `任务${id}`,
  description: '', template_id: null, status: 'open', occurrence_key: null, due_date,
  recurrence_rule: null, auto_complete: null, linked_entity_type: null,
  linked_entity_id: null, completed_at: null, completed_by: null,
  created_at: '2026-10-01T00:00:00', project_name,
} as DueTask)

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date('2026-10-10T10:00:00'))
})
afterEach(() => { cleanup(); vi.useRealTimers() })

describe('UpcomingCalendar (3.0 三期 Task 4)', () => {
  it('groups by day: includes today, excludes overdue and beyond 7 days', () => {
    const { container } = render(
      <UpcomingCalendar
        tasks={[mk('a', '2026-10-10'), mk('b', '2026-10-08'), mk('c', '2026-10-18'), mk('d', '2026-10-12')]}
        onOpenProject={vi.fn()} />)
    const text = container.textContent!
    expect(text).toContain('10/10')       // 今日含
    expect(text).toContain('10/12')       // 周内含
    expect(text).not.toContain('任务b')   // 逾期不含
    expect(text).not.toContain('任务c')   // 8 天后不含
    expect(text).toContain('affairs.dow') // 周几复用 affairs.dowN
  })

  it('clicking an item navigates to affairs tab', () => {
    const onOpenProject = vi.fn()
    const { getByText } = render(
      <UpcomingCalendar tasks={[mk('a', '2026-10-12')]} onOpenProject={onOpenProject} />)
    fireEvent.click(getByText(/任务a/))
    expect(onOpenProject).toHaveBeenCalledWith('pa', 'affairs')
  })

  it('renders nothing when no tasks within range', () => {
    const { container } = render(<UpcomingCalendar tasks={[mk('b', '2026-10-08')]} onOpenProject={vi.fn()} />)
    expect(container.querySelector('section')).toBeNull()
  })
})
```

- [ ] **Step 3: 跑两个测试文件确认失败**

Run: `node node_modules/vitest/vitest.mjs run tests/TodayTodo.test.tsx tests/UpcomingCalendar.test.tsx`
Expected: FAIL(模块不存在)

- [ ] **Step 4: 实现 TodayTodo**

`src/features/home/TodayTodo.tsx`:

```tsx
/** src/features/home/TodayTodo.tsx — 首页今日待办(3.0 §7.1:跨项目,逾期置顶;
 *  §10 裁决 10:只读 + 直达,打勾为快捷完成,不做字段编辑) */
import { useTranslation } from 'react-i18next'
import { Check } from 'lucide-react'
import { isOverdue, daysOverdue, type DueTask } from '../project/affairs/types'
import { useToast } from '../knowledge/Toast'
import s from './TodayTodo.module.css'

export type HomeTab = 'affairs' | 'materials' | 'reports'

interface TodayTodoProps {
  tasks: DueTask[]
  onOpenProject: (projectId: string, tab: HomeTab) => void
}

function localISO(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${m}-${day}`
}

export function TodayTodo({ tasks, onOpenProject }: TodayTodoProps) {
  const { t } = useTranslation()
  const { showToast } = useToast()

  const dueText = (task: DueTask): string => {
    if (isOverdue(task)) return t('homeTodo.overdueDays', { count: daysOverdue(task) })
    if (task.due_date === localISO(new Date())) return t('homeTodo.dueToday')
    const dueMs = new Date(`${task.due_date!.slice(0, 10)}T00:00:00`).getTime()
    const todayMs = new Date(`${localISO(new Date())}T00:00:00`).getTime()
    return t('homeTodo.dueInDays', { count: Math.round((dueMs - todayMs) / 86400000) })
  }

  const handleDone = async (task: DueTask) => {
    try {
      await window.electronAPI.invoke('affairs:updateTask', {
        projectId: task.project_id, taskId: task.id, status: 'done',
      })
      // 跨页边沿:App 重拉 listDue(侧栏色点/卡片墙/本列表同步)——打开时拉取+事件边沿,非轮询(0.4 §3.1)
      window.dispatchEvent(new CustomEvent('app:tasks-changed'))
    } catch {
      showToast(t('homeTodo.doneFailed'), 'error')
    }
  }

  if (tasks.length === 0) return null
  return (
    <section className={s.section}>
      <div className={s.title}>{t('homeTodo.title')}</div>
      <div className={s.list}>
        {tasks.map((task) => (
          <div key={task.id} className={s.row} onClick={() => onOpenProject(task.project_id, 'affairs')}>
            <span className={`${s.due} ${isOverdue(task) ? s.dueOverdue : ''}`}>{dueText(task)}</span>
            <span className={s.text}><b>{task.project_name}</b> · {task.title}</span>
            {task.task_type === 'material' && (
              <button className={s.action} title={t('homeTodo.upload')}
                onClick={(e) => { e.stopPropagation(); onOpenProject(task.project_id, 'materials') }}>
                {t('homeTodo.upload')}
              </button>
            )}
            {task.task_type === 'report' && (
              <button className={s.action} title={t('homeTodo.open')}
                onClick={(e) => { e.stopPropagation(); onOpenProject(task.project_id, 'reports') }}>
                {t('homeTodo.open')}
              </button>
            )}
            <button className={s.action} title={t('homeTodo.done')} aria-label={t('homeTodo.done')}
              onClick={(e) => { e.stopPropagation(); void handleDone(task) }}>
              <Check size={14} />
            </button>
          </div>
        ))}
      </div>
    </section>
  )
}
```

`src/features/home/TodayTodo.module.css`:

```css
/* 3.0 三期 §7.1:首页今日待办 */
.section { margin-bottom: 20px; }
.title { font-size: 13px; font-weight: 600; color: var(--text-primary); margin-bottom: 8px; }
.list { display: flex; flex-direction: column; gap: 4px; }
.row {
  display: flex; align-items: center; gap: 8px;
  padding: 6px 10px; border-radius: var(--radius-sm);
  background: var(--bg-secondary); cursor: pointer;
}
.row:hover { background: var(--bg-hover); }
.due { font-size: 12px; color: var(--text-secondary); white-space: nowrap; }
.dueOverdue { color: var(--error); font-weight: 600; }
.text { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; color: var(--text-primary); }
.text b { font-weight: 600; }
.action {
  border: none; background: transparent; color: var(--accent);
  font-size: 12px; cursor: pointer; padding: 2px 6px; border-radius: 4px;
  display: inline-flex; align-items: center;
}
.action:hover { background: var(--bg-hover); }
```

- [ ] **Step 5: 实现 UpcomingCalendar**

`src/features/home/UpcomingCalendar.tsx`:

```tsx
/** src/features/home/UpcomingCalendar.tsx — 首页近期日历(3.0 §6.1.8:未来 7 天跨项目事务按日分组) */
import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import type { DueTask } from '../project/affairs/types'
import type { HomeTab } from './TodayTodo'
import s from './UpcomingCalendar.module.css'

interface UpcomingCalendarProps {
  tasks: DueTask[]
  onOpenProject: (projectId: string, tab: HomeTab) => void
}

function localISO(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${m}-${day}`
}

export function UpcomingCalendar({ tasks, onOpenProject }: UpcomingCalendarProps) {
  const { t } = useTranslation()

  const days = useMemo(() => {
    const today = localISO(new Date())
    const limit = new Date()
    limit.setDate(limit.getDate() + 7)
    const byDay = new Map<string, DueTask[]>()
    for (const task of tasks) {
      const due = task.due_date?.slice(0, 10)
      if (!due || due < today || due > localISO(limit)) continue
      const arr = byDay.get(due) ?? []
      arr.push(task)
      byDay.set(due, arr)
    }
    return [...byDay.entries()].sort(([a], [b]) => a.localeCompare(b))
  }, [tasks])

  if (days.length === 0) return null
  return (
    <section className={s.section}>
      <div className={s.title}>{t('homeTodo.calendarTitle')}</div>
      {days.map(([day, list]) => (
        <div key={day} className={s.dayRow}>
          <span className={s.dayLabel}>
            {day.slice(5).replace('-', '/')} · {t(`affairs.dow${new Date(`${day}T00:00:00`).getDay()}`)}
          </span>
          <div className={s.dayItems}>
            {list.map((task) => (
              <span key={task.id} className={s.item}
                onClick={() => onOpenProject(task.project_id, 'affairs')}>
                <b>{task.project_name}</b> · {task.title}
              </span>
            ))}
          </div>
        </div>
      ))}
    </section>
  )
}
```

`src/features/home/UpcomingCalendar.module.css`:

```css
/* 3.0 三期 §6.1.8:首页近期日历 */
.section { margin-bottom: 20px; }
.title { font-size: 13px; font-weight: 600; color: var(--text-primary); margin-bottom: 8px; }
.dayRow { display: flex; gap: 10px; padding: 4px 0; align-items: baseline; }
.dayLabel { font-size: 12px; color: var(--text-muted); white-space: nowrap; min-width: 64px; }
.dayItems { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.item {
  font-size: 13px; color: var(--text-primary); cursor: pointer;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.item:hover { color: var(--accent); }
.item b { font-weight: 600; }
```

- [ ] **Step 6: i18n 追加 9 键(homeTodo 内)**

zh-CN:`"title": "今日待办"`、`"overdueDays": "逾期 {{count}} 天"`、`"dueToday": "今日到期"`、`"dueInDays": "剩余 {{count}} 天"`、`"done": "标记完成"`、`"doneFailed": "操作失败,请重试"`、`"upload": "上传"`、`"open": "打开"`、`"calendarTitle": "近期日历"`

en-US:`"title": "Today"`、`"overdueDays": "{{count}}d overdue"`、`"dueToday": "Due today"`、`"dueInDays": "{{count}}d left"`、`"done": "Mark done"`、`"doneFailed": "Operation failed, please retry"`、`"upload": "Upload"`、`"open": "Open"`、`"calendarTitle": "Coming week"`

- [ ] **Step 7: 验证**

Run: `node node_modules/vitest/vitest.mjs run tests/TodayTodo.test.tsx tests/UpcomingCalendar.test.tsx`
Expected: 全部 PASS
Run: `node node_modules/typescript/lib/tsc.js --noEmit`
Expected: exit 0

- [ ] **Step 8: 用户手动提交(建议)**`feat(home): today todo + upcoming calendar (3.0 三期 T4)`

### Task 5: 项目卡片墙 + HomePage 装配

**Files:**
- Create: `src/features/home/ProjectCardWall.tsx` + `.module.css`
- Modify: `src/features/home/HomePage.tsx`(装配 + 替换最近项目列表)
- Modify: `src/features/home/HomePage.module.css`(删最近项目专用死类)
- Test: `tests/ProjectCardWall.test.tsx`、`tests/HomePage.test.tsx`(新建)
- Modify: `src/i18n/locales/{zh-CN,en-US}.json`(homeTodo 追加 2 键)

**Interfaces:**
- Consumes: Task 2 `ProjectHealthSummary`;Task 3 `HealthDot`;Task 4 `HomeTab`;既有 `STAGE_MAP`(src/features/project/types.ts)与 `Project`(knowledge/types)
- Produces:
  - `ProjectCardWall({ projects, healthData?, nextDueByProject?, onSelect }: { projects: Project[]; healthData?: Record<string, ProjectHealthSummary>; nextDueByProject?: Record<string, string | null>; onSelect: (projectId: string) => void })`——projects 空渲染 `home.noProjects`
  - HomePage 新增可选 prop:`dueData?: { tasks: DueTask[]; reviews_counts: Record<string, number> } | null`、`healthByProject?: Record<string, ProjectHealthSummary>`
  - HomePage 区块顺序(§6.1.8):状态卡 → 进行中 → 今日待办(空隐藏)→ 近期日历(空隐藏)→ recentGrid(左=卡片墙,右=活动流)→ 快捷入口(移至最后)

- [ ] **Step 1: 写失败测试 tests/ProjectCardWall.test.tsx(新建)**

```tsx
/** tests/ProjectCardWall.test.tsx — 3.0 三期 Task 5:首页项目卡片墙 */
import { describe, it, expect, vi, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, o?: Record<string, unknown>) =>
    (o ? `${k}:${JSON.stringify(o)}` : k) as string }),
}))
vi.mock('../src/features/home/ProjectCardWall.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))
vi.mock('../src/features/project/affairs/HealthDot.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, fireEvent, cleanup } from '@testing-library/react'
import { ProjectCardWall } from '../src/features/home/ProjectCardWall'
import type { Project } from '../src/features/knowledge/types'

afterEach(() => cleanup())

const proj = { id: 'p1', name: '招聘项目', description: '', type: 'general', stage: 'before', file_count: 0 } as Project

describe('ProjectCardWall (3.0 三期 Task 5)', () => {
  it('renders card per project with stage label fallback and next due date', () => {
    const { container, getByText } = render(
      <ProjectCardWall projects={[proj]}
        healthData={{ p1: { overdue: 0, weekDue: 2, reviewsPending: 0 } }}
        nextDueByProject={{ p1: '2026-10-12' }}
        onSelect={vi.fn()} />)
    expect(getByText('招聘项目')).toBeTruthy()
    const text = container.textContent!
    expect(text).toContain('project.stage.before')   // general 走 STAGE_MAP fallback
    expect(text).toContain('homeTodo.nextDue')
    expect(text).toContain('10/12')
    expect(container.querySelector('span[class*="orange"]')).toBeTruthy()   // 色点=橙
  })

  it('omits stage pill and next-due when absent', () => {
    const { container, getByText } = render(
      <ProjectCardWall projects={[{ ...proj, stage: 'none' }]} onSelect={vi.fn()} />)
    expect(getByText('招聘项目')).toBeTruthy()
    expect(container.textContent).not.toContain('project.stage')
    expect(container.textContent).not.toContain('homeTodo.nextDue')
  })

  it('clicking card calls onSelect', () => {
    const onSelect = vi.fn()
    const { getByText } = render(<ProjectCardWall projects={[proj]} onSelect={onSelect} />)
    fireEvent.click(getByText('招聘项目'))
    expect(onSelect).toHaveBeenCalledWith('p1')
  })

  it('renders empty hint when no projects', () => {
    const { container } = render(<ProjectCardWall projects={[]} onSelect={vi.fn()} />)
    expect(container.textContent).toContain('home.noProjects')
  })
})
```

- [ ] **Step 2: 写失败测试 tests/HomePage.test.tsx(新建)**

```tsx
/** tests/HomePage.test.tsx — 3.0 三期 Task 5:首页装配(待办/日历/卡片墙替换最近项目) */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, o?: Record<string, unknown>) =>
    (o ? `${k}:${JSON.stringify(o)}` : k) as string }),
}))
vi.mock('../src/features/activity/ActivityContext', () => ({
  useActivity: () => ({ status: null, feed: [], markRead: vi.fn() }),
}))
for (const css of ['HomePage', 'TodayTodo', 'UpcomingCalendar', 'ProjectCardWall']) {
  vi.mock(`../src/features/home/${css}.module.css`, () => ({
    default: new Proxy({}, { get: (_t, k) => String(k) }),
  }))
}
vi.mock('../src/features/project/affairs/HealthDot.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, fireEvent, cleanup, act } from '@testing-library/react'
import { HomePage } from '../src/features/home/HomePage'
import type { DueTask } from '../src/features/project/affairs/types'

const flush = async () => { for (let i = 0; i < 6; i++) await act(async () => { await Promise.resolve() }) }

const mk = (id: string, due_date: string): DueTask => ({
  id, project_id: 'p1', phase: 'before', task_type: 'milestone', title: `任务${id}`,
  description: '', template_id: null, status: 'open', occurrence_key: null, due_date,
  recurrence_rule: null, auto_complete: null, linked_entity_type: null,
  linked_entity_id: null, completed_at: null, completed_by: null,
  created_at: '2026-10-01T00:00:00', project_name: '招聘项目',
} as DueTask)

const base = { onNavigate: vi.fn(), onOpenSettings: vi.fn(), onSelectProject: vi.fn() }
const projects = [{ id: 'p1', name: '招聘项目', description: '', type: 'general', stage: 'before', file_count: 0 }] as any[]

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date('2026-10-10T10:00:00'))
  ;(window as any).electronAPI = { invoke: vi.fn(async (channel: string) =>
    channel === 'system:getHealth'
      ? { status: 'ok', services: { llm: { status: 'ok' }, vlm: { status: 'ok' }, embedding: { status: 'ok' }, parsing: { status: 'ok' } } }
      : {}) }
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.clearAllMocks() })

describe('HomePage workbench assembly (3.0 三期 Task 5)', () => {
  it('renders today todo section with dueData; hidden without', async () => {
    const r1 = render(<HomePage {...base} projects={projects} dueData={{ tasks: [mk('t1', '2026-10-08')], reviews_counts: {} }} />)
    await flush()
    expect(r1.container.textContent).toContain('homeTodo.title')
    expect(r1.container.textContent).toContain('任务t1')
    r1.unmount()
    const r2 = render(<HomePage {...base} projects={projects} />)
    await flush()
    expect(r2.container.textContent).not.toContain('homeTodo.title')
  })

  it('card wall replaces recent projects list', async () => {
    const { container } = render(
      <HomePage {...base} projects={projects}
        dueData={{ tasks: [], reviews_counts: {} }}
        healthByProject={{ p1: { overdue: 0, weekDue: 0, reviewsPending: 0 } }} />)
    await flush()
    expect(container.textContent).toContain('homeTodo.cardWallTitle')
    expect(container.textContent).not.toContain('home.recentProjects')   // 旧列表标题退场
    expect(container.textContent).toContain('招聘项目')
  })

  it('card click navigates to project detail', async () => {
    const dispatchSpy = vi.spyOn(window, 'dispatchEvent')
    const onSelectProject = vi.fn()
    const { getByText } = render(
      <HomePage {...base} projects={projects} onSelectProject={onSelectProject}
        dueData={{ tasks: [], reviews_counts: {} }} />)
    await flush()
    fireEvent.click(getByText('招聘项目'))
    const ev = dispatchSpy.mock.calls.map(([e]) => e as CustomEvent).find((e) => e.type === 'app:navigate-project-tab')
    expect(ev).toBeTruthy()
    expect((ev as CustomEvent).detail).toEqual({ projectId: 'p1', container: 'project' })
    expect(onSelectProject).toHaveBeenCalledWith('p1')
  })
})
```

- [ ] **Step 3: 跑两个测试文件确认失败**

Run: `node node_modules/vitest/vitest.mjs run tests/ProjectCardWall.test.tsx tests/HomePage.test.tsx`
Expected: FAIL(模块不存在 / 断言不满足)

- [ ] **Step 4: 实现 ProjectCardWall**

`src/features/home/ProjectCardWall.tsx`:

```tsx
/** src/features/home/ProjectCardWall.tsx — 首页项目卡片墙(3.0 §7.1:替代最近项目列表;
 *  每卡=健康色点+项目名+阶段 pill+下一事务日期。台账摘要为四期,不实现) */
import { useTranslation } from 'react-i18next'
import type { Project } from '../knowledge/types'
import { STAGE_MAP } from '../project/types'
import type { ProjectHealthSummary } from '../project/affairs/types'
import { HealthDot } from '../project/affairs/HealthDot'
import s from './ProjectCardWall.module.css'

interface ProjectCardWallProps {
  projects: Project[]
  healthData?: Record<string, ProjectHealthSummary>
  nextDueByProject?: Record<string, string | null>
  onSelect: (projectId: string) => void
}

export function ProjectCardWall({ projects, healthData, nextDueByProject, onSelect }: ProjectCardWallProps) {
  const { t } = useTranslation()

  const stageLabel = (p: Project): string | null => {
    if (p.stage === 'none') return null
    const key = STAGE_MAP[p.type]?.find((x) => x.key === p.stage)?.labelKey ?? `project.stage.${p.stage}`
    return t(key)
  }

  if (projects.length === 0) return <div className={s.empty}>{t('home.noProjects')}</div>
  return (
    <div className={s.grid}>
      {projects.map((p) => {
        const sum = healthData?.[p.id]
        const nextDue = nextDueByProject?.[p.id]
        const label = stageLabel(p)
        return (
          <div key={p.id} className={s.card} onClick={() => onSelect(p.id)}>
            <div className={s.head}>
              {sum && <HealthDot summary={sum} />}
              <span className={s.name}>{p.name}</span>
            </div>
            <div className={s.meta}>
              {label && <span className={s.stage}>{label}</span>}
              {nextDue && (
                <span className={s.nextDue}>
                  {t('homeTodo.nextDue')} {nextDue.slice(5).replace('-', '/')}
                </span>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}
```

`src/features/home/ProjectCardWall.module.css`:

```css
/* 3.0 三期 §7.1:首页项目卡片墙 */
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.card {
  border: 1px solid var(--border); border-radius: var(--radius-sm);
  padding: 8px 10px; cursor: pointer; min-width: 0;
}
.card:hover { background: var(--bg-hover); }
.head { display: flex; align-items: center; gap: 6px; min-width: 0; }
.name { font-size: 13px; font-weight: 600; color: var(--text-primary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.meta { display: flex; gap: 8px; margin-top: 4px; flex-wrap: wrap; }
.stage {
  font-size: 11px; padding: 1px 6px; border-radius: 8px;
  background: var(--bg-tertiary); color: var(--text-secondary);
}
.nextDue { font-size: 11px; color: var(--text-muted); }
.empty { font-size: 12px; color: var(--text-muted); padding: 8px 0; }
```

- [ ] **Step 5: i18n 追加 2 键(homeTodo 内)**

zh-CN:`"cardWallTitle": "项目"`、`"nextDue": "下一事务"`;en-US:`"cardWallTitle": "Projects"`、`"nextDue": "Next due"`

- [ ] **Step 6: HomePage 装配改造(src/features/home/HomePage.tsx)**

1. import 追加(`useMemo` 并入既有 react import 行):

```tsx
import { TodayTodo, type HomeTab } from './TodayTodo'
import { UpcomingCalendar } from './UpcomingCalendar'
import { ProjectCardWall } from './ProjectCardWall'
import type { DueTask, ProjectHealthSummary } from '../project/affairs/types'
```

2. props 接口追加(可选,向后兼容):

```tsx
  dueData?: { tasks: DueTask[]; reviews_counts: Record<string, number> } | null
  healthByProject?: Record<string, ProjectHealthSummary>
```

3. 组件体内计算(解构参数同步加 `dueData`/`healthByProject`):

```tsx
  const todoTasks = dueData?.tasks ?? []

  /** 卡片墙「下一事务」:该项目 due 最早的日期(listDue 已限逾期+7 日内) */
  const nextDueByProject = useMemo(() => {
    const acc: Record<string, string | null> = {}
    for (const task of todoTasks) {
      const due = task.due_date?.slice(0, 10)
      if (!due) continue
      const cur = acc[task.project_id]
      if (!cur || due < cur) acc[task.project_id] = due
    }
    return acc
  }, [dueData])

  /** 待办/日历直达:进项目详情对应 Tab(affairs/materials/reports) */
  const handleOpenProjectTab = (projectId: string, tab: HomeTab) => {
    window.dispatchEvent(new CustomEvent('app:navigate-project-tab', {
      detail: { projectId, tab, container: 'project' },
    }))
  }
```

4. 区块装配(按 §6.1.8 顺序;状态卡/进行中区块不动,其后插入两个新区块,`recentGrid` 左列换卡片墙,快捷入口整块移至 recentGrid 之后):

```tsx
      {/* ── Today todo (3.0 §7.1;空隐藏) ── */}
      <TodayTodo tasks={todoTasks} onOpenProject={handleOpenProjectTab} />

      {/* ── Upcoming calendar (§6.1.8;空隐藏) ── */}
      <UpcomingCalendar tasks={todoTasks} onOpenProject={handleOpenProjectTab} />

      {/* ── Project card wall + activity feed ── */}
      <div className={s.sectionTitle}>{t('home.recentTitle')}</div>
      <div className={s.recentGrid}>
        <div className={s.recentSection}>
          <div className={s.sectionTitle} style={{ fontSize: 12, marginBottom: 8 }}>
            {t('homeTodo.cardWallTitle')}
          </div>
          <ProjectCardWall
            projects={projects}
            healthData={healthByProject}
            nextDueByProject={nextDueByProject}
            onSelect={(id) => {
              onSelectProject(id)
              window.dispatchEvent(new CustomEvent('app:navigate-project-tab', {
                detail: { projectId: id, container: 'project' },
              }))
            }}
          />
        </div>
        {/* 活动流区块:现状代码原样保留 */}
      </div>

      {/* ── Quick start(移至最后,§6.1.8)── */}
      <div className={s.sectionTitle}>{t('home.quickTitle')}</div>
      <div className={s.quickGrid}>…现状代码原样迁移…</div>
```

5. **删除**现状最近项目逻辑(L70-73 `recentProjects` 排序/slice 与 L226-259 渲染 JSX;`recentMore`/`viewAll` 按钮随删)——被卡片墙替代。
6. `HomePage.module.css` 同类清理:删除仅被旧最近项目列表使用的死类(`recentItem`/`recentItemIcon`/`recentItemText`/`recentItemMeta`/`recentMore`——删除前在文件内 Grep 确认无其他引用;`recentList`/`recentEmpty`/`recentSection` 活动流仍在用,保留)。

- [ ] **Step 7: 验证**

Run: `node node_modules/vitest/vitest.mjs run tests/ProjectCardWall.test.tsx tests/HomePage.test.tsx`
Expected: 全部 PASS
Run: `node node_modules/vitest/vitest.mjs run`(全量;Expected: 无回归)
Run: `node node_modules/typescript/lib/tsc.js --noEmit`
Expected: exit 0

- [ ] **Step 8: 用户手动提交(建议)**`feat(home): project card wall + workbench assembly (3.0 三期 T5)`

---

### Task 6: App 编排 + 事件边沿 + 文档回改 + 全量验证

**Files:**
- Modify: `src/App.tsx`(dueData 快照 + loadDueSummary + 事件监听 + 下发两侧)
- Modify: `src/features/project/affairs/AffairsTab.tsx`(handleChanged 追加 dispatch)
- Modify: `docs/3.0project-lifecycle-design.md`(§8 三期行 + §9 对齐表)

**Interfaces:**
- Consumes: Task 1 IPC `affairs:listDue`;Task 2 `summarizeDueByProject`;Task 3 `ProjectSidebar.healthData`;Task 5 `HomePage.dueData/healthByProject`
- Produces: 全链路闭环——任务写操作(AffairsTab 任意写/首页打勾)→ `app:tasks-changed` → App 重拉 `affairs:listDue` → 侧栏色点/首页待办/日历/卡片墙同步

- [ ] **Step 1: App.tsx 编排**

1. import 追加(并入既有行):

```tsx
import { summarizeDueByProject } from './features/project/affairs/types'
import type { DueTask, ProjectHealthSummary } from './features/project/affairs/types'
```

2. `projects` state(L96)附近追加:

```tsx
  // ── 3.0 三期:首页今日工作台 + 侧栏色点单一数据快照(§7.2)──
  const [dueData, setDueData] = useState<{ tasks: DueTask[]; reviews_counts: Record<string, number> } | null>(null)

  const loadDueSummary = useCallback(async () => {
    try {
      const result: any = await window.electronAPI.invoke('affairs:listDue')
      setDueData({ tasks: result?.tasks ?? [], reviews_counts: result?.reviews_counts ?? {} })
    } catch {
      // 后端未就绪/失败:待办与日历隐藏、色点不渲染——展示层对 null 已降级
      setDueData(null)
    }
  }, [])

  // 打开时拉取 + 导航边沿重拉(侧栏色点常驻,任何 nav 切换都可能改变项目数据);非轮询(0.4 §3.1)
  useEffect(() => { void loadDueSummary() }, [activeNav, loadDueSummary])

  // 任务写操作边沿(AffairsTab 写操作 / 首页打勾后 dispatch)
  useEffect(() => {
    const h = () => { void loadDueSummary() }
    window.addEventListener('app:tasks-changed', h)
    return () => window.removeEventListener('app:tasks-changed', h)
  }, [loadDueSummary])

  const healthByProject = useMemo(
    () => summarizeDueByProject(dueData?.tasks ?? [], dueData?.reviews_counts),
    [dueData],
  )
```

3. 下发两侧接线:

```tsx
<ProjectSidebar
  …既有 props 不动…
  healthData={healthByProject}
/>
<HomePage
  …既有 props 不动…
  dueData={dueData}
  healthByProject={healthByProject}
/>
```

- [ ] **Step 2: AffairsTab 写操作边沿(src/features/project/affairs/AffairsTab.tsx)**

`handleChanged`(L74,TaskDrawer `onChanged` 与 TaskFormModal `onSaved` 的共同汇聚点)追加 dispatch:

```tsx
  const handleChanged = useCallback(() => {
    window.dispatchEvent(new CustomEvent('app:tasks-changed'))  // 3.0 三期:跨页边沿(侧栏色点/首页待办同步)
    void load()
  }, [load])
```

- [ ] **Step 3: 文档回改(docs/3.0project-lifecycle-design.md,§2.1 双向对齐)**

1. §8 分期路线:三期行尾追加 `✅ 已交付(2026-10-10)`;
2. §9 双向对齐表:核对三期相关行(0.3 图标——本期零新图标,Folder/Check 均为既有;2.1 行不变;3.0 自身行更新交付状态),若 §7.2 有「三期」字样的前瞻表述(如 `affairs:listDue(三期)`),更新为 `affairs:listDue(已交付)`;
3. 核对 §5.1 分层组件图 L269 `affairs:*` 通道清单:`listDue 三期` → `listDue(已交付)`。

- [ ] **Step 4: 全量验证**

Run: `node node_modules/typescript/lib/tsc.js --noEmit`(Expected: exit 0)
Run: `node node_modules/vitest/vitest.mjs run`(Expected: 全量 PASS,含新增 4 个测试文件)
Run: `C:/Users/Kelvin/.conda/envs/inkcube/python.exe -m pytest python/tests -q`(Expected: 全量 PASS)
Run: `node --check electron/ipc-handlers.js`(Expected: exit 0)

- [ ] **Step 5: 运行验证(用户配合)**

`npm run dev` 启动完整栈,人工核对:①首页显示今日待办/近期日历(有数据时)②卡片墙色点与侧栏色点同色 ③事务 Tab 内完成任务后返回首页,待办与色点同步消失/变绿 ④首页打勾后侧栏色点即时变化 ⑤中英文切换文案完整。

- [ ] **Step 6: 用户手动提交(建议)**`feat(home): app-level due snapshot + tasks-changed edge (3.0 三期 T6)`

---

## Self-Review 结论(已执行)

1. **Spec 覆盖**:§7.1 今日待办(逾期置顶=T4/动作按钮=T4)✓、卡片墙(色点/阶段 pill/下一事务=T5)✓、§7.2 listDue(内嵌 summary=T1)✓、色点(侧栏=T3/卡片墙=T5)✓、§6.1.8 布局=T5 ✓、裁决 10 只读+直达+打勾=T4 ✓;近期日历按日分组=T4 ✓。台账摘要(§7.1 四期)不实现,组件注释已声明。
2. **占位符扫描**:全部步骤含完整代码;Task 5 Step 6 的「活动流/快捷入口现状代码原样保留」为移动指令(非新代码),锚点行号已给。
3. **类型一致性**:`DueTask`/`ProjectHealthSummary`/`HealthLevel`/`healthLevel`/`summarizeDueByProject`(T2)↔ `HealthDot.summary`(T3/T5)↔ `ProjectList/ProjectSidebar.healthData`(T3/T6)↔ `HomePage.dueData/healthByProject`(T5/T6)↔ `HomeTab`(T4→T5)↔ IPC `affairs:listDue` 返回键 `tasks`/`reviews_counts`(T1→T6)一致;`app:tasks-changed`(T4/T6)一致。

## 交付核对清单(执行完成后逐项过)

- [ ] 0.4 §3.1 轮询清单未增长(dueData=打开拉取+nav 边沿+事件边沿,无 interval)
- [ ] 零硬编码文案(全部 i18n,zh/en 双份)
- [ ] 零新图标(Folder/Check 既有);`HomePage.module.css` 死类已清理且全仓无引用
- [ ] 侧栏色点与卡片墙色点同一数据源同一组件(HealthDot)
- [ ] 测试落位:纯函数入 AffairsTab.test、组件独立 test 文件、后端入 test_affairs_api.py;全量绿(vitest + pytest + tsc)
- [ ] 3.0 §8/§9/§5.1 交付标注完成(§2.1 双向对齐)

