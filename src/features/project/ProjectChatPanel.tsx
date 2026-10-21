/** src/features/project/ProjectChatPanel.tsx — Right-side resizable chat panel with real RAG Q&A */
import { useState, useRef, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { MessageCircle, X, Send, FileText, Search, ArrowLeft, Trash2, BookPlus, Check } from 'lucide-react'
import { Markdown } from '../../components/Markdown'
import { UsageText } from '../usage/UsageText'
import { TaskTemplatePanel } from '../task/TaskTemplatePanel'
import { usePlaceholderFill } from '../task/usePlaceholderFill'
import { hasUnfilledPlaceholders } from '../task/promptTemplate'
import { useToast } from '../knowledge/Toast'
import type { UsageSummary } from '../../types'
import s from './ProjectChatPanel.module.css'

interface ChatSource {
  filename: string
  page?: number | null
  section_path?: string
  content_type: string
  score: number
}

interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  sources?: ChatSource[]
  /** The user question that triggered this answer (for archive context) */
  sourceQuestion?: string
  /** Whether this is an error message (not archivable) */
  isError?: boolean
  /** 本轮问答消耗（chat 响应体 usage，2.10 §4.5；无记录不渲染） */
  usage?: UsageSummary
}

interface ProjectChatPanelProps {
  projectId: string
  projectName: string
  selectedFileId?: string
  selectedFilename?: string
  onClearFileScope?: () => void
  /** Custom IPC channel for chat (default: 'knowledge:chat') */
  chatIpcChannel?: string
  /** Controlled open state (for mutual exclusion with other panels) */
  isOpen?: boolean
  /** Callback when panel opens (from toggle) */
  onOpen?: () => void
  /** Callback when panel requests to close */
  onClose?: () => void
}

const DEFAULT_WIDTH = 720
const MIN_WIDTH = 260
const MAX_WIDTH = 720

/** 企业类占位符（zh/en 模板各一）：项目对话场景自动预填项目名（0.5 §4.4） */
const COMPANY_PH_RE = /\{(企业名|company)\}/g

