/** src/features/settings/LogViewer.tsx — Log viewer modal dialog. */
import { useState, useEffect, useRef, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import s from './LogViewer.module.css'

type LogLevel = 'ALL' | 'ERROR' | 'WARN' | 'INFO'
type LogSource = 'ALL' | 'python' | 'electron'

interface LogInfo {
  files: Array<{
    name: string
    size: number
    sizeFormatted: string
    lines: number
    modified: string
    current?: boolean
  }>
  totalSize: number
  totalSizeFormatted: string
  totalLines: number
  maxBytes: number
  backupCount: number
}

interface LogViewerProps {
  onClose: () => void
}

export function LogViewer({ onClose }: LogViewerProps) {
  const { t } = useTranslation()
  const [logContent, setLogContent] = useState('')
  const [logInfo, setLogInfo] = useState<LogInfo | null>(null)
  const [filter, setFilter] = useState<LogLevel>('ALL')
  const [sourceFilter, setSourceFilter] = useState<LogSource>('ALL')
  const [search, setSearch] = useState('')
  const [autoScroll, setAutoScroll] = useState(true)
  const [showInfo, setShowInfo] = useState(false)
  const scrollRef = useRef<HTMLPreElement>(null)

  const loadLogs = useCallback(async () => {
    try {
      const [content, info] = await Promise.all([
        window.electronAPI.invoke('system:getLogs') as Promise<string>,
        window.electronAPI.invoke('system:getLogInfo') as Promise<LogInfo>,
      ])
      setLogContent(content || '')
      setLogInfo(info)
    } catch (err) {
      console.error('Failed to load logs:', err)
    }
  }, [])

  useEffect(() => {
    loadLogs()
    // Auto-refresh every 3 seconds
    const interval = setInterval(loadLogs, 3000)
    return () => clearInterval(interval)
  }, [loadLogs])

  // Auto-scroll to bottom
  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [logContent, autoScroll])

  // Check if line is a separator
  const isSeparator = (line: string) => line.startsWith('───') && line.endsWith('───')

  // Parse and filter log lines
  const lines = logContent.split('\n').filter(Boolean)
  const filteredLines = lines.filter(line => {
    // Skip separators in filtering (always show them)
    if (isSeparator(line)) return true

    // Source filter
    if (sourceFilter !== 'ALL') {
      if (!line.includes(`[${sourceFilter}]`)) return false
    }

    // Level filter
    if (filter !== 'ALL') {
      const levelMap: Record<LogLevel, string> = {
        ALL: '', ERROR: '[ERROR]', WARN: '[WARN', INFO: '[INFO]',
      }
      if (!line.includes(levelMap[filter])) return false
    }
    // Search filter
    if (search.trim()) {
      return line.toLowerCase().includes(search.toLowerCase())
    }
    return true
  })

  // Count actual log lines (excluding separators)
  const actualLines = filteredLines.filter(l => !isSeparator(l))
  const totalActualLines = lines.filter(l => !isSeparator(l))

  const handleCopy = () => {
    navigator.clipboard.writeText(filteredLines.join('\n'))
  }

  const handleExport = async () => {
    try {
      await window.electronAPI.invoke('system:exportLogs')
    } catch (err) {
      console.error('Export failed:', err)
    }
  }

  // Listen for app:show-logs event
  useEffect(() => {
    const handler = () => {
      // Already open, just scroll to bottom
      setAutoScroll(true)
    }
    window.addEventListener('app:show-logs', handler)
    return () => window.removeEventListener('app:show-logs', handler)
  }, [])

  return (
    <div className={s.overlay} onClick={onClose}>
      <div className={s.modal} onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className={s.header}>
          <h3 className={s.title}>{t('log.title')}</h3>
          <div className={s.headerRight}>
            {logInfo && (
              <span className={s.sizeInfo} title={t('log.sizeTooltip')}>
                📁 {logInfo.totalSizeFormatted} · {logInfo.files.length} {t('log.files')}
              </span>
            )}
            <button
              className={s.infoBtn}
              onClick={() => setShowInfo(!showInfo)}
              title={t('log.showInfo')}
            >
              ℹ️
            </button>
            <button className={s.closeBtn} onClick={onClose}>×</button>
          </div>
        </div>

        {/* Log info panel */}
        {showInfo && logInfo && (
          <div className={s.infoPanel}>
            <div className={s.infoHeader}>{t('log.fileDetails')}</div>
            {logInfo.files.map(f => (
              <div key={f.name} className={`${s.infoRow} ${f.current ? s.infoRowCurrent : ''}`}>
                <span className={s.infoName}>{f.name}</span>
                <span className={s.infoSize}>{f.sizeFormatted}</span>
                <span className={s.infoLines}>{f.lines} {t('log.lines')}</span>
                <span className={s.infoDate}>
                  {new Date(f.modified).toLocaleString()}
                </span>
              </div>
            ))}
            <div className={s.infoFooter}>
              {t('log.maxSize')}: 5 MB · {t('log.backupCount')}: {logInfo.backupCount}
            </div>
          </div>
        )}

        {/* Filter bar */}
        <div className={s.filterBar}>
          <div className={s.levelTabs}>
            {(['ALL', 'ERROR', 'WARN', 'INFO'] as LogLevel[]).map(level => (
              <button
                key={level}
                className={`${s.levelTab} ${filter === level ? s.levelTabActive : ''} ${level === 'ERROR' ? s.tabError : level === 'WARN' ? s.tabWarn : ''}`}
                onClick={() => setFilter(level)}
              >
                {t(`log.level_${level.toLowerCase()}`)}
              </button>
            ))}
          </div>
          <div className={s.sourceTabs}>
            {(['ALL', 'python', 'electron'] as LogSource[]).map(source => (
              <button
                key={source}
                className={`${s.sourceTab} ${sourceFilter === source ? s.sourceTabActive : ''}`}
                onClick={() => setSourceFilter(source)}
              >
                {source === 'ALL' ? t('log.sourceAll') : source === 'python' ? '🐍 Python' : '⚡ Electron'}
              </button>
            ))}
          </div>
          <div className={s.searchWrap}>
            <input
              type="text"
              className={s.searchInput}
              placeholder={t('log.searchPlaceholder')}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            {search && (
              <button className={s.searchClear} onClick={() => setSearch('')}>×</button>
            )}
          </div>
        </div>

        {/* Log content */}
        <pre
          className={s.logContent}
          ref={scrollRef}
          onScroll={(e) => {
            const el = e.currentTarget
            const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 30
            setAutoScroll(atBottom)
          }}
        >
          {actualLines.length === 0
            ? t('log.noLogs')
            : filteredLines.map((line, i) => {
                if (isSeparator(line)) {
                  return <div key={i} className={s.separator}>{line}</div>
                }
                return (
                  <span
                    key={i}
                    className={
                      line.includes('[ERROR]') ? s.lineError
                      : line.includes('[WARN') ? s.lineWarn
                      : line.includes('[INFO]') ? s.lineInfo
                      : ''
                    }
                  >
                    {line}{'\n'}
                  </span>
                )
              })
          }
        </pre>

        {/* Footer */}
        <div className={s.footer}>
          <span className={s.lineCount}>
            {actualLines.length} / {totalActualLines.length} {t('log.lines')}
          </span>
          <div className={s.footerActions}>
            <label className={s.autoScrollLabel}>
              <input
                type="checkbox"
                checked={autoScroll}
                onChange={(e) => setAutoScroll(e.target.checked)}
              />
              {t('log.autoScroll')}
            </label>
            <button className={s.footerBtn} onClick={loadLogs}>{t('log.refresh')}</button>
            <button className={s.footerBtn} onClick={handleCopy}>{t('log.copyAll')}</button>
            <button className={s.footerBtnPrimary} onClick={handleExport}>{t('log.export')}</button>
          </div>
        </div>
      </div>
    </div>
  )
}
