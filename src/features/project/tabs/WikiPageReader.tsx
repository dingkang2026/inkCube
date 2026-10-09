/** src/features/project/tabs/WikiPageReader.tsx — Wiki page reader with frontmatter, backlinks, sources */
import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Link2, FileText, Clock, Tag, Edit3, ExternalLink,
} from 'lucide-react'
import { Markdown } from '../../../components/Markdown'
import { parseFrontmatter, PAGE_TYPE_LABEL_KEYS, formatChangelogTime, ACTION_LABEL_KEYS } from './wikiUtils'
import { useToast } from '../../knowledge/Toast'
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
  /** 摄入跟踪列表（wiki_source_pages）：src 页 header「打开原文件」的映射数据源 */
  sourcePages?: Array<{ source_page_key: string | null; file_id: string; filename?: string | null }>
  onEdit: () => void
  onNavigateToPage: (pageKey: string) => void
}

export function WikiPageReader({
  projectId, pageDetail, sourcePages, onEdit, onNavigateToPage,
}: WikiPageReaderProps) {
  const { t, i18n } = useTranslation()
  const toast = useToast()
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

  // src 来源摘要页 → 原文件映射（source_page_key → file_id/filename）；
  // 映射不到（数据异常）不渲染按钮不报错（2.2 §10.4）
  const sourcePageFile = pageType === 'source'
    ? sourcePages?.find(sp => sp.source_page_key === page.page_key)
    : undefined

  const openOriginal = useCallback(async (fileId: string, filename?: string | null) => {
    if (!fileId || !filename) return
    try {
      const result: any = await window.electronAPI.invoke('file:openOriginal', {
        projectId, fileId, filename,
      })
      if (result && !result.success) {
        toast(
          result.reason === 'not_found'
            ? t('knowledge.originalNotFound', { defaultValue: '原文件不存在，可能已被删除' })
            : t('knowledge.openFailed', { defaultValue: '打开失败: {{reason}}', reason: result.message || result.reason }),
          'error',
        )
      }
    } catch (err) {
      console.error('Open original failed:', err)
      toast(t('knowledge.openFailed', { defaultValue: '打开失败: {{reason}}', reason: String(err) }), 'error')
    }
  }, [projectId, toast, t])

  return (
    <div className={s.readerPage}>
      {/* Header */}
      <div className={s.readerHeader}>
        <div className={s.readerHeaderInfo}>
          <h3 className={s.readerTitle}>{page.title}</h3>
          <div className={s.readerMeta}>
            <span className={s.pageTypeBadge}>
              {PAGE_TYPE_LABEL_KEYS[pageType]
                ? t(PAGE_TYPE_LABEL_KEYS[pageType], { defaultValue: pageType })
                : pageType}
            </span>
            {page.status === 'user_edited' && <span className={s.editedBadge}>{t('project.wiki.readerEditedBadge', { defaultValue: '用户已编辑' })}</span>}
            {page.compile_version > 0 && <span className={s.versionBadge}>v{page.compile_version}</span>}
          </div>
        </div>
        <div className={s.readerHeaderActions}>
          {sourcePageFile && (
            <button
              className={s.editBtn}
              onClick={() => void openOriginal(sourcePageFile.file_id, sourcePageFile.filename)}
            >
              <ExternalLink size={12} /> {t('knowledge.openOriginal', { defaultValue: '打开原文件' })}
            </button>
          )}
          <button className={s.editBtn} onClick={onEdit}>
            <Edit3 size={12} /> {t('project.wiki.edit', { defaultValue: '编辑' })}
          </button>
        </div>
      </div>

      {/* Frontmatter panel (collapsible) */}
      {(refs.length > 0 || tags.length > 0) && (
        <div className={s.frontmatterPanel}>
          {refs.length > 0 && (
            <div className={s.frontmatterRow}>
              <Link2 size={12} className={s.frontmatterIcon} />
              <span className={s.frontmatterLabel}>{t('project.wiki.readerRefsLabel', { defaultValue: '引用' })}:</span>
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
              <span className={s.frontmatterLabel}>{t('project.wiki.readerTagsLabel', { defaultValue: '标签' })}:</span>
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
            {t('project.wiki.backlinks', { defaultValue: '反向链接' })} ({backlinks.length})
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
                <span className={s.pageTypeBadgeSm}>
                  {PAGE_TYPE_LABEL_KEYS[bl.page_type]
                    ? t(PAGE_TYPE_LABEL_KEYS[bl.page_type], { defaultValue: bl.page_type })
                    : bl.page_type}
                </span>
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
            {t('project.wiki.sources', { defaultValue: '来源文件' })} ({pageDetail.sources.length})
          </summary>
          <ul className={s.sourcesList}>
            {pageDetail.sources.map((src: any) => (
              <li key={src.id}>
                <span className={s.sourceName}>{src.filename || src.file_id}</span>
                {src.contribution && <span className={s.sourceContrib}>{src.contribution}</span>}
                {src.file_id && (
                  <button
                    className={s.sourceOpenBtn}
                    title={t('knowledge.openOriginal', { defaultValue: '打开原文件' })}
                    onClick={() => void openOriginal(src.file_id, src.filename)}
                  >
                    <ExternalLink size={12} />
                  </button>
                )}
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
            {t('project.wiki.pageHistory', { defaultValue: '页面历史' })} ({pageDetail.change_log.length})
          </summary>
          <div className={s.miniTimeline}>
            {pageDetail.change_log.slice(0, 5).map((log: any) => (
              <div key={log.id} className={s.timelineItem}>
                <span className={s.timelineTime}>{formatChangelogTime(log.created_at, i18n.language)}</span>
                <span className={s.timelineAction}>
                  {ACTION_LABEL_KEYS[log.action]
                    ? t(ACTION_LABEL_KEYS[log.action], { defaultValue: log.action })
                    : log.action}
                </span>
                {log.summary && <span className={s.timelineSummary}>{log.summary}</span>}
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  )
}
