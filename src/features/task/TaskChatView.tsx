import { useState, useRef, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { FolderOpen, Wrench, Paperclip, Pause, Square, SendHorizonal, AlertTriangle, Check, SkipForward, X, Puzzle, Play } from 'lucide-react'
import { TaskMessageList } from './TaskMessageList'
import type { TaskMessage, StreamStep, PlanData } from '../../types'
import type { ShellConfirmData, SkillConfirmData } from './useTaskChat'
import { useFeatureGuard } from '../readiness/useReadiness'
import styles from './TaskChatView.module.css'

interface SlashCommand {
  name: string
  description: string
}

interface TaskChatViewProps {
  taskId: string | null
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
  onSendMessage: (content: string) => void
  onStopGenerate: () => void
  onPauseTask?: () => void
  onShellConfirm?: (confirmed: boolean, allowSession?: boolean) => void
  onSkillConfirm?: (confirmed: boolean) => void
  onResumeTask?: () => void
  canResume?: boolean
  onFilesUploaded?: () => void
}

export function TaskChatView({
  taskId,
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
}: TaskChatViewProps) {
  const { t } = useTranslation()
  const guard = useFeatureGuard()
  const [input, setInput] = useState('')
  const [isDragOver, setIsDragOver] = useState(false)
  const [slashCommands, setSlashCommands] = useState<SlashCommand[]>([])
  const [slashVisible, setSlashVisible] = useState(false)
  const [slashIndex, setSlashIndex] = useState(0)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const dragCounterRef = useRef(0)
  const sendingRef = useRef(false)  // synchronous guard against double-submit
  const guardPendingRef = useRef(false)  // entry-guard in flight (0.4 §7.3)
  const isEmpty = messages.length === 0 && !isGenerating

  // Fetch installed skills for slash command autocomplete
  useEffect(() => {
    if (!taskId) return
    window.electronAPI.invoke('tasks:getSkills', { taskId }).then((res: unknown) => {
      const skills = (res as { skills?: { name: string; description: string }[] })?.skills || []
      const cmds: SlashCommand[] = [{ name: '/help', description: t('task.slashHelpDesc', '查看可用命令') }]
      for (const s of skills) {
        cmds.push({ name: `/${s.name}`, description: s.description || s.name })
      }
      setSlashCommands(cmds)
    }).catch(() => {
      setSlashCommands([{ name: '/help', description: t('task.slashHelpDesc', '查看可用命令') }])
    })
  }, [taskId, t])

  useEffect(() => {
    if (!isGenerating) {
      textareaRef.current?.focus()
    }
  }, [isGenerating])

  // Reset synchronous guard when generation finishes
  useEffect(() => {
    if (!isGenerating) {
      sendingRef.current = false
    }
  }, [isGenerating])

  /** 入口 guard（0.4 §5.3 第二层）：任务发送依赖 LLM 配置；放行后才置 sending 防重标记 */
  const guardSend = (text: string, afterSend?: () => void) => {
    if (sendingRef.current || guardPendingRef.current) return
    guardPendingRef.current = true
    void guard('task', () => {
      sendingRef.current = true
      onSendMessage(text)
      afterSend?.()
    }).finally(() => {
      guardPendingRef.current = false
    })
  }

  const handleSend = () => {
    const text = input.trim()
    if (!text) return
    guardSend(text, () => {
      setInput('')
      if (textareaRef.current) {
        textareaRef.current.style.height = 'auto'
      }
    })
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    // Slash command navigation
    if (slashVisible) {
      const filtered = slashCommands.filter((c) => c.name.startsWith(input.trimStart()))
      const len = filtered.length
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setSlashIndex((i) => (i + 1) % len)
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setSlashIndex((i) => (i - 1 + len) % len)
        return
      }
      if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault()
        if (filtered[slashIndex]) {
          selectSlashCommand(filtered[slashIndex])
        }
        return
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        setSlashVisible(false)
        return
      }
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      if (!isGenerating) handleSend()
    }
  }

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const el = textareaRef.current
    if (el) {
      el.style.height = 'auto'
      el.style.height = Math.min(el.scrollHeight, 200) + 'px'
    }
    // Update slash command popup visibility using the new value
    updateSlashPopup(e.target.value)
  }

  const updateSlashPopup = (value: string) => {
    const text = value.trimStart()
    if (text.startsWith('/') && !text.includes('\n')) {
      const filtered = slashCommands.filter((c) => c.name.startsWith(text))
      if (filtered.length > 0) {
        setSlashVisible(true)
        setSlashIndex(0)
      } else {
        setSlashVisible(false)
      }
    } else {
      setSlashVisible(false)
    }
  }

  const selectSlashCommand = (cmd: SlashCommand) => {
    setInput(cmd.name + ' ')
    setSlashVisible(false)
    textareaRef.current?.focus()
  }

  // ── Drag & Drop file upload ──
  const handleUploadFiles = useCallback(async (files: File[]) => {
    if (!taskId || files.length === 0) return
    try {
      // Use Electron IPC to upload dropped files
      const filePaths = files.map((f) => (f as File & { path: string }).path).filter(Boolean)
      if (filePaths.length === 0) return
      const result = await window.electronAPI.invoke('tasks:uploadFiles', { taskId, filePaths }) as { uploaded?: { name: string; size: number }[] }
      if (result?.uploaded?.length) {
        onFilesUploaded?.()
        window.dispatchEvent(new Event('task:refresh-files'))
      }
    } catch (err) {
      alert(t('task.uploadFailed') + ': ' + (err instanceof Error ? err.message : String(err)))
    }
  }, [taskId, onFilesUploaded])

  const handleDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (!hasActiveTask) return
    dragCounterRef.current++
    if (e.dataTransfer.types.includes('Files')) {
      setIsDragOver(true)
    }
  }, [hasActiveTask])

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    dragCounterRef.current--
    if (dragCounterRef.current <= 0) {
      dragCounterRef.current = 0
      setIsDragOver(false)
    }
  }, [])

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
  }, [])

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDragOver(false)
    dragCounterRef.current = 0
    if (!hasActiveTask) return
    const files = Array.from(e.dataTransfer.files)
    if (files.length > 0) {
      handleUploadFiles(files)
    }
  }, [hasActiveTask, handleUploadFiles])

  return (
    <div
      className={`${styles.window} ${isDragOver ? styles.dragOver : ''}`}
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      {isDragOver && (
        <div className={styles.dropOverlay}>
          <div className={styles.dropIcon}><FolderOpen size={32} /></div>
          <div className={styles.dropText}>{t('task.dropHint')}</div>
        </div>
      )}
      {isEmpty ? (
        <div className={styles.emptyState}>
          <div className={styles.emptyLogo}><Wrench size={36} /></div>
          <p className={styles.emptyGreeting}>{t('task.greeting')}</p>
          <p className={styles.emptyHint}>{t('task.hint')}</p>
          <div className={styles.quickActions}>
            <button className={styles.quickCard} onClick={() => guardSend(t('task.example1'))}>
              {t('task.example1')}
            </button>
            <button className={styles.quickCard} onClick={() => guardSend(t('task.example2'))}>
              {t('task.example2')}
            </button>
            <button className={styles.quickCard} onClick={() => guardSend(t('task.example3'))}>
              {t('task.example3')}
            </button>
          </div>
        </div>
      ) : (
        <TaskMessageList
          messages={messages}
          streamContent={streamContent}
          streamSteps={streamSteps}
          streamReasoning={streamReasoning}
          streamIteration={streamIteration}
          streamIterationThoughts={streamIterationThoughts}
          streamIterationReasoning={streamIterationReasoning}
          planData={planData}
          isGenerating={isGenerating}
        />
      )}

      {/* Shell confirmation card — inline in conversation flow */}
      {pendingShellConfirm && (
        <ShellConfirmCard
          command={pendingShellConfirm.command}
          onConfirm={onShellConfirm || (() => {})}
          t={t}
        />
      )}

      {/* Skill confirmation card — inline in conversation flow */}
      {pendingSkillConfirm && (
        <SkillConfirmCard
          skillName={pendingSkillConfirm.skillName}
          onConfirm={onSkillConfirm || (() => {})}
          t={t}
        />
      )}

      {/* Slash command autocomplete popup — rendered inside inputArea for correct positioning */}

      {/* Resume banner — shown when task has saved state (crash recovery or pause) */}
      {canResume && !isGenerating && hasActiveTask && (
        <div className={styles.resumeBanner}>
          <span className={styles.resumeIcon}><AlertTriangle size={14} /></span>
          <span>{t('task.resumeHint', '此任务可恢复')}</span>
          <button className={styles.resumeBtn} onClick={onResumeTask}>
            <Play size={12} /> {t('task.resumeBtn', '恢复')}
          </button>
        </div>
      )}

      <div className={styles.inputArea}>
        {slashVisible && !isGenerating && hasActiveTask && (
          <div className={styles.slashPopup}>
            {slashCommands
              .filter((c) => c.name.startsWith(input.trimStart()))
              .map((cmd, i) => (
                <div
                  key={cmd.name}
                  className={`${styles.slashItem} ${i === slashIndex ? styles.slashItemActive : ''}`}
                  onMouseDown={(e) => {
                    e.preventDefault()
                    selectSlashCommand(cmd)
                  }}
                  onMouseEnter={() => setSlashIndex(i)}
                >
                  <span className={styles.slashName}>{cmd.name}</span>
                  <span className={styles.slashDesc}>{cmd.description}</span>
                </div>
              ))}
          </div>
        )}
        <div className={styles.wrapper}>
          <div className={styles.inputRow}>
            <button
              className={styles.uploadBtn}
              onClick={async () => {
                if (!taskId) return
                try {
                  const result = await window.electronAPI.invoke('tasks:pickAndUpload', { taskId }) as { uploaded?: { name: string; size: number }[] }
                  if (result?.uploaded?.length) {
                    onFilesUploaded?.()
                    window.dispatchEvent(new Event('task:refresh-files'))
                  }
                } catch (err) {
                  alert(t('task.uploadFailed') + ': ' + (err instanceof Error ? err.message : String(err)))
                }
              }}
              disabled={!hasActiveTask || isGenerating}
              title={t('task.uploadFiles') + ' (或拖放文件到对话区)'}
            >
              <Paperclip size={16} />
            </button>
            <textarea
              ref={textareaRef}
              className={styles.textarea}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              onInput={handleInput}
              placeholder={hasActiveTask ? t('task.inputPlaceholder') : t('task.selectTaskFirst')}
              disabled={!hasActiveTask || isGenerating}
              rows={1}
            />
            {isGenerating ? (
              <>
                <button
                  className={`${styles.sendBtn} ${styles.pauseBtn}`}
                  onClick={onPauseTask}
                  title={t('task.pauseTask')}
                >
                  <Pause size={14} />
                </button>
                <button className={`${styles.sendBtn} ${styles.stopBtn}`} onClick={onStopGenerate} title={t('task.stopGenerate')}>
                  <Square size={14} fill="currentColor" />
                </button>
              </>
            ) : (
              <button
                className={styles.sendBtn}
                onClick={handleSend}
                disabled={!input.trim() || !hasActiveTask}
                title={t('task.send')}
              >
                <SendHorizonal size={16} />
              </button>
            )}
          </div>
        </div>
        <div className={styles.hint}>
          {t('task.inputHint')} · <span className={styles.slashHint}>/ {t('task.slashHint', '查看命令')}</span>
        </div>
      </div>
    </div>
  )
}

