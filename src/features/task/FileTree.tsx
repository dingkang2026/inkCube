import { useState, useEffect, useCallback, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { FilePreview } from './FilePreview'
import { ImportTargetDialog } from './ImportTargetDialog'
import { ScheduleFormModal } from '../schedule/ScheduleFormModal'
import { CalendarClock, Folder, Loader2, RefreshCw, Upload } from 'lucide-react'
import { fileIcon, formatSize } from './utils'
import styles from './FileTree.module.css'

interface FileNode {
  name: string
  type: 'file' | 'dir'
  size?: number
  children?: FileNode[]
  /** relative path from workspace root */
  path?: string
}

interface FileTreeProps {
  taskId: string | null
  treeWidth?: number
  previewWidth?: number
  onFileSelect?: (file: SelectedFile | null) => void
  onInnerResize?: (delta: number) => void
}

interface SelectedFile {
  path: string
  name: string
  size: number
}

// ── Tree Node ──

function TreeNode({
  node, depth, selectedPath, onFileClick,
}: {
  node: FileNode
  depth: number
  selectedPath: string | null
  onFileClick: (file: SelectedFile) => void
}) {
  const [expanded, setExpanded] = useState(depth < 1)

  if (node.type === 'dir') {
    return (
      <div className={styles.node}>
        <div
          className={styles.nodeRow}
          style={{ paddingLeft: 8 + depth * 16 }}
          onClick={() => setExpanded(!expanded)}
        >
          <span className={styles.arrow}>{expanded ? '▾' : '▸'}</span>
          <span className={styles.icon}><Folder size={14} /></span>
          <span className={styles.name}>{node.name}</span>
        </div>
        {expanded && node.children?.map((child) => (
          <TreeNode
            key={child.path || child.name}
            node={child}
            depth={depth + 1}
            selectedPath={selectedPath}
            onFileClick={onFileClick}
          />
        ))}
      </div>
    )
  }

  const filePath = node.path || node.name
  const isSelected = selectedPath === filePath
  const FileIcon = fileIcon(node.name)

  return (
    <div className={styles.node}>
      <div
        className={`${styles.nodeRow} ${styles.fileRow} ${isSelected ? styles.fileRowSelected : ''}`}
        style={{ paddingLeft: 8 + depth * 16 + 16 }}
        onClick={() => onFileClick({ path: filePath, name: node.name, size: node.size || 0 })}
      >
        <span className={styles.icon}><FileIcon size={14} /></span>
        <span className={styles.name}>{node.name}</span>
        {node.size !== undefined && (
          <span className={styles.size}>{formatSize(node.size)}</span>
        )}
      </div>
    </div>
  )
}

/** Recursively assign path to each node */
function assignPaths(nodes: FileNode[], parentPath = ''): FileNode[] {
  return nodes.map((node) => {
    const path = parentPath ? `${parentPath}/${node.name}` : node.name
    if (node.type === 'dir' && node.children) {
      return { ...node, path, children: assignPaths(node.children, path) }
    }
    return { ...node, path }
  })
}

// ── Main FilePanel ──

export function FileTree({ taskId, treeWidth = 200, previewWidth = 350, onFileSelect, onInnerResize }: FileTreeProps) {
  const { t } = useTranslation()
  const [tree, setTree] = useState<FileNode[]>([])
  const [loading, setLoading] = useState(false)
  const [selectedFile, setSelectedFile] = useState<SelectedFile | null>(null)
  const [importing, setImporting] = useState(false)
  // 渠道③「设为定时」（2.11 §9.1）：任务级入口，refinePrompt 从对话提炼订阅内容
  const [scheduling, setScheduling] = useState(false)

  const handleFileClick = useCallback((file: SelectedFile) => {
    setSelectedFile(file)
    onFileSelect?.(file)
  }, [onFileSelect])

  const handleClosePreview = useCallback(() => {
    setSelectedFile(null)
    onFileSelect?.(null)
  }, [onFileSelect])

  // ── Inner resize handle (between tree and preview) ──
  const resizeRef = useRef<{ startX: number } | null>(null)

  const handleInnerResize = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    resizeRef.current = { startX: e.clientX }
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'

    const onMove = (ev: MouseEvent) => {
      if (!resizeRef.current) return
      // Dragging right = tree grows, preview shrinks
      const delta = ev.clientX - resizeRef.current.startX
      onInnerResize?.(-delta)
      resizeRef.current.startX = ev.clientX
    }
    const onUp = () => {
      resizeRef.current = null
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }, [onInnerResize])

  // ── Load tree ──
  const loadTree = useCallback(async () => {
    if (!taskId) return
    setLoading(true)
    try {
      const res = await window.electronAPI.invoke('tasks:getFiles', taskId)
      const raw = (res as { tree: FileNode[] }).tree || []
      setTree(assignPaths(raw))
    } catch (err) {
      console.error('Failed to load file tree:', err)
    } finally {
      setLoading(false)
    }
  }, [taskId])

  // Use taskId directly as dependency to avoid cross-component setState during render
  useEffect(() => {
    if (!taskId) return
    setLoading(true)
    window.electronAPI.invoke('tasks:getFiles', taskId)
      .then((res) => {
        const raw = (res as { tree: FileNode[] }).tree || []
        setTree(assignPaths(raw))
      })
      .catch((err) => {
        console.error('Failed to load file tree:', err)
      })
      .finally(() => {
        setLoading(false)
      })
  }, [taskId])

  useEffect(() => {
    const handler = () => loadTree()
    window.addEventListener('task:refresh-files', handler)
    return () => window.removeEventListener('task:refresh-files', handler)
  }, [loadTree])

  return (
    <div className={styles.panel}>
      {/* ── Left: file tree sub-panel ── */}
      <div className={styles.treePanel} style={{ width: treeWidth, flexShrink: 0 }}>
        <div className={styles.header}>
          <span className={styles.headerTitle}>{t('task.workspace')}</span>
          <button
            className={styles.iconBtn}
            disabled={!taskId}
            onClick={() => setScheduling(true)}
            title={t('task.scheduleFromTask')}
          >
            <CalendarClock size={14} />
          </button>
          <button
            className={styles.iconBtn}
            disabled={!selectedFile}
            onClick={() => setImporting(true)}
            title={t('task.import.title')}
          >
            <Upload size={14} />
          </button>
          <button className={styles.iconBtn} onClick={loadTree} title={t('task.refresh')}>
            {loading ? <Loader2 size={14} className={styles.spinning} /> : <RefreshCw size={14} />}
          </button>
        </div>
        <div className={styles.treeSection}>
          {tree.length === 0 && !loading ? (
            <div className={styles.empty}>{t('task.noFiles')}</div>
          ) : (
            tree.map((node) => (
              <TreeNode
                key={node.path || node.name}
                node={node}
                depth={0}
                selectedPath={selectedFile?.path ?? null}
                onFileClick={handleFileClick}
              />
            ))
          )}
        </div>
      </div>

      {/* ── Right: file preview sub-panel ── */}
      {selectedFile && (
        <>
          <div className={styles.innerHandle} onMouseDown={handleInnerResize} />
          <div className={styles.previewSection} style={{ width: previewWidth }}>
            <FilePreview
              taskId={taskId}
              filePath={selectedFile.path}
              fileName={selectedFile.name}
              fileSize={selectedFile.size}
              onClose={handleClosePreview}
            />
          </div>
        </>
      )}

      {importing && selectedFile && taskId && (
        <ImportTargetDialog
          taskId={taskId}
          path={selectedFile.path}
          filename={selectedFile.name}
          onDone={() => setImporting(false)}
          onClose={() => setImporting(false)}
        />
      )}

      {scheduling && taskId && (
        <ScheduleFormModal
          open
          initial={{ taskId }}
          onClose={() => setScheduling(false)}
          onSaved={() => setScheduling(false)}
        />
      )}
    </div>
  )
}
