/** src/features/project/ProjectChatPanel.tsx — Right-side resizable chat panel with real RAG Q&A */
import { useState, useRef, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { MessageCircle, X, Send, FileText, Search, ArrowLeft, Trash2 } from 'lucide-react'
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
}

interface ProjectChatPanelProps {
  projectId: string
  projectName: string
  selectedFileId?: string
  selectedFilename?: string
  onClearFileScope?: () => void
}

const DEFAULT_WIDTH = 360
const MIN_WIDTH = 260
const MAX_WIDTH = 600

export function ProjectChatPanel({ projectId, projectName, selectedFileId, selectedFilename, onClearFileScope }: ProjectChatPanelProps) {
  const { t } = useTranslation()
  const [isOpen, setIsOpen] = useState(false)
  const [inputValue, setInputValue] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [loading, setLoading] = useState(false)
  const [panelWidth, setPanelWidth] = useState(DEFAULT_WIDTH)
  const [isDraggingState, setIsDraggingState] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const isDragging = useRef(false)
  const startX = useRef(0)
  const startWidth = useRef(DEFAULT_WIDTH)

  const scope = selectedFileId ? 'file' : 'project'
  const title = selectedFileId ? (selectedFilename || t('knowledge.chatWithFile')) : projectName
  const titleIcon = selectedFileId
    ? <FileText size={14} style={{ flexShrink: 0 }} />
    : <Search size={14} style={{ flexShrink: 0 }} />

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // Clear messages when switching projects
  useEffect(() => {
    setMessages([])
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
      const result: any = await window.electronAPI.invoke('knowledge:chat', {
        projectId,
        message: userMsg,
        fileId: selectedFileId || undefined,
      })

      setMessages(prev => [...prev, {
        id: `m-${Date.now() + 1}`,
        role: 'assistant',
        content: result.answer || t('knowledge.chatNoAnswer'),
        sources: result.sources || [],
      }])
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setMessages(prev => [...prev, {
        id: `m-${Date.now() + 1}`,
        role: 'assistant',
        content: `${t('knowledge.chatError')}: ${msg}`,
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
  }

  return (
    <div className={s.wrapper}>
      {/* Toggle tab — visible when collapsed */}
      {!isOpen && (
        <div className={s.toggleTab} onClick={() => setIsOpen(true)}>
          <MessageCircle size={16} />
          <span className={s.toggleLabel}>{t('project.chat.title')}</span>
          <span className={s.toggleBadge}>{messages.length}</span>
        </div>
      )}

      {/* Expanded panel */}
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
    </div>
  )
}
