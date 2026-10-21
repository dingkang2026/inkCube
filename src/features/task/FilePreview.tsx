import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { Eye, FileText, ImageOff, Package, Upload, X } from 'lucide-react'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneLight } from 'react-syntax-highlighter/dist/esm/styles/prism'
import { Markdown } from '../../components/Markdown'
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

  const loadContent = useCallback(async () => {
    if (!taskId || !filePath) return
    setLoading(true)
    setError('')
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
  }, [loadContent])

  const handleOpenFolder = useCallback(() => {
    if (absPath) {
      window.electronAPI.invoke('shell:showItemInFolder', absPath)
    }
  }, [absPath])

  return (
    <div className={styles.panel}>
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
        {taskId && (
          <button className={styles.toggleBtn} onClick={() => setImporting(true)} title={t('task.import.title')}>
            <Upload size={14} />
          </button>
        )}
        <button className={styles.closeBtn} onClick={onClose} title={t('task.closePreview')}>
          <X size={14} />
        </button>
      </div>
      <div className={styles.body}>
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
            <button className={styles.openFolderBtn} onClick={handleOpenFolder}>
              {t('task.openContainingFolder')}
            </button>
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
            wrapLines={false}
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
