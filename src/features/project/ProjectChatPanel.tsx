/** src/features/project/ProjectChatPanel.tsx — Right-side resizable chat panel with real RAG Q&A */
import { useState, useRef, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { MessageCircle, X, Send, FileText, Search, ArrowLeft, Trash2, BookPlus, Check } from 'lucide-react'
import { renderMarkdown } from '../knowledge/markdown'
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
  const [inputValue, setInputValue] = useState('')
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
    const userMsg = inputValue.trim()
    setInputValue('')

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
        })
        return updated
      })
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
                <p>{t('knowledge.chatHint')}</p>
              </div>
            )}
            {messages.map((msg) => (
              <div key={msg.id} className={`${s.message} ${msg.role === 'user' ? s.messageUser : s.messageAssistant}`}>
                <div className={s.messageContent}>
                  {msg.role === 'assistant' ? (
                    <div
                      className={s.markdownBody}
                      dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.content) }}
                    />
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
          <div className={s.inputRow}>
            <input
              className={s.input}
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
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
