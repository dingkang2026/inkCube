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

/** 工作区图片扩展名 → MIME（与 tasks.py image_exts 同构；b64 端点响应不含 mime 字段，
 * SVG 必须标 image/svg+xml 才走 SVGImage 解码路径，否则静默破图） */
const EXT_MIME: Record<string, string> = {
  png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', gif: 'image/gif',
  svg: 'image/svg+xml', webp: 'image/webp', bmp: 'image/bmp', ico: 'image/x-icon',
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
  // ── md 渲染模式的工作区图片解析（根因修复 2026-10-21）──
  // LLM 产出的 md 用裸相对路径引用工作区图片（imgs/x.png）：既被 SafeImage 的 CSP
  // 白名单误判为外链，放行后 Electron 相对路径基座也指不到工作区。走既有
  // tasks:readFile(b64) 通道转 data: URI（端点自带工作区前缀校验 + 50MB 上限），
  // 不扩 inkcube:// 协议白名单（避免把任意自定义目录任务暴露给渲染进程）；
  // md→docx 走后端 pandoc 直读磁盘，与本映射无关，不改源文件。
  const [workspaceImages, setWorkspaceImages] = useState<Record<string, string>>({})
  const workspaceImagesRef = useRef(workspaceImages)
  workspaceImagesRef.current = workspaceImages
  const pendingImagesRef = useRef(new Set<string>())
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

  // md 渲染模式查找自动切源码：查找对象是文件源文本
  const handleFindQueryChange = (v: string) => {
    if (isMarkdown && mdRendered && v) setMdRendered(false)
    find.setFindQuery(v)
  }

  // 相对路径图片 → 工作区绝对路径 → b64 通道拉取 → data: URI（ResolveImage 契约三态）
  // 越界判定不在本地做（review 2026-10-21）：../ 越出 md 目录/根相对 / 的「工作区根」语义前端
  // 不可得，统一按 md 目录基座归一后交服务端裁决（端点前缀校验为真边界），越界/缺失 → 失败占位可诊断。
  const resolveTaskImage = useCallback((src: string): string | undefined | null => {
    if (!absPath) return null // md 路径未知（加载中）：落回 SafeImage 原判定，加载完成后重渲染接管
    if (/^[a-z][a-z0-9+.-]*:/i.test(src) || src.startsWith('//')) return null // 带 scheme：原判定
    // 裸相对 / ./ / ../ 相对路径 → 相对 md 所在目录归一化（前导 / 的空段被跳过，语义退化为 md 目录相对）
    const baseDir = absPath.replace(/\\/g, '/').split('/').slice(0, -1)
    const segs = [...baseDir]
    for (const part of src.split(/[?#]/)[0].replace(/\\/g, '/').split('/')) {
      if (!part || part === '.') continue
      if (part === '..') { segs.pop(); continue }
      segs.push(part)
    }
    const abs = segs.map((seg) => { try { return decodeURIComponent(seg) } catch { return seg } }).join('/')
    if (!abs) return null // 退化路径（../ pop 至盘符根）：交回原判定
    const hit = workspaceImagesRef.current[abs]
    if (hit) return hit
    if (pendingImagesRef.current.has(abs)) return undefined // 加载中/已失败：占位（防重试风暴）
    pendingImagesRef.current.add(abs)
    // path 传图片绝对路径：端点 (workspace / path).resolve() 对绝对入参即其本身，
    // 工作区前缀校验照常生效（越界返回 PATH_OUTSIDE_WORKSPACE）
    void (async () => {
      try {
        const res = await window.electronAPI.invoke('tasks:readFile', { taskId, path: abs, b64: true }) as {
          type?: string; data?: string; error?: string
        }
        if (!res.error && res.type === 'file' && res.data) {
          const mime = EXT_MIME[abs.split('.').pop()?.toLowerCase() || ''] || 'application/octet-stream'
          setWorkspaceImages((prev) => ({ ...prev, [abs]: `data:${mime};base64,${res.data}` }))
        } else {
          // 失败保持占位（pendingImagesRef 已拦截重复请求）：留痕供诊断（原始 src + 归一化路径 + 原因）
          console.warn('md image resolve failed:', src, '→', abs, res.error || res)
        }
      } catch (err) {
        console.warn('md image resolve failed:', src, '→', abs, err)
      }
    })()
    return undefined
  }, [absPath, taskId])

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
    setWorkspaceImages({}) // 图片缓存同批失效，避免切换文件后串图
    pendingImagesRef.current.clear()
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
            <Markdown content={content} resolveImage={resolveTaskImage} />
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
