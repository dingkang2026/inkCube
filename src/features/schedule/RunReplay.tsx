/** src/features/schedule/RunReplay.tsx — 期次执行回放（2.11 §8.3，E3 T8）。
 *  只读：复用 TaskMessageList（isGenerating=false），无输入区/暂停/操作菜单——
 *  档案语义（规则 6），workspace 保留期清理后消息流仍可回看。
 *  标题「{订阅名} · M/D 执行回放（只读）」（订阅名/期次经 readReport 取）。 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { TaskMessageList } from '../task/TaskMessageList'
import s from './RunReplay.module.css'

interface RunReplayProps {
  runId: string
  taskId: string
}

type Msgs = Parameters<typeof TaskMessageList>[0]['messages']

export function RunReplay({ runId, taskId }: RunReplayProps) {
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
        <h2 className={s.title}>
          {title && `${title} `}
          {t('schedule.replay.title')}
        </h2>
      </header>
      <div className={s.messages}>
        <TaskMessageList messages={messages} streamContent="" streamSteps={[]} isGenerating={false} />
      </div>
    </div>
  )
}
