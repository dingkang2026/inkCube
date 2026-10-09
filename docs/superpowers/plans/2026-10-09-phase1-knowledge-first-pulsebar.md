# 一期:知识置首 + 脉搏带 v1 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 项目详情页实现「知识置首 + 脉搏带 v1 + 知识总览健康卡/结构页完成度」——纯前端,零后端改动。

**Architecture:** PulseBar 作为 ProjectDetailPage 头部与 Tab 栏之间的状态感知层,数据全部来自已有 IPC(`wiki:listReviews` / `wiki:listPages` / `wiki:getSchemas` / `wiki:compileStatus`)与 0.4 全局心跳(`useActivity`),不新增轮询端点;WikiTab 新增 `focusSignal` prop 支持健康卡/chips 直达治理视图;WikiHomePage 增强「首页」视图(健康卡 + 结构页完成度),子导航与其余视图不动。

**Tech Stack:** React 18 + TypeScript + react-i18next + vitest/@testing-library + CSS Modules;Lucide 图标(`Library`/`Activity` 新增导入)。

**Spec:** [docs/3.0project-lifecycle-design.md](../../3.0project-lifecycle-design.md) §2(脉搏带/Tab)、§6.1.1-6.1.3(布局)、§6.3(状态色/图标映射)、§8(一期范围)、§9(对齐);视觉基准 [prototypes/3.0-project-lifecycle-prototype.html](../../prototypes/3.0-project-lifecycle-prototype.html)。

## Global Constraints

- **禁止执行任何 git 操作**(add/commit/push 等)——每个任务的提交步骤由用户手动完成;执行者验证通过后输出建议提交信息并暂停等待用户
- **0.4 轮询清单不增长**:本计划零新增轮询;reviews/listPages/schemas 均为「挂载拉取 + 编译终态边沿补拉」(复用 ProjectDetailPage 现有 `useEdgeRefetch(wikiCompileActive, ...)` 时机)
- **i18n 零硬编码**:新增文案一律进 `src/i18n/locales/zh-CN.json` 与 `en-US.json`,key 前缀 `pulse.*`(脉搏带)、`project.wiki.homePage.*`(健康卡/完成度,沿用现有前缀)
- **图标规范**:Lucide 中央注册表,禁 emoji;状态色用既有 token(`--error`/`--warning`/`--accent`/`--text-muted`/`--success`,见 0.3 §4.3)
- **tab key `wiki` 不变**,仅显示名改「知识」、排序置首(3.0 §10 决策 2)
- 验证命令:前端语法/类型 `npx tsc --noEmit`;测试 `npx vitest run`;构建 `npm run build`;运行验证 `npm run dev`
- Python 环境使用 `C:/Users/Kelvin/.conda/envs/inkcube/python.exe`(本计划无 Python 改动,仅备用)
- 测试文件集中于 `tests/`(跟随项目现状,与被测组件同目录的规范在此仓库以 `tests/` 集中落)

---

### Task 1: Tab 重排改名——知识置首

**Files:**
- Modify: `src/features/project/ProjectDetailPage.tsx:1-42`(TABS 数组、默认 activeTab、头注释)
- Modify: `src/i18n/locales/zh-CN.json:678-681`、`src/i18n/locales/en-US.json`(对应行,`project.tab.*`)
- Test: `tests/ProjectDetailPage.test.tsx`(新增默认 Tab 断言)

**Interfaces:**
- Consumes: 现状 `TabKey = 'materials' | 'wiki' | 'reports' | 'forms'`(不改 key 集合)
- Produces: TABS 顺序 `wiki → materials → reports → forms`;`useState<TabKey>('wiki')` 为默认;`tabSignal` 白名单数组不变(L69)

- [ ] **Step 1: 写失败测试**——在 `tests/ProjectDetailPage.test.tsx` 的 describe 内追加:

```tsx
it('defaults to the wiki tab (knowledge-first, 3.0 §8 phase 1)', async () => {
  const { container } = render(<ProjectDetailPage project={project} />)
  await flush()
  const tabs = Array.from(container.querySelectorAll('button'))
  const active = tabs.find((b) => b.className.includes('tabActive'))
  expect(active?.textContent).toContain('project.tab.wiki')
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run tests/ProjectDetailPage.test.tsx`
Expected: FAIL——当前默认 `materials`,active 文案为 `project.tab.materials`

