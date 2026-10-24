# V2-E1a 结构骨架实施计划

> Spec: docs/0.6v2-ux-redesign.md §2/§3.2/§3.3/§3.6/§3.8/§5/§7/§9（E1a 行）；配套 docs/2.11scheduled-task-design.md §5.3/§8。
> 执行方式：inline 顺序执行；**禁止 git 操作（用户手动提交）**；每任务完成即跑对应验证，全部完成后跑终验五件套。
> 已查证关键事实：scheduler._execute 组装点 L384（prompt_full = incr_note + s["prompt"]）；db.last_done_run_report L2151 唯一调用方 = scheduler L372；refinePrompt 链 = FileTree.tsx L110/L198 + ScheduleFormModal initial.taskId + ipc-handlers.js schedules:refinePrompt + routers/schedules.py L34-41/L67/L236-283 + i18n L739/L931；SettingsModel schemas.py L4-75，save_settings 自由 kv（routers 层 SettingsModel 过滤）；inbox 端点 routers L142-162（unread/limit/offset），db.list_run_inbox 未读前置排序；useSchedules totals={n,total(未读),abnormal} 自含终态边沿；NavTabs TABS L13-17；App scheduleView L142-148、Ctrl 键表 L649、handleNavSchedules L693、ScheduleSideSummary L831；HomePage IntelDigest 侧列 L285、QuickActions 两卡；ProjectDetailPage TABS 五键（wiki/affairs/materials/reports/forms）；TaskChatView 预填 = usePlaceholderFill.start（模板链，转任务跨页预填需 App 级信号）；task_templates.category TEXT 无 CHECK 约束（可加 reg）。

## Global Constraints

- 双语 i18n（zh-CN + en-US 同步）；零新增轮询（徽章=useSchedules 现有模式）；分页契约 limit/offset/has_more/total。
- 删除链反向全链清理（UI→invoke→IPC→python→db→测试→文档）；孤儿审计 orphan_channel_audit 四组=0。
- 用户可见文案一律用户词（§3.9）；E1a 只做结构，视觉六原则 E1b（新组件样式克制、对齐现状 token）。
- 验证命令：`npx tsc --noEmit`、`npx vitest run`、`C:/Users/Kelvin/.conda/envs/inkcube/python.exe -m pytest python/tests -v -k <模块>`、终验加 `npm run build` + `node scripts/orphan_channel_audit.js`。

## Tasks

### T1 时间基准注入（scheduler + db + 测试）
- db/database.py：新增 `last_done_run(schedule_id) -> dict|None`（SELECT id, report_path, finished_at ... status='done' ORDER BY finished_at DESC LIMIT 1）；新增 `count_schedule_runs(schedule_id) -> int`（COUNT(*)）；**删除** last_done_run_report（改其唯一调用方）。
- scheduler.py `_execute`：上期查询改 last_done_run()；prompt_full 前拼：
  `【本期执行：{now:%Y-%m-%d %H:%M} · 第 {N} 期】仅报告本期新动态`；有上期时加 ` · 上期：{prev finished_at:%Y-%m-%d %H:%M}】仅报告上期之后的新动态`（首期无上期段）。时间统一 `_now_dt()`（测试 monkeypatch 点）。
- tests：test_scheduler.py 新增两条（首期无「上期：」、次期含上期时刻且 prompt 不存库断言：get_task_messages user 首条含「【本期执行」）；既有 last_done_run_report 引用改名。
- 验证：pytest -k scheduler。

### T2 refinePrompt 删除链
- FileTree.tsx：删「设为定时订阅」按钮（L110 注释块 + L198）与 onSetSchedule prop 链（查调用方 TaskPage 一并清）。
- ScheduleFormModal.tsx：删 initial prop、refining state、refineStarted ref、refine effect（L119-140）、setStep 三元（L102→'source'）、头部注释渠道③句。
- ipc-handlers.js：删 schedules:refinePrompt handler 整块。
- routers/schedules.py：删 _REFINE_SYSTEM_PROMPT/_REFINE_MAX_MESSAGES/_REFINE_MSG_CLIP/RefinePromptBody/refine_prompt 端点；_extract_json 若仅 refine 消费则同删（Grep 确认）。
- i18n：删 task.scheduleFromTask（zh+en）、schedule.form.refineFailed（zh+en）。
- 测试：test_schedules_api.py 删 refine 用例。
- 验证：tsc + pytest -k schedules + orphan audit（B 组=0）。

