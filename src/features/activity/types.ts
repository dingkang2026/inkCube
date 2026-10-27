/** src/features/activity/types.ts — Activity Hub 类型定义
 *  与后端 routers/activities.py 聚合端点返回结构对齐（0.4lifecycle-design.md §8.1 #0）。
 *  通知与活跃侧 entity_type 统一用后端写入值（0.4 §6.5）：
 *  file / wiki_compile / report / task / model_download / ledger_parse（v1.7 修复：不再用领域别名 wiki/model）。
 */

/** 活跃侧实体类型（活动注册表 kind，0.4 §2.2 后端写入值）：affair 仅通知侧
 *  （事务到期提醒，2.11 §3.2 无活跃条目），不在此联合内；
 *  profile_extract = 产业画像提取（2.6 §4.8，仅项目详情头部消费，不入 summary 计数） */
export type ActiveEntityType = 'file' | 'wiki_compile' | 'report' | 'task' | 'model_download' | 'ledger_parse' | 'profile_extract' | 'form_template' | 'form_fill' | 'project_export' | 'project_import' | 'ppt' | 'schedule'

/** 通知条目类型（notifications 表 entity_type 取值 = 活跃侧 + affair + schedule_run；
 *  schedule 双侧兼有：订阅级通知（档案合并失败/错过跳过/中断清扫，2.11 §6/§5.4）
 *  + 订阅期次执行中活动源（2.11 D2 复核）） */
export type NotificationEntityType = ActiveEntityType | 'affair' | 'schedule_run'

/** 通知 kind（notifications 表写入值，2.11 扩信息类）：终态 completed / failed /
 *  paused（报告级暂停，2.5 §3.1 修订）/ awaiting_input（PPT 门禁等待确认，2.3 §3.4）；
 *  信息类 file_changed（模板源文件变更，2.11 §3.3）/ due / overdue（事务到期提醒，2.11 §3.2）
 *  / archive_failed（订阅档案合并失败：产出已生成待下期补并，2.11 §6）
 *  / missed（>24h 未运行跳过，2.11 §5.4）/ interrupted（进程中断启动清扫，review P0-1）
 *  / governance（编译终态自动治理链汇总通知，E3-2026-10；review M-2 2026-10-27 补登记——
 *  后端 activity_events.governance_wiki 已写入此值而前端联合类型漏项，消费时才暴露） */
export type NotificationKind = 'completed' | 'failed' | 'paused' | 'awaiting_input' | 'file_changed' | 'due' | 'overdue' | 'archive_failed' | 'missed' | 'interrupted' | 'governance'

/** 活动挂载容器（0.4 §3.6）：活动所属 projects 行的 kind，基础库活动导航分流用；
 *  'templates' = 模板类全局资产（PPT 模板任务，2.3 §4.2，无项目实体） */
export type ActivityContainer = 'project' | 'baselib' | 'templates'

export interface ActiveItem {
  entity_type: ActiveEntityType
  entity_id?: string
  project_id?: string | null
  project_name?: string | null
  /** 挂载容器 project / baselib / templates（0.4 §3.6 导航分流；task / model_download 无项目不携带） */
  container?: ActivityContainer | null
  entity_title?: string | null
  /** file: 活跃文件数（解析中 + 图片描述中） */
  count?: number
  /** file: 其中处于图片描述阶段的数量 */
  image_describing?: number
  /** wiki_compile: queued / ingesting / storing */
  status?: string
  /** wiki_compile: 页面进度文本，如 "3/8" */
  progress_text?: string
  /** report / model_download: 0-100 进度 */
  progress?: number
}

export interface NotificationItem {
  id: number
  entity_type: NotificationEntityType
  entity_id: string | null
  project_id: string | null
  container?: ActivityContainer | null
  kind: NotificationKind
  entity_title: string | null
  detail: string | null
  read_at: string | null
  created_at: string
}

export interface ActivitySummary {
  active_files: number
  active_compiles: number
  generating_reports: number
  running_tasks: number
  downloading_models: number
  /** 填表：分析中/待分析模板数（2.4 §3.4） */
  analyzing_form_templates: number
  /** 填表：排队/填写中实例数（2.4 §3.4） */
  filling_forms: number
  /** PPT 交付物：生成中/待确认数（2.3 §6） */
  active_ppts: number
  /** 定时订阅：执行中期次数（2.11 D2 复核，影子任务不入 task 源后的正源） */
  running_schedules: number
}

export interface ActivityStatus {
  active: ActiveItem[]
  notifications: {
    unread: NotificationItem[]
    recent: NotificationItem[]
  }
  summary: ActivitySummary
}

/** 按项目聚合的活动计数（徽章 / 活跃点 / Tab 角标取数） */
export interface ProjectActivity {
  projectId: string
  projectName: string
  /** 挂载容器（BaseLibFooter 基础库行活跃点与项目行对齐取数，0.4 §5.7） */
  container: ActivityContainer
  /** 解析中文件数（含图片描述中） */
  files: number
  /** 图片描述中数量（files 的子集） */
  imageDescribing: number
  /** Wiki 编译中 */
  compiles: number
  /** 生成中报告数 */
  reports: number
  /** 填表进行中实例数（排队/填写中，2.4 §3.4） */
  forms: number
  /** PPT 交付物进行中数（生成中/待确认，2.3 §6） */
  ppts: number
}

export const EMPTY_SUMMARY: ActivitySummary = {
  active_files: 0,
  active_compiles: 0,
  generating_reports: 0,
  running_tasks: 0,
  downloading_models: 0,
  analyzing_form_templates: 0,
  filling_forms: 0,
  active_ppts: 0,
  running_schedules: 0,
}