export function ProjectChatPanel({ projectId, projectName, selectedFileId, selectedFilename, onClearFileScope, chatIpcChannel = 'knowledge:chat', isOpen: controlledIsOpen, onOpen, onClose }: ProjectChatPanelProps) {
  const { t } = useTranslation()
  const [internalIsOpen, setInternalIsOpen] = useState(false)
  // Use controlled state if provided, otherwise use internal state
  const isOpen = controlledIsOpen !== undefined ? controlledIsOpen : internalIsOpen
  const setIsOpen = (value: boolean) => {
    if (controlledIsOpen !== undefined) {
      if (!value) onClose?.()
    } else {
      setInternalIsOpen(value)
    }
  }
  // 输入框 + 占位符表单联动（0.5 §4.4）：模板预填后企业类占位符自动填项目名，
  // 其余变量按名去重、填一次全量替换；手动编辑文本框即接管
  const phForm = usePlaceholderFill()
  const inputValue = phForm.text
  const showToast = useToast()
  // 未填占位符首次发送 → toast 警示不发送，再次点击放行（与任务聊天同款守卫）
  const placeholderWarnedRef = useRef(false)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [loading, setLoading] = useState(false)
  const [panelWidth, setPanelWidth] = useState(DEFAULT_WIDTH)
  const [isDraggingState, setIsDraggingState] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const isDragging = useRef(false)
  const startX = useRef(0)
  const startWidth = useRef(DEFAULT_WIDTH)
  const archiveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const archiveErrorTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Archive state
  const [archivingMsgId, setArchivingMsgId] = useState<string | null>(null)
  const [archiveTitle, setArchiveTitle] = useState('')
  const [archiveSaving, setArchiveSaving] = useState(false)
  const [archiveSuccessId, setArchiveSuccessId] = useState<string | null>(null)
  const [archiveErrorMsgId, setArchiveErrorMsgId] = useState<string | null>(null)

  const scope = selectedFileId ? 'file' : 'project'
  const title = selectedFileId ? (selectedFilename || t('knowledge.chatWithFile')) : projectName
  const titleIcon = selectedFileId
    ? <FileText size={14} style={{ flexShrink: 0 }} />
    : <Search size={14} style={{ flexShrink: 0 }} />

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // Clear messages and archive state when switching projects
  useEffect(() => {
    setMessages([])
    setArchivingMsgId(null)
    setArchiveSuccessId(null)
    setArchiveErrorMsgId(null)
    setArchiveSaving(false)
    if (archiveTimerRef.current) clearTimeout(archiveTimerRef.current)
    if (archiveErrorTimerRef.current) clearTimeout(archiveErrorTimerRef.current)
  }, [projectId])

  // ── Drag resize logic ──
  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    isDragging.current = true
    setIsDraggingState(true)
    startX.current = e.clientX
    startWidth.current = panelWidth
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }, [panelWidth])

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isDragging.current) return
      const delta = startX.current - e.clientX
      const newWidth = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, startWidth.current + delta))
      setPanelWidth(newWidth)
    }
    const handleMouseUp = () => {
      if (isDragging.current) {
        isDragging.current = false
        setIsDraggingState(false)
        document.body.style.cursor = ''
        document.body.style.userSelect = ''
      }
    }
    document.addEventListener('mousemove', handleMouseMove)
    document.addEventListener('mouseup', handleMouseUp)
    return () => {
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleMouseUp)
    }
  }, [])

  const handleSend = async () => {
    if (!inputValue.trim() || loading) return
    // 未填占位符首次发送 → toast 警示不发送；再次点击放行（与任务聊天同款守卫）
    if (hasUnfilledPlaceholders(inputValue) && !placeholderWarnedRef.current) {
      placeholderWarnedRef.current = true
      showToast(t('task.placeholderWarn'), 'error')
      return
    }
    placeholderWarnedRef.current = false
    const userMsg = inputValue.trim()
    phForm.reset()

    setMessages(prev => [...prev, {
      id: `m-${Date.now()}`,
      role: 'user',
      content: userMsg,
    }])
    setLoading(true)

    try {
      const result: any = await window.electronAPI.invoke(chatIpcChannel, {
        projectId,
        message: userMsg,
        fileId: selectedFileId || undefined,
      })

      setMessages(prev => {
        const updated = [...prev]
        // Find the last user message before this assistant message
        const lastUserMsg = [...updated].reverse().find(m => m.role === 'user')
        updated.push({
          id: `m-${Date.now() + 1}`,
          role: 'assistant',
          content: result.answer || t('knowledge.chatNoAnswer'),
          sources: result.sources || [],
          sourceQuestion: lastUserMsg?.content || '',
          usage: result.usage || undefined,
        })
        return updated
      })
      // 单次调用即单轮（2.10 §5.3）：通知 L2 呈现位按去抖节奏重取
      if (result.usage) window.dispatchEvent(new Event('app:usage-changed'))
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setMessages(prev => [...prev, {
        id: `m-${Date.now() + 1}`,
        role: 'assistant',
        content: `${t('knowledge.chatError')}: ${msg}`,
        isError: true,
      }])
    } finally {
      setLoading(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleClear = () => {
    setMessages([])
    setArchivingMsgId(null)
    setArchiveSuccessId(null)
    setArchiveErrorMsgId(null)
  }

  // 0.5 §4.4:点模板卡预填；企业类占位符(zh/en)预填项目名，其余变量在占位符
  // 表单按名去重填写、一次替换全文（usePlaceholderFill 联动）
  const handlePick = useCallback((prompt: string) => {
    phForm.start(prompt.replace(COMPANY_PH_RE, projectName))
    placeholderWarnedRef.current = false
  }, [projectName, phForm])

  // ── Archive to Wiki ──
  const handleArchiveStart = (msgId: string, defaultTitle: string) => {
    if (archivingMsgId === msgId) {
      setArchivingMsgId(null) // toggle off
      return
    }
    setArchivingMsgId(msgId)
    setArchiveTitle(defaultTitle)
  }

  const handleArchiveConfirm = async (msg: ChatMessage) => {
    if (!archiveTitle.trim() || archiveSaving) return
    setArchiveSaving(true)
    try {
      await window.electronAPI.invoke('wiki:archive', {
        projectId,
        title: archiveTitle.trim(),
        content: msg.content,
        sourceQuestion: msg.sourceQuestion || '',
      })
      setArchivingMsgId(null)
      setArchiveSuccessId(msg.id)
      // Clear success indicator after 3s
      if (archiveTimerRef.current) clearTimeout(archiveTimerRef.current)
      archiveTimerRef.current = setTimeout(() => setArchiveSuccessId(prev => prev === msg.id ? null : prev), 3000)
    } catch (err) {
      console.error('Failed to archive to Wiki:', err)
      // Brief error feedback
      setArchiveErrorMsgId(msg.id)
      if (archiveErrorTimerRef.current) clearTimeout(archiveErrorTimerRef.current)
      archiveErrorTimerRef.current = setTimeout(() => setArchiveErrorMsgId(prev => prev === msg.id ? null : prev), 3000)
    } finally {
      setArchiveSaving(false)
    }
  }

  const handleArchiveKeyDown = (e: React.KeyboardEvent, msg: ChatMessage) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleArchiveConfirm(msg)
    } else if (e.key === 'Escape') {
      setArchivingMsgId(null)
    }
  }

  return (
    <>
      {/* Wrapper — stays in contentArea flex row, positions the toggle tab */}
      {!isOpen && (
        <div className={s.wrapper}>
          <div className={s.toggleTab} onClick={() => { setIsOpen(true); onOpen?.() }}>
            <MessageCircle size={16} />
            <span className={s.toggleLabel}>{t('project.chat.title')}</span>
            <span className={s.toggleBadge}>{messages.length}</span>
          </div>
        </div>
      )}

      {/* Backdrop — 模板管理 drawer 同款：展开时点击面板外任意处收起（遮罩不透传点击） */}
      {isOpen && <div className={s.backdrop} onClick={() => setIsOpen(false)} />}

      {/* Panel — absolute, positioned relative to .page (full page height) */}
      {isOpen && (
        <div className={s.panel} style={{ width: panelWidth }}>
          {/* Drag handle */}
          <div
            className={`${s.dragHandle} ${isDraggingState ? s.dragHandleActive : ''}`}
            onMouseDown={handleMouseDown}
          />

          {/* Header */}
          <div className={s.panelHeader}>
            <div className={s.panelTitle}>
              {selectedFileId && onClearFileScope && (
                <button
                  className={s.backBtn}
                  onClick={onClearFileScope}
                  title={t('knowledge.chatWithAll')}
                >
                  <ArrowLeft size={14} />
                </button>
              )}
              {titleIcon}
              <span className={s.titleText}>{title}</span>
              <span className={s.scopeBadge}>
                {scope === 'file' ? t('knowledge.scopeFile') : t('knowledge.scopeProject')}
              </span>
            </div>
            <div className={s.headerActions}>
              {messages.length > 0 && (
                <button className={s.headerBtn} onClick={handleClear} title={t('knowledge.chatClear')}>
                  <Trash2 size={13} />
                </button>
              )}
              <button className={s.closeBtn} onClick={() => setIsOpen(false)} title={t('project.chat.close')}>
                <X size={14} />
              </button>
            </div>
          </div>

          {/* Messages */}
          <div className={s.messages}>
            {messages.length === 0 && (
              <div className={s.emptyChat}>
                <p>{t('project.chat.emptyHint')}</p>
                <TaskTemplatePanel scope="project" projectId={projectId} onPick={handlePick} />
              </div>
            )}
            {messages.map((msg) => (
              <div key={msg.id} className={`${s.message} ${msg.role === 'user' ? s.messageUser : s.messageAssistant}`}>
                <div className={s.messageContent}>
                  {msg.role === 'assistant' ? (
                    <Markdown content={msg.content} className={s.markdownBody} />
                  ) : (
                    msg.content
                  )}
                </div>
                {msg.sources && msg.sources.length > 0 && (
                  <div className={s.sources}>
                    {msg.sources.map((src, j) => (
                      <span key={j} className={s.sourceTag}>
                        {src.filename}
                        {src.page != null && ` p.${src.page}`}
                        {src.section_path && ` › ${src.section_path}`}
                      </span>
                    ))}
                  </div>
                )}
                {/* Archive to Wiki — assistant messages only, not errors */}
                {msg.role === 'assistant' && !msg.isError && (
                  <div className={s.messageActions}>
                    {/* 本轮消耗（2.10 §6.1/线框 3）：「保存为 Wiki」操作行同款小字 */}
                    <UsageText usage={msg.usage} />
                    {archiveSuccessId === msg.id ? (
                      <span className={s.archiveSuccess}>
                        <Check size={12} />
                        {t('project.wiki.archived', { defaultValue: '已保存到 Wiki' })}
                      </span>
                    ) : archiveErrorMsgId === msg.id ? (
                      <span className={s.archiveError}>
                        {t('project.wiki.archiveFailed', { defaultValue: '保存失败' })}
                      </span>
                    ) : archivingMsgId === msg.id ? (
                      <div className={s.archiveForm}>
                        <input
                          className={s.archiveInput}
                          value={archiveTitle}
                          onChange={(e) => setArchiveTitle(e.target.value)}
                          onKeyDown={(e) => handleArchiveKeyDown(e, msg)}
                          placeholder={t('project.wiki.archiveTitlePlaceholder', { defaultValue: '页面标题' })}
                          autoFocus
                          disabled={archiveSaving}
                        />
                        <button
                          className={s.archiveConfirmBtn}
                          onClick={() => handleArchiveConfirm(msg)}
                          disabled={!archiveTitle.trim() || archiveSaving}
                        >
                          {archiveSaving ? '...' : t('project.wiki.archiveConfirm', { defaultValue: '保存' })}
                        </button>
                        <button
                          className={s.archiveCancelBtn}
                          onClick={() => setArchivingMsgId(null)}
                          disabled={archiveSaving}
                        >
                          {t('common.cancel', { defaultValue: '取消' })}
                        </button>
                      </div>
                    ) : (
                      <button
                        className={s.archiveBtn}
                        onClick={() => handleArchiveStart(
                          msg.id,
                          (msg.sourceQuestion || '').slice(0, 30) || 'Chat 归档',
                        )}
                        title={t('project.wiki.archiveToWiki', { defaultValue: '保存为 Wiki 页面' })}
                      >
                        <BookPlus size={12} />
                        {t('project.wiki.archiveToWiki', { defaultValue: '保存为 Wiki' })}
                      </button>
                    )}
                  </div>
                )}
              </div>
            ))}
            {loading && (
              <div className={`${s.message} ${s.messageAssistant}`}>
                <div className={s.messageContent}>
                  <span className={s.typing}>{t('knowledge.chatThinking')}</span>
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>

          {/* Input */}
          {/* 占位符表单(0.5 §4.4,模板预填+未接管):变量按名去重,填一次全量替换 */}
          {!phForm.manual && phForm.names.length > 0 && (
            <div className={s.phForm}>
              {phForm.names.map((name) => (
                <label key={name} className={s.phItem}>
                  <span className={s.phName}>{name}</span>
                  <input
                    className={s.phInput}
                    value={phForm.values[name] ?? ''}
                    aria-label={name}
                    onChange={(e) => phForm.setValue(name, e.target.value)}
                  />
                </label>
              ))}
              <span className={s.phHint}>{t('task.phFormHint')}</span>
            </div>
          )}
          <div className={s.inputRow}>
            <input
              className={s.input}
              value={inputValue}
              onChange={(e) => phForm.onInputEdit(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={t('knowledge.chatInputPlaceholder')}
              disabled={loading}
            />
            <button className={s.sendBtn} onClick={handleSend} disabled={!inputValue.trim() || loading}>
              <Send size={14} />
            </button>
          </div>
        </div>
      )}
    </>
  )
}