/** Inline shell command confirmation card */
function ShellConfirmCard({
  command,
  onConfirm,
  t,
}: {
  command: string
  onConfirm: (confirmed: boolean, allowSession?: boolean) => void
  t: (key: string) => string
}) {
  const [timeout, setTimeout_] = useState(30)

  useEffect(() => {
    if (timeout <= 0) {
      onConfirm(false)
      return
    }
    const timer = setInterval(() => setTimeout_((s) => s - 1), 1000)
    return () => clearInterval(timer)
  }, [timeout, onConfirm])

  return (
    <div className={styles.shellConfirm}>
      <div className={styles.shellConfirmHeader}>
        <span className={styles.shellConfirmIcon}><AlertTriangle size={16} /></span>
        <span>{t('task.shellConfirmTitle')}</span>
        <span className={styles.shellConfirmTimer}>{timeout}s</span>
      </div>
      <pre className={styles.shellConfirmCmd}>{command}</pre>
      <div className={styles.shellConfirmActions}>
        <button className={styles.shellConfirmAllow} onClick={() => onConfirm(true, false)}>
          <Check size={14} /> {t('task.shellConfirmAllow')}
        </button>
        <button className={styles.shellConfirmSession} onClick={() => onConfirm(true, true)}>
          <SkipForward size={14} /> {t('task.shellConfirmAllowSession')}
        </button>
        <button className={styles.shellConfirmDeny} onClick={() => onConfirm(false)}>
          <X size={14} /> {t('task.shellConfirmDeny')}
        </button>
      </div>
    </div>
  )
}

