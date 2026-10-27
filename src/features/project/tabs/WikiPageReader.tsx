/** src/features/project/tabs/WikiPageReader.tsx — Wiki page reader with frontmatter, backlinks, sources.
 *  Ctrl/Cmd+F 文档内查找（公共基座 useMarkdownBlockFind + FindBar，2026-10-23）：
 *  范围限正文 markdownBody，版本快照弹层不接（只读对比场景）。 */
import { useState, useEffect, useCallback, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Link2, FileText, Clock, Tag, Edit3, ExternalLink,
  Trash2, Wand2, History, Loader2, X,
} from 'lucide-react'
import { Markdown } from '../../../components/Markdown'
import { FindBar } from '../../../components/FindBar'
import { useMarkdownBlockFind } from '../../../hooks/useInFileFind'
import { parseFrontmatter, PAGE_TYPE_LABEL_KEYS, formatChangelogTime, ACTION_LABEL_KEYS } from './wikiUtils'
import { WikiLedgerTimeline } from './WikiLedgerTimeline'
import { useToast } from '../../knowledge/Toast'
import s from './WikiTab.module.css'

/** 版本行（wiki:listVersions → db.list_wiki_page_versions 行结构，不含 content） */
interface PageVersionRow {
  id: string
  version: number
  change_summary: string | null
  created_at: string
}

/** 版本快照（wiki:getVersion → db.get_wiki_page_version 全行，含 content） */
interface PageVersionSnapshot extends PageVersionRow {
  content: string | null
}

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
  /** 页面删除后回调（§5.3）：父级关预览 + 刷新列表（此刻页面已不存在） */
  onDeleted: (pageKey: string) => void
  /** 页面内容变化后回调（§7.1 富化）：父级重拉 pageDetail */
  onRefresh?: () => void
  /** 台账页（is_ledger schema 命中，父级零新增请求判定）：阅读形态切时间轴（V2 E3 §3.5） */
  isLedger?: boolean
}

