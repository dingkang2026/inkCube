// 任务欢迎页内置模板注册表与占位符解析（0.5 E1，文案存 i18n task.tpl.<id>.*）。
// V2-E1a T12 模板分家：TASK_TEMPLATES 仅任务模板；订阅白名单独立 INTEL_TEMPLATES。
export type TemplateCategory = 'info' | 'mon' | 'ind' | 'reg' | 'rep' | 'off';

export interface TaskTemplateDef {
  id: string; // 与 i18n task.tpl.<id>.* 对应
  category: TemplateCategory;
  icon: string; // lucide 图标名
}

/** 任务模板 6 条（0.6 §3.2 V2-E1a T12 收窄：舆情/产业 4 条移入订阅白名单） */
export const TASK_TEMPLATES: TaskTemplateDef[] = [
  { id: 'companyBasic', category: 'info', icon: 'Building2' },
  { id: 'companyDeep', category: 'info', icon: 'SearchCheck' },
  { id: 'ddChecklist', category: 'rep', icon: 'ClipboardList' },
  { id: 'businessModel', category: 'rep', icon: 'Puzzle' },
  { id: 'fileOrganize', category: 'off', icon: 'FolderTree' },
  { id: 'dataSummary', category: 'off', icon: 'Table' },
];

/** 订阅白名单 7 条（0.6 §3.2 V2-E1a T12：仅 ScheduleFormModal 消费）；
 *  industryPolicy/regionPolicy 为 T12 新增（产业政策/区域招商政策扫描）；
 *  industrySentiment 为 2026-10-24 新增（产业舆情扫描，盯一条赛道而非一家企业） */
export const INTEL_TEMPLATES: TaskTemplateDef[] = [
  { id: 'sentiment', category: 'mon', icon: 'Newspaper' },
  { id: 'competitor', category: 'mon', icon: 'Activity' },
  { id: 'industrySentiment', category: 'ind', icon: 'Radio' },
  { id: 'industryEvents', category: 'ind', icon: 'TrendingUp' },
  { id: 'industryLeads', category: 'ind', icon: 'Target' },
  { id: 'industryPolicy', category: 'ind', icon: 'Landmark' },
  { id: 'regionPolicy', category: 'reg', icon: 'MapPin' },
];

/** 订阅产出分类（corporate/industry/region/general）← 模板短分类（0.6 §3.2 T12） */
export const INTEL_CATEGORY_MAP: Record<TemplateCategory, string> = {
  mon: 'corporate',
  ind: 'industry',
  reg: 'region',
  info: 'corporate',
  rep: 'general',
  off: 'general',
};

export const TEMPLATE_CATEGORIES = ['all', 'info', 'mon', 'ind', 'reg', 'rep', 'off'] as const;

/** 是否仍有未填写的 {占位符} */
export function hasUnfilledPlaceholders(text: string): boolean {
  return /\{[^{}]+\}/.test(text);
}

/** 按首次出现顺序提取去重后的占位符名（不含花括号）。
 *  同一变量在 prompt 中出现多次只计一次——表单化填写的基础（0.5 §4.4）。 */
export function uniquePlaceholderNames(prompt: string): string[] {
  const names: string[] = [];
  for (const m of prompt.matchAll(/\{([^{}]+)\}/g)) {
    if (!names.includes(m[1])) names.push(m[1]);
  }
  return names;
}

/** 占位符值替换：{名称} → 已填值，未填保留原样（由 hasUnfilledPlaceholders 拦截）。
 *  同名占位符全文一次性替换——用户每变量只填一次（0.5 §4.4）。 */
export function applyValues(template: string, values: Record<string, string>): string {
  return template.replace(/\{([^{}]+)\}/g, (raw, name: string) => values[name] ?? raw);
}

/** 内置模板 id 前缀（合并后 MergedTemplate.id 形态 'builtin_companyBasic'） */
export const BUILTIN_PREFIX = 'builtin_';

/** 后端 category（db 存名）→ 前端 tab 分类（面板/管理弹层用短名） */
export const CATEGORY_ALIASES: Record<string, TemplateCategory> = {
  info_gather: 'info',
  monitor: 'mon',
  industry: 'ind',
  region: 'reg',
  report_invest: 'rep',
  office: 'off',
};

