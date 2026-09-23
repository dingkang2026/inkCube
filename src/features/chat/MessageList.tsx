import { useState, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Bot, User, Copy, Check } from 'lucide-react'
import { Markdown } from '../../components/Markdown'
import type { Message, StreamStep } from '../../types'
import s from './MessageList.module.css'

interface MessageListProps {
  messages: Message[]
  streamContent: string
  streamSteps: StreamStep[]
  isGenerating: boolean
}

export function MessageList({ messages, streamContent, streamSteps, isGenerating }: MessageListProps) {
  const { i18n } = useTranslation()
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamContent, streamSteps])

  return (
    <div className={s.list}>
      {messages.filter((msg) => msg.role !== 'observation').map((msg) => (
        <MessageBubble key={msg.id} message={msg} />
      ))}

      {streamSteps.map((step, i) => (
        <StreamStepBubble key={i} step={step} />
      ))}

      {streamContent && (
        <div className={`${s.message} ${s.assistant}`}>
          <div className={s.avatar}><Bot size={18} /></div>
          <div className={`${s.bubble} ${s.assistantBubble}`}>
            <Markdown content={streamContent} className={s.markdown} />
            {isGenerating && <span className={s.streamingCursor}>▍</span>}
          </div>
        </div>
      )}

      {isGenerating && !streamContent && streamSteps.length === 0 && (
        <div className={`${s.message} ${s.assistant}`}>
          <div className={s.avatar}><Bot size={18} /></div>
          <div className={`${s.bubble} ${s.assistantBubble} ${s.thinking}`}>
            {i18n.t('chat.thinking')}
          </div>
        </div>
      )}

      <div ref={bottomRef} />
    </div>
  )
}

function MessageBubble({ message }: { message: Message }) {
  const { t, i18n } = useTranslation()
  const [copied, setCopied] = useState(false)
  const isUser = message.role === 'user'
  const time = new Date(message.created_at).toLocaleTimeString(i18n.language, {
    hour: '2-digit',
    minute: '2-digit',
  })

  if (message.role === 'reasoning') {
    return (
      <details className={s.thinkingBlock}>
        <summary>{t('chat.reasoningProcess')}</summary>
        <div className={s.blockContent}><Markdown content={message.content} /></div>
      </details>
    )
  }

  if (message.role === 'thought') {
    return (
      <details className={s.thinkingBlock}>
        <summary>{t('chat.thoughtProcess')}</summary>
        <div className={s.blockContent}><Markdown content={message.content} /></div>
      </details>
    )
  }

  if (message.role === 'action') {
    return (
      <details className={s.actionBlock}>
        <summary>{t('chat.toolCall')}: {message.metadata?.tool_name}</summary>
        <div className={s.blockContent}>
          <pre>{JSON.stringify(message.metadata?.tool_input, null, 2)}</pre>
          {message.metadata?.tool_output && (
            <div className={s.toolOutput}>{message.metadata.tool_output}</div>
          )}
        </div>
      </details>
    )
  }

  const handleCopy = async (e: React.MouseEvent) => {
    e.stopPropagation()
    try {
      await navigator.clipboard.writeText(message.content)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch { /* ignore */ }
  }

  return (
    <div className={`${s.message} ${isUser ? s.user : s.assistant}`}>
      <div className={s.avatar}>{isUser ? <User size={18} /> : <Bot size={18} />}</div>
      <div className={s.bubbleWrap}>
        <div className={`${s.bubble} ${isUser ? s.userBubble : s.assistantBubble}`}>
          {isUser ? (
            <p>{message.content}</p>
          ) : (
            <Markdown content={message.content} className={s.markdown} />
          )}
        </div>
        <div className={`${s.timeRow} ${isUser ? s.timeRowUser : ''}`}>
          <span className={s.time}>{time}</span>
          <button
            className={s.copyBtn}
            onClick={handleCopy}
            title={copied ? t('chat.copied') : t('chat.copy')}
          >
            {copied ? <Check size={12} /> : <Copy size={12} />}
          </button>
        </div>
      </div>
    </div>
  )
}

function StreamStepBubble({ step }: { step: StreamStep }) {
  const { t } = useTranslation()

  if (step.type === 'reasoning') {
    return (
      <details className={s.thinkingBlock} open>
        <summary>{t('chat.reasoningProcess')}</summary>
        <div className={s.blockContent}><Markdown content={step.content} /></div>
      </details>
    )
  }

  if (step.type === 'thought') {
    return (
      <details className={s.thinkingBlock} open>
        <summary>{t('chat.thoughtProcess')}</summary>
        <div className={s.blockContent}><Markdown content={step.content} /></div>
      </details>
    )
  }

  return (
    <details className={s.actionBlock} open>
      <summary>{t('chat.toolCall')}: {step.tool}</summary>
      <div className={s.blockContent}>
        <pre>{JSON.stringify(step.input, null, 2)}</pre>
        {step.output ? (
          <div className={s.toolOutput}>{step.output}</div>
        ) : (
          <div className={`${s.toolOutput} ${s.thinking}`}>{t('chat.executing')}</div>
        )}
      </div>
    </details>
  )
}
