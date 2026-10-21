/** src/features/activity/NotificationCenter.tsx — 通知中心全局面板（2.11 §6.1 D）
 *  历史面 = notifications:list 分页（打开时拉首页 + 「加载更多」，一次性查询，台账零增长）；
 *  实时面 = feed 注入（面板打开期间新到达的通知经 ActivityContext 实时上浮，按 id 去重）。
 *  「全部已读」走 ActivityContext.markAllRead（readAll 一次 IPC + feed/徽章乐观全清）；
 *  条目点击 = markRead + 关面板 + navigateToNotification；行内删除 = 一次 IPC +
 *  视图层 hiddenIds 隐藏（不改 ActivityContext，徽章不受影响，§6.4 规则 6）。
 *  历史受后端 keep=100 清理上限约束（§6.4 规则 5，超出即消失是设计预期）。 */
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { CheckCheck, X } from 'lucide-react'
import { useActivity } from './ActivityContext'
import { navigateToNotification } from './ActivityMonitor'
import { NotificationRow } from './NotificationRow'
import type { NotificationItem } from './types'
import s from './NotificationCenter.module.css'

const PAGE_SIZE = 50

interface NotificationCenterProps {
  open: boolean
  onClose: () => void
}

export function NotificationCenter({ open, onClose }: NotificationCenterProps) {
  const { t } = useTranslation()
  const { feed, markRead, markAllRead } = useActivity()
  const [paged, setPaged] = useState<NotificationItem[]>([])
  const [total, setTotal] = useState(0)
  // 服务端游标：已消费的 offset 位置（+= 每页返回条数，与去重无关）。
  // hasMore 不能用 paged.length：面板打开期间新到达会使后续分页与已加载尾部
  // 重叠（append 按 id 去重丢弃），paged.length 永远追不上 total → 按钮常驻、
  // 点击空转（review 问题 1）；也不能用 items.length（live 注入条目会抵扣分页
  // 名额致按钮提前消失）
  const [nextOffset, setNextOffset] = useState(0)
  const [loading, setLoading] = useState(false)
  // 本面板会话内已删除条目：仅视图层过滤（分页历史 + feed live 注入统一生效）；
  // 不改 ActivityContext——徽章计数与首页动态区不随删除变化，下次打开自然重置
  const [hiddenIds, setHiddenIds] = useState<ReadonlySet<number>>(new Set())

  const loadPage = useCallback(async (offset: number) => {
    setLoading(true)
    try {
      const res = await window.electronAPI.invoke('notifications:list', { offset, limit: PAGE_SIZE }) as { items?: NotificationItem[]; total?: number }
      setTotal(res.total ?? 0)
      setNextOffset(offset + (res.items?.length ?? 0))
      setPaged((prev) => {
        if (offset === 0) return res.items ?? []
        // append 按 id 去重：面板打开期间有新通知到达（服务端列表头部插入）时，
        // offset 基于旧快照会与已加载尾部重叠（review M-1），防重复渲染
        const seen = new Set(prev.map((p) => p.id))
        return [...prev, ...(res.items ?? []).filter((n) => !seen.has(n.id))]
      })
    } catch {
      // 失败保持现有列表（历史面非关键路径，下次打开重试）
    } finally {
      setLoading(false)
    }
  }, [])

  // 打开时拉首页；打开期间新到达的条目经 feed 实时注入（下方合并），无需重拉
  useEffect(() => {
    if (open) {
      setPaged([])
      setTotal(0)
      setNextOffset(0)
      setHiddenIds(new Set())
      void loadPage(0)
    }
  }, [open, loadPage])

  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if (e.key === 'Escape') onClose()
  }, [onClose])

  useEffect(() => {
    if (!open) return
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [open, handleKeyDown])

  if (!open) return null

  // feed 实时注入：不在分页结果内的条目（打开期间新到达）上浮，按 id 去重降序；
  // 本会话已删除条目统一在最终列表过滤（分页历史与 live 注入同口径）
  const pagedIds = new Set(paged.map((n) => n.id))
  const liveItems = feed.filter((n) => !pagedIds.has(n.id))
  const items = [...liveItems, ...paged]
    .filter((n) => !hiddenIds.has(n.id))
    .sort((a, b) => b.id - a.id)
  const unreadCount = items.filter((n) => !n.read_at).length
  const hasMore = nextOffset < total

  const handleOpen = (n: NotificationItem) => {
    void markRead([n.id])
    onClose()
    navigateToNotification(n)
  }

  const handleReadAll = () => {
    void markAllRead()
    const now = new Date().toISOString()
    setPaged((prev) => prev.map((n) => (n.read_at ? n : { ...n, read_at: now })))
  }

  // 行内删除（§6.4 规则 6）：幂等端点 + 视图层隐藏；失败保持行原样（非关键路径，
  // 下次打开重拉自然恢复），与 loadPage 的静默容错同口径
  const handleDelete = (n: NotificationItem) => {
    void window.electronAPI.invoke('notifications:delete', { id: n.id })
      .then(() => {
        setHiddenIds((prev) => new Set(prev).add(n.id))
        setTotal((prev) => Math.max(0, prev - 1))
      })
      .catch(() => { /* 保持行可见，用户可重试 */ })
  }

  return (
    <div className={s.overlay} onClick={onClose}>
      <div className={s.panel} onClick={(e) => e.stopPropagation()}>
        <div className={s.header}>
          <span className={s.title}>{t('notificationCenter.title')}</span>
          <button
            className={s.readAll}
            onClick={handleReadAll}
            disabled={unreadCount === 0}
            title={t('notificationCenter.markAllRead')}
          >
            <CheckCheck size={14} />
            {t('notificationCenter.markAllRead')}
          </button>
          <button className={s.close} onClick={onClose} title={t('window.close')}>
            <X size={16} />
          </button>
        </div>
        <div className={s.list}>
          {items.length === 0 ? (
            <div className={s.empty}>{t('notificationCenter.empty')}</div>
          ) : (
            items.map((n) => (
              <NotificationRow key={n.id} item={n} onOpen={handleOpen} onDelete={handleDelete} />
            ))
          )}
        </div>
        {hasMore && (
          <button
            className={s.loadMore}
            onClick={() => void loadPage(nextOffset)}
            disabled={loading}
          >
            {loading ? '…' : t('notificationCenter.loadMore')}
          </button>
        )}
      </div>
    </div>
  )
}
