import { useEffect, useRef, useState, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import type { TaskMessage, StreamStep, PlanData } from '../../types'
import { TaskHistoryReplay, IterationCard, buildHistoryItems, type IterationGroup } from './TaskHistoryReplay'
import s from './TaskMessageList.module.css'

interface TaskMessageListProps {
  messages: TaskMessage[]
  streamContent: string
  streamSteps: StreamStep[]
  streamReasoning?: string
  streamIteration?: number
  streamIterationThoughts?: Map<number, string>
  streamIterationReasoning?: Map<number, string>
  planData?: PlanData | null
  isGenerating: boolean
}

// ── Iteration grouping（分组类型与回放渲染资产见 TaskHistoryReplay.tsx）──

/** Compute iteration status from its steps */
function computeStepsStatus(steps: StreamStep[]): 'ok' | 'error' | 'running' {
  let status: 'ok' | 'error' | 'running' = 'ok'
  for (const step of steps) {
    if (step.type === 'note') continue // 环境准备提示行不驱动迭代状态
    if (!step.output) { status = 'running'; break }
    try {
      const parsed = JSON.parse(step.output)
      if (typeof parsed === 'object' && parsed !== null) {
        if ((parsed.exit_code !== undefined && parsed.exit_code !== 0) || parsed.error) { status = 'error'; break }
      }
    } catch { /* ok */ }
  }
  return status
}

/** Build iteration groups from streaming state (exported for tests).
 *  Data-driven: an iteration card exists only when that iteration produced
 *  content (reasoning/analysis snapshot, tool steps, or live streaming text);
 *  the current iteration additionally renders its live streams. No range
 *  guessing — resume (sparse iteration numbers) emits no empty cards and the
 *  current iteration can never overlap a historical one (1.2 §4.2). */
export function buildStreamGroups(
  streamSteps: StreamStep[],
  streamReasoning: string,
  streamContent: string,
  streamIteration: number,
  streamIterationReasoning: Map<number, string>,
  streamIterationThoughts: Map<number, string>,
): IterationGroup[] {
  const stepsByIteration = new Map<number, StreamStep[]>()
  for (const step of streamSteps) {
    const iter = step.iteration ?? 0
    if (!stepsByIteration.has(iter)) stepsByIteration.set(iter, [])
    stepsByIteration.get(iter)!.push(step)
  }

  const iters = new Set<number>([
    ...streamIterationReasoning.keys(),
    ...streamIterationThoughts.keys(),
    ...stepsByIteration.keys(),
  ])
  // Include the current iteration when anything is streaming live (reasoning
  // or analysis); otherwise an iteration that only streams text would show no card
  if (streamReasoning.trim() || streamContent.trim()) iters.add(streamIteration)

  const groups: IterationGroup[] = []
  for (const iter of [...iters].sort((a, b) => a - b)) {
    const steps = stepsByIteration.get(iter) || []
    // step_note（skill 首用环境准备提示，2.8 §5.5）单独收集成 notes，
    // 不参与工具行包装与状态计算；其余步骤仍按 action/observation 配对渲染
    const notes = steps.filter((st) => st.type === 'note').map((st) => st.text)
    const actionSteps = steps.filter((st): st is Extract<StreamStep, { type: 'action' }> => st.type !== 'note')
    const actions = actionSteps.map((step, idx) => ({
      id: `stream-${iter}-${idx}`,
      task_id: '',
      role: 'action' as const,
      content: '',
      metadata: { tool_name: step.tool, tool_input: step.input, tool_output: step.output, diff: step.diff, file_path: step.filePath },
      created_at: '',
    }))
    const isCurrent = iter === streamIteration
    // Locked snapshots win over live streams; live flags mark still-streaming fields
    const reasoning = streamIterationReasoning.get(iter) ?? (isCurrent && streamReasoning.trim() ? streamReasoning : null)
    const thought = streamIterationThoughts.get(iter) ?? (isCurrent && streamContent.trim() ? streamContent : null)
    let status = computeStepsStatus(actionSteps)
    if (status === 'ok' && isCurrent && (reasoning || thought || actions.length > 0 || notes.length > 0)) status = 'running'
    groups.push({
      iteration: iter,
      reasoning: reasoning || null,
      thought: thought || null,
      notes,
      actions,
      status,
      liveReasoning: isCurrent && !streamIterationReasoning.has(iter) && !!streamReasoning.trim(),
      liveThought: isCurrent && !streamIterationThoughts.has(iter) && !!streamContent.trim(),
    })
  }

  return groups
}

// ── Main component ──

export function TaskMessageList({
  messages, streamContent, streamSteps, streamReasoning,
  streamIteration = 0, streamIterationThoughts, streamIterationReasoning,
  planData, isGenerating,
}: TaskMessageListProps) {
  const bottomRef = useRef<HTMLDivElement>(null)
  // Combined expand state: autoExpanded tracks which iterations were auto-expanded
  // (so we know which ones to collapse when a new iteration starts).
  // expandedIterations tracks all currently expanded iterations.
  const [expandState, setExpandState] = useState({
    expanded: new Set<number>(),
    autoExpanded: new Set<number>(),
  })

  // 历史段判空（无任何可渲染内容时显示初始思考点）：仅取长度，回放渲染由 TaskHistoryReplay 承接
  const historyEmpty = useMemo(() => buildHistoryItems(messages).length === 0, [messages])

  // Build streaming iteration groups
  const streamGroups = useMemo(() => {
    if (!isGenerating) return []
    return buildStreamGroups(streamSteps, streamReasoning || '', streamContent, streamIteration, streamIterationReasoning || new Map(), streamIterationThoughts || new Map())
  }, [streamSteps, streamReasoning, streamContent, streamIteration, streamIterationReasoning, streamIterationThoughts, isGenerating])

  // Auto-expand current streaming iteration, collapse previous auto-expanded ones
  useEffect(() => {
    if (!isGenerating) return
    setExpandState((prev) => {
      const nextAuto = new Set(prev.autoExpanded)
      nextAuto.add(streamIteration)
      const nextExpanded = new Set<number>()
      for (const iter of prev.expanded) {
        // Keep user-expanded iterations, collapse auto-expanded ones
        if (!prev.autoExpanded.has(iter)) nextExpanded.add(iter)
      }
      nextExpanded.add(streamIteration) // always expand current
      return { expanded: nextExpanded, autoExpanded: nextAuto }
    })
  }, [streamIteration, isGenerating])

  // Reset expand state when not generating
  useEffect(() => {
    if (!isGenerating) {
      setExpandState({ expanded: new Set(), autoExpanded: new Set() })
    }
  }, [isGenerating])

  const toggleIteration = (iter: number) => {
    setExpandState((prev) => {
      const nextExpanded = new Set(prev.expanded)
      if (nextExpanded.has(iter)) nextExpanded.delete(iter)
      else nextExpanded.add(iter)
      const nextAuto = new Set(prev.autoExpanded)
      nextAuto.delete(iter) // no longer auto-managed
      return { expanded: nextExpanded, autoExpanded: nextAuto }
    })
  }

  const isExpanded = (iter: number) => expandState.expanded.has(iter)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamContent, streamSteps, streamIteration])

  return (
    <div className={s.list}>
      {/* Plan progress card */}
      {planData && planData.steps.length > 0 && (
        <PlanProgressCard plan={planData} />
      )}

      {/* 历史段：持久化消息回放（气泡 + 迭代卡，资产在 TaskHistoryReplay） */}
      <TaskHistoryReplay messages={messages} />

      {/* 流式段：当前生成中的迭代卡（仅生成期出现在历史之后） */}
      {streamGroups.map((group, idx) => (
        <IterationCard
          key={`iter-${group.iteration}-${idx}`}
          group={group}
          open={isExpanded(group.iteration)}
          onToggle={() => toggleIteration(group.iteration)}
          isStreaming={true}
        />
      ))}

      {/* Thinking indicator */}
      {isGenerating && !streamContent && streamSteps.length === 0 && historyEmpty && (
        <div className={s.thinkingIndicator}>
          <span className={s.thinkingDots}>
            <span></span><span></span><span></span>
          </span>
        </div>
      )}

      <div ref={bottomRef} />
    </div>
  )
}

// ── Plan Progress Card（任务面板专属；气泡/迭代卡/工具行渲染资产见 TaskHistoryReplay.tsx）──

function PlanProgressCard({ plan }: { plan: PlanData }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(true)
  return (
    <div className={s.planCard}>
      <button className={s.planHeader} onClick={() => setOpen(!open)}>
        <span className={`${s.chevron} ${open ? s.chevronOpen : ''}`}>▸</span>
        <span className={s.planTitle}>{t('task.executionPlan')}</span>
        <span className={s.planBadge}>{plan.total} {t('task.stepsCount')}</span>
      </button>
      {open && (
        <div className={s.planBody}>
          {plan.steps.map((step) => (
            <div key={step.id} className={s.planStep}>
              <span className={s.planStepNum}>{step.id}</span>
              <span className={s.planStepDesc}>{step.desc}</span>
              {step.files.length > 0 && <span className={s.planStepFiles}>{step.files.join(', ')}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

