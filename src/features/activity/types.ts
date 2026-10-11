/** src/features/activity/types.ts — Activity Hub 类型定义
 *  与后端 routers/activities.py 聚合端点返回结构对齐（0.4lifecycle-design.md §8.1 #0）。
 *  通知与活跃侧 entity_type 统一用后端写入值（0.4 §6.5）：
 *  file / wiki_compile / report / task / model_download / ledger_parse（v1.7 修复：不再用领域别名 wiki/model）。
 */

/** 活跃/通知实体类型（后端写入值，通知与活跃侧一致） */
export type ActiveEntityType = 'file' | 'wiki_compile' | 'report' | 'task' | 'model_download' | 'ledger_parse'

/** 通知条目类型（notifications 表 entity_type 取值，同上） */
export type NotificationEntityType = ActiveEntityType

/** 终态通知 kind：completed / failed / paused（报告级暂停，2.5 §3.1 修订） */
export type NotificationKind = 'completed' | 'failed' | 'paused'

/** 活动挂载容器（0.4 §3.6）：活动所属 projects 行的 kind，基础库活动导航分流用 */
export type ActivityContainer = 'project' | 'baselib'

export interface ActiveItem {
  entity_type: ActiveEntityType
  entity_id?: string
  project_id?: string | null
  project_name?: string | null
  /** 挂载容器 project / baselib（0.4 §3.6 导航分流；task / model_download 无项目不携带） */
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
}

export const EMPTY_SUMMARY: ActivitySummary = {
  active_files: 0,
  active_compiles: 0,
  generating_reports: 0,
  running_tasks: 0,
  downloading_models: 0,
}
