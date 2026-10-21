import { useState, useRef, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { FolderOpen, Bot, Paperclip, Pause, Square, SendHorizonal, AlertTriangle, Check, SkipForward, X, Puzzle, Play } from 'lucide-react'
import { TaskMessageList } from './TaskMessageList'
import { TaskTemplatePanel } from './TaskTemplatePanel'
import { hasUnfilledPlaceholders } from './promptTemplate'
import { usePlaceholderFill } from './usePlaceholderFill'
import type { TaskMessage, StreamStep, PlanData } from '../../types'
import type { ShellConfirmData, SkillConfirmData } from './useTaskChat'
import { useFeatureGuard } from '../readiness/useReadiness'
import { useToast } from '../knowledge/Toast'
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
  onSendMessage: (content: string) => void | Promise<void>
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
  const showToast = useToast()
  // 输入框 + 占位符表单联动（0.5 §4.4）：模板预填后变量按名去重，填一次全文替换；
  // 手动编辑文本框即接管（表单停用），发送成功后 reset
  const phForm = usePlaceholderFill()
  const input = phForm.text
  const setInput = phForm.setText
  const [isDragOver, setIsDragOver] = useState(false)
  const [slashCommands, setSlashCommands] = useState<SlashCommand[]>([])
  const [slashVisible, setSlashVisible] = useState(false)
  const [slashIndex, setSlashIndex] = useState(0)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const dragCounterRef = useRef(0)
  const sendingRef = useRef(false)  // synchronous guard against double-submit
  const guardPendingRef = useRef(false)  // entry-guard in flight (0.4 §7.3)
  // 发送占位符守卫(0.5 E1 T7):含未填 {占位符} 首次发送 → toast 警示不发送;再次点击放行;
  // 重选模板/发送成功后重置,保证每个新预填的模板都得到一次提醒
  const placeholderWarnedRef = useRef(false)
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

  /** 输入内容变化后 textarea 高度自适应（手动输入/模板预填/发送清空共用，上限 200px） */
  useEffect(() => {
    const el = textareaRef.current
    if (el) {
      el.style.height = 'auto'
      el.style.height = Math.min(el.scrollHeight, 200) + 'px'
    }
  }, [input])

  /** 入口 guard（0.4 §5.3 第二层）：任务发送依赖 LLM 配置；放行后才置 sending 防重标记。
   *  onSendMessage 允许异步（零任务态发送需先建任务，0.5 §4.4）：await 到「发送受理」为止，
   *  不等生成结束（isGenerating 生命周期归 useTaskChat）；失败（建任务失败等）→ 保留输入、
   *  复位 sendingRef 供直接重试，错误 toast 由链路上游（useTasks createFailed）负责 */
  const guardSend = (text: string, afterSend?: () => void) => {
    if (sendingRef.current || guardPendingRef.current) return
    guardPendingRef.current = true
    void guard('task', async () => {
      sendingRef.current = true
      try {
        await onSendMessage(text)
        afterSend?.()
      } catch {
        sendingRef.current = false
      }
    }).finally(() => {
      guardPendingRef.current = false
    })
  }

  /** 模板点卡预填(0.5 §4.4):登记基底 prompt 激活占位符表单,只填不发送,重置发送守卫；
   *  skill 版多行模板预填后 toast 告知发送方式（Enter 换行/Ctrl+Enter 发送，0.5 §7.3） */
  const handlePickTemplate = (prompt: string) => {
    phForm.start(prompt)
    placeholderWarnedRef.current = false
    if (prompt.includes('\n')) showToast(t('task.templateMultilineHint'))
    textareaRef.current?.focus()
  }

  const handleSend = () => {
    const text = input.trim()
    if (!text) return
    // 发送守卫:含未填占位符首次点击 → toast 警示不发送;再次点击放行
    if (hasUnfilledPlaceholders(text) && !placeholderWarnedRef.current) {
      placeholderWarnedRef.current = true
      showToast(t('task.placeholderWarn'))
      return
    }
    placeholderWarnedRef.current = false
    guardSend(text, () => {
      phForm.reset()
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
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      // 多行内容发送通道：Ctrl/Cmd+Enter 任意形态可发（0.5 §7.3）
      e.preventDefault()
      if (!isGenerating) handleSend()
      return
    }
    if (e.key === 'Enter' && !e.shiftKey && !input.includes('\n')) {
      // 单行裸 Enter 发送；含换行时裸 Enter=换行（textarea 默认行为）
      e.preventDefault()
      if (!isGenerating) handleSend()
    }
  }

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    // 高度自适应由 input effect 统一处理；此处仅更新 slash 弹层可见性
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
    // 程序化覆写输入：摆脱模板联动（否则残留基底 prompt 与表单状态）
    phForm.detach()
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
          <div className={styles.emptyLogo}><Bot size={36} /></div>
          <p className={styles.emptyGreeting}>
            {(() => {
              // 时段问候(0.5 §4.4):6-12 早上 / 12-18 下午 / 其余晚上
              const hour = new Date().getHours()
              const greetKey = hour >= 6 && hour < 12
                ? 'task.greetMorning'
                : hour >= 12 && hour < 18
                  ? 'task.greetAfternoon'
                  : 'task.greetEvening'
              return `${t(greetKey)}${t('task.greetLead')}`
            })()}
          </p>
          <p className={styles.emptyHint}>{t('task.hint')}</p>
          <TaskTemplatePanel onPick={handlePickTemplate} />
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
        {/* 占位符表单(0.5 §4.4,仅空态+模板预填+未接管):变量按名去重,填一次全量替换;
            接管(手动编辑文本框)后隐藏,未填变量由发送守卫 toast 兜底 */}
        {isEmpty && !phForm.manual && phForm.names.length > 0 && (
          <div className={styles.phForm}>
            {phForm.names.map((name) => (
              <label key={name} className={styles.phItem}>
                <span className={styles.phName}>{name}</span>
                <input
                  className={styles.phInput}
                  value={phForm.values[name] ?? ''}
                  aria-label={name}
                  onChange={(e) => phForm.setValue(name, e.target.value)}
                />
              </label>
            ))}
            <span className={styles.phHint}>{t('task.phFormHint')}</span>
          </div>
        )}
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
            {/* 上传保持任务门控：文件必须挂任务工作区（tasks:pickAndUpload 需要 taskId），
                与零任务态可输入的「发送即建任务」（0.5 §4.4）不冲突 */}
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
            {/* 零任务态不禁用（0.5 §4.4 发送即建任务）：发送时由 App 层自动建任务 */}
            <textarea
              ref={textareaRef}
              className={styles.textarea}
              value={input}
              onChange={(e) => phForm.onInputEdit(e.target.value)}
              onKeyDown={handleKeyDown}
              onInput={handleInput}
              placeholder={t('task.inputPlaceholder')}
              disabled={isGenerating}
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
                disabled={!input.trim()}
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
