import { useState, useRef, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Square, SendHorizonal } from 'lucide-react'
import styles from './MessageInput.module.css'

interface MessageInputProps {
  onSend: (content: string) => void
  onStop: () => void
  isGenerating: boolean
  disabled: boolean
}

export function MessageInput({ onSend, onStop, isGenerating, disabled }: MessageInputProps) {
  const { t } = useTranslation()
  const [input, setInput] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (!isGenerating) {
      textareaRef.current?.focus()
    }
  }, [isGenerating])

  const handleSend = () => {
    const text = input.trim()
    if (!text || disabled) return
    onSend(text)
    setInput('')
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      if (!isGenerating) handleSend()
    }
  }

  const handleInput = () => {
    const el = textareaRef.current
    if (el) {
      el.style.height = 'auto'
      el.style.height = Math.min(el.scrollHeight, 200) + 'px'
    }
  }

  return (
    <div className={styles.inputArea}>
      <div className={styles.wrapper}>
        <textarea
          ref={textareaRef}
          className={styles.textarea}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          onInput={handleInput}
          placeholder={disabled ? t('chat.selectConversationFirst') : t('chat.inputPlaceholder')}
          disabled={disabled || isGenerating}
          rows={1}
        />
        {isGenerating ? (
          <button className={`${styles.sendBtn} ${styles.stopBtn}`} onClick={onStop} title={t('chat.stopGenerate')}>
            <Square size={14} fill="currentColor" />
          </button>
        ) : (
          <button
            className={styles.sendBtn}
            onClick={handleSend}
            disabled={!input.trim() || disabled}
            title={t('chat.send')}
          >
            <SendHorizonal size={16} />
          </button>
        )}
      </div>
      <div className={styles.hint}>
        {t('chat.inputHint')}
      </div>
    </div>
  )
}
