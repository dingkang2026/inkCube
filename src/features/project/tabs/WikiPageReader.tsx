/** src/features/project/tabs/WikiPageReader.tsx — Wiki page reader with frontmatter, backlinks, sources */
import { useState, useEffect, useCallback } from 'react'
import {
  Link2, FileText, Clock, Tag, Edit3,
} from 'lucide-react'
import { Markdown } from '../../../components/Markdown'
import { parseFrontmatter, PAGE_TYPE_LABELS, formatChangelogTime, ACTION_LABELS } from './wikiUtils'
import s from './WikiTab.module.css'

interface WikiPageReaderProps {
  projectId: string
  pageDetail: {
    page: {
      id: string; page_key: string; title: string; content: string | null
      status: string; compile_version: number; page_type: string
      refs: string[]; sources: string[]; tags: string[]
      last_compiled_at: string | null; updated_at: string
    }
    sources: Array<{ id: string; file_id: string; filename?: string; contribution?: string }>
    change_log: Array<{ id: string; action: string; summary: string; source_title: string; created_at: string }>
  }
  onEdit: () => void
  onNavigateToPage: (pageKey: string) => void
}

export function WikiPageReader({
  projectId, pageDetail, onEdit, onNavigateToPage,
}: WikiPageReaderProps) {
  const page = pageDetail.page
  const { meta, body } = parseFrontmatter(page.content || '')
  const [backlinks, setBacklinks] = useState<Array<{ page_key: string; title: string; page_type: string }>>([])

  // Load backlinks
  const loadBacklinks = useCallback(async () => {
    try {
      const result: any = await window.electronAPI.invoke('wiki:getBacklinks', {
        projectId, pageKey: page.page_key,
      })
      setBacklinks(result.backlinks || [])
    } catch (err) {
      console.error('Failed to load backlinks:', err)
    }
  }, [projectId, page.page_key])

  useEffect(() => { loadBacklinks() }, [loadBacklinks])

  // Listen for wiki-link clicks
  useEffect(() => {
    const handler = (e: Event) => {
      const { pageKey } = (e as CustomEvent).detail
      if (pageKey) onNavigateToPage(pageKey)
    }
    window.addEventListener('wiki-link-click', handler)
    return () => window.removeEventListener('wiki-link-click', handler)
  }, [onNavigateToPage])

  // Merge frontmatter refs with meta refs
  const refs = page.refs?.length ? page.refs : (Array.isArray(meta.refs) ? meta.refs : [])
  const tags = page.tags?.length ? page.tags : (Array.isArray(meta.tags) ? meta.tags : [])
  const pageType = page.page_type || meta.type || 'structure'

  return (
    <div className={s.readerPage}>
      {/* Header */}
      <div className={s.readerHeader}>
        <div className={s.readerHeaderInfo}>
          <h3 className={s.readerTitle}>{page.title}</h3>
          <div className={s.readerMeta}>
            <span className={s.pageTypeBadge}>{PAGE_TYPE_LABELS[pageType] || pageType}</span>
            {page.status === 'user_edited' && <span className={s.editedBadge}>用户已编辑</span>}
            {page.compile_version > 0 && <span className={s.versionBadge}>v{page.compile_version}</span>}
          </div>
        </div>
        <button className={s.editBtn} onClick={onEdit}>
          <Edit3 size={12} /> 编辑
        </button>
      </div>

      {/* Frontmatter panel (collapsible) */}
      {(refs.length > 0 || tags.length > 0) && (
        <div className={s.frontmatterPanel}>
          {refs.length > 0 && (
            <div className={s.frontmatterRow}>
              <Link2 size={12} className={s.frontmatterIcon} />
              <span className={s.frontmatterLabel}>引用:</span>
              <div className={s.frontmatterTags}>
                {refs.map((ref: string) => (
                  <span key={ref} className={s.refTag} onClick={() => onNavigateToPage(ref)}>{ref}</span>
                ))}
              </div>
            </div>
          )}
          {tags.length > 0 && (
            <div className={s.frontmatterRow}>
              <Tag size={12} className={s.frontmatterIcon} />
              <span className={s.frontmatterLabel}>标签:</span>
              <div className={s.frontmatterTags}>
                {tags.map((tag: string) => (
                  <span key={tag} className={s.tagBadge}>{tag}</span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Markdown content */}
      <div className={s.markdownBody}>
        <Markdown content={body} />
      </div>

      {/* Backlinks panel */}
      {backlinks.length > 0 && (
        <details className={s.backlinksPanel}>
          <summary className={s.collapsibleSummary}>
            <Link2 size={12} />
            反向链接 ({backlinks.length})
          </summary>
          <div className={s.backlinksList}>
            {backlinks.map((bl) => (
              <div
                key={bl.page_key}
                className={s.backlinkItem}
                onClick={() => onNavigateToPage(bl.page_key)}
              >
                <FileText size={12} className={s.backlinkIcon} />
                <span className={s.backlinkTitle}>{bl.title}</span>
                <span className={s.pageTypeBadgeSm}>{PAGE_TYPE_LABELS[bl.page_type] || bl.page_type}</span>
              </div>
            ))}
          </div>
        </details>
      )}

      {/* Sources panel */}
      {pageDetail.sources && pageDetail.sources.length > 0 && (
        <details className={s.sourcesPanel}>
          <summary className={s.collapsibleSummary}>
            <FileText size={12} />
            来源文件 ({pageDetail.sources.length})
          </summary>
          <ul className={s.sourcesList}>
            {pageDetail.sources.map((src: any) => (
              <li key={src.id}>
                <span className={s.sourceName}>{src.filename || src.file_id}</span>
                {src.contribution && <span className={s.sourceContrib}>{src.contribution}</span>}
              </li>
            ))}
          </ul>
        </details>
      )}

      {/* Page changelog */}
      {pageDetail.change_log && pageDetail.change_log.length > 0 && (
        <details className={s.pageChangelog}>
          <summary className={s.collapsibleSummary}>
            <Clock size={12} />
            页面历史 ({pageDetail.change_log.length})
          </summary>
          <div className={s.miniTimeline}>
            {pageDetail.change_log.slice(0, 5).map((log: any) => (
              <div key={log.id} className={s.timelineItem}>
                <span className={s.timelineTime}>{formatChangelogTime(log.created_at)}</span>
                <span className={s.timelineAction}>{ACTION_LABELS[log.action] || log.action}</span>
                {log.summary && <span className={s.timelineSummary}>{log.summary}</span>}
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  )
}
