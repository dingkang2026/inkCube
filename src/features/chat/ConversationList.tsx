import { useTranslation } from 'react-i18next'
import type { Conversation } from '../../types'
import { ConversationItem } from './ConversationItem'
import styles from './ConversationList.module.css'

interface ConversationListProps {
  conversations: Conversation[]
  activeId: string | null
  onSelect: (id: string) => void
  onNew: () => void
  onDelete: (id: string) => void
  onRename: (id: string, title: string) => void
}

export function ConversationList({
  conversations,
  activeId,
  onSelect,
  onNew,
  onDelete,
  onRename,
}: ConversationListProps) {
  const { t } = useTranslation()

  return (
    <div className={styles.list}>
      {/* New chat button — always visible at top */}
      <button className={styles.newBtn} onClick={onNew}>
        <span className={styles.newIcon}>+</span>
        {t('sidebar.newChat')}
      </button>

      {conversations.length === 0 ? (
        <div className={styles.empty}>
          {t('sidebar.empty')}
        </div>
      ) : (
        groupByTime(conversations, t).map(([group, items]) => (
          <div key={group} className={styles.group}>
            <div className={styles.label}>
              <span>{group}</span>
            </div>
            {items.map((conv) => (
              <ConversationItem
                key={conv.id}
                conversation={conv}
                isActive={conv.id === activeId}
                onSelect={() => onSelect(conv.id)}
                onDelete={() => onDelete(conv.id)}
                onRename={(title) => onRename(conv.id, title)}
              />
            ))}
          </div>
        ))
      )}
    </div>
  )
}

function groupByTime(
  conversations: Conversation[],
  t: (key: string) => string,
): [string, Conversation[]][] {
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const yesterday = new Date(today.getTime() - 86400000)

  const groups: Record<string, Conversation[]> = {}

  for (const conv of conversations) {
    const date = new Date(conv.updated_at)
    let group: string
    if (date >= today) group = t('sidebar.today')
    else if (date >= yesterday) group = t('sidebar.yesterday')
    else group = t('sidebar.earlier')

    if (!groups[group]) groups[group] = []
    groups[group].push(conv)
  }

  const order = [t('sidebar.today'), t('sidebar.yesterday'), t('sidebar.earlier')]
  return order.filter((g) => groups[g]).map((g) => [g, groups[g]])
}
