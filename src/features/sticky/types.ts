/** src/features/sticky/types.ts — 便签（2.12）共享类型与色板常量。 */

export type StickyColor = 'yellow' | 'red' | 'blue' | 'green' | 'purple'

export interface StickyNote {
  id: string
  content: string
  color: StickyColor | null
  project_id: string | null
  archived_at: string | null
  created_at: string
  updated_at: string
}

export type StickyView = 'inbox' | 'archived'

/** 固定色板（spec §1：借「色」不借「体系」——单选即存，无自定义色/按色筛选） */
export const STICKY_COLORS: StickyColor[] = ['yellow', 'red', 'blue', 'green', 'purple']

/** 色值映射（CSS module 内以类引用，此处供测试与潜在 tooltip 复用） */
export const STICKY_COLOR_HEX: Record<StickyColor, string> = {
  yellow: '#f59e0b',
  red: '#ef4444',
  blue: '#3b82f6',
  green: '#22c55e',
  purple: '#a855f7',
}
