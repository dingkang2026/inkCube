/** src/features/schedule/SubscriptionAdminView.tsx — 定时订阅全宽管理台
 *  （2.11 §8.1，E3 T7）。左列 350px = 新建按钮 + 分类页签 + 状态筛选 + 搜索
 *  （防抖 300ms 服务端 q）+ 批量条 + 订阅行（名称·周期·状态点·未读点）；
 *  右列 = 未选中收件箱（未读优先时间流）/ 选中订阅详情 + 期次历史。
 *  刷新纪律：挂载拉一次 + 写操作后 refresh，零轮询（§9.2 规则 10）。 */
import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, Search, Play, Pencil, Trash2, ArchiveRestore } from 'lucide-react'
import { useToast } from '../knowledge/Toast'
import { useConfirm } from '../../components/ConfirmDialog'
import { apiErrorText } from '../../i18n/errorCodes'
import { ScheduleFormModal, parseInterval, WEEKDAYS } from './ScheduleFormModal'
import { RunRow } from './RunRow'
import { usePagedList } from '../../hooks/usePagedList'
import type { ScheduleRow, ScheduleRunRow } from './useSchedules'
import s from './SubscriptionAdminView.module.css'

const CATEGORIES = ['all', 'industry', 'corporate', 'general'] as const
type Category = (typeof CATEGORIES)[number]
type StatusFilter = 'all' | 'abnormal' | 'paused'

/** 周期人话文案：intervalValue → 「每天 09:00 / 每周一、四 09:00 / 每月 1,15 09:00」 */
function periodText(row: ScheduleRow, t: (k: string, p?: Record<string, unknown>) => string): string {
  const { days, time } = parseInterval(row.interval_type, row.interval_value)
  if (row.interval_type === 'daily') return t('schedule.periodText.daily', { time })
  if (row.interval_type === 'weekly') {
    return t('schedule.periodText.weekly', {
      days: days.map((d) => t(`schedule.wd.${WEEKDAYS[d - 1]}`)).join(t('schedule.wdJoin')),
      time,
    })
  }
  return t('schedule.periodText.monthly', { days: days.join(','), time })
}

interface SubscriptionAdminViewProps {
  onOpenRead: (runId: string) => void
  /** taskId 仅在期次携带 task_id 时回调（收件箱/详情路由已收窄） */
  onOpenReplay: (runId: string, taskId: string) => void
}

