/** src/features/project/ProjectChatPanel.tsx — Right-side resizable chat panel (mock, UI-First) */
import { useState, useRef, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { MessageCircle, X, Send } from 'lucide-react'
import s from './ProjectChatPanel.module.css'

interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: string
}

const MOCK_MESSAGES: ChatMessage[] = [
  { id: 'm1', role: 'user', content: '这个项目有哪些核心专利？', timestamp: '10:23' },
  { id: 'm2', role: 'assistant', content: '根据 BP.pdf 和专利查询结果，该项目公司拥有 12 项核心专利技术，其中发明专利 8 项（涵盖新型材料合成工艺、智能控制系统等），实用新型 4 项。主要专利集中在 2022-2025 年间申请。', timestamp: '10:23' },
  { id: 'm3', role: 'user', content: '竞争对手有哪些？', timestamp: '10:25' },
  { id: 'm4', role: 'assistant', content: '根据行业研究报告，主要竞争对手包括：\n1. A 公司 — 市场份额约 18%，技术路线相似\n2. B 公司 — 市场份额约 12%，差异化定位\n3. C 集团 — 市场份额约 25%，全产业链布局', timestamp: '10:25' },
]

const DEFAULT_WIDTH = 360
const MIN_WIDTH = 260
const MAX_WIDTH = 600

export function ProjectChatPanel() {
  const { t } = useTranslation()
  const [isOpen, setIsOpen] = useState(false)
  const [inputValue, setInputValue] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>(MOCK_MESSAGES)
  const [panelWidth, setPanelWidth] = useState(DEFAULT_WIDTH)
  const [isDraggingState, setIsDraggingState] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const isDragging = useRef(false)
  const startX = useRef(0)
  const startWidth = useRef(DEFAULT_WIDTH)

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

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

  const handleSend = () => {
    if (!inputValue.trim()) return
    const userMsg: ChatMessage = {
      id: `m-${Date.now()}`,
      role: 'user',
      content: inputValue.trim(),
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    }
    setMessages(prev => [...prev, userMsg])
    setInputValue('')

    setTimeout(() => {
      const assistantMsg: ChatMessage = {
        id: `m-${Date.now() + 1}`,
        role: 'assistant',
        content: t('project.chat.mockResponse'),
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      }
      setMessages(prev => [...prev, assistantMsg])
    }, 800)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
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
              <MessageCircle size={14} />
              <span>{t('project.chat.title')}</span>
              <span className={s.panelCount}>{messages.length}</span>
            </div>
            <button className={s.closeBtn} onClick={() => setIsOpen(false)} title={t('project.chat.close')}>
              <X size={14} />
            </button>
          </div>

          {/* Messages */}
          <div className={s.messages}>
            {messages.map((msg) => (
              <div key={msg.id} className={`${s.message} ${msg.role === 'user' ? s.messageUser : s.messageAssistant}`}>
                <div className={s.messageContent}>{msg.content}</div>
                <div className={s.messageTime}>{msg.timestamp}</div>
              </div>
            ))}
            <div ref={messagesEndRef} />
          </div>

          {/* Input */}
          <div className={s.inputRow}>
            <input
              className={s.input}
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={t('project.chat.placeholder')}
            />
            <button className={s.sendBtn} onClick={handleSend} disabled={!inputValue.trim()}>
              <Send size={14} />
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