/** Inline skill execution confirmation card */
function SkillConfirmCard({
  skillName,
  onConfirm,
  t,
}: {
  skillName: string
  onConfirm: (confirmed: boolean) => void
  t: (key: string) => string
}) {
  const [timeout, setTimeout_] = useState(60)

  useEffect(() => {
    if (timeout <= 0) {
      onConfirm(false)
      return
    }
    const timer = setInterval(() => setTimeout_((s) => s - 1), 1000)
    return () => clearInterval(timer)
  }, [timeout, onConfirm])

  return (
    <div className={styles.shellConfirm}>
      <div className={styles.shellConfirmHeader}>
        <span className={styles.shellConfirmIcon}><Puzzle size={16} /></span>
        <span>{t('task.skillConfirmTitle')}</span>
        <span className={styles.shellConfirmTimer}>{timeout}s</span>
      </div>
      <pre className={styles.shellConfirmCmd}>{t('task.skillConfirmDesc')} {skillName}</pre>
      <div className={styles.shellConfirmActions}>
        <button className={styles.shellConfirmAllow} onClick={() => onConfirm(true)}>
          <Check size={14} /> {t('task.skillConfirmAllow')}
        </button>
        <button className={styles.shellConfirmDeny} onClick={() => onConfirm(false)}>
          <X size={14} /> {t('task.skillConfirmDeny')}
        </button>
      </div>
    </div>
  )
}
