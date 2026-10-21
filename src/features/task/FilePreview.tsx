import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Eye, FileText, FolderOpen, ImageOff, Package, Search, Upload, X } from 'lucide-react'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneLight } from 'react-syntax-highlighter/dist/esm/styles/prism'
import { Markdown } from '../../components/Markdown'
import { FindBar } from '../../components/FindBar'
import { useInFileFind, scrollElementToCenter } from '../../hooks/useInFileFind'
import { formatSize } from './utils'
import { ImportTargetDialog } from './ImportTargetDialog'
import styles from './FilePreview.module.css'

interface FilePreviewProps {
  taskId: string | null
  filePath: string
  fileName: string
  fileSize: number
  onClose: () => void
}

// Map file extension to syntax highlighter language
const EXT_TO_LANG: Record<string, string> = {
  py: 'python',
  js: 'javascript',
  jsx: 'jsx',
  ts: 'typescript',
  tsx: 'tsx',
  json: 'json',
  yaml: 'yaml',
  yml: 'yaml',
  css: 'css',
  scss: 'scss',
  less: 'less',
  html: 'html',
  htm: 'html',
  xml: 'xml',
  svg: 'svg',
  sh: 'bash',
  bash: 'bash',
  zsh: 'bash',
  bat: 'batch',
  ps1: 'powershell',
  sql: 'sql',
  md: 'markdown',
  go: 'go',
  rs: 'rust',
  java: 'java',
  kt: 'kotlin',
  c: 'c',
  cpp: 'cpp',
  h: 'c',
  hpp: 'cpp',
  cs: 'csharp',
  rb: 'ruby',
  php: 'php',
  swift: 'swift',
  lua: 'lua',
  r: 'r',
  dart: 'dart',
  toml: 'toml',
  ini: 'ini',
  dockerfile: 'docker',
  makefile: 'makefile',
  cmake: 'cmake',
  gradle: 'gradle',
}

function getLanguage(ext: string): string {
  return EXT_TO_LANG[ext] || 'text'
}

/** 文件内查找：返回每个命中所在行号（0-based，行内多次命中逐个计入，非重叠匹配） */
export function computeMatches(content: string, query: string): number[] {
  const q = query.toLowerCase()
  if (!q) return []
  const out: number[] = []
  content.split('\n').forEach((line, i) => {
    const hay = line.toLowerCase()
    let idx = hay.indexOf(q)
    while (idx !== -1) {
      out.push(i)
      idx = hay.indexOf(q, idx + q.length)
    }
  })
  return out
}

