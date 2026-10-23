/** src/features/schedule/ReadView.tsx — 期次阅读页（2.11 §8.2，E3 T8）。
 *  挂载即 readReport + markRead（打开即已读，规则 10；markRead 仅 done——
 *  running/failed 无未读语义）；摘要卡 = run.summary 直读（无文件解析，
 *  解析失败显示占位不阻塞阅读 §8.3）；running 态正文占位（2026-10-23）；
 *  Markdown 全文（D5 副本，含来源节）。
 *  头部动作条（2026-10-22）：返回（回宿主页：管理台恢复选中 / 首页）+
 *  执行过程（有 task_id 时切 RunReplay；期次行点击已统一进本页，此为回放唯一入口）；
 *  底部动作条：订阅设置（编辑弹层，去向绑定/更换在③步）；已绑定去向 →
 *  「已沉淀至 {目标名}·档案」可点跳容器（2026-10-23）；失败 run → 原因 + 重跑（runNow）；
 *  running run → 执行中占位 + 头部「执行过程」实时回放可达（2026-10-23）；
 *  Ctrl/Cmd+F 文档内查找（块级定位，复用 useInFileFind + FindBar，2026-10-23）。 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeft } from 'lucide-react'
import { Markdown } from '../../components/Markdown'
import { useToast } from '../knowledge/Toast'
import { ScheduleFormModal } from './ScheduleFormModal'
import type { ScheduleRow, ScheduleRunRow } from './useSchedules'
import { FindBar } from '../../components/FindBar'
import { useInFileFind, scrollElementToCenter } from '../../hooks/useInFileFind'
import s from './ReadView.module.css'

/** 文档内查找块级选择器（同 ReportPreview：叶子块匹配避免祖先容器重复计数） */
const FIND_BLOCK_SELECTOR = 'p, h1, h2, h3, h4, h5, h6, li, pre, blockquote, td, th'

interface ReadViewProps {
  runId: string
  /** 返回宿主页（App 分发：管理台恢复选中订阅 / 首页） */
  onBack: () => void
  /** 「执行过程」：App 切 scheduleView replay 态（RunReplay） */
  onReplay: (taskId: string) => void
}

