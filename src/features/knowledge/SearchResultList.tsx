/** src/features/knowledge/SearchResultList.tsx */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { SearchResult } from './types'
import { Markdown } from '../../components/Markdown'
import s from './SearchResultList.module.css'

interface SearchResultListProps {
  results: SearchResult[]
  isRecent?: boolean
}

/** Content type icons for left-side indicator */
const CONTENT_TYPE_ICONS: Record<string, string> = {
  heading: 'H',
  paragraph: '¶',
  table: '⊞',
  figure: '🖼',
  caption: '◎',
  list: '≡',
}

/** Content type label keys — resolved via i18n at render time */
const CONTENT_TYPE_KEYS: Record<string, string> = {
  heading: 'knowledge.ctHeading',
  paragraph: 'knowledge.ctParagraph',
  table: 'knowledge.ctTable',
  figure: 'knowledge.ctFigure',
  caption: 'knowledge.ctCaption',
  list: 'knowledge.ctList',
}

export function SearchResultList({ results, isRecent }: SearchResultListProps) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState<number | null>(null)

  if (results.length === 0) return null

  return (
    <div className={s.container}>
      <div className={s.header}>
        <span className={s.count}>
          {isRecent
            ? t('knowledge.recentChunks', { count: results.length })
            : t('knowledge.searchResults', { count: results.length })}
        </span>
      </div>
      <div className={s.list}>
        {results.map((result, index) => {
          const isExpanded = expanded === index
          // Display strategy (2.2 §4.3): structured markdown renders as HTML
          // (tables); recent chunks stay plain text while collapsed and only
          // render the full markdown on expand.
          const mdContent = result.metadata?.markdown_content
          const isTable = result.metadata?.content_type === 'table'
          const fullBody = mdContent || result.content
          const previewBody = isRecent ? result.content : fullBody
          const contentPreview = previewBody.length > 300
            ? previewBody.slice(0, 300) + '…'
            : previewBody
          const typeIcon = CONTENT_TYPE_ICONS[result.metadata?.content_type] || ''
          // Markdown rendering: expanded shows the full body; collapsed search
          // tables show a markdown preview; recent chunks stay plain text.
          const renderMarkdownBody = !!mdContent && (isExpanded || (!isRecent && isTable))
          // Normalize score to 0-100% for bar width
          const scorePercent = Math.round((result._score || 0) * 100)

          return (
            <div key={result.id} className={s.card}>
              <div className={s.cardLeft}>
                <span className={s.typeIcon} title={result.metadata?.content_type}>
                  {typeIcon}
                </span>
              </div>
              <div className={s.cardBody}>
                <div className={s.cardHeader}>
                  {!isRecent && <span className={s.rank}>#{index + 1}</span>}
                  <span className={s.filename}>{result.metadata?.filename || '—'}</span>
                  {result.metadata?.content_type && (
                    <span className={s.typeBadge}>
                      {CONTENT_TYPE_KEYS[result.metadata.content_type]
                        ? t(CONTENT_TYPE_KEYS[result.metadata.content_type])
                        : result.metadata.content_type}
                    </span>
                  )}
                  {result.metadata?.page != null && (
                    <span className={s.pageInfo}>p.{result.metadata.page}</span>
                  )}
                </div>
                {result.metadata?.section_path && (
                  <div className={s.sectionPath}>{result.metadata.section_path}</div>
                )}
                <div className={`${s.content} ${isTable ? s.tableContent : ''}`}>
                  {renderMarkdownBody ? (
                    <Markdown content={fullBody} className={s.markdownRendered} />
                  ) : isExpanded ? (
                    <>{fullBody}</>
                  ) : (
                    <>{contentPreview}</>
                  )}
                </div>
                <div className={s.cardFooter}>
                  {fullBody.length > 300 && (
                    <button className={s.expandBtn} onClick={() => setExpanded(isExpanded ? null : index)}>
                      {isExpanded ? t('knowledge.collapse') : t('knowledge.expand')}
                    </button>
                  )}
                  {!isRecent && (
                    <div className={s.scoreBar} title={`Score: ${scorePercent}%`}>
                      <div className={s.scoreFill} style={{ width: `${scorePercent}%` }} />
                    </div>
                  )}
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
