import { useState, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { Wrench, FileText, Repeat, ScrollText, Folder } from 'lucide-react'
import { ResizeHandle } from '../../components/layout/ResizeHandle'
import { useResizeHandle } from '../../hooks/useResizeHandle'
import { TaskChatView } from './TaskChatView'
import { FileTree } from './FileTree'
import { TaskLogViewer } from './TaskLogViewer'
import type { TaskMessage, StreamStep, PlanData } from '../../types'
import type { ShellConfirmData, SkillConfirmData } from './useTaskChat'
import styles from './TaskPage.module.css'

const TREE_WIDTH = 200
const PREVIEW_WIDTH = 350
const HANDLE_WIDTH = 4

interface TaskPageProps {
  taskId: string | null
  taskTitle?: string
  taskMode?: 'coding' | 'general'
  hasActiveTask: boolean
  messages: TaskMessage[]
  isGenerating: boolean
  streamContent: string
  streamSteps: StreamStep[]
  streamReasoning?: string
  streamIteration?: number
  streamIterationThoughts?: Map<number, string>
  streamIterationReasoning?: Map<number, string>
  planData?: PlanData | null
  pendingShellConfirm?: ShellConfirmData | null
  pendingSkillConfirm?: SkillConfirmData | null
  onSendMessage: (content: string) => void | Promise<void>
  onStopGenerate: () => void
  onPauseTask?: () => void
  onShellConfirm?: (confirmed: boolean) => void
  onSkillConfirm?: (confirmed: boolean) => void
  onResumeTask?: () => void
  canResume?: boolean
  onFilesUploaded?: () => void
  onSwitchMode?: (mode: 'coding' | 'general') => void
}

export function TaskPage({
  taskId,
  taskTitle,
  taskMode = 'coding',
  hasActiveTask,
  messages,
  isGenerating,
  streamContent,
  streamSteps,
  streamReasoning,
  streamIteration,
  streamIterationThoughts,
  streamIterationReasoning,
  planData,
  pendingShellConfirm,
  pendingSkillConfirm,
  onSendMessage,
  onStopGenerate,
  onPauseTask,
  onShellConfirm,
  onSkillConfirm,
  onResumeTask,
  canResume,
  onFilesUploaded,
  onSwitchMode,
}: TaskPageProps) {
  const { t } = useTranslation()
  const filePanel = useResizeHandle(TREE_WIDTH)
  const [filePanelCollapsed, setFilePanelCollapsed] = useState(false)
  const [previewVisible, setPreviewVisible] = useState(false)
  const [previewWidth, setPreviewWidth] = useState(PREVIEW_WIDTH)
  const [logViewerVisible, setLogViewerVisible] = useState(false)

  // Outer resize: when preview visible → adjust preview; otherwise → adjust tree
  const handleResize = useCallback(
    (delta: number) => {
      if (previewVisible) {
        setPreviewWidth((prev) => Math.max(50, prev - delta))
      } else {
        filePanel.handleResize(-delta)
      }
    },
    [previewVisible, filePanel],
  )

  // Inner resize: tree ↔ preview trade-off, total stays constant
  const handleInnerResize = useCallback(
    (delta: number) => {
      if (!previewVisible) return
      setPreviewWidth((prev) => {
        const newPreview = Math.max(50, prev + delta)
        const actualDelta = newPreview - prev
        // Shrink/grow tree by the same amount (inverse)
        filePanel.handleResize(-actualDelta)
        return newPreview
      })
    },
    [previewVisible, filePanel],
  )

  const toggleFilePanel = useCallback(() => {
    setFilePanelCollapsed((prev) => {
      if (!prev) {
        // Collapsing: reset preview state
        setPreviewVisible(false)
      }
      return !prev
    })
  }, [])

  const handleFileSelect = useCallback((file: unknown) => {
    setPreviewVisible(file !== null)
  }, [])

  // Panel width: tree only vs tree + handle + preview
  const panelWidth = filePanelCollapsed
    ? 0
    : previewVisible
      ? filePanel.width + HANDLE_WIDTH + previewWidth
      : filePanel.width

  return (
    <div className={styles.page}>
      {/* ── Top bar: spans full width ── */}
      <div className={styles.topBar}>
        <span className={styles.title}>{taskTitle || t('task.newTask')}</span>
        <div className={styles.topBarRight}>
          <button
            className={`${styles.modeBadge} ${taskMode === 'general' ? styles.modeGeneral : styles.modeCoding}`}
            onClick={() => onSwitchMode?.(taskMode === 'coding' ? 'general' : 'coding')}
            title={t('task.switchMode')}
          >
            {taskMode === 'coding' ? <Wrench size={14} /> : <FileText size={14} />} {taskMode === 'coding' ? t('task.modeCoding') : t('task.modeGeneral')}
            {onSwitchMode && <Repeat size={12} className={styles.modeSwitchIcon} />}
          </button>
          {hasActiveTask && (
            <>
              <button
                className={styles.fileToggle}
                onClick={() => setLogViewerVisible(true)}
                title={t('task.viewExecLog', '查看执行日志')}
              >
                <ScrollText size={14} />
              </button>
              <button
              className={`${styles.fileToggle} ${!filePanelCollapsed ? styles.fileToggleActive : ''}`}
              onClick={toggleFilePanel}
              title={filePanelCollapsed ? t('task.showPanel') : t('task.collapsePanel')}
            >
              <Folder size={14} />
              </button>
            </>
          )}
        </div>
      </div>

      {/* ── Below: Chat + File panel (side by side) ── */}
      <div className={styles.content}>
        <TaskChatView
          taskId={taskId}
          hasActiveTask={hasActiveTask}
          messages={messages}
          isGenerating={isGenerating}
          streamContent={streamContent}
          streamSteps={streamSteps}
          streamReasoning={streamReasoning}
          streamIteration={streamIteration}
          streamIterationThoughts={streamIterationThoughts}
          streamIterationReasoning={streamIterationReasoning}
          planData={planData}
          pendingShellConfirm={pendingShellConfirm}
          pendingSkillConfirm={pendingSkillConfirm}
          onSendMessage={onSendMessage}
          onStopGenerate={onStopGenerate}
          onPauseTask={onPauseTask}
          onShellConfirm={onShellConfirm}
          onSkillConfirm={onSkillConfirm}
          onResumeTask={onResumeTask}
          canResume={canResume}
          onFilesUploaded={onFilesUploaded}
        />
        {hasActiveTask && !filePanelCollapsed && (
          <>
            <ResizeHandle
              onResize={handleResize}
              onDoubleClick={() => filePanel.resetWidth()}
            />
            <div
              className={styles.filePanelWrap}
              style={{ width: panelWidth }}
            >
              <FileTree
                taskId={taskId}
                treeWidth={filePanel.width}
                previewWidth={previewWidth}
                onFileSelect={handleFileSelect}
                onInnerResize={handleInnerResize}
              />
            </div>
          </>
        )}
      </div>

      {/* Execution Log Viewer overlay */}
      <TaskLogViewer
        taskId={taskId}
        visible={logViewerVisible}
        onClose={() => setLogViewerVisible(false)}
      />
    </div>
  )
}