### T3 SettingsModel persona 字段
- schemas.py SettingsModel 尾部：`persona: str = ""`（''=未设置；business_attraction/investment/both/other）、`persona_prompted: bool = False`。
- 前端 Settings 类型（useSettings.ts 内类型定义处）同步两字段；SettingsPage 不加 UI（设置页「通用」区改身份入口随 E1b，产品决策：E1a 先保存储与首启链路，设置页入口 E1b 补——已属 §3.8 驱动面范围外，登记文档）。
- 验证：pytest -k settings（test_settings_api 若有全量字段断言则补）。

### T4 PersonaPicker 组件 + App 双流程
- 新建 src/features/onboarding/PersonaPicker.tsx + .module.css：全屏 overlay（z 高于向导）、大标题 t('onboarding.persona.title')、四卡（key/icon/label/desc，icon：Compass/TrendingUp/Layers/Shape）、跳过链接；props: onSelect(persona), onSkip。
- App.tsx：settingsLoaded 后 effect 判定 `!settings.persona_prompted` → showPersona=true（新老统一，且不在 onboardingResumeStep 挂起期间）；选择 → handleSaveSettings({...settings, persona, persona_prompted:true}) → setShowPersona(false) → 若 isNewUser 条件成立则 setShowOnboarding(true)；跳过 → 同保存 persona:'' + prompted:true。新用户流程顺序：Persona → OnboardingWizard → 首页。
- i18n：onboarding.persona.*（title/skip/4×label/4×desc，zh+en）。
- 测试：PersonaPicker.test.tsx（渲染四卡、onSelect/onSkip 回调）。
- 验证：vitest run PersonaPicker + tsc。

### T5 inbox 端点扩展（sort/category）
- db.list_run_inbox 加参 `sort: str = 'unread'`（unread=read_at NULL 前置；latest=纯 finished_at DESC）、`category: str|None`（JOIN 后 `AND s.category = ?`）；routers schedule_inbox 加 `sort: str = Query('unread')`（校验 unread|latest）与 `category: str|None = Query(None)`；total 同口径加 category 过滤。
- tests：test_schedules_pagination.py 或 test_schedules_api.py 补 sort=latest 顺序断言 + category 过滤断言。
- 验证：pytest -k inbox or schedules。

### T6 NavTabs 4 Tab + 徽章 + Ctrl+4 + App intel 路由
- NavTabs.tsx：TABS 二位插 `{ key:'intel', labelKey:'nav.intel', Icon: Radar }`；徽章：useSchedules() 的 totals.total>0 显示（复用现有 taskBadge 样式，title=schedule.badge.unread）。
- App.tsx：NAV_KEYS 加 '4':'intel'；activeNav==='intel' 主区渲染 `<IntelPage tab={intelTab} onTabChange={setIntelTab} onOpenRead=... />`（T7 组件）；新增 `const [intelTab, setIntelTab] = useState<'feed'|'admin'>('feed')`。
- i18n：nav.intel（情报/Intel，zh+en）、schedule.badge.unread。
- 验证：tsc（IntelPage 先占位空壳，T7 实装——同任务内完成则免）。

### T7 IntelPage + IntelFeed（inbox/summary 接生）
- 新建 src/features/intel/IntelPage.tsx：页头（display 标题 t('intel.title') +「新建订阅」主钮→ tab='admin' + 发ScheduleFormModal open）+ 子 Tab（情报流/订阅管理）+ 内容分派（feed=IntelFeed；admin=SubscriptionAdminView 透传 onOpenRead/onOpenArchive/restore 由 App 层 props 下传）。
- 新建 src/features/intel/IntelFeed.tsx + .module.css：挂载/筛选变更拉 `schedules:inbox { sort, category, limit:20, offset }`；chips 全部/企业/产业/区域/通用（值 ''/corporate/industry/region/general）；排序切换 未读优先⇄最新；卡片=summary[0]标题+订阅名+类别chip+相对时间+状态点+未读点；done/failed→onOpenRead(run)；running→tab='admin'；has_more「加载更多」；零订阅空态（引导文案+新建订阅钮）；schedules 活动终态边沿重拉第一页（useEdgeRefetch）。
- 类别 i18n：intel.cat.corporate/industry/region/general。
- 测试：IntelFeed.test.tsx（mock invoke：chips 过滤调用参数、加载更多 offset 递增、空态渲染）。
- 验证：vitest run IntelFeed + tsc。