- [ ] **Step 3: 实现**——`ProjectDetailPage.tsx` 三处:

```tsx
/** src/features/project/ProjectDetailPage.tsx — 项目详情页:知识置首 + 脉搏带(3.0 一期) */
// L30-35 TABS 重排(wiki 置首;key 不变):
const TABS: { key: TabKey; labelKey: string; Icon: typeof FolderOpen }[] = [
  { key: 'wiki', labelKey: 'project.tab.wiki', Icon: BookOpen },
  { key: 'materials', labelKey: 'project.tab.materials', Icon: FolderOpen },
  { key: 'reports', labelKey: 'project.tab.reports', Icon: FileText },
  { key: 'forms', labelKey: 'project.tab.forms', Icon: ClipboardList },
]
// L42 默认 Tab:
const [activeTab, setActiveTab] = useState<TabKey>('wiki')
```

注意:L1 头注释「4-tab structure」改为「knowledge-first tabs + PulseBar(3.0 phase 1)」;知识 Tab 图标暂保持 `BookOpen`(`Library` 更换并入 Task 3 一并做,避免本任务测试夹带图标变更)。

- [ ] **Step 4: i18n 改名**——`zh-CN.json` project.tab 段:`"wiki": "知识"`(en-US 同步 `"wiki": "Knowledge"`);其余三个 key 文案不动。

- [ ] **Step 5: 跑测试确认通过**

Run: `npx vitest run tests/ProjectDetailPage.test.tsx`
Expected: PASS(含既有 5 个用例——`.tabPulse` 断言按 className 匹配,不受顺序影响)

- [ ] **Step 6: 提示用户提交**(建议信息:`feat(project): knowledge-first tab order (3.0 phase1 task1)`)

---

### Task 2: PulseBar 组件(展开态 + 折叠态)

**Files:**
- Create: `src/features/project/PulseBar.tsx`
- Test: `tests/PulseBar.test.tsx`

**Interfaces:**
- Consumes: `useActivity`(`src/features/activity/ActivityContext`):无参形态返回 `byProject: Map<string, {files,compiles,reports}>`;`useActivity('wiki_compile')` 返回 `items: [{project_id}]`
- Produces(后续任务依赖,签名精确):

```tsx
// src/features/project/PulseBar.tsx
export interface PulseReviewCounts { contradiction: number; gap: number }
export interface PulseBarProps {
  projectId: string
  stageLabel: string                    // 阶段显示名(状态句前缀),由调用方 t() 后传入
  reviews: PulseReviewCounts            // wiki:listReviews 分型计数(未裁决)
  pendingCompile: number                // 待编译文件数(调用方从 listPages 计算)
  onOpenWiki: (view?: 'lint') => void   // chip 点击;lint=治理视图
}
export function PulseBar(props: PulseBarProps): JSX.Element
// 组件内部:
//  - 折叠态持久化:localStorage key `pulsebar:collapsed:${projectId}`('1'=折叠)
//  - 编译中/报告生成中从 useActivity 内部派生,不经 props
//  - chips 全零且无运行态 → 「一切正常」灰点行(3.0 §6.1.1)
```

- [ ] **Step 1: 写失败测试** `tests/PulseBar.test.tsx`:

```tsx
/** tests/PulseBar.test.tsx — 脉搏带 v1:chips 显隐/状态句/折叠持久化/「一切正常」态 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

const byProject = new Map<string, { files: number; compiles: number; reports: number }>()
vi.mock('../src/features/activity/ActivityContext', () => ({
  useActivity: (kind?: string) =>
    kind === 'wiki_compile'
      ? { items: [] }
      : { byProject, items: [] },
}))
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }))
vi.mock('../src/features/project/PulseBar.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, cleanup } from '@testing-library/react'
import { PulseBar } from '../src/features/project/PulseBar'

const base = { projectId: 'p1', stageLabel: '推进中', pendingCompile: 0, onOpenWiki: vi.fn() }
const store: Record<string, string> = {}
beforeEach(() => {
  byProject.clear()
  Object.keys(store).forEach((k) => delete store[k])
  ;(global as any).localStorage = {
    getItem: (k: string) => store[k] ?? null,
    setItem: (k: string, v: string) => { store[k] = v },
  }
  vi.clearAllMocks()
})
afterEach(() => cleanup())

describe('PulseBar v1', () => {
  it('shows all-clear row when no signals', () => {
    const { container } = render(<PulseBar {...base} reviews={{ contradiction: 0, gap: 0 }} />)
    expect(container.textContent).toContain('pulse.allClear')
    expect(container.querySelectorAll('.chip').length).toBe(0)
  })
  it('renders contradiction/gap/pendingCompile chips with counts, hides zero chips', () => {
    const { container } = render(
      <PulseBar {...base} reviews={{ contradiction: 3, gap: 5 }} pendingCompile={5} />
    )
    const text = container.textContent!
    expect(text).toContain('pulse.chipContradiction')
    expect(text).toContain('3')
    expect(text).toContain('pulse.chipGap')
    expect(text).toContain('pulse.chipPendingCompile')
    expect(text).not.toContain('pulse.chipReports') // 无生成中报告 → 隐藏
  })
  it('shows reports chip when heartbeat has active reports', () => {
    byProject.set('p1', { files: 0, compiles: 0, reports: 1 })
    const { container } = render(
      <PulseBar {...base} reviews={{ contradiction: 0, gap: 0 }} />
    )
    expect(container.textContent).toContain('pulse.chipReports')
  })
  it('renders status sentence with stage label and top signal', () => {
    const { container } = render(
      <PulseBar {...base} reviews={{ contradiction: 3, gap: 0 }} />
    )
    expect(container.textContent).toContain('推进中')
    expect(container.textContent).toContain('pulse.sentenceContradiction')
  })
  it('persists collapsed state per project', () => {
    const { container } = render(
      <PulseBar {...base} reviews={{ contradiction: 1, gap: 0 }} />
    )
    container.querySelector<HTMLButtonElement>('.pulseCollapse')!.click()
    expect(store['pulsebar:collapsed:p1']).toBe('1')
    expect(container.textContent).toContain('pulse.collapsed') // 折叠态单行
  })
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run tests/PulseBar.test.tsx`
Expected: FAIL——模块不存在

- [ ] **Step 3: 实现** `src/features/project/PulseBar.tsx`(完整组件):