export function FilePreview({ taskId, filePath, fileName, fileSize, onClose }: FilePreviewProps) {
  const { t } = useTranslation()
  const [loading, setLoading] = useState(true)
  const [content, setContent] = useState<string>('')
  const [fileType, setFileType] = useState<'text' | 'image' | 'binary'>('text')
  const [imageData, setImageData] = useState<string>('')
  const [imageMime, setImageMime] = useState<string>('')
  const [error, setError] = useState<string>('')
  const [imageError, setImageError] = useState(false)
  const [absPath, setAbsPath] = useState<string>('')
  const [mdRendered, setMdRendered] = useState(true)
  const [importing, setImporting] = useState(false)
  const ext = fileName.split('.').pop()?.toLowerCase() || ''
  const isMarkdown = ext === 'md'
  // ── 文件内查找（关键词定位）：状态机 useInFileFind，命中行高亮为本场景私有 ──
  const panelRef = useRef<HTMLDivElement>(null)
  const bodyRef = useRef<HTMLDivElement>(null)

  const computeLineMatches = useCallback(
    (q: string) => (fileType === 'text' ? computeMatches(content, q) : []),
    [content, fileType],
  )

  // 滚动容器使目标行居中（不用 scrollIntoView：避免连带滚动外层页面）
  const scrollToLine = useCallback((line: number) => {
    const container = bodyRef.current
    if (!container) return
    const el = container.querySelector(`[data-findline="${line}"]`)
    if (el) scrollElementToCenter(container, el)
  }, [])

  const find = useInFileFind({ compute: computeLineMatches, locate: scrollToLine })
  const { findOpen, findQuery, matches, safeIdx, inputRef, openFind, closeFind, gotoMatch } = find
  const matchLineSet = useMemo(() => new Set(matches), [matches])

  // md 渲染模式下开始查找时自动切回源码：查找对象是文件源文本
  const handleFindQueryChange = (v: string) => {
    if (isMarkdown && mdRendered && v) setMdRendered(false)
    find.setFindQuery(v)
  }

  // Ctrl/Cmd+F 仅在焦点位于预览面板内时接管（不污染全局快捷键）
  useEffect(() => {
    const root = panelRef.current
    if (!root) return
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === 'f'
        && root.contains(document.activeElement)) {
        e.preventDefault()
        e.stopPropagation()
        openFind()
      }
    }
    root.addEventListener('keydown', onKeyDown)
    return () => root.removeEventListener('keydown', onKeyDown)
  }, [openFind])

  // 预览打开即聚焦面板：让 Ctrl+F 立即可用
  useEffect(() => {
    panelRef.current?.focus()
  }, [])

  const loadContent = useCallback(async () => {
    if (!taskId || !filePath) return
    setLoading(true)
    setError('')
    setAbsPath('') // 先失效上一文件的路径：加载失败时头部开目录按钮不得指向旧文件
    try {
      const res = await window.electronAPI.invoke('tasks:readFile', { taskId, path: filePath }) as {
        type: string; content?: string; data?: string; mime?: string; size?: number; ext?: string; error?: string
      }
      if (res.error) {
        setError(res.error)
      } else {
        setFileType(res.type as 'text' | 'image' | 'binary')
        setAbsPath((res as any).abs_path || '')
        if (res.type === 'text' && res.content !== undefined) {
          setContent(res.content)
        } else if (res.type === 'image') {
          setImageData(res.data || '')
          setImageMime(res.mime || 'image/png')
          setImageError(false)
        }
      }
    } catch (err) {
      setError(String(err))
    } finally {
      setLoading(false)
    }
  }, [taskId, filePath])

  useEffect(() => {
    loadContent()
    setMdRendered(true) // Reset to rendered mode when file changes
    closeFind()
  }, [loadContent, closeFind])

  const handleOpenFolder = useCallback(() => {
    if (absPath) {
      window.electronAPI.invoke('shell:showItemInFolder', absPath)
    }
  }, [absPath])

  return (
    <div className={styles.panel} ref={panelRef} tabIndex={-1} onMouseDown={() => panelRef.current?.focus()}>
      <div className={styles.header}>
        <span className={styles.fileName} title={fileName}>{fileName}</span>
        <span className={styles.fileSize}>{formatSize(fileSize)}</span>
        {isMarkdown && fileType === 'text' && (
          <button
            className={styles.toggleBtn}
            onClick={() => setMdRendered(!mdRendered)}
            title={mdRendered ? t('task.viewSource') : t('task.viewRendered')}
          >
            {mdRendered ? <FileText size={14} /> : <Eye size={14} />}
          </button>
        )}
        {fileType === 'text' && (
          <button className={styles.toggleBtn} onClick={openFind} title={t('task.findInFile')}>
            <Search size={14} />
          </button>
        )}
        {taskId && (
          <button className={styles.toggleBtn} onClick={() => setImporting(true)} title={t('task.import.title')}>
            <Upload size={14} />
          </button>
        )}
        {/* 打开所在目录：系统文件管理器定位文件；abs_path 由 tasks:readFile 响应携带，加载完成前置灰 */}
        <button
          className={styles.toggleBtn}
          onClick={handleOpenFolder}
          disabled={!absPath}
          title={t('task.openContainingFolder')}
        >
          <FolderOpen size={14} />
        </button>
        <button className={styles.closeBtn} onClick={onClose} title={t('task.closePreview')}>
          <X size={14} />
        </button>
      </div>
      {findOpen && fileType === 'text' && (
        <FindBar
          inputRef={inputRef}
          query={findQuery}
          count={matches.length}
          current={safeIdx}
          onQueryChange={handleFindQueryChange}
          onPrev={() => gotoMatch(safeIdx - 1)}
          onNext={() => gotoMatch(safeIdx + 1)}
          onClose={closeFind}
        />
      )}
      <div className={styles.body} ref={bodyRef}>
        {loading ? (
          <div className={styles.loading}>{t('task.loadingFile')}</div>
        ) : error ? (
          <div className={styles.error}>{t('task.loadFailed')}: {error}</div>
        ) : fileType === 'image' ? (
          <div className={styles.imageWrap}>
            {imageError ? (
              <div className={styles.binaryInfo}>
                <div className={styles.binaryIcon}><ImageOff size={40} /></div>
                <div>{t('task.imageLoadFailed')}</div>
                <div className={styles.binarySize}>{imageMime} · {formatSize(fileSize)}</div>
              </div>
            ) : (
              <img
                src={`data:${imageMime};base64,${imageData}`}
                alt={fileName}
                onError={() => {
                  console.error('Image load failed:', { mime: imageMime, dataLen: imageData.length })
                  setImageError(true)
                }}
              />
            )}
          </div>
        ) : fileType === 'binary' ? (
          <div className={styles.binaryInfo}>
            <div className={styles.binaryIcon}><Package size={40} /></div>
            <div>{t('task.binaryFileHint')}</div>
            <div className={styles.binarySize}>{formatSize(fileSize)}</div>
          </div>
        ) : isMarkdown && mdRendered ? (
          <div className={styles.markdownBody}>
            <Markdown content={content} />
          </div>
        ) : (
          <SyntaxHighlighter
            language={getLanguage(ext)}
            style={oneLight}
            showLineNumbers
            wrapLines
            // 查找定位：命中行整行高亮 + data-findline 供滚动定位（词级高亮因 token 切碎不可靠，不做）
            lineProps={(lineNumber: number) => {
              const line = lineNumber - 1
              if (!matchLineSet.has(line)) return {}
              const isCurrent = matches[safeIdx] === line
              return {
                'data-findline': line,
                style: { background: isCurrent ? 'rgba(255, 152, 0, 0.4)' : 'rgba(255, 200, 0, 0.22)' },
              } as any
            }}
            customStyle={{
              margin: 0,
              padding: '12px 16px',
              borderRadius: 0,
              flex: 1,
              minHeight: '100%',
              overflow: 'auto',
            }}
            lineNumberStyle={{ minWidth: '2.5em', paddingRight: '1em', color: 'var(--text-muted)' }}
          >
            {content}
          </SyntaxHighlighter>
        )}
      </div>

      {importing && taskId && (
        <ImportTargetDialog
          taskId={taskId}
          path={filePath}
          filename={fileName}
          onDone={() => setImporting(false)}
          onClose={() => setImporting(false)}
        />
      )}
    </div>
  )
}
