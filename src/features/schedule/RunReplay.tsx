/** src/features/schedule/RunReplay.tsx — 期次执行回放（2.11 §8.3，E3 T8）。
 *  只读：复用 TaskMessageList（isGenerating=false），无输入区/暂停/操作菜单——
 *  档案语义（规则 6），workspace 保留期清理后消息流仍可回看。
 *  标题「{订阅名} · M/D 执行回放（只读）」（订阅名/期次经 readReport 取）。
 *  头部动作条（2026-10-22）：返回（回宿主页：管理台恢复选中 / 首页）+
 *  查看报告（切 ReadView——修复期次历史进回放后无报告入口的不对称）。
 *  传给 TaskMessageList 的 props 必须引用稳定：字面量每次渲染新引用会
 *  触发其自动滚 effect（isGenerating=false 本无流式变化）空转拉底。 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeft, FileText } from 'lucide-react'
import { TaskMessageList } from '../task/TaskMessageList'
import s from './RunReplay.module.css'

interface RunReplayProps {
  runId: string
  taskId: string
  /** 返回宿主页（App 分发：管理台恢复选中订阅 / 首页） */
  onBack: () => void
  /** 查看报告：App 切 scheduleView read 态（同 runId，来路沿用） */
  onOpenReport: () => void
}

/** 引用稳定化（见文件头注释）：模块级常量，避免每次渲染新引用 */
const EMPTY_STEPS: never[] = []
const EMPTY_STREAM = ''

type Msgs = Parameters<typeof TaskMessageList>[0]['messages']

export function RunReplay({ runId, taskId, onBack, onOpenReport }: RunReplayProps) {
  const { t } = useTranslation()
  const [title, setTitle] = useState('')
  const [messages, setMessages] = useState<Msgs>([] as unknown as Msgs)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const [msgRes, reportRes] = await Promise.all([
          window.electronAPI.invoke('tasks:getMessages', taskId) as Promise<{ messages?: unknown[] }>,
          window.electronAPI.invoke('schedules:readReport', runId) as Promise<{
            run?: { started_at: string }
            schedule?: { name: string }
          }>,
        ])
        if (cancelled) return
        setMessages((msgRes.messages ?? []) as unknown as Msgs)
        if (reportRes.schedule?.name && reportRes.run?.started_at) {
          const d = new Date(reportRes.run.started_at)
          setTitle(`${reportRes.schedule.name} · ${d.getMonth() + 1}/${d.getDate()}`)
        }
      } catch {
        /* 标题缺省；消息失败留空流（回放失败不阻塞界面） */
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [runId, taskId])

  return (
    <div className={s.page}>
      <header className={s.head}>
        <div className={s.titleRow}>
          <button
            className={s.backBtn}
            data-testid="replay-back-btn"
            title={t('schedule.back')}
            aria-label={t('schedule.back')}
            onClick={onBack}
          >
            <ArrowLeft size={15} />
          </button>
          <h2 className={s.title}>
            {title && `${title} `}
            {t('schedule.replay.title')}
          </h2>
          <span className={s.spacer} />
          <button className={s.actionBtn} data-testid="replay-open-report" onClick={onOpenReport}>
            <FileText size={13} />{t('schedule.replay.viewReport')}
          </button>
        </div>
      </header>
      <div className={s.messages}>
        <TaskMessageList
          messages={messages}
          streamContent={EMPTY_STREAM}
          streamSteps={EMPTY_STEPS}
          isGenerating={false}
        />
      </div>
    </div>
  )
}