```tsx
/** src/features/project/PulseBar.tsx — 项目脉搏带 v1(3.0 §2/§6.1.1)
 *  状态感知层:规则拼接状态句 + chips 行;数据源全部已有(wiki:listReviews 计数
 *  由调用方传入,运行态走 0.4 心跳);折叠偏好 per 项目落 localStorage。
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Activity, AlertTriangle, Loader2 } from 'lucide-react'
import { useActivity } from '../activity/ActivityContext'
import s from './PulseBar.module.css'

export interface PulseReviewCounts { contradiction: number; gap: number }
export interface PulseBarProps {
  projectId: string
  stageLabel: string
  reviews: PulseReviewCounts
  pendingCompile: number
  onOpenWiki: (view?: 'lint') => void
}

export function PulseBar({ projectId, stageLabel, reviews, pendingCompile, onOpenWiki }: PulseBarProps) {
  const { t } = useTranslation()
  const { byProject } = useActivity()
  const compileItems = useActivity('wiki_compile').items
  const act = byProject.get(projectId)
  const compiling = (act?.compiles ?? 0) > 0
  const reportsActive = (act?.reports ?? 0) > 0

  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(`pulsebar:collapsed:${projectId}`) === '1'
  )
  const toggle = () => {
    const next = !collapsed
    setCollapsed(next)
    localStorage.setItem(`pulsebar:collapsed:${projectId}`, next ? '1' : '0')
  }

  const chips: Array<{ key: string; label: string; count?: number; cls: string; view?: 'lint' }> = []
  if (reviews.contradiction > 0) chips.push({ key: 'contradiction', label: t('pulse.chipContradiction'), count: reviews.contradiction, cls: s.chipErr, view: 'lint' })
  if (reviews.gap > 0) chips.push({ key: 'gap', label: t('pulse.chipGap'), count: reviews.gap, cls: s.chipErr, view: 'lint' })
  if (pendingCompile > 0) chips.push({ key: 'pending', label: t('pulse.chipPendingCompile'), count: pendingCompile, cls: s.chipWarn })
  if (reportsActive) chips.push({ key: 'reports', label: t('pulse.chipReports'), cls: s.chipAcc })

  // 状态句:优先级 矛盾 > 缺口 > 报告生成中 > 一切正常(3.0 §2 规则拼接 v1)
  const sentence = reviews.contradiction > 0
    ? t('pulse.sentenceContradiction', { count: reviews.contradiction })
    : reviews.gap > 0
      ? t('pulse.sentenceGap', { count: reviews.gap })
      : reportsActive
        ? t('pulse.sentenceReports')
        : t('pulse.allClear')

  if (collapsed) {
    return (
      <div className={`${s.pulse} ${s.folded}`}>
        <button className={s.foldedRow} onClick={toggle}>
          <Activity size={14} />
          <span className={s.sent}>{t('pulse.title')}</span>
          {reviews.contradiction > 0 && (
            <span className={`${s.chip} ${s.chipErr}`}>
              <AlertTriangle size={12} />{reviews.contradiction}
            </span>
          )}
          <span className={s.expandHint}>{t('pulse.expand')} ▾</span>
        </button>
      </div>
    )
  }
  return (
    <div className={s.pulse}>
      <div className={s.head}>
        {compiling
          ? <Loader2 size={14} className={s.spin} />
          : <span className={`${s.dot} ${chips.length ? s.dotWarn : s.dotOk}`} />}
        <span className={s.sent}><b>{stageLabel}</b> · {sentence}</span>
        <button className={s.collapseBtn} onClick={toggle}>{t('pulse.collapse')} ▴</button>
      </div>
      {chips.length > 0 && (
        <div className={s.chips}>
          {chips.map((c) => (
            <button key={c.key} className={`${s.chip} ${c.cls}`}
              onClick={() => onOpenWiki(c.view)}>
              {c.key === 'reports' && compilingReportIcon()}
              {c.label}{c.count != null ? ` ${c.count}` : ''}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
```

(注:`compilingReportIcon()` 以 `<Loader2 size={12} className={s.spin} />` 内联替换;测试按文案匹配不受影响。)

- [ ] **Step 4: 样式与 i18n**——新建 `src/features/project/PulseBar.module.css`(`.pulse/.head/.sent/.chips/.chip/.chipErr/.chipWarn/.chipAcc/.dot/.dotOk/.dotWarn/.folded/.foldedRow/.collapseBtn/.expandHint/.spin`;配色对齐原型:`.chipErr{border-color:#fecaca;background:#fef2f2;color:var(--error)}` 等,圆角 99px、padding 3px 12px)。`zh-CN.json` 顶层新增:

```json
"pulse": {
  "title": "脉搏",
  "allClear": "暂无待处理事项",
  "chipContradiction": "矛盾",
  "chipGap": "缺口",
  "chipPendingCompile": "待编译",
  "chipReports": "报告生成中",
  "sentenceContradiction": "{{count}} 个矛盾待裁决",
  "sentenceGap": "{{count}} 项知识缺口待补",
  "sentenceReports": "报告生成中",
  "collapse": "收起",
  "expand": "展开",
  "collapsed": "已折叠"
}
```

en-US 对应英文。

- [ ] **Step 5: 跑测试确认通过**

Run: `npx vitest run tests/PulseBar.test.tsx`
Expected: PASS

- [ ] **Step 6: 提示用户提交**(建议信息:`feat(project): PulseBar v1 component (3.0 phase1 task2)`)

---

### Task 3: PulseBar 接入 ProjectDetailPage(拉取 + chips 直达)

**Files:**
- Modify: `src/features/project/ProjectDetailPage.tsx`(挂载 PulseBar、reviews/listPages 拉取、知识 Tab 图标换 `Library`)
- Modify: `src/features/project/tabs/WikiTab.tsx`(提取 pending 计数纯函数并导出)
- Test: `tests/ProjectDetailPage.test.tsx`(PulseBar 接线断言)

