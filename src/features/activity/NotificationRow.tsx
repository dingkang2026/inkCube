/** src/features/activity/NotificationRow.tsx — 通知行共享渲染（2.11 §6.1）
 *  首页动态区与通知中心共用：kind 色点（四态，与 ActivityMonitor dispatchToasts
 *  同一映射 §6.2）+ i18n 文案（activity.<entity_type>.<kind>，后端零文案）+
 *  相对时间 + 未读点；点击行为由调用方注入（markRead / 导航 / 关面板各异）。
 *  onDelete 注入时悬停显行内删除（仅通知中心传入；首页动态区速览无删除）。 */
import { useTranslation } from 'react-i18next'
import { Trash2 } from 'lucide-react'
import type { NotificationItem } from './types'
import s from './NotificationRow.module.css'

interface NotificationRowProps {
  item: NotificationItem
  onOpen: (n: NotificationItem) => void
  onDelete?: (n: NotificationItem) => void
}

export function NotificationRow({ item, onOpen, onDelete }: NotificationRowProps) {
  const { t } = useTranslation()
  const dot = item.kind === 'failed' ? s.dotErr
    : item.kind === 'paused' || item.kind === 'awaiting_input' || item.kind === 'due' ? s.dotInfo
    : item.kind === 'file_changed' || item.kind === 'overdue' || item.kind === 'archive_failed'
      || item.kind === 'missed' || item.kind === 'interrupted' ? s.dotWarn
    : s.dotOk
  const minutes = Math.floor((Date.now() - new Date(item.created_at).getTime()) / 60000)
  const hours = Math.floor(minutes / 60)
  const time = minutes < 1 ? t('activity.time.justNow')
    : minutes < 60 ? t('activity.time.minutesAgo', { count: minutes })
    : hours < 24 ? t('activity.time.hoursAgo', { count: hours })
    : t('activity.time.daysAgo', { count: Math.floor(hours / 24) })

  return (
    <div
      className={`${s.row} ${!item.read_at ? s.unread : ''}`}
      onClick={() => onOpen(item)}
    >
      <span className={`${s.dot} ${dot}`} />
      <span className={s.text}>
        {t(`activity.${item.entity_type}.${item.kind}`, { title: item.entity_title || '' })}
      </span>
      {!item.read_at && <span className={s.unreadDot} />}
      <span className={s.time}>{time}</span>
      {onDelete && (
        <button
          className={s.deleteBtn}
          title={t('notificationCenter.delete')}
          onClick={(e) => {
            e.stopPropagation()
            onDelete(item)
          }}
        >
          <Trash2 size={12} />
        </button>
      )}
    </div>
  )
}
