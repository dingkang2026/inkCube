/** src/features/project/affairs/SuggestionCard.tsx — AI 建议卡区块(3.0 四期 T8,3.1 §5.3)
 *  自包含数据域:挂载 + refreshSignal 边沿拉 affairs:listSuggestions(pending),零新增轮询;
 *  [采纳] affairs:acceptSuggestion → 成功 dispatch app:tasks-changed(侧栏/首页待办
 *    既有刷新链自动生效,三期已铺)+ onAccepted 回调父级刷新看板(整体 review W1:
 *    AffairsTab 不监听该事件,看板 state 需显式重拉)+ 本地移除 + Toast;
 *    目标失效(IPC errorDetail=target_task_missing,后端已转 dismissed 永不再提)→
 *    失效 Toast + 本地移除(任务未变,不触发 onAccepted);
 *  [忽略] affairs:dismissSuggestion → 本地移除(任务未变,不触发 onAccepted)。
 *  快切项目守卫 latestPidRef(同 AffairsTab 模式);拉取失败静默保留空态(增强信息不阻断主视图)。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Lightbulb } from 'lucide-react'
import type { TaskSuggestion } from './types'
import { useToast } from '../../knowledge/Toast'
import s from './SuggestionCard.module.css'

interface SuggestionCardProps {
  projectId: string
  /** 父级边沿信号(解析/编译终态 useEdgeRefetch 时 +1):变化即重拉,零新增轮询 */
  refreshSignal?: number
  /** 采纳成功后回调(整体 review W1:看板/侧栏等父级数据域需显式重拉;
   *  忽略与目标失效不改任务状态,不触发) */
  onAccepted?: () => void
}

export function SuggestionCard({ projectId, refreshSignal, onAccepted }: SuggestionCardProps) {
  const { t } = useTranslation()
  const showToast = useToast()
  const [suggestions, setSuggestions] = useState<TaskSuggestion[]>([])
  // 单飞互斥:采纳/忽略进行中禁用该行按钮,防双击重复 IPC(重复采纳会建两条任务)
  const [busyId, setBusyId] = useState<string | null>(null)
  const latestPidRef = useRef(projectId)
  useEffect(() => { latestPidRef.current = projectId }, [projectId])

  const load = useCallback(async () => {
    const pid = projectId
    try {
      const r: any = await window.electronAPI.invoke('affairs:listSuggestions', { projectId: pid })
      if (latestPidRef.current !== pid) return
      setSuggestions(r?.suggestions ?? [])
    } catch (err) {
      console.error('Failed to load suggestions:', err)
    }
  }, [projectId])

  useEffect(() => { void load() }, [load])
  useEffect(() => { if (refreshSignal !== undefined && refreshSignal > 0) void load() }, [refreshSignal, load])

  const removeLocal = (id: string) =>
    setSuggestions((list) => list.filter((x) => x.id !== id))

  const handleAccept = async (sug: TaskSuggestion) => {
    if (busyId) return
    setBusyId(sug.id)
    try {
      await window.electronAPI.invoke('affairs:acceptSuggestion', { projectId, suggestionId: sug.id })
      window.dispatchEvent(new CustomEvent('app:tasks-changed'))
      onAccepted?.()                       // W1:父级看板 state 不监听事件,显式重拉
      removeLocal(sug.id)
      showToast(t('affairs.suggestion.acceptedToast'), 'success')
    } catch (err) {
      if ((err as { errorDetail?: string })?.errorDetail === 'target_task_missing') {
        // 目标已失效:后端已转 dismissed(永不再提),本地同步移除
        removeLocal(sug.id)
        showToast(t('affairs.suggestion.invalidated'), 'error')
      } else {
        showToast(t('affairs.suggestion.actionFailed', {
          reason: err instanceof Error ? err.message : String(err),
        }), 'error')
      }
    } finally {
      setBusyId(null)
    }
  }

  const handleDismiss = async (sug: TaskSuggestion) => {
    if (busyId) return
    setBusyId(sug.id)
    try {
      await window.electronAPI.invoke('affairs:dismissSuggestion', { projectId, suggestionId: sug.id })
      removeLocal(sug.id)
    } catch (err) {
      showToast(t('affairs.suggestion.actionFailed', {
        reason: err instanceof Error ? err.message : String(err),
      }), 'error')
    } finally {
      setBusyId(null)
    }
  }

  if (suggestions.length === 0) return null

  return (
    <section className={s.wrap}>
      <div className={s.header}>
        <Lightbulb size={14} />
        <h3 className={s.title}>{t('affairs.suggestion.title')}</h3>
      </div>
      <div className={s.list}>
        {suggestions.map((sug) => (
          <div key={sug.id} className={s.item}>
            <div className={s.itemMain}>
              <div className={s.itemTitle}>{sug.title}</div>
              {sug.description && <div className={s.itemDesc}>{sug.description}</div>}
            </div>
            <div className={s.itemActions}>
              <button className={s.acceptBtn} disabled={busyId === sug.id}
                onClick={() => void handleAccept(sug)}>
                {t('affairs.suggestion.accept')}
              </button>
              <button className={s.dismissBtn} disabled={busyId === sug.id}
                onClick={() => void handleDismiss(sug)}>
                {t('affairs.suggestion.dismiss')}
              </button>
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}