### T8 SubscriptionAdminView 迁入 + admin peer 态退役
- App.tsx：scheduleView 联合类型删 'admin' 分支；scheduleView 三态 read/replay/archive 的 from 扩展 `'admin'|'home'|'intel'`；handleScheduleBack(from) 增 'intel' → setActiveNav('intel')（intelTab 保持）；原 admin 分支渲染逻辑移除（SubscriptionAdminView 改由 IntelPage 渲染，App 透传 onOpenRead/onOpenArchive，read/replay/archive 的 scheduleId 语义保留）。
- handleNavSchedules（L693）/ScheduleSideSummary（L831）→ setActiveNav('intel') + setIntelTab('admin')；事件名 app:navigate-schedules 保留（0.6 §5 兼容）。peerPageOpen 去掉 scheduleView 但 intel 页本身是 nav 页（无 peer 问题）；handleNavChange 清 scheduleView 保留。
- 新增事件 app:navigate-intel（→ feed tab），IntelCardZone「查看全部」消费（T10）。
- 验证：tsc + 全局 Grep 无 scheduleView.type==='admin' 残留。

### T9 ReadView 转跟进任务 + 预填信号
- ReadView.tsx：props 加 onTrackFollow(run: ScheduleRunRow)；头部动作条（L197 replay 钮旁）加「转跟进任务」钮；失败 toast 留原页（App 侧 catch 后 toast，ReadView 只回调）。
- App.tsx：handleTrackFollow(run)：invoke('tasks:create',{title:run.summary?.[0]||run.schedule_name, workspace_path:''}) → setActiveTaskId(id) → setTaskPrefill({text, ts}) → handleNavChange('tasks')；catch → toast(apiErrorText)。预填文本 = `# {标题}\n{要点 join '\n'}\n\n请围绕以上情报制定跟进计划`（不自动发送）。
- TaskPage 透传 prefill 信号 → TaskChatView 新 prop incomingPrefill：effect 消费设置输入框值（不走 start()，避免占位符表单误激活），消费后回调 onPrefillConsumed 清 App 信号。
- i18n：schedule.read.trackFollow、trackFollowFailed、（任务侧 toast 复用现有）。
- 验证：tsc + vitest run（TaskChatView 若有既有测试补回归）。

### T10 首页重排
- IntelDigest.tsx → 改名 IntelCardZone.tsx（同目录）：结构改为「区块头（标题+查看全部→dispatch app:navigate-intel）+ 卡片网格（现有行数据源 digest 不变）」；`if(!totals.n)` 改渲染空态引导（文案 + 「新建订阅」钮 → dispatch app:navigate-schedules）；E1b 再做 1 大 N 小视觉。
- HomePage.tsx：IntelCardZone 从侧列移至主列顶部（HomeOverviewBar 之后、进行中区之前）；侧列剩动态流+QuickActions。
- QuickActions.tsx：props 加 onCreateSchedule；第三卡「新建订阅」（icon: Radar）→ App 传 handleNavSchedules 同链（intel+admin）。HomePage 新 prop onCreateSchedule 由 App 下传（= () => { setActiveNav('intel'); setIntelTab('admin') }）。
- i18n：home.quick.newSchedule(+Desc)、intel.zone.empty 等。
- 验证：tsc + vitest run（IntelDigest 既有测试改名/更新）。

