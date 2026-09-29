/** src/features/project/tabs/wikiUtils.ts — Wiki helper utilities */

/** Parsed YAML frontmatter from a wiki page */
export interface WikiFrontmatter {
  type?: string
  title?: string
  refs?: string[]
  sources?: string[]
  tags?: string[]
}

/** Parse YAML frontmatter block from markdown content */
export function parseFrontmatter(content: string): { meta: WikiFrontmatter; body: string } {
  const match = content.match(/^---\n([\s\S]*?)\n---\n?([\s\S]*)$/)
  if (!match) return { meta: {}, body: content }

  const meta: WikiFrontmatter = {}
  const lines = match[1].split('\n')
  let currentKey = ''

  for (const line of lines) {
    const kvMatch = line.match(/^(\w+):\s*(.*)$/)
    if (kvMatch) {
      currentKey = kvMatch[1]
      const value = kvMatch[2].trim()
      if (value) {
        ;(meta as Record<string, unknown>)[currentKey] = parseYamlValue(value)
      } else {
        ;(meta as Record<string, unknown>)[currentKey] = []
      }
    } else if (line.match(/^\s*-\s+(.+)/) && currentKey) {
      const arr = (meta as Record<string, unknown>)[currentKey]
      if (Array.isArray(arr)) {
        arr.push(line.replace(/^\s*-\s+/, '').trim())
      }
    }
  }

  return { meta, body: match[2] }
}

/** Parse a YAML value: inline array [a, b, c] or plain string */
function parseYamlValue(value: string): string | string[] {
  if (value.startsWith('[') && value.endsWith(']')) {
    return value
      .slice(1, -1)
      .split(',')
      .map((s) => s.trim().replace(/^["']|["']$/g, ''))
      .filter(Boolean)
  }
  return value.replace(/^["']|["']$/g, '')
}

/** Page type labels for display */
export const PAGE_TYPE_LABELS: Record<string, string> = {
  structure: '结构页',
  entity: '实体页',
  concept: '概念页',
  process: '流程页',
  reference: '参考页',
}

/** Action labels for changelog display */
export const ACTION_LABELS: Record<string, string> = {
  created: '创建',
  updated: '更新',
  user_edited: '用户编辑',
  deleted: '删除',
  archived: '归档',
  ingest: '摄入',
  enriched: '链接补全',
  dedup_merge: '去重合并',
  overview_updated: '概览更新',
}

/** Format a timestamp for changelog display */
export function formatChangelogTime(isoStr: string): string {
  try {
    const d = new Date(isoStr)
    const now = new Date()
    const diffMs = now.getTime() - d.getTime()
    const diffMin = Math.floor(diffMs / 60000)
    if (diffMin < 1) return '刚刚'
    if (diffMin < 60) return `${diffMin} 分钟前`
    const diffHr = Math.floor(diffMin / 60)
    if (diffHr < 24) return `${diffHr} 小时前`
    const diffDay = Math.floor(diffHr / 24)
    if (diffDay < 7) return `${diffDay} 天前`
    return d.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
  } catch {
    return isoStr
  }
}
