/** src/features/knowledge/ChatPanel.tsx */
import { useState, useRef, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { FileText, Search, ArrowLeft, Trash2, X, SendHorizonal } from 'lucide-react'
import { renderMarkdown } from './markdown'
import s from './ChatPanel.module.css'

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  sources?: ChatSource[]
}

interface ChatSource {
  filename: string
  page?: number | null
  section_path?: string
  content_type: string
  score: number
}

interface ChatPanelProps {
  projectId: string
  projectName: string
  fileId?: string
  filename?: string
  onClose: () => void
  onSwitchScope?: () => void  // switch from single-file to project-wide
}

export function ChatPanel({ projectId, projectName, fileId, filename, onClose, onSwitchScope }: ChatPanelProps) {
  const { t } = useTranslation()
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)

  const scope = fileId ? 'file' : 'project'
  const title = fileId
    ? filename || t('knowledge.chatWithFile')
    : projectName
  const titleIcon = fileId ? <FileText size={14} style={{ flexShrink: 0 }} /> : <Search size={14} style={{ flexShrink: 0 }} />

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const handleSend = async () => {
    if (!input.trim() || loading) return
    const userMsg = input.trim()
    setInput('')

    setMessages(prev => [...prev, { role: 'user', content: userMsg }])
    setLoading(true)

    try {
      const result: any = await window.electronAPI.invoke('knowledge:chat', {
        projectId,
        message: userMsg,
        fileId: fileId || undefined,
      })

      setMessages(prev => [...prev, {
        role: 'assistant',
        content: result.answer || t('knowledge.chatNoAnswer'),
        sources: result.sources || [],
      }])
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setMessages(prev => [...prev, {
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
    <div className={s.panel}>
      {/* Header */}
      <div className={s.header}>
        <div className={s.headerInfo}>
          {fileId && onSwitchScope && (
            <button
              className={s.backBtn}
              onClick={onSwitchScope}
              title={t('knowledge.chatWithAll')}
            >
              <ArrowLeft size={16} />
            </button>
          )}
          <span className={s.title}>{titleIcon}{title}</span>
          <span className={s.scopeBadge}>
            {scope === 'file' ? t('knowledge.scopeFile') : t('knowledge.scopeProject')}
          </span>
        </div>
        <div className={s.headerActions}>
          {messages.length > 0 && (
            <button className={s.headerBtn} onClick={handleClear} title={t('knowledge.chatClear')}>
              <Trash2 size={14} />
            </button>
          )}
          <button className={s.headerBtn} onClick={onClose} title={t('knowledge.chatClose')}>
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
        {messages.map((msg, i) => (
          <div key={i} className={`${s.message} ${msg.role === 'user' ? s.userMessage : s.assistantMessage}`}>
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
          <div className={`${s.message} ${s.assistantMessage}`}>
            <div className={s.typing}>{t('knowledge.chatThinking')}</div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className={s.inputArea}>
        <textarea
          className={s.input}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={t('knowledge.chatInputPlaceholder')}
          disabled={loading}
          rows={2}
        />
        <button
          className={s.sendBtn}
          onClick={handleSend}
          disabled={loading || !input.trim()}
        >
          {loading ? <span style={{ opacity: 0.6 }}>...</span> : <SendHorizonal size={16} />}
        </button>
      </div>
    </div>
  )
}