/** 前端 tab 分类 → 后端 category（save 落库用，与 CATEGORY_ALIASES 互逆） */
export const BACKEND_CATEGORY: Record<TemplateCategory, string> = {
  info: 'info_gather',
  mon: 'monitor',
  ind: 'industry',
  reg: 'region',
  rep: 'report_invest',
  off: 'office',
};

/** GET /task-templates templates[] 元素（自定义行，db 列直出；pinned 序列化为 0/1） */
export interface CustomRow {
  id: string;
  scope: 'global' | 'project';
  project_id: string | null;
  category: string;
  title: string;
  prompt: string;
  pinned: number | boolean;
  created_at: string;
  updated_at: string;
}

/** 合并后供面板/管理弹层渲染的模板；内置文案经 t() 解析，自定义直读行值 */
export interface MergedTemplate {
  id: string; // 'builtin_companyBasic' | 'tpl_custom_xxx'
  builtin: boolean;
  category: TemplateCategory;
  title: string;
  desc: string; // 内置取 i18n desc；自定义取 prompt 前 50 字符
  prompt: string;
  pinned: boolean;
}

/** 管理弹层新建/编辑表单草稿（hook save 入参）；编辑须回传 pinned——
 *  后端 upsert 的 pinned 默认 False，不回传会覆盖既有置顶态 */
export interface TemplateDraft {
  id?: string;
  category: TemplateCategory;
  title: string;
  prompt: string;
  pinned?: boolean;
}

/** 内置 + 自定义合并排序：置顶优先（内置在前、自定义按 updated_at 倒序混排其后），
 *  非置顶内置保持自然序在前，非置顶自定义按 updated_at 倒序收尾（0.5 §4.4） */
export function mergeTemplates(
  builtins: TaskTemplateDef[],
  t: (key: string) => string,
  custom: CustomRow[],
  builtinPinned: string[],
): MergedTemplate[] {
  const builtinMerged: MergedTemplate[] = builtins.map((b) => ({
    id: `${BUILTIN_PREFIX}${b.id}`,
    builtin: true,
    category: b.category,
    title: t(`task.tpl.${b.id}.title`),
    desc: t(`task.tpl.${b.id}.desc`),
    prompt: t(`task.tpl.${b.id}.prompt`),
    pinned: builtinPinned.includes(`tpl_builtin_${b.id}`),
  }));
  const customMerged: MergedTemplate[] = [...custom]
    .sort((a, b) => (a.updated_at < b.updated_at ? 1 : a.updated_at > b.updated_at ? -1 : 0))
    .map((r) => ({
      id: r.id,
      builtin: false,
      category: CATEGORY_ALIASES[r.category] ?? 'info',
      title: r.title,
      desc: r.prompt.slice(0, 50),
      prompt: r.prompt,
      pinned: !!r.pinned,
    }));
  return [
    ...builtinMerged.filter((m) => m.pinned),
    ...customMerged.filter((m) => m.pinned),
    ...builtinMerged.filter((m) => !m.pinned),
    ...customMerged.filter((m) => !m.pinned),
  ];
}

/** persona → 非置顶区置顶的内置模板 id（0.6 §3.2 V2-E1a T12）；
 *  both/other/'' 不重排。kind 区分任务/订阅两套白名单 */
const PERSONA_TOP: Record<string, { task: string[]; intel: string[] }> = {
  business_attraction: { task: ['companyBasic', 'companyDeep'], intel: ['regionPolicy'] },
  investment: { task: ['ddChecklist', 'businessModel'], intel: ['industryEvents'] },
};

/** persona 排序：仅重排非置顶区（mergeTemplates 已把 pinned 提前，不碰），
 *  区内 persona 置顶模板提前、其余保序。纯前端重排，不落库 */
export function sortTemplatesByPersona(
  list: MergedTemplate[], persona: string, kind: 'task' | 'intel',
): MergedTemplate[] {
  const top = PERSONA_TOP[persona]?.[kind] ?? [];
  if (top.length === 0) return list;
  const pinIdx = list.findIndex((m) => !m.pinned);
  const head = pinIdx === -1 ? list : list.slice(0, pinIdx);
  const rest = pinIdx === -1 ? [] : list.slice(pinIdx);
  const restSorted = [
    ...rest.filter((m) => top.includes(m.id.slice(BUILTIN_PREFIX.length))),
    ...rest.filter((m) => !top.includes(m.id.slice(BUILTIN_PREFIX.length))),
  ];
  return [...head, ...restSorted];
}
