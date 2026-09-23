import { useTranslation } from 'react-i18next'
import { MessageList } from './MessageList'
import { MessageInput } from './MessageInput'
import type { Message, StreamStep } from '../../types'
import styles from './ChatView.module.css'

interface ChatViewProps {
  messages: Message[]
  isGenerating: boolean
  streamContent: string
  streamSteps: StreamStep[]
  onSendMessage: (content: string) => void
  onStopGenerate: () => void
  conversationTitle?: string
}

export function ChatView({
  messages,
  isGenerating,
  streamContent,
  streamSteps,
  onSendMessage,
  onStopGenerate,
  conversationTitle,
}: ChatViewProps) {
  const { t } = useTranslation()
  const isEmpty = messages.length === 0 && !isGenerating

  return (
    <main className={styles.window}>
      <div className={styles.header}>
        <span className={styles.title}>{conversationTitle || t('chat.newConversation')}</span>
      </div>

      {isEmpty ? (
        <div className={styles.emptyState}>
          <div className={styles.emptyLogo}>{t('sidebar.logo')}</div>
          <p className={styles.emptyGreeting}>{t('chat.greeting')}</p>
          <p className={styles.emptyHint}>{t('chat.hint')}</p>
          <div className={styles.quickActions}>
            <button className={styles.quickCard} onClick={() => onSendMessage(t('chat.explainConcept').replace(/^[^\s]+ /, ''))}>
              {t('chat.explainConcept')}
            </button>
            <button className={styles.quickCard} onClick={() => onSendMessage(t('chat.writeDoc').replace(/^[^\s]+ /, ''))}>
              {t('chat.writeDoc')}
            </button>
            <button className={styles.quickCard} onClick={() => onSendMessage(t('chat.searchKB').replace(/^[^\s]+ /, ''))}>
              {t('chat.searchKB')}
            </button>
            <button className={styles.quickCard} onClick={() => onSendMessage(t('chat.generatePPT').replace(/^[^\s]+ /, ''))}>
              {t('chat.generatePPT')}
            </button>
          </div>
        </div>
      ) : (
        <MessageList messages={messages} streamContent={streamContent} streamSteps={streamSteps} isGenerating={isGenerating} />
      )}

      <MessageInput
        onSend={onSendMessage}
        onStop={onStopGenerate}
        isGenerating={isGenerating}
        disabled={!conversationTitle}
      />
    </main>
  )
}