export function WikiPageReader({
  projectId, pageDetail, sourcePages, onEdit, onNavigateToPage, onDeleted, onRefresh, isLedger,
}: WikiPageReaderProps) {
  const { t, i18n } = useTranslation()
  const toast = useToast()
  const page = pageDetail.page
  const { meta, body } = parseFrontmatter(page.content || '')
  const [backlinks, setBacklinks] = useState<Array<{ page_key: string; title: string; page_type: string }>>([])
  // 删除（§5.3）/富化（§7.1）/版本历史（§7.1）状态
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const [deleting, setDeleting] = useState(false)
  // 删除确认：Esc = 取消（删除请求在途不可关，与点蒙板同语义）
  useEffect(() => {
    if (!showDeleteConfirm) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !deleting) setShowDeleteConfirm(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [showDeleteConfirm, deleting])
  const [enriching, setEnriching] = useState(false)
  // 版本历史分页（0.1dev-guide 统一约定）：懒加载哨兵（null=未展开）+ limit 递增全量重拉，
  // 不适配 usePagedList（它挂载即首拉，与「展开才拉」语义冲突）
  const versionsLimitRef = useRef(20)
  const [versions, setVersions] = useState<PageVersionRow[] | null>(null)
  const [versionsLoading, setVersionsLoading] = useState(false)
  const [versionsHasMore, setVersionsHasMore] = useState(false)
  const [versionsTotal, setVersionsTotal] = useState<number | null>(null)
  const [versionsLoadingMore, setVersionsLoadingMore] = useState(false)
  const [snapshot, setSnapshot] = useState<PageVersionSnapshot | null>(null)
  // 快照请求序号：快速连点多版本时以最后一次点击为准（晚到的旧响应不覆盖/不误弹错）
  const snapshotReqRef = useRef(0)

  // ── 文档内查找（公共基座 useMarkdownBlockFind + FindBar）：范围限正文 markdownBody
  // （扫描根与滚动容器合一，正文长时 overflow 滚动在其自身），版本快照弹层不接
  // （2026-10-23 裁决：只读对比场景）。台账页时间轴形态（V2 E3）无 Markdown 块
  // 可扫且正文区非本组件渲染，Ctrl+F 不接管（回落原文后恢复）
  const [ledgerFallback, setLedgerFallback] = useState(false)
  const findEnabled = !isLedger || ledgerFallback
  const bodyRef = useRef<HTMLDivElement | null>(null)
  const find = useMarkdownBlockFind(bodyRef, body, findEnabled)

  // Ctrl/Cmd+F 接管：readerPage 为预览面板主内容（与编辑器互斥挂载），window 级监听
  // （同 ReadView/ArchiveView 先例）；时间轴形态不接管（浏览器默认行为留给外部）
  useEffect(() => {
    if (!findEnabled) return
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === 'f') {
        e.preventDefault()
        e.stopPropagation()
        find.openFind()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [findEnabled, find.openFind])

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

  // 切页复位：父级以 key=page_key 强制逐页重挂载，版本/快照/确认框随实例生命周期天然复位
  // 删除页面（§5.3）：后端级联清理（来源/变更日志/审查项/他页反链），成功后回调父级关预览
  const handleDelete = async () => {
    setDeleting(true)
    try {
      await window.electronAPI.invoke('wiki:deletePage', { projectId, pageKey: page.page_key })
      toast(t('project.wiki.deleteDone', { defaultValue: '页面已删除' }), 'success')
      setShowDeleteConfirm(false)
      onDeleted(page.page_key)
    } catch (err) {
      console.error('Failed to delete page:', err)
      const reason = err instanceof Error ? err.message : String(err)
      toast(t('project.wiki.deleteFailed', { reason, defaultValue: '删除失败: {{reason}}' }), 'error')
    } finally {
      setDeleting(false)
    }
  }

  // 富化 Wiki 链接（§7.2.4 四步安全补全）：LLM 建议 + 代码替换，links_added>0 才写回
  const handleEnrich = async () => {
    setEnriching(true)
    try {
      const result: any = await window.electronAPI.invoke('wiki:enrichWikilinks', {
        projectId, pageKey: page.page_key,
      })
      if (result?.reason === 'content_changed') {
        // 内容级 CAS 拒绝（复审 Major 2026-10-27）：读-改-写窗口内他方已写，
        // LLM 建议整批丢弃——刷新让用户看到现行内容后再试
        toast(t('project.wiki.enrichContentChanged', { defaultValue: '页面内容刚被其他操作修改，已放弃本次补全，请刷新后重试' }), 'warning')
        onRefresh?.()
        return
      }
      const added = result?.links_added ?? 0
      if (added > 0) {
        toast(t('project.wiki.enrichDone', { count: added, defaultValue: '已补全 {{count}} 个链接' }), 'success')
        onRefresh?.()
      } else {
        toast(t('project.wiki.enrichNone', { defaultValue: '未发现可补全的链接' }), 'info')
      }
    } catch (err) {
      console.error('Failed to enrich wikilinks:', err)
      const reason = err instanceof Error ? err.message : String(err)
      toast(t('project.wiki.enrichFailed', { reason, defaultValue: '补全失败: {{reason}}' }), 'error')
    } finally {
      setEnriching(false)
    }
  }

  // 版本列表（§7.1）：面板首次展开时懒加载（DB 查询轻量，不预取）；20/页「加载更多」
  const loadVersions = useCallback(async (opts?: { more?: boolean }) => {
    if (opts?.more) {
      if (!versionsHasMore || versionsLoadingMore) return
      setVersionsLoadingMore(true)
      versionsLimitRef.current += 20
    } else {
      setVersionsLoading(true)
    }
    try {
      const result: any = await window.electronAPI.invoke('wiki:listVersions', {
        projectId, pageKey: page.page_key, limit: versionsLimitRef.current,
      })
      setVersions(result.versions || [])
      setVersionsHasMore(!!result.has_more)
      setVersionsTotal(typeof result.total === 'number' ? result.total : null)
    } catch (err) {
      console.error('Failed to load versions:', err)
      if (!opts?.more) setVersions([])
    } finally {
      setVersionsLoading(false)
      setVersionsLoadingMore(false)
    }
  }, [projectId, page.page_key, versionsHasMore, versionsLoadingMore])

  // 查看版本快照（wiki:getVersion 全行含 content）：只读展示，再点收起
  const handleViewVersion = async (versionId: string) => {
    if (snapshot?.id === versionId) {
      setSnapshot(null)
      return
    }
    const reqId = ++snapshotReqRef.current
    try {
      const result: any = await window.electronAPI.invoke('wiki:getVersion', { projectId, versionId })
      if (snapshotReqRef.current !== reqId) return
      if (!result?.version) {
        toast(t('project.wiki.versionLoadFailed', { defaultValue: '快照加载失败' }), 'error')
        return
      }
      setSnapshot(result.version)
    } catch (err) {
      console.error('Failed to load version snapshot:', err)
      if (snapshotReqRef.current === reqId) {
        toast(t('project.wiki.versionLoadFailed', { defaultValue: '快照加载失败' }), 'error')
      }
    }
  }

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
          {/* 富化链接（§7.2.4）：LLM 建议候选 + 确定性文本替换 */}
          <button
            className={s.editBtn}
            onClick={() => void handleEnrich()}
            disabled={enriching}
            title={t('project.wiki.enrichHint', { defaultValue: '让 AI 识别可建立关联的页面并自动补全 [[链接]]' })}
          >
            {enriching ? <Loader2 size={12} className={s.spin} /> : <Wand2 size={12} />}
            {t('project.wiki.enrichBtn', { defaultValue: '补全链接' })}
          </button>
          {/* 删除页面（§5.3）：级联清理 + 不可撤销，二次确认 */}
          <button
            className={s.readerDeleteBtn}
            onClick={() => setShowDeleteConfirm(true)}
            disabled={deleting}
            title={t('project.wiki.deleteBtn', { defaultValue: '删除' })}
          >
            <Trash2 size={12} /> {t('project.wiki.deleteBtn', { defaultValue: '删除' })}
          </button>
        </div>
      </div>

      {find.findOpen && (
        <FindBar
          inputRef={find.inputRef}
          query={find.findQuery}
          count={find.matches.length}
          current={find.safeIdx}
          onQueryChange={find.setFindQuery}
          onPrev={() => find.gotoMatch(find.safeIdx - 1)}
          onNext={() => find.gotoMatch(find.safeIdx + 1)}
          onClose={find.closeFind}
        />
      )}

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

      {/* 正文区（V2 E3）：台账页时间轴形态（条目流替代 Markdown，首拉空/失败回落
          原文）；其余页 Markdown（正文查找扫描根，ref 供 useMarkdownBlockFind 使用） */}
      {isLedger && !ledgerFallback ? (
        <div className={s.markdownBody}>
          <WikiLedgerTimeline
            projectId={projectId}
            pageKey={page.page_key}
            onFallback={() => setLedgerFallback(true)}
          />
        </div>
      ) : (
        <div className={s.markdownBody} ref={bodyRef}>
          <Markdown content={body} />
        </div>
      )}

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

      {/* Version history panel（§7.1，listVersions/getVersion）：展开懒加载，行内查看只读快照 */}
      <details
        className={s.pageChangelog}
        onToggle={(e) => {
          if ((e.currentTarget as HTMLDetailsElement).open && versions === null) void loadVersions()
        }}
      >
        <summary className={s.collapsibleSummary}>
          <History size={12} />
          {t('project.wiki.versionsTitle', { defaultValue: '历史版本' })}
          {versions !== null && ` (${versionsTotal ?? versions.length})`}
        </summary>
        {versionsLoading && versions === null && (
          <div className={s.versionsHint}><Loader2 size={14} className={s.spin} /></div>
        )}
        {versions !== null && versions.length === 0 && (
          <p className={s.versionsHint}>{t('project.wiki.versionsEmpty', { defaultValue: '暂无版本记录' })}</p>
        )}
        {versions !== null && versions.length > 0 && (
          <div className={s.miniTimeline}>
            {versions.map((v) => (
              <div key={v.id} className={s.versionItem}>
                <span className={s.versionBadge}>v{v.version}</span>
                <span className={s.versionItemTime}>{formatChangelogTime(v.created_at, i18n.language)}</span>
                <span className={s.versionItemSummary}>{v.change_summary || '—'}</span>
                <button
                  className={s.versionViewBtn}
                  onClick={() => void handleViewVersion(v.id)}
                >
                  {snapshot?.id === v.id
                    ? t('project.wiki.versionHide', { defaultValue: '收起' })
                    : t('project.wiki.versionView', { defaultValue: '查看' })}
                </button>
              </div>
            ))}
            {versionsHasMore && (
              <button
                className={s.loadMoreBtn}
                onClick={() => void loadVersions({ more: true })}
                disabled={versionsLoadingMore}
              >
                {versionsLoadingMore ? t('common.loading') : t('common.loadMore')}
              </button>
            )}
          </div>
        )}
        {snapshot && (
          <div className={s.versionSnapshot}>
            <div className={s.versionSnapshotHeader}>
              <span>
                {t('project.wiki.versionSnapshotTitle', { version: snapshot.version, defaultValue: 'v{{version}} 快照（只读）' })}
              </span>
              <button
                className={s.versionSnapshotClose}
                onClick={() => setSnapshot(null)}
                title={t('common.close', { defaultValue: '关闭' })}
              >
                <X size={12} />
              </button>
            </div>
            <div className={s.versionSnapshotBody}>
              <Markdown content={parseFrontmatter(snapshot.content || '').body} />
            </div>
          </div>
        )}
      </details>

      {/* 删除确认（§5.3：级联清理不可撤销，recompile 确认框同款） */}
      {showDeleteConfirm && (
        <div className={s.confirmOverlay} onClick={() => !deleting && setShowDeleteConfirm(false)}>
          <div className={s.confirmDialog} onClick={(e) => e.stopPropagation()}>
            <div className={s.confirmHeader}>
              <Trash2 size={16} />
              <span>{t('project.wiki.deleteConfirmTitle', { defaultValue: '删除页面' })}</span>
            </div>
            <div className={s.confirmBody}>
              {t('project.wiki.deleteConfirmBody', {
                title: page.title,
                defaultValue: `将删除「${page.title}」及其反向链接条目、版本历史等关联数据。此操作不可撤销，确定删除？`,
              })}
            </div>
            <div className={s.confirmActions}>
              <button className={s.confirmCancelBtn} onClick={() => setShowDeleteConfirm(false)}>
                {t('common.cancel', { defaultValue: '取消' })}
              </button>
              <button className={s.confirmDangerBtn} onClick={() => void handleDelete()} disabled={deleting}>
                {deleting && <Loader2 size={13} className={s.spin} />}
                {t('project.wiki.deleteBtn', { defaultValue: '删除' })}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
