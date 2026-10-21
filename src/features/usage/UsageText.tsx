/** src/features/usage/UsageText.tsx — 通用用量小字（2.10 §6.5 线框 2/3/4/6）。
 *  `↑输入 ↓输出` 紧凑格式；tooltip 千分位完整值 + 调用次数 + 实际模型（多模型轮
 *  显示去重列表）；usage 缺失（历史消息无记录/空聚合）不渲染。
 *  传入 onClick 转可点触发器（TaskItem 轮次下钻，2.10 §5.2）：button 语义 +
 *  展开态高亮，stopPropagation 防卡片导航冒泡。 */
import { useTranslation } from 'react-i18next'
import type { UsageSummary } from '../../types'
import { compactTokens, thousands, modelsList } from './format'
import s from './UsageText.module.css'

export function UsageText({
  usage,
  onClick,
  expanded = false,
}: {
  usage?: UsageSummary | null
  /** 传入即转可点触发器（UsageRounds 展开开关）；stopPropagation 防卡片导航冒泡 */
  onClick?: () => void
  /** 展开态高亮（配合 onClick 使用） */
  expanded?: boolean
}) {
  const { t } = useTranslation()
  if (!usage || !usage.calls) return null
  const title = [
    `${t('usage.titleInput')}: ${thousands(usage.prompt_tokens)}`,
    `${t('usage.titleOutput')}: ${thousands(usage.completion_tokens)}`,
    `${t('usage.titleTotal')}: ${thousands(usage.total_tokens)} tokens`,
    t('usage.titleCalls', { count: usage.calls }),
    ...modelsList(usage.models),
  ].join(' · ')
  const text = `↑${compactTokens(usage.prompt_tokens)} ↓${compactTokens(usage.completion_tokens)}`
  if (!onClick) {
    return (
      <span className={s.usage} title={title}>
        {text}
      </span>
    )
  }
  return (
    <button
      type="button"
      className={`${s.usage} ${s.usageBtn}${expanded ? ` ${s.usageBtnOpen}` : ''}`}
      title={title}
      onClick={(e) => {
        e.stopPropagation()
        onClick()
      }}
    >
      {text}
    </button>
  )
}
