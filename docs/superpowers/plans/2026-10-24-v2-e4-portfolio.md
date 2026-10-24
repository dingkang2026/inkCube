# V2-E4 组合盘子实施计划（0.6 §9 / §3.1⑤ / §3.7）

> 依据：[0.6v2-ux-redesign.md](../../docs/0.6v2-ux-redesign.md) §9 E4 行 + §3.1⑤ + §3.7 + §6.3 规则 9。
> 范围：首页卡片升级（阶段进度 + AI 脉搏句 + 矛盾/缺口计数行）+ 卡片墙 ↔ 泳道切换（StageLaneView）。
> 验收：卡片与泳道零新增请求（全部现有数据派生）；泳道阶段列序 = STAGE_COLUMNS；vitest/pytest/tsc/build/orphan audit 全绿 + CodeReview 收口。

## 现状盘点（2026-10-24 探查）

| 项 | 现状 |
|---|---|
| 卡片墙 | `src/features/home/ProjectCardWall.tsx`：健康点 + 名 + 新产出角标 + 阶段 pill + 下一事务行 + 台账摘要行；props 全由 HomePage 组装下发 |
| 健康数据 | `affairs:listDue` → `reviews_counts: {pid: int}`（db `count_pending_reviews_by_project`，仅 resolved=0 总数）→ `summarizeDueByProject` → `ProjectHealthSummary{overdue,weekDue,reviewsPending}`；消费方仅 summarizeDueByProject 一处解包（App L230 → 侧栏色点 + 卡片墙） |
| 矛盾/缺口 | E3 封面口径：contradiction 一型；gap = data-gap + missing-page 两型合并（WikiTab L860）；批量链路无分型——本 E4 扩展 |
| 脉搏句 | `Project.pulse_ai_status`（knowledge/types.ts L14）已在 projects 流，纯前端可读 |
| 阶段 | `STAGE_MAP[type]`（investment/recruitment 各 3 阶段、general 空）+ `STAGE_COLUMNS`（before/during/after/none 看板列，ReportsTab 已有看板先例） |
| 切换记忆 | localStorage `inkcube.*` 惯例（onboarding.firstProject / report.export.orientation） |

## 设计裁决

1. **分型口径单一事实源**：`count_pending_reviews_by_project` 一条 `GROUP BY project_id, type` 查询聚合出 `{pid: {total, contradiction, gap}}`；gap = data-gap + missing-page（对齐 E3 封面）；total 维持现语义（色点/橙档零变化）。
2. **reviews_counts 结构升级不做兼容层**：int → typed bucket，消费方（summarizeDueByProject + 单测）同变更内全链同步改；affairs.py 透传零改动；零 DDL 零迁移（API 响应结构，非持久化产物）。
3. **阶段进度 = pill + 序数**：卡片阶段 pill 内附「n/m」（`STAGE_MAP[type]` 序位/总长）；general（无阶段集）与 stage=none 维持现状不显示序数——卡片空间紧凑，不引入进度条组件。
4. **脉搏句空不渲染**：卡片墙/泳道卡均 `pulse_ai_status` 非空才渲染一行；规则句回落是项目页 PulseBar 职责，首页不重复。
5. **泳道不变量（§6.3-9）**：列 = STAGE_COLUMNS 四列；空列折叠窄列（阶段语义可见）；全空 → 空态引导；混型同列、卡上类型 chip；零新请求零新状态字段。
6. **切换记忆**：`localStorage['inkcube.home.portfolioView']` = `'wall' | 'lane'`，默认 wall；切换钮放项目区标题行右侧（titleRow 先例）。

## 任务分解

### E4-T1 矛盾/缺口分型计数（后端 + 派生链）
- `python/db/database.py`：`count_pending_reviews_by_project` 改查 `GROUP BY project_id, type`，聚合返回 `{pid: {"total": int, "contradiction": int, "gap": int}}`（docstring 登记 E4 口径）。
- `src/features/project/affairs/types.ts`：`ProjectHealthSummary` 加 `contradiction/gap` 字段；`summarizeDueByProject` 读新结构（total→reviewsPending）。
- pytest：`python/tests/` listDue reviews_counts 分型断言更新/新增；前端 summarizeDueByProject 单测同步。
- 验收：pytest + vitest 绿；色点档位行为零变化（total 语义不变）。