### T11 项目四 Tab（产出合并）
- 新建 src/features/project/tabs/OutputTab.tsx：props（project, onOpenSettings, tabSignal 区锚点 forms）；内部二级分区「报告/填表」＝渲染原 reports 区块组件与 forms 区块组件（从 ProjectDetailPage 迁移渲染分支，非重写）；头部「模板管理」直达钮（→ app:navigate-template-management）。
- ProjectDetailPage.tsx：TABS 改四键（affairs/wiki/output/materials，labelKey：project.tab.affairs/wiki/output/materials）；删 forms/reports 分支渲染改 OutputTab；tabSignal 消费：`'forms'→output+填表锚`、`'reports'→output+报告锚`（一个版本周期兼容，注释登记 E3 清理）。
- HomePage handleOngoingClick 的 tab 值 'forms'/'reports' 保持发送（由 ProjectDetailPage 兼容映射，零改动）。
- i18n：project.tab.output（产出/Output）+ 分区标题（报告/填表）。
- 验证：tsc + vitest run（ProjectDetailPage 相关既有测试）。

### T12 模板分家 + 两新模板 + 分类自动映射 + persona 排序
- promptTemplate.ts：
  - TemplateCategory 加 `'reg'`；BACKEND_CATEGORY 加 `reg:'region'`（task_templates.category TEXT 无约束，已核实）。
  - `TASK_TEMPLATES` 收窄为任务 6 条（companyBasic/companyDeep/ddChecklist/businessModel/fileOrganize/dataSummary）。
  - 新增 `INTEL_TEMPLATES`（订阅白名单 6 条：sentiment/competitor→mon；industryEvents/industryLeads/industryPolicy(new, icon Landmark, ind)→ind；regionPolicy(new, icon MapPin, reg)→reg）。
  - 新增 `INTEL_CATEGORY_MAP: Record<TemplateCategory, string>` = { mon:'corporate', ind:'industry', reg:'region', info:'corporate', rep:'general', off:'general' }。
- i18n：task.tpl.industryPolicy.*（zh：产业政策扫描；prompt 参照 industryEvents 结构改 {产业} 主题=政策）、task.tpl.regionPolicy.*（区域招商政策扫描，{区域}）；en-US 对应。
- ScheduleFormModal.tsx：第①步模板列表 = INTEL_TEMPLATES + 自定义模板（useTaskTemplates 已合并？现状用 templates 全量——改为 builtin 换 INTEL_TEMPLATES）；点模板时 setCategory(INTEL_CATEGORY_MAP[cat])；自由输入面板加四分类选择（corporate/industry/region/general 直落 category）；订阅管理页签（SubscriptionAdminView 筛选页签若有）加区域项。
- TaskTemplatePanel.tsx：数据源换 TASK_TEMPLATES（情报 6 条自动消失）；TEMPLATE_CATEGORIES 分组渲染时空组不渲染（mon/ind/reg 无内置且自定义可归——空组隐藏逻辑=渲染前过滤无模板组）。
- persona 排序：TaskTemplatePanel 与 ScheduleFormModal 各自 useSettings() 读 persona；排序函数 sortTemplatesByPersona(list, persona)：business_attraction→(任务:companyBasic,companyDeep / 订阅:regionPolicy)置顶；investment→(任务:ddChecklist,businessModel / 订阅:industryEvents)置顶；both/other/'' 原序。纯前端重排。
- 验证：tsc + vitest run（useTaskTemplates/面板既有测试更新）。

### T13 i18n 全量核对 + 文档同步 + 终验
- Select-String 扫 zh-CN/en-US 新 key 全部落位（nav.intel/intel.*/onboarding.persona.*/project.tab.output/home.quick.newSchedule/schedule.read.trackFollow/schedule.badge.unread…）。
- 文档登记（§10 表）：0.3 §2.1/2.2/2.3 4Tab+Ctrl+4；0.4 §5 徽章行；0.5 §4.2/§4.4（首页情报区上移+模板分家+设为定时退役）；3.0 项目 Tab 顺序翻转；2.1 五→四 Tab；2.4 填表宿主迁产出；2.11 §8 宿主变更+inbox/summary 消费登记；0.6 §3.8 设置页身份入口随 E1b（产品决策登记）。
- 终验：`npx tsc --noEmit` → `npm run build` → `npx vitest run` → `python -m pytest python/tests -v`（全量）→ `node scripts/orphan_channel_audit.js` 四组=0 → Select-String 残留扫描（refinePrompt/scheduleFromTask/last_done_run_report 零命中）。
