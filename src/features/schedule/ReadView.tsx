/** src/features/schedule/ReadView.tsx — 期次阅读页（2.11 §8.2，E3 T8）。
 *  挂载即 readReport + markRead（打开即已读，规则 10）；摘要卡 = run.summary
 *  直读（无文件解析，解析失败显示占位不阻塞阅读 §8.3）；Markdown 全文（D5
 *  副本，含来源节）；底部动作条：订阅设置（编辑弹层，去向绑定/更换在③步）/
 *  打开原任务（onReplay → RunReplay）；已绑定去向 → 「已沉淀至 {目标名}·档案」
 *  （目标名经列表通道懒解析）；失败 run → 原因 + 重跑（runNow）。
 *  全内容区替换渲染，无返回按钮，侧栏 NavTabs 切换退出（0.3 §2.1 peer 页惯例）。 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Markdown } from '../../components/Markdown'
import { useToast } from '../knowledge/Toast'
import { ScheduleFormModal } from './ScheduleFormModal'
import type { ScheduleRow, ScheduleRunRow } from './useSchedules'
import s from './ReadView.module.css'

interface ReadViewProps {
  runId: string
  /** 「打开原任务」：App 切 scheduleView replay 态（RunReplay） */
  onReplay: (taskId: string) => void
}

export function ReadView({ runId, onReplay }: ReadViewProps) {
  const { t } = useTranslation()
  const showToast = useToast()
  const [run, setRun] = useState<ScheduleRunRow | null>(null)
  const [schedule, setSchedule] = useState<ScheduleRow | null>(null)
  const [content, setContent] = useState('')
  const [loadFailed, setLoadFailed] = useState(false)
  const [targetName, setTargetName] = useState<string | null>(null)
  const [formOpen, setFormOpen] = useState(false)
  const [rerunning, setRerunning] = useState(false)

  useEffect(() => {
    let cancelled = false
    setRun(null)
    setSchedule(null)
    setContent('')
    setLoadFailed(false)
    setTargetName(null)
    const load = async () => {
      try {
        const res = await window.electronAPI.invoke('schedules:readReport', runId) as {
          run?: ScheduleRunRow
          schedule?: ScheduleRow
          content?: string
        }
        if (cancelled) return
        if (!res.run || !res.schedule) {
          setLoadFailed(true)
          return
        }
        setRun(res.run)
        setSchedule(res.schedule)
        setContent(res.content ?? '')
        // 打开即已读（§8.2 规则 10）：fire-and-forget，失败不影响阅读
        void window.electronAPI.invoke('schedules:markRead', runId).catch(() => {})
      } catch {
        if (!cancelled) setLoadFailed(true)
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [runId])

  // 沉淀态目标名懒解析（§8.2）：绑定去向时经列表通道 find（订阅设置弹层同源）
  useEffect(() => {
    if (!schedule?.target_type || !schedule.target_id) return
    let cancelled = false
    const targetId = schedule.target_id
    const resolve = async () => {
      try {
        if (schedule.target_type === 'project') {
          // 选择器形态：limit=0 全量（按 id 反查目标名，分页不得截断）
          const res = await window.electronAPI.invoke('knowledge:listProjects', { limit: 0 }) as {
            projects?: { id: string; name: string }[]
          }
          if (!cancelled) setTargetName(res.projects?.find((p) => p.id === targetId)?.name ?? null)
        } else {
          const res = await window.electronAPI.invoke('baseLib:list', { limit: 0 }) as {
            base_libs?: { id: string; name: string }[]
          }
          if (!cancelled) setTargetName(res.base_libs?.find((b) => b.id === targetId)?.name ?? null)
        }
      } catch {
        if (!cancelled) setTargetName(null)
      }
    }
    void resolve()
    return () => {
      cancelled = true
    }
  }, [schedule])

  const rerun = async () => {
    if (rerunning || !schedule) return
    setRerunning(true)
    try {
      // R3-P2-4：run-now 忙时让路 {ok:false, deferred:true}——非失败，分级提示
      // （与管理台 runNow 同语义），不得假报「已开始重跑」
      const res = await window.electronAPI.invoke('schedules:runNow', schedule.id) as {
        ok?: boolean
        deferred?: boolean
      }
      if (res?.deferred) showToast(t('schedule.read.rerunDeferred'), 'info')
      else if (res?.ok) showToast(t('schedule.read.rerunQueued'), 'info')
      else showToast(t('schedule.read.rerunFailed'), 'error')
    } catch {
      showToast(t('schedule.read.rerunFailed'), 'error')
    } finally {
      setRerunning(false)
    }
  }

  if (loadFailed) {
    return <div className={s.page}><p className={s.hint}>{t('schedule.read.loadFailed')}</p></div>
  }
  if (!run || !schedule) {
    return <div className={s.page}><p className={s.hint}>{t('schedule.read.loading')}</p></div>
  }

  const start = new Date(run.started_at)
  const pad = (n: number) => String(n).padStart(2, '0')
  const dateLabel = `${start.getMonth() + 1}/${start.getDate()} ${pad(start.getHours())}:${pad(start.getMinutes())}`
  let duration = ''
  if (run.finished_at) {
    const secs = Math.max(0, Math.round((new Date(run.finished_at).getTime() - start.getTime()) / 1000))
    duration = secs >= 60 ? `${Math.floor(secs / 60)}m${secs % 60}s` : `${secs}s`
  }
  const failed = run.status === 'failed'

  return (
    <div className={s.page}>
      <header className={s.head}>
        <h2 className={s.title}>{schedule.name} · {dateLabel}</h2>
        <span className={`${s.state} ${failed ? s.stateFailed : ''}`}>
          {t(`schedule.read.state.${run.status}`)}
        </span>
        {duration && <span className={s.duration}>{duration}</span>}
      </header>
      {failed && (
        <div className={s.failedCard}>
          <p className={s.failedText}>{run.error || t('schedule.read.failedNoReason')}</p>
          <button className={s.rerunBtn} onClick={() => void rerun()} disabled={rerunning}>
            {rerunning ? t('schedule.read.rerunning') : t('schedule.read.rerun')}
          </button>
        </div>
      )}
      <div className={s.summaryCard}>
        {run.summary?.length ? (
          <ul className={s.summaryList}>
            {run.summary.map((p) => <li key={p}>{p}</li>)}
          </ul>
        ) : (
          <p className={s.hint}>{t('schedule.read.noSummary')}</p>
        )}
      </div>
      <div className={s.report}>
        <Markdown content={content} />
      </div>
      <footer className={s.actions}>
        {schedule.target_type && schedule.target_id && targetName && (
          <span className={s.archived} data-testid="read-archived">
            {t('schedule.read.archivedTo')}
            <strong>{targetName}</strong>
            {t('schedule.read.archivedUnit')}
          </span>
        )}
        <span className={s.spacer} />
        {run.task_id && (
          <button className={s.actionBtn} onClick={() => onReplay(run.task_id!)}>
            {t('schedule.read.openTask')}
          </button>
        )}
        <button className={s.actionBtn} onClick={() => setFormOpen(true)}>
          {t('schedule.read.settings')}
        </button>
      </footer>
      {formOpen && (
        <ScheduleFormModal
          open
          editing={schedule}
          onClose={() => setFormOpen(false)}
          onSaved={() => setFormOpen(false)}
        />
      )}
    </div>
  )
}