export function ReadView({ runId, onBack, onReplay }: ReadViewProps) {
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
        // 打开即已读（§8.2 规则 10）：fire-and-forget，失败不影响阅读；
        // 仅 done——running/failed 无未读语义，running 提前标已读会吞掉
        // 终态后的未读点与新产出计数（2026-10-23）
        if (res.run.status === 'done') {
          void window.electronAPI.invoke('schedules:markRead', runId).catch(() => {})
        }
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

  // 跳目标容器（2026-10-23 动线补全：订阅→沉淀物）：复用全局导航分流事件
  // （Activity Hub 同款，App.tsx 原生支持 container='baselib' 分流）
  const openTarget = () => {
    if (!schedule || !schedule.target_type || !schedule.target_id) return
    window.dispatchEvent(new CustomEvent('app:navigate-project-tab', {
      detail: {
        projectId: schedule.target_id,
        container: schedule.target_type === 'baselib' ? 'baselib' : 'project',
      },
    }))
  }

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

  // ── 文档内查找（块级定位，复用 ReportPreview 同款框架 useInFileFind + FindBar）──
  // canFind 与 Markdown 渲染分支同条件：running 无正文不可查（2026-10-23）
  const canFind = !!run && run.status !== 'running'
  const reportRef = useRef<HTMLDivElement | null>(null)
  // 叶子块元素与文本缓存：元素供定位，文本供匹配（正文渲染后收集，随正文/可见态变化重算）
  const blocksRef = useRef<HTMLElement[]>([])
  const [blockTexts, setBlockTexts] = useState<string[]>([])
  useEffect(() => {
    const container = reportRef.current
    if (!canFind || !container) {
      blocksRef.current = []
      setBlockTexts([])
      return
    }
    const leaves = Array.from(container.querySelectorAll<HTMLElement>(FIND_BLOCK_SELECTOR))
      .filter((el) => !el.querySelector(FIND_BLOCK_SELECTOR))
    blocksRef.current = leaves
    setBlockTexts(leaves.map((el) => (el.textContent || '').toLowerCase()))
  }, [canFind, content])

  // 闪烁 timer 托管（同 ReportPreview：1500ms = CSS 动画 1.4s + 100ms 余量）
  const flashTimerRef = useRef<number | undefined>(undefined)
  const locateBlock = useCallback((idx: number) => {
    const container = reportRef.current
    const el = blocksRef.current[idx]
    if (!container || !el) return
    scrollElementToCenter(container, el)
    window.clearTimeout(flashTimerRef.current)
    el.classList.remove(s.findFlash)
    void el.offsetWidth
    el.classList.add(s.findFlash)
    flashTimerRef.current = window.setTimeout(() => el.classList.remove(s.findFlash), 1500)
  }, [])

  const computeBlockMatches = useCallback(
    (q: string) => {
      if (!q || blockTexts.length === 0) return []
      const needle = q.toLowerCase()
      const out: number[] = []
      blockTexts.forEach((txt, i) => { if (txt.includes(needle)) out.push(i) })
      return out
    },
    [blockTexts],
  )

  const find = useInFileFind({ compute: computeBlockMatches, locate: locateBlock })

  // Ctrl/Cmd+F 接管：ReadView 为全内容区页，window 级监听（同 ReportPreview 先例）
  useEffect(() => {
    if (!canFind) return
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === 'f') {
        e.preventDefault()
        e.stopPropagation()
        find.openFind()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [canFind, find.openFind])

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
        <button
          className={s.backBtn}
          data-testid="read-back-btn"
          title={t('schedule.back')}
          aria-label={t('schedule.back')}
          onClick={onBack}
        >
          <ArrowLeft size={15} />
        </button>
        <h2 className={s.title}>{schedule.name} · {dateLabel}</h2>
        <span className={`${s.state} ${failed ? s.stateFailed : ''}`}>
          {t(`schedule.read.state.${run.status}`)}
        </span>
        {duration && <span className={s.duration}>{duration}</span>}
        <span className={s.spacer} />
        {run.task_id && (
          <button className={s.actionBtn} data-testid="read-open-replay" onClick={() => onReplay(run.task_id!)}>
            {t('schedule.read.openReplay')}
          </button>
        )}
      </header>
      {find.findOpen && canFind && (
        <FindBar
          inputRef={find.inputRef}
          query={find.findQuery}
          count={find.matches.length}
          current={find.safeIdx}
          onQueryChange={find.setFindQuery}
          onPrev={() => find.gotoMatch(find.safeIdx - 1)}
          onNext={() => find.gotoMatch(find.safeIdx + 1)}
          onClose={find.closeFind}
        />
      )}
      {failed && (
        <div className={s.failedCard}>
          <p className={s.failedText}>{run.error || t('schedule.read.failedNoReason')}</p>
          <button className={s.rerunBtn} onClick={() => void rerun()} disabled={rerunning}>
            {rerunning ? t('schedule.read.rerunning') : t('schedule.read.rerun')}
          </button>
        </div>
      )}
      <div className={s.summaryCard}>
        {run.status === 'running' ? (
          // 执行中态（2026-10-23）：正文占位提示，无轮询——完成后返回重进即见终态
          <p className={s.hint}>{t('schedule.read.runningHint')}</p>
        ) : run.summary?.length ? (
          <ul className={s.summaryList}>
            {run.summary.map((p) => <li key={p}>{p}</li>)}
          </ul>
        ) : (
          <p className={s.hint}>{t('schedule.read.noSummary')}</p>
        )}
      </div>
      <div className={s.report} ref={reportRef}>
        {run.status === 'running' ? null : <Markdown content={content} />}
      </div>
      <footer className={s.actions}>
        {schedule.target_type && schedule.target_id && targetName && (
          <button
            className={s.archived}
            data-testid="read-archived"
            title={t('schedule.read.archivedJumpHint', { name: schedule.name })}
            onClick={openTarget}
          >
            {t('schedule.read.archivedTo')}
            <strong>{targetName}</strong>
            {t('schedule.read.archivedUnit')}
          </button>
        )}
        <span className={s.spacer} />
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