### E4-T2 卡片升级（ProjectCardWall）
- 阶段 pill + n/m 序数；AI 脉搏句行（非空渲染，单行省略）；矛盾/缺口行（`矛盾 N · 缺口 N`，>0 才渲染对应项，全 0 整行不渲染）。
- module.css 三处样式；i18n 键（`home.wall.pulseLabel` 等 zh/en）。
- `tests/ProjectCardWall.test.tsx` 补：序数渲染（investment 2/3）、general 无序数、脉搏句有/无、矛盾缺口行有/无。

### E4-T3 StageLaneView 泳道 + 视图切换
- 新建 `src/features/home/StageLaneView.tsx` + `.module.css`：props `{projects, healthData, nextDueByProject, onSelect}`；四列布局、列头 = 阶段名 + 计数、空列折叠窄列、全空空态；卡 = 类型 chip + 名 + 脉搏句 + 下一事务行。
- `HomePage.tsx`：项目区 titleRow 加「卡片墙 ⇄ 泳道」分段切换（LayoutGrid/Columns 图标），localStorage 记忆；泳道模式下加载更多钮保留（projects 快照同源）。
- i18n zh/en：切换钮 aria/tooltip、泳道空态等键。
- 测试：`tests/StageLaneView.test.tsx` 新建（分组/列头计数/空列折叠/点击回调/脉搏句/chip）+ HomePage 切换记忆用例。

### E4-T4 i18n 全量核查 + 文档对齐
- zh/en 新键终扫（无孤儿键、无硬编码残留）；`node scripts/orphan_channel_audit.js` 复核（本 E4 零新通道，应维持七组 0）。
- 0.6 §9 E4 行标已落地；§3.1⑤/§3.7 现状标注核对；`reviews_counts` 响应结构变更在 0.4（listDue 契约归属文档）对齐登记。

### E4-T5 终验 + CodeReview + 收口
- pytest 全量 + `npx tsc --noEmit` + vitest 全量 + `npm run build` + orphan audit。
- 派发 CodeReview subagent（纵向五维度）→ 修复收口（补回归测试）→ 文档对齐检查 → E4 收口总结。

## 执行顺序
T1 → T2 → T3 → T4 → T5，逐任务跑绿后推进；T5 收口前不得进入 V2.1 候选议题。

## CodeReview 收口记录（2026-10-24，Blocker 1 / Major 1 / Minor 4 全清）

| # | 级别 | 问题 | 修复 |
|---|---|---|---|
| 1 | Blocker | `count_pending_reviews_by_project` 升级分型桶后，`ledger_service.update_pulse_status` 仍按 `{pid: int}` 读取——dict repr 拼进 LLM prompt 污染脉搏句（现状盘点 L12「消费方仅 summarizeDueByProject 一处」漏查 Python 内部消费方） | 解包 `total`（`int(bucket.get("total", 0))`）+ `test_pulse_ai_status.py` 回归用例（seed resolved=0 review，断言传给 `_pulse_sentence` 的计数为 int 1） |
| 2 | Major | 泳道全空态缺 §3.7 设计规格的「新建项目」引导（实现只复用了卡片墙纯文案空态） | StageLaneView 加 `onCreateProject?` prop（HomePage 透传同款回调）+ `.emptyCta` 引导钮 + 用例（无回调仅文案 / 有回调点击触发） |
| 3 | Minor | `stageInfo` 对未知 project type 裸查表可 TypeError（后端 type 无枚举校验） | `STAGE_MAP[p.type ?? 'general'] ?? []` 守卫（与 StageLaneView chip 守卫同口径） |
| 4 | Minor | `localStorage.getItem` 渲染路径无防护，与同函数 setItem try/catch 不对称 | useState 初始化器 try/catch 回落 'wall' |
| 5 | Minor | 计划 T3 契约含 `healthData` 但实现未接（0.6 §6.1 数据列与 §3.7 卡行规格两处文档有出入） | 收敛为裁剪：泳道卡按 §3.7 卡行规格（名+脉搏句+下一事务），健康信号留在卡片墙；0.6 §6.1 组件行已登记 |
| 6 | Minor | 计划 T4 要求 reviews_counts 变更在 0.4 登记，但 0.4 实无 listDue 契约描述（登记无 anchor） | 修正归属：落 3.0 §7.2 色点数据流行（listDue 内嵌 summary 描述处）；0.4 职责为活动/轮询，不承载 IPC 响应契约 |

终验（修复后）：pytest 2144 passed + 2 skipped；vitest 949/949（117 文件）；tsc 0 错；build 19.41s；orphan audit 七组 0（T4 实测，本 E4 零新通道）。