export function SubscriptionAdminView({ onOpenRead, onOpenReplay }: SubscriptionAdminViewProps) {
  const { t } = useTranslation()
  const showToast = useToast()
  const confirmDialog = useConfirm()

  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [checked, setChecked] = useState<Set<string>>(new Set())
  const [category, setCategory] = useState<Category>('all')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [search, setSearch] = useState('')
  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<ScheduleRow | null>(null)
  const [rebuildingId, setRebuildingId] = useState<string | null>(null)
  const debounceRef = useRef<number | null>(null)
  // 筛选防抖 300ms（保留原语义）：防抖后的值作为 usePagedList params，输入过程不触发请求
  const [debouncedFilters, setDebouncedFilters] = useState<{
    category: Category; statusFilter: StatusFilter; q: string
  }>({ category: 'all', statusFilter: 'all', q: '' })

  const errCode = (err: unknown) => (err as { errorCode?: string })?.errorCode

  // 列表：筛选变化防抖 300ms 后下沉服务端（0.1dev-guide 统一约定：50/页 + 加载更多）
  useEffect(() => {
    if (debounceRef.current !== null) window.clearTimeout(debounceRef.current)
    debounceRef.current = window.setTimeout(() => {
      debounceRef.current = null
      setDebouncedFilters({ category, statusFilter, q: search })
    }, 300)
    return () => {
      if (debounceRef.current !== null) {
        window.clearTimeout(debounceRef.current)
        debounceRef.current = null
      }
    }
  }, [category, statusFilter, search])

  const listParams = useMemo(() => {
    const p: Record<string, unknown> = {}
    if (debouncedFilters.category !== 'all') p.category = debouncedFilters.category
    if (debouncedFilters.statusFilter !== 'all') p.enabled = 0
    if (debouncedFilters.q.trim()) p.q = debouncedFilters.q.trim()
    return p
  }, [debouncedFilters])

  const {
    items: scheduleRows, hasMore: hasMoreSchedules, loadingMore: loadingMoreSchedules,
    error: schedulesError,
    reload: reloadSchedules, loadMore: loadMoreSchedules,
  } = usePagedList<ScheduleRow>({ channel: 'schedules:list', itemsKey: 'schedules', params: listParams })

  // abnormal/paused 仍为客户端细分（基于 consecutive_failures，服务端只有 enabled 维度）：
  // 过滤作用于当前已加载行，加载更多后趋于完整
  const schedules = useMemo(() => {
    if (debouncedFilters.statusFilter === 'abnormal') return scheduleRows.filter((r) => !r.enabled && r.consecutive_failures > 0)
    if (debouncedFilters.statusFilter === 'paused') return scheduleRows.filter((r) => !r.enabled && r.consecutive_failures === 0)
    return scheduleRows
  }, [scheduleRows, debouncedFilters.statusFilter])

  // 收件箱（未读优先时间流）：原 limit=100 一次性拉取改 50/页「加载更多」
  const {
    items: inbox, hasMore: hasMoreInbox, loadingMore: loadingMoreInbox,
    error: inboxError,
    reload: reloadInbox, loadMore: loadMoreInbox,
  } = usePagedList<ScheduleRunRow>({ channel: 'schedules:inbox', itemsKey: 'runs' })

  // 期次历史：50/页（默认页长）；未选中不拉（enabled 门控），取消选中后右列切收件箱不渲染旧 items
  const {
    items: runs, hasMore: hasMoreRuns, loadingMore: loadingMoreRuns,
    error: runsError,
    reload: reloadRuns, loadMore: loadMoreRuns,
  } = usePagedList<ScheduleRunRow>({
    channel: 'schedules:runs', itemsKey: 'runs',
    params: { id: selectedId ?? '' }, enabled: selectedId !== null,
  })
  // 列表加载失败提示（对齐 FormsTab m-3：静默吞掉会让列表空白且无任何提示）
  useEffect(() => {
    for (const e of [schedulesError, inboxError, runsError]) {
      if (e) showToast(`${t('common.listLoadFailed')}: ${e}`, 'error')
    }
  }, [schedulesError, inboxError, runsError, showToast, t])

  const refreshAll = useCallback(() => {
    void reloadSchedules()
    void reloadInbox()
    if (selectedId) void reloadRuns()
  }, [reloadSchedules, reloadInbox, reloadRuns, selectedId])

  // 行未读点：收件箱中该订阅存在未读 run
  const unreadScheduleIds = new Set(
    inbox.filter((r) => r.status === 'done' && !r.read_at).map((r) => r.schedule_id))

  const toggleChecked = (id: string, on: boolean) => {
    setChecked((prev) => {
      const next = new Set(prev)
      if (on) next.add(id)
      else next.delete(id)
      return next
    })
  }

  const runNow = async (row: ScheduleRow) => {
    try {
      // review P2-6：异步受理——受理即返回（执行在后台完成，终态经通知中心
      // 反馈）；deferred = 上期在途/系统忙，提示稍后重试（重跑文案）
      const res = await window.electronAPI.invoke('schedules:runNow', row.id) as {
        ok?: boolean; deferred?: boolean
      }
      if (res?.deferred) showToast(t('schedule.runNowDeferred'), 'warning')
      else if (res?.ok) showToast(t('schedule.runNowDone', { name: row.name }), 'success')
      else showToast(t('schedule.runNowFailed'), 'error')
    } catch (err) {
      console.error('Run schedule now failed:', err)
      showToast(apiErrorText(errCode(err), t('schedule.runNowFailed'), t), 'error')
    }
    refreshAll()
  }

  /** §6 重建情报档案（绑定/更换去向后的首次构建、档案修复同用）；
   *  同步等待全部分批合并完成，期数多时耗时较长（Loading 由按钮禁用态表达；
   *  in-flight 防双击重复触发——后端同锁互斥，双请求仍会串行浪费配额） */
  const rebuildArchive = async (row: ScheduleRow) => {
    if (rebuildingId) return
    setRebuildingId(row.id)
    try {
      // review P2-3：后端返回真实态 {ok, batches}——部分批失败不得虚报成功，
      // 分级提示（已完成批次不回滚，可再次重建续批）
      const res = (await window.electronAPI.invoke('schedules:rebuildArchive', row.id)) as { ok?: boolean }
      if (res?.ok) {
        showToast(t('schedule.rebuildArchiveDone', { name: row.name }), 'success')
      } else {
        showToast(t('schedule.rebuildArchivePartial', { name: row.name }), 'error')
      }
    } catch (err) {
      console.error('Rebuild archive failed:', err)
      showToast(apiErrorText(errCode(err), t('schedule.rebuildArchiveFailed'), t), 'error')
    } finally {
      setRebuildingId(null)
    }
    refreshAll()
  }

  const toggleEnabled = async (row: ScheduleRow, enabled: boolean) => {
    try {
      await window.electronAPI.invoke('schedules:toggle', { id: row.id, enabled })
    } catch (err) {
      console.error('Toggle schedule failed:', err)
      showToast(apiErrorText(errCode(err), t('schedule.opFailed'), t), 'error')
    }
    refreshAll()
  }

  /** 单条删除：确认弹层列出执行历史影响（§9.2 规则 5），与批量删除同一 confirm */
  const deleteOne = async (id: string) => {
    const ok = await confirmDialog({
      message: t('schedule.batchDeleteConfirm', { n: 1 }),
      danger: true,
    })
    if (!ok) return
    try {
      await window.electronAPI.invoke('schedules:delete', id)
    } catch (err) {
      console.error('Delete schedule failed:', err)
      showToast(apiErrorText(errCode(err), t('schedule.opFailed'), t), 'error')
    }
    if (selectedId === id) setSelectedId(null)
    setChecked((prev) => {
      const next = new Set(prev)
      next.delete(id)
      return next
    })
    refreshAll()
  }

  const batchDelete = async () => {
    const ids = [...checked]
    const ok = await confirmDialog({
      message: t('schedule.batchDeleteConfirm', { n: ids.length }),
      danger: true,
    })
    if (!ok) return
    for (const id of ids) {
      try {
        await window.electronAPI.invoke('schedules:delete', id)
      } catch (err) {
        console.error('Batch delete schedule failed:', err)
      }
    }
    setChecked(new Set())
    setSelectedId(null)
    refreshAll()
  }

  /** 单条删除期次（2.11 §4.1，2026-10-20 审计补齐）：确认后 invoke，成功按
   *  所在列表 reload（limitsRef 模式按已加载量重拉，不退回第一页）；
   *  running 拒删（后端 409）在 UI 层前置拦截——不渲染删除按钮 */
  const deleteRun = async (run: ScheduleRunRow, source: 'inbox' | 'runs') => {
    const ok = await confirmDialog({
      message: t('schedule.deleteRunConfirm'),
      danger: true,
    })
    if (!ok) return
    try {
      await window.electronAPI.invoke('schedules:deleteRun', run.id)
      if (source === 'inbox') void reloadInbox()
      else void reloadRuns()
    } catch (err) {
      console.error('Delete schedule run failed:', err)
      showToast(apiErrorText(errCode(err), t('schedule.opFailed'), t), 'error')
    }
  }

  const batchToggle = async (enabled: boolean) => {
    for (const id of checked) {
      try {
        await window.electronAPI.invoke('schedules:toggle', { id, enabled })
      } catch (err) {
        console.error('Batch toggle schedule failed:', err)
      }
    }
    setChecked(new Set())
    refreshAll()
  }

  const selected = schedules.find((r) => r.id === selectedId) ?? null

  return (
    <div className={s.page}>
      {/* ── 左列：筛选 + 订阅列表 ── */}
      <div className={s.left}>
        <button className={s.newBtn} onClick={() => { setEditing(null); setFormOpen(true) }}>
          <Plus size={14} />{t('schedule.newSubscription')}
        </button>
        <div className={s.tabs} role="tablist">
          {CATEGORIES.map((cat) => (
            <button key={cat} role="tab" aria-selected={category === cat}
              className={`${s.tab} ${category === cat ? s.tabActive : ''}`}
              onClick={() => setCategory(cat)}>
              {t(`schedule.tab.${cat}`)}
            </button>
          ))}
        </div>
        <div className={s.filters}>
          <select
            aria-label={t('schedule.statusFilter')}
            className={s.select}
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
          >
            <option value="all">{t('schedule.statusAll')}</option>
            <option value="abnormal">{t('schedule.statusAbnormal')}</option>
            <option value="paused">{t('schedule.statusPaused')}</option>
          </select>
          <div className={s.searchBox}>
            <Search size={13} />
            <input
              placeholder={t('schedule.searchPlaceholder')}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
        </div>
        {checked.size > 0 && (
          <div className={s.batchBar} data-testid="batch-bar">
            <span>{t('schedule.checkedCount', { n: checked.size })}</span>
            <button onClick={() => batchToggle(false)}>{t('schedule.batchPause')}</button>
            <button onClick={() => batchToggle(true)}>{t('schedule.batchResume')}</button>
            <button className={s.dangerBtn} onClick={() => batchDelete()}>
              {t('schedule.batchDelete')}
            </button>
          </div>
        )}
        <div className={s.rows}>
          {schedules.length === 0 ? (
            <div className={s.empty}>{t('schedule.empty')}</div>
          ) : schedules.map((row) => (
            <div
              key={row.id}
              className={`${s.schedRow} ${selectedId === row.id ? s.schedRowActive : ''}`}
            >
              <input
                type="checkbox"
                aria-label={t('schedule.checkRow', { name: row.name })}
                checked={checked.has(row.id)}
                onChange={(e) => toggleChecked(row.id, e.target.checked)}
              />
              <button className={s.schedMain} onClick={() => setSelectedId(row.id)}>
                <span className={`${s.stateDot} ${row.is_running ? s.dotRunning : ''} ${row.consecutive_failures > 0 ? s.dotAbnormal : ''} ${!row.enabled ? s.dotPaused : ''}`} />
                <span className={s.schedName}>{row.name}</span>
                <span className={s.schedPeriod}>{periodText(row, t)}</span>
                {unreadScheduleIds.has(row.id) && <span className={s.unreadDot} />}
              </button>
              <span className={s.rowActions}>
                <button title={t('schedule.runNow')} onClick={() => void runNow(row)}>
                  <Play size={13} />
                </button>
                <button title={t('schedule.edit')} onClick={() => { setEditing(row); setFormOpen(true) }}>
                  <Pencil size={13} />
                </button>
                <button
                  title={t('schedule.delete')}
                  className={s.dangerBtn}
                  onClick={() => void deleteOne(row.id)}
                >
                  <Trash2 size={13} />
                </button>
              </span>
            </div>
          ))}
          {hasMoreSchedules && (
            <button
              className={s.loadMoreBtn}
              onClick={() => { void loadMoreSchedules() }}
              disabled={loadingMoreSchedules}
            >
              {loadingMoreSchedules ? t('common.loading') : t('common.loadMore')}
            </button>
          )}
        </div>
      </div>

      {/* ── 右列：收件箱 / 订阅详情 ── */}
      <div className={s.right}>
        {!selected ? (
          <>
            <div className={s.paneTitle}>{t('schedule.inboxTitle')}</div>
            <div className={s.runList}>
              {inbox.length === 0 ? (
                <div className={s.empty}>{t('schedule.inboxEmpty')}</div>
              ) : inbox.map((run) => (
                <RunRow key={run.id} run={run} onOpen={() => onOpenRead(run.id)}
                  onDelete={() => void deleteRun(run, 'inbox')} />
              ))}
              {hasMoreInbox && (
                <button
                  className={s.loadMoreBtn}
                  onClick={() => { void loadMoreInbox() }}
                  disabled={loadingMoreInbox}
                >
                  {loadingMoreInbox ? t('common.loading') : t('common.loadMore')}
                </button>
              )}
            </div>
          </>
        ) : (
          <>
            <div className={s.detailCard}>
              <div className={s.detailHead}>
                <span className={s.detailName}>{selected.name}</span>
                <span className={s.detailPeriod}>{periodText(selected, t)}</span>
              </div>
              <div className={s.detailMeta}>
                <span>{t(`schedule.tab.${selected.category}`, { defaultValue: selected.category })}</span>
                <span>·</span>
                <span>
                  {selected.enabled
                    ? t('schedule.stateEnabled')
                    : t('schedule.statePaused')}
                </span>
                {selected.consecutive_failures > 0 && (
                  <span className={s.abnormalText}>
                    {t('schedule.failures', { n: selected.consecutive_failures })}
                  </span>
                )}
              </div>
              <p className={s.detailPrompt}>{selected.prompt}</p>
              <div className={s.detailActions}>
                <button onClick={() => void runNow(selected)}>
                  <Play size={13} />{t('schedule.runNow')}
                </button>
                {selected.target_id && (
                  <button
                    disabled={rebuildingId === selected.id}
                    onClick={() => void rebuildArchive(selected)}
                  >
                    <ArchiveRestore size={13} />{t('schedule.rebuildArchive')}
                  </button>
                )}
                <button onClick={() => void toggleEnabled(selected, !selected.enabled)}>
                  {selected.enabled ? t('schedule.pause') : t('schedule.resume')}
                </button>
                <button onClick={() => { setEditing(selected); setFormOpen(true) }}>
                  <Pencil size={13} />{t('schedule.edit')}
                </button>
                <button
                  className={s.dangerBtn}
                  onClick={() => void deleteOne(selected.id)}
                >
                  <Trash2 size={13} />{t('schedule.delete')}
                </button>
              </div>
            </div>
            <div className={s.paneTitle}>{t('schedule.runsTitle')}</div>
            <div className={s.runList}>
              {runs.length === 0 ? (
                <div className={s.empty}>{t('schedule.runsEmpty')}</div>
              ) : runs.map((run) => (
                <RunRow
                  key={run.id}
                  run={run}
                  onOpen={() => {
                    if (run.task_id) onOpenReplay(run.id, run.task_id)
                    else onOpenRead(run.id)
                  }}
                  onDelete={() => void deleteRun(run, 'runs')}
                />
              ))}
              {hasMoreRuns && (
                <button
                  className={s.loadMoreBtn}
                  onClick={() => { void loadMoreRuns() }}
                  disabled={loadingMoreRuns}
                >
                  {loadingMoreRuns ? t('common.loading') : t('common.loadMore')}
                </button>
              )}
            </div>
          </>
        )}
      </div>

      <ScheduleFormModal
        open={formOpen}
        editing={formOpen ? editing : null}
        onClose={() => setFormOpen(false)}
        onSaved={refreshAll}
      />
    </div>
  )
}