**Interfaces:**
- Consumes: Task 2 `PulseBar` 全部签名;Task 1 的 TABS 顺序
- Produces: `WikiTab.tsx` 新增导出 `export function countPendingWikiFiles(pages: Array<{ status?: string; is_source?: boolean }>): number`(以 `loadPages` 现有 pendingCount 计算逻辑为准提取,执行者核对 L156-168 真实字段);ProjectDetailPage 新增 state `reviewsCounts: {contradiction, gap}` 与 `pendingCompile: number`

- [ ] **Step 1: 写失败测试**——`tests/ProjectDetailPage.test.tsx` 追加(mock 需补 PulseBar 桩):

```tsx
vi.mock('../src/features/project/PulseBar', () => ({
  PulseBar: (props: { reviews: { contradiction: number }; pendingCompile: number }) => (
    <div data-testid="pulsebar" data-contradiction={props.reviews?.contradiction}
      data-pending={props.pendingCompile} />
  ),
}))
// describe 内追加:
it('mounts PulseBar and refetches reviews on compile terminal edge', async () => {
  invokeMock.mockImplementation(async (ch: string) => {
    if (ch === 'wiki:listReviews') return { reviews: [{ type: 'contradiction' }, { type: 'contradiction' }, { type: 'data-gap' }] }
    if (ch === 'wiki:listPages') return { pages: [] }
    return { task: null, ingest_summaries: [] }
  })
  const { rerender, container } = render(<ProjectDetailPage project={project} />)
  await flush()
  const bar = container.querySelector('[data-testid="pulsebar"]')!
  expect(bar.getAttribute('data-contradiction')).toBe('2')
  compileItems.push({ project_id: 'p1' })
  rerender(<ProjectDetailPage project={project} />)
  await flush()
  compileItems.length = 0
  rerender(<ProjectDetailPage project={project} />)
  await flush()
  // 边沿补拉:compileStatus 与 listReviews 各再拉一次(无轮询)
  const reviewCalls = invokeMock.mock.calls.filter((c) => c[0] === 'wiki:listReviews').length
  expect(reviewCalls).toBe(2)
})
```

(注:listReviews 返回结构执行者以 `WikiTab.tsx L170-180` 实际读取字段为准——若真实字段为 `result.items` 则同步修正 mock 与实现。)

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run tests/ProjectDetailPage.test.tsx`
Expected: FAIL——PulseBar 未接入,`[data-testid="pulsebar"]` 不存在

- [ ] **Step 3: 实现**:

1. `WikiTab.tsx`:把 `loadPages` 内待编译计数逻辑提取为导出纯函数 `countPendingWikiFiles`(保持原行为,PulseBar 复用;字段名以现状为准)
2. `ProjectDetailPage.tsx`:
   - 新增 state:`const [reviewsCounts, setReviewsCounts] = useState({ contradiction: 0, gap: 0 })`、`const [pendingCompile, setPendingCompile] = useState(0)`
   - 新增拉取回调(挂载 + 编译终态边沿,不新增轮询):

```tsx
const loadPulseData = useCallback(async () => {
  try {
    const [rv, pg]: any[] = await Promise.all([
      window.electronAPI.invoke('wiki:listReviews', { projectId: project.id, resolved: false }),
      window.electronAPI.invoke('wiki:listPages', { projectId: project.id }),
    ])
    const items: Array<{ type: string }> = rv?.reviews ?? rv?.items ?? []
    setReviewsCounts({
      contradiction: items.filter((r) => r.type === 'contradiction').length,
      gap: items.filter((r) => r.type === 'data-gap' || r.type === 'missing-page' || r.type === 'broken-link').length,
    })
    setPendingCompile(countPendingWikiFiles(pg?.pages ?? []))
  } catch (err) {
    console.error('Failed to load pulse data:', err) // app.log 由 Electron 侧统一;此处仅控制台,不阻塞
  }
}, [project.id])
useEffect(() => { void loadPulseData() }, [loadPulseData])
```

   - 在既有 `useEdgeRefetch(wikiCompileActive, ...)` 的回调里追加 `void loadPulseData()`(同一时机,零新轮询)
   - 头部与 Tab 栏之间挂载:

```tsx
const [wikiFocus, setWikiFocus] = useState<{ view: 'lint'; ts: number } | null>(null)
<PulseBar
  projectId={project.id}
  stageLabel={t(STAGE_MAP[projectType].find((x) => x.key === currentStage)?.labelKey ?? '')}
  reviews={reviewsCounts}
  pendingCompile={pendingCompile}
  onOpenWiki={(view) => {
    setActiveTab('wiki')
    if (view === 'lint') setWikiFocus({ view, ts: Date.now() })
  }}
