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

/** Page type label i18n keys（经 t() 翻译，缺失时回退原始 type） */
export const PAGE_TYPE_LABEL_KEYS: Record<string, string> = {
  structure: 'project.wiki.pageTypeStructure',
  entity: 'project.wiki.pageTypeEntity',
  concept: 'project.wiki.pageTypeConcept',
  process: 'project.wiki.pageTypeProcess',
  reference: 'project.wiki.pageTypeReference',
}

/** Action label i18n keys for changelog display（经 t() 翻译） */
export const ACTION_LABEL_KEYS: Record<string, string> = {
  created: 'project.wiki.actionCreated',
  updated: 'project.wiki.actionUpdated',
  user_edited: 'project.wiki.actionUserEdited',
  deleted: 'project.wiki.actionDeleted',
  archived: 'project.wiki.actionArchived',
  ingest: 'project.wiki.actionIngest',
  enriched: 'project.wiki.actionEnriched',
  dedup_merge: 'project.wiki.actionDedupMerge',
  governance: 'project.wiki.actionGovernance',
  overview_updated: 'project.wiki.actionOverviewUpdated',
}

/** Format a timestamp for changelog display (locale-aware relative time) */
export function formatChangelogTime(isoStr: string, lang: string): string {
  try {
    const d = new Date(isoStr)
    const diffMin = Math.floor((Date.now() - d.getTime()) / 60000)
    const rtf = new Intl.RelativeTimeFormat(lang, { numeric: 'always' })
    if (diffMin < 60) return rtf.format(-Math.max(diffMin, 0), 'minute')
    const diffHr = Math.floor(diffMin / 60)
    if (diffHr < 24) return rtf.format(-diffHr, 'hour')
    const diffDay = Math.floor(diffHr / 24)
    if (diffDay < 7) return rtf.format(-diffDay, 'day')
    return d.toLocaleDateString(lang, { month: 'short', day: 'numeric' })
  } catch {
    return isoStr
  }
}
