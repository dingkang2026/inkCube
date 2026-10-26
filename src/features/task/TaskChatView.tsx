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
  /** 确认卡倒计时到 0 的本地清理（不发拒绝 POST）：后端超时分支负责留痕 */
  onConfirmExpire?: (kind: 'shell' | 'skill') => void
  onResumeTask?: () => void
  canResume?: boolean
  onFilesUploaded?: (taskId: string) => void
  /** 零任务态「上传即建任务」句柄（0.5 §4.4）：返回新建/现有任务 id，失败返回 null */
  onEnsureTask?: () => Promise<string | null>
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
  onConfirmExpire,
  onResumeTask,
  canResume,
  onFilesUploaded,
  onEnsureTask,
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
  // 用户发送强制贴底信号（上翻暂停时发送仍是显式意图，发送受理即递增）
  const [sendCount, setSendCount] = useState(0)
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
  // 确认卡到期回调（稳定引用：Card 内 effect 依赖它，逐渲染新建会重启计时器）
  const handleShellExpire = useCallback(() => onConfirmExpire?.('shell'), [onConfirmExpire])
  const handleSkillExpire = useCallback(() => onConfirmExpire?.('skill'), [onConfirmExpire])

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
      setSendCount((c) => c + 1)
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
  /** 拖放/上传统一入口（0.5 §4.4 上传即建任务）：零任务态先经 onEnsureTask 建任务，
   *  文件必须挂任务工作区（tasks:uploadFiles 需要 taskId）；建任务失败 useTasks 已
   *  toast 反馈，句柄为 null 时静默终止。Electron 32+ 移除 File.path，真实磁盘路径
   *  经 preload webUtils.getPathForFile 换取 */
  const handleUploadFiles = useCallback(async (files: File[]) => {
    if (files.length === 0) return
    const id = taskId ?? (await onEnsureTask?.()) ?? null
    if (!id) return
    // Electron 32+ 移除 File.path，真实磁盘路径经 preload webUtils.getPathForFile 换取；
    // 拖入无路径的虚拟文件（如网页图片）：全部无路径明确反馈，混合场景部分跳过同样提示
    const all = files.map((f) => window.electronAPI.getPathForFile(f))
    const filePaths = all.filter(Boolean)
    if (filePaths.length === 0) {
      showToast(t('task.dropUnsupported'))
      return
    }
    if (filePaths.length < all.length) {
      showToast(t('task.dropPartialSkipped', { count: all.length - filePaths.length }))
    }
    try {
      const result = await window.electronAPI.invoke('tasks:uploadFiles', { taskId: id, filePaths }) as { uploaded?: { name: string; size: number }[] }
      if (result?.uploaded?.length) {
        // 显式传 id：零任务态建任务后 state 未反映到本闭包，消费方不能依赖外部 task state
        onFilesUploaded?.(id)
        window.dispatchEvent(new Event('task:refresh-files'))
      }
    } catch (err) {
      showToast(t('task.uploadFailed') + ': ' + (err instanceof Error ? err.message : String(err)))
    }
  }, [taskId, onEnsureTask, onFilesUploaded, showToast, t])

  // ── Clipboard image paste upload ──
  /** 粘贴截图直传（0.5 §4.4 上传即建任务）：剪贴板 image/* 无磁盘路径，
   *  webUtils.getPathForFile 不适用 → Blob 转 base64 走 tasks:uploadClipboardImage
   *  字节通道，main 进程与磁盘上传共用同一 multipart/python 端点。
   *  零任务态先经 onEnsureTask 建任务；仅拦截图片项，纯文本粘贴不受影响 */
  const handlePasteFiles = useCallback(async (files: File[]) => {
    if (files.length === 0) return
    const id = taskId ?? (await onEnsureTask?.()) ?? null
    if (!id) return
    try {
      for (const file of files) {
        const dataBase64 = await new Promise<string>((resolve, reject) => {
          const reader = new FileReader()
          reader.onload = () => resolve(String(reader.result).split(',')[1] ?? '')
          reader.onerror = () => reject(reader.error ?? new Error('read clipboard image failed'))
          reader.readAsDataURL(file)
        })
        const result = await window.electronAPI.invoke('tasks:uploadClipboardImage', { taskId: id, dataBase64 }) as { uploaded?: { name: string; size: number }[]; error?: string }
        // 护栏拒绝与 python 200-错误体（FILE_TOO_LARGE/TASK_NOT_FOUND）是 resolved 值：
        // 转 throw 复用下方 catch → toast，否则静默吞掉
        if (result?.error) throw new Error(result.error)
        if (result?.uploaded?.length) {
          onFilesUploaded?.(id)
          window.dispatchEvent(new Event('task:refresh-files'))
        }
      }
    } catch (err) {
      showToast(t('task.pasteUploadFailed') + ': ' + (err instanceof Error ? err.message : String(err)))
    }
  }, [taskId, onEnsureTask, onFilesUploaded, showToast, t])

  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    const images = Array.from(e.clipboardData?.items ?? [])
      .filter((item) => item.type.startsWith('image/'))
      .map((item) => item.getAsFile())
      .filter((f): f is File => f !== null)
    if (images.length === 0) return
    // 仅拦截图片粘贴：防二进制字节以乱码进入输入框；文本粘贴走默认行为
    e.preventDefault()
    void handlePasteFiles(images)
  }, [handlePasteFiles])

  const handleDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    dragCounterRef.current++
    if (e.dataTransfer.types.includes('Files')) {
      setIsDragOver(true)
    }
  }, [])

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
    const files = Array.from(e.dataTransfer.files)
    if (files.length > 0) {
      handleUploadFiles(files)
    }
  }, [handleUploadFiles])

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
          taskId={taskId}
          forceScrollSignal={sendCount}
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
          expiresAt={pendingShellConfirm.expiresAt}
          onConfirm={onShellConfirm || (() => {})}
          onExpire={handleShellExpire}
          t={t}
        />
      )}

      {/* Skill confirmation card — inline in conversation flow */}
      {pendingSkillConfirm && (
        <SkillConfirmCard
          skillName={pendingSkillConfirm.skillName}
          expiresAt={pendingSkillConfirm.expiresAt}
          onConfirm={onSkillConfirm || (() => {})}
          onExpire={handleSkillExpire}
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
            {/* 上传即建任务（0.5 §4.4）：零任务态点击/拖放先经 onEnsureTask 建任务，
                文件挂新任务工作区；建任务失败 useTasks 已 toast，句柄 null 时静默终止 */}
            <button
              className={styles.uploadBtn}
              onClick={async () => {
                const id = taskId ?? (await onEnsureTask?.()) ?? null
                if (!id) return
                try {
                  const result = await window.electronAPI.invoke('tasks:pickAndUpload', { taskId: id }) as { uploaded?: { name: string; size: number }[] }
                  if (result?.uploaded?.length) {
                    onFilesUploaded?.(id)
                    window.dispatchEvent(new Event('task:refresh-files'))
                  }
                } catch (err) {
                  showToast(t('task.uploadFailed') + ': ' + (err instanceof Error ? err.message : String(err)))
                }
              }}
              disabled={isGenerating}
              title={t('task.uploadDropHint')}
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
              onPaste={handlePaste}
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
  expiresAt,
  onConfirm,
  onExpire,
  t,
}: {
  command: string
  expiresAt: number
  onConfirm: (confirmed: boolean, allowSession?: boolean) => void
  onExpire: () => void
  t: (key: string) => string
}) {
  // 倒计时源自后端 confirm_shell 载荷的 timeout（1.1 §7.1：shell/code_executor/git
  // commit 共 120s），绝对截止时刻推算剩余——bg 往返后不重置；每 tick 重算而非
  // 递减，无 interval 漂移累计误差。到 0 仅本地清理（onExpire）：真实等待终止
  // 由后端超时分支留痕「确认超时，已跳过」，不发拒绝 POST（避免超时被记成拒绝）
  const [remainSec, setRemainSec] = useState(() =>
    expiresAt > 0 ? Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000)) : 0)

  useEffect(() => {
    if (expiresAt <= 0) return  // timeout 缺失（异常载荷）：无倒计时，与历史行为一致
    const tick = () => {
      const r = Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000))
      setRemainSec(r)
      if (r === 0) onExpire()
    }
    tick()
    const timer = setInterval(tick, 1000)
    return () => clearInterval(timer)
  }, [expiresAt, onExpire])

  return (
    <div className={styles.shellConfirm}>
      <div className={styles.shellConfirmHeader}>
        <span className={styles.shellConfirmIcon}><AlertTriangle size={16} /></span>
        <span>{t('task.shellConfirmTitle')}</span>
        {expiresAt > 0 && <span className={styles.shellConfirmTimer}>{remainSec}s</span>}
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
  expiresAt,
  onConfirm,
  onExpire,
  t,
}: {
  skillName: string
  expiresAt: number
  onConfirm: (confirmed: boolean) => void
  onExpire: () => void
  t: (key: string) => string
}) {
  // 倒计时语义同 ShellConfirmCard（skill 窗口 180s，1.1 §7.2）；到 0 仅本地清理，
  // 后端超时分支留痕「确认超时，已跳过 Skill」
  const [remainSec, setRemainSec] = useState(() =>
    expiresAt > 0 ? Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000)) : 0)

  useEffect(() => {
    if (expiresAt <= 0) return
    const tick = () => {
      const r = Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000))
      setRemainSec(r)
      if (r === 0) onExpire()
    }
    tick()
    const timer = setInterval(tick, 1000)
    return () => clearInterval(timer)
  }, [expiresAt, onExpire])

  return (
    <div className={styles.shellConfirm}>
      <div className={styles.shellConfirmHeader}>
        <span className={styles.shellConfirmIcon}><Puzzle size={16} /></span>
        <span>{t('task.skillConfirmTitle')}</span>
        {expiresAt > 0 && <span className={styles.shellConfirmTimer}>{remainSec}s</span>}
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