/>
```

   - 知识 Tab 图标 `BookOpen` → `Library`(从 lucide-react 导入;与顶层导航区分,3.0 §6.3 图标映射)

- [ ] **Step 4: 跑测试确认通过**

Run: `npx vitest run tests/ProjectDetailPage.test.tsx tests/PulseBar.test.tsx`
Expected: PASS

- [ ] **Step 5: 提示用户提交**(建议信息:`feat(project): wire PulseBar into detail page (3.0 phase1 task3)`)

---

### Task 4: WikiTab focusSignal——直达治理视图

**Files:**
- Modify: `src/features/project/tabs/WikiTab.tsx:30-60`(props 接口)、`L105` 附近(useEffect)
- Test: `tests/WikiTab.test.tsx`(追加用例;现有 mock 结构沿用)

**Interfaces:**
- Consumes: Task 3 的 `wikiFocus` state(经 props 传入)
- Produces: `WikiTabProps` 新增 `focusSignal?: { view: 'lint'; ts: number } | null`;信号变化 → `setViewMode('lint')`(已有 `handleGoLint` 同款效果,直接 `setViewMode` 即可,保持滚动行为一致)

- [ ] **Step 1: 写失败测试**——`tests/WikiTab.test.tsx` 追加(mock 结构照文件现有模式):

```tsx
it('switches to lint view when focusSignal arrives', async () => {
  const { rerender, container } = render(
    <WikiTab projectId="p1" projectType="recruitment" focusSignal={null} />
  )
  // setViewMode 同步生效,无需异步排空;选择器以该文件现有 lint 用例为准
  rerender(<WikiTab projectId="p1" projectType="recruitment"
    focusSignal={{ view: 'lint', ts: Date.now() }} />)
  expect(container.textContent).toContain('project.wiki.lint')
})
```

(mock 结构照该文件现有模式;若现有用例含窗口 electronAPI mock 则沿用。)

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run tests/WikiTab.test.tsx`
Expected: FAIL——props 不存在(或有 TS 报错)

- [ ] **Step 3: 实现**——`WikiTab.tsx` props 接口加 `focusSignal`,并加:

```tsx
useEffect(() => {
  if (focusSignal?.view) setViewMode(focusSignal.view)
}, [focusSignal])
```

`ProjectDetailPage.tsx` 的 `<WikiTab ...>` 传 `focusSignal={wikiFocus}`。

- [ ] **Step 4: 跑测试确认通过**

Run: `npx vitest run tests/WikiTab.test.tsx`
Expected: PASS

- [ ] **Step 5: 提示用户提交**(建议信息:`feat(wiki): focusSignal for lint view jump (3.0 phase1 task4)`)

---

### Task 5: WikiHomePage 健康卡

**Files:**
- Modify: `src/features/project/tabs/WikiHomePage.tsx`(新 props + 健康卡区块,置于现有 purpose 区块之前)
- Modify: `src/features/project/tabs/WikiTab.tsx`(reviews 计数 + onOpenLint 传入 WikiHomePage)
- Test: `tests/WikiHomePage.test.tsx`(新建)

**Interfaces:**
- Consumes: `WikiTab` 已有 `reviews` state(字段 `type`);Task 4 focusSignal
- Produces: `WikiHomePageProps` 新增:

```tsx
reviewCounts?: { contradiction: number; gap: number; deadLink: number }
onOpenLint?: () => void
```

健康卡渲染规则(3.0 §6.1.2):三计数全 0 → 整卡不渲染;`gap` = data-gap + missing-page(死链单独一型);「去处理」→ `onOpenLint()`。

- [ ] **Step 1: 写失败测试** `tests/WikiHomePage.test.tsx`(mock i18n/CSS 同 ProjectDetailPage.test 模式):

```tsx
it('hides health card when all counts are zero', () => {
  const { container } = render(<WikiHomePage {...minimal} />)
  expect(container.textContent).not.toContain('project.wiki.homePage.healthTitle')
})
it('renders health card with three typed counts and opens lint', () => {
  const onOpenLint = vi.fn()
  const { container, getByText } = render(
    <WikiHomePage {...minimal}
      reviewCounts={{ contradiction: 3, gap: 5, deadLink: 2 }} onOpenLint={onOpenLint} />
  )
  const text = container.textContent!
  expect(text).toContain('3'); expect(text).toContain('5'); expect(text).toContain('2')
  getByText('project.wiki.homePage.healthGo').click()
  expect(onOpenLint).toHaveBeenCalledOnce()
})
```

(`minimal` 为满足现有必选 props 的最小对象,参照 `WikiHomePageProps` 现状构造。)

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run tests/WikiHomePage.test.tsx`
Expected: FAIL

- [ ] **Step 3: 实现**——WikiHomePage 头部插入健康卡 JSX(AlertTriangle 图标 + 三计数 chip + 「去处理」按钮,样式对齐原型 `kpi-row/chip err`);WikiTab 的 `<WikiHomePage>` 调用处传:

```tsx
reviewCounts={{
  contradiction: reviews.filter((r) => r.type === 'contradiction').length,
  gap: reviews.filter((r) => r.type === 'data-gap' || r.type === 'missing-page').length,
  deadLink: reviews.filter((r) => r.type === 'broken-link').length,
}}
onOpenLint={() => { setViewMode('lint') }}
```

i18n 追加 `project.wiki.homePage.healthTitle`(知识健康)/`healthGo`(去处理)/`healthContradiction`(矛盾)/`healthGap`(缺口)/`healthDeadLink`(死链),中英双语。

- [ ] **Step 4: 跑测试确认通过**

Run: `npx vitest run tests/WikiHomePage.test.tsx tests/WikiTab.test.tsx`
Expected: PASS

- [ ] **Step 5: 提示用户提交**(建议信息:`feat(wiki): health card on wiki home (3.0 phase1 task5)`)

---

### Task 6: 结构页完成度骨架

**Files:**
- Modify: `src/features/project/tabs/WikiTab.tsx`(挂载时拉 `wiki:getSchemas` 传 props)
- Modify: `src/features/project/tabs/WikiHomePage.tsx`(完成度区块:进度条 + generated/missing chips)
- Test: `tests/WikiHomePage.test.tsx`(追加)

**Interfaces:**
- Consumes: `wiki:getSchemas {projectId}` → `{schemas: Array<{page_key,title,...}>, project_type}`(WikiSchemaManager.tsx:56 已证);WikiHomePage 现有 `pages` prop(页面含 `page_key`,执行者核对 `WikiPage` interface L13)
- Produces: `WikiHomePageProps` 新增 `schemas?: Array<{ page_key: string; title: string }>`;完成度 = `schemas` 中 `page_key` 已存在于 `pages` 的比例;missing 项渲染虚线骨架 chip

- [ ] **Step 1: 写失败测试**——`tests/WikiHomePage.test.tsx` 追加:

```tsx
it('renders schema completion bar with missing-page skeleton chips', () => {
  const { container } = render(
    <WikiHomePage {...minimal}
      schemas={[
        { page_key: 'enterprise_profile', title: '企业画像' },
        { page_key: 'policy_ledger', title: '政策台账' },
        { page_key: 'risk_ledger', title: '风险台账' },
      ]}
      pages={[{ page_key: 'enterprise_profile' }, { page_key: 'policy_ledger' }] as any}
    />
  )
  const text = container.textContent!
  expect(text).toContain('2/3')                                  // 进度
  expect(text).toContain('企业画像')                              // 已生成 chip
  expect(text).toContain('风险台账')                              // 骨架占位 chip
})
it('hides completion block when schemas empty', () => {
  const { container } = render(<WikiHomePage {...minimal} schemas={[]} />)
  expect(container.textContent).not.toContain('project.wiki.homePage.completionTitle')
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run tests/WikiHomePage.test.tsx`
Expected: FAIL

- [ ] **Step 3: 实现**:

1. `WikiTab.tsx`:新增 `loadSchemas`(invoke `wiki:getSchemas`,挂载 + 编译终态边沿各一次,对齐 `loadPages` 现有 useEffect 结构)→ state `schemas` → 传 `schemas={schemas}` 给 WikiHomePage
2. `WikiHomePage.tsx`:健康卡之下渲染完成度区块——`generated = schemas.filter(s => pageKeys.has(s.page_key)).length`;进度条(width = generated/total);已生成 chip(Check 图标 + title)/missing chip(虚线框 + title,点击暂无动作,二期接缺口补料);total=0 整块隐藏
3. i18n:`project.wiki.homePage.completionTitle`(结构页完成度)/`completionGenerated`(已生成),中英双语

- [ ] **Step 4: 跑测试确认通过**

Run: `npx vitest run tests/WikiHomePage.test.tsx tests/WikiTab.test.tsx`
Expected: PASS

- [ ] **Step 5: 提示用户提交**(建议信息:`feat(wiki): schema completion skeleton on home (3.0 phase1 task6)`)

---

### Task 7: 收尾——全量验证 + 文档双向对齐

**Files:**
- Modify: `src/styles/global.css`(新增 `--warning: #f59e0b`)
- Modify: `docs/0.3ux-design.md`(§4.3 状态色表加 `--warning` 行;§3 图标表加 `Library`/`Activity`/`Plus`)
- Modify: `docs/2.6wiki-design.md`(§10 补记:WikiHomePage 健康卡/完成度、focusSignal)
- Modify: `docs/2.1project-design.md`(§2/§6:Tab 知识置首、脉搏带)
- Modify: `docs/3.0project-lifecycle-design.md`(§9 对齐表:0.3/2.1/2.6 时机列改「已完成」)

- [ ] **Step 1: `--warning` token**——`global.css` `:root` 加 `--warning:#f59e0b;`(PulseBar `.chipWarn` 引用;对照原型)

- [ ] **Step 2: 全量验证**(按 coding-habits §2.5 层级):

```bash
npx tsc --noEmit          # 1. 语法/类型
npx vitest run            # 2. 前端全量测试
npm run build             # 3. 构建
npm run dev               # 4. 运行验证(手动冒烟清单见 Step 3)
```

- [ ] **Step 3: 运行冒烟清单**(对照原型逐项):打开项目默认落「知识」;脉搏带展开/收起(刷新后记住折叠态);矛盾 chip 点击 → 知识 Tab 治理视图;知识首页健康卡三计数 + 「去处理」;健康卡全 0 时隐藏;结构页完成度进度条与骨架 chip;编译一个文件后心跳 chips 变化、无新增网络轮询(devtools network 面板确认 listReviews/listPages 仅挂载与终态边沿各一次)

- [ ] **Step 4: 文档回改**(§9 对齐动作落地;0.3 §6 差异表「随设置页样式下次改动时顺手修正注释」一类不动)

- [ ] **Step 5: 提示用户提交**(建议信息:`docs: phase1 alignment for 0.3/2.1/2.6/3.0 (3.0 phase1 task7)`)

---

## 后续期次(不在本计划,启动时按本模板各自出计划)

- **二期 时期任务模型**:playbook_templates/phase_tasks DDL + playbook_seed.py(46 条,§3.3 逐条)+ seed/migrate_backfill 启动挂钩 + routers/tasks.py + 事务 Tab 三视图 + 完成闭环事件 + 脉搏带应办 chip(§8 验收:新建项目零配置即有任务)
- **三期 首页今日工作台**:tasks:listDue 聚合 + 卡片墙健康色点 + 近期日历 + 侧栏色点
- **四期 智能层**:AI 状态句/台账时间轴卡/事件驱动建议/里程碑台账提取
