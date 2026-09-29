import { useEffect, useRef, useState, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Play, Terminal, Folder, Search, Globe, Sparkles, Package, Wrench, BookOpen,
  FileText, Map as MapIcon, Puzzle, FolderOpen, Bot, User, Brain, Lightbulb, AlertTriangle, Copy, Check,
} from 'lucide-react'
import { Markdown } from '../../components/Markdown'
import type { TaskMessage, StreamStep, PlanData } from '../../types'
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

// Tool display i18n label keys (icons from TOOL_ICONS)
const TOOL_META: Record<string, { labelKey: string }> = {
  code_executor: { labelKey: 'task.tools.code_executor' },
  shell: { labelKey: 'task.tools.shell' },
  file_ops: { labelKey: 'task.tools.file_ops' },
  code_search: { labelKey: 'task.tools.code_search' },
  web_fetch: { labelKey: 'task.tools.web_fetch' },
  web_search: { labelKey: 'task.tools.web_search' },
  code_lint: { labelKey: 'task.tools.code_lint' },
  code_format: { labelKey: 'task.tools.code_format' },
  package_manager: { labelKey: 'task.tools.package_manager' },
  project_scaffold: { labelKey: 'task.tools.project_scaffold' },
  knowledge_search: { labelKey: 'task.tools.knowledge_search' },
  doc_generator: { labelKey: 'task.tools.doc_generator' },
  repo_map: { labelKey: 'task.tools.repo_map' },
  skill_runner: { labelKey: 'task.tools.skill_runner' },
  git_ops: { labelKey: 'task.tools.git_ops' },
  lsp_client: { labelKey: 'task.tools.lsp_client' },
}

const TOOL_ICONS: Record<string, any> = {
  code_executor: Play,
  shell: Terminal,
  file_ops: Folder,
  code_search: Search,
  web_fetch: Globe,
  web_search: Globe,
  code_lint: Search,
  code_format: Sparkles,
  package_manager: Package,
  project_scaffold: Wrench,
  knowledge_search: BookOpen,
  doc_generator: FileText,
  repo_map: MapIcon,
  skill_runner: Puzzle,
  git_ops: FolderOpen,
  lsp_client: Brain,
}

function getToolMeta(toolName: string) {
  return TOOL_META[toolName] || { labelKey: '' }
}

function getToolIcon(toolName: string) {
  return TOOL_ICONS[toolName] || null
}

function formatToolInput(input: Record<string, unknown>): string {
  const entries = Object.entries(input)
  if (entries.length === 0) return ''
  if (input.code && typeof input.code === 'string') {
    const firstLine = input.code.split('\n')[0]
    return firstLine.length > 60 ? firstLine.slice(0, 60) + '...' : firstLine
  }
  if (input.command && typeof input.command === 'string') return input.command
  if (input.action && input.path) return `${input.action}: ${input.path}`
  if (input.pattern) return `/${input.pattern}/`
  if (input.url) return input.url as string
  const [, val] = entries[0]
  const valStr = typeof val === 'string' ? val : JSON.stringify(val)
  return valStr.length > 60 ? valStr.slice(0, 60) + '...' : valStr
}

// ── Iteration grouping ──

interface IterationGroup {
  iteration: number
  reasoning: string | null
  thought: string | null
  actions: TaskMessage[]
  status: 'ok' | 'error' | 'running'
}

/** Compute iteration status from its steps */
function computeStepsStatus(steps: StreamStep[]): 'ok' | 'error' | 'running' {
  let status: 'ok' | 'error' | 'running' = 'ok'
  for (const step of steps) {
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

/** Build iteration groups from streaming state */
function buildStreamGroups(
  streamSteps: StreamStep[],
  streamReasoning: string,
  streamIteration: number,
  streamIterationReasoning: Map<number, string>,
  streamIterationThoughts: Map<number, string>,
): IterationGroup[] {
  const groups: IterationGroup[] = []

  // Build groups from accumulated steps
  const stepsByIteration = new Map<number, StreamStep[]>()
  for (const step of streamSteps) {
    const iter = step.iteration ?? 0
    if (!stepsByIteration.has(iter)) stepsByIteration.set(iter, [])
    stepsByIteration.get(iter)!.push(step)
  }

  // Determine max iteration from steps
  const maxStepIter = stepsByIteration.size > 0 ? Math.max(...stepsByIteration.keys()) : 0

  // Build historical iterations (0..maxStepIter or streamIteration-1)
  const lastHistoricalIter = streamIteration > 0 ? streamIteration - 1 : maxStepIter
  for (let i = 0; i <= lastHistoricalIter; i++) {
    const steps = stepsByIteration.get(i) || []
    const actions = steps.map((step) => ({
      id: `stream-${i}-${steps.indexOf(step)}`,
      task_id: '',
      role: 'action' as const,
      content: '',
      metadata: { tool_name: step.tool, tool_input: step.input, tool_output: step.output, diff: step.diff, file_path: step.filePath },
      created_at: '',
    }))
    let status = computeStepsStatus(steps)
    groups.push({
      iteration: i,
      reasoning: streamIterationReasoning.get(i) || null,
      thought: streamIterationThoughts.get(i) || null,
      actions,
      status,
    })
  }

  // Current iteration (streamIteration)
  const currentSteps = stepsByIteration.get(streamIteration) || []
  if (currentSteps.length > 0 || streamReasoning) {
    const actions = currentSteps.map((step, idx) => ({
      id: `stream-cur-${idx}`,
      task_id: '',
      role: 'action' as const,
      content: '',
      metadata: { tool_name: step.tool, tool_input: step.input, tool_output: step.output, diff: step.diff, file_path: step.filePath },
      created_at: '',
    }))
    let status = computeStepsStatus(currentSteps)
    if (status === 'ok' && (currentSteps.length > 0 || streamReasoning)) status = 'running'
    groups.push({
      iteration: streamIteration,
      reasoning: streamReasoning || null,
      thought: streamIterationThoughts.get(streamIteration) || null,
      actions,
      status,
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

  const enrichedMessages = useMemo(() => enrichMessages(messages), [messages])

  // Build streaming iteration groups
  const streamGroups = useMemo(() => {
    if (!isGenerating) return []
    return buildStreamGroups(streamSteps, streamReasoning || '', streamIteration, streamIterationReasoning || new Map(), streamIterationThoughts || new Map())
  }, [streamSteps, streamReasoning, streamIteration, streamIterationReasoning, streamIterationThoughts, isGenerating])

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

  // Build a unified render list preserving original message order.
  // Interleave MessageBubbles (user/assistant) with IterationCards (reasoning+thought+action).
  type RenderItem = { type: 'bubble'; msg: TaskMessage } | { type: 'iter'; group: IterationGroup; isStreaming: boolean }

  const renderList = useMemo((): RenderItem[] => {
    const items: RenderItem[] = []
    let currentGroup: IterationGroup | null = null

    for (const msg of enrichedMessages) {
      if (msg.role === 'reasoning') {
        // Start new iteration group
        if (currentGroup) items.push({ type: 'iter', group: currentGroup, isStreaming: false })
        currentGroup = { iteration: items.filter(i => i.type === 'iter').length, reasoning: msg.content, thought: null, actions: [], status: 'ok' }
      } else if (msg.role === 'thought') {
        // Defensive: create group if no preceding reasoning
        if (!currentGroup) {
          currentGroup = { iteration: items.filter(i => i.type === 'iter').length, reasoning: null, thought: msg.content, actions: [], status: 'ok' }
        } else {
          currentGroup.thought = msg.content
        }
      } else if (msg.role === 'action') {
        if (currentGroup) {
          currentGroup.actions.push(msg)
          if (currentGroup.status !== 'error' && msg.metadata?.tool_output) {
            try {
              const parsed = JSON.parse(msg.metadata.tool_output as string)
              if (typeof parsed === 'object' && parsed !== null) {
                if ((parsed.exit_code !== undefined && parsed.exit_code !== 0) || parsed.error) {
                  currentGroup.status = 'error'
                }
              }
            } catch { /* ok */ }
          }
        }
      } else if (msg.role === 'observation') {
        // Skip — output already merged into action by enrichMessages
      } else {
        // user or assistant message bubble
        if (currentGroup) {
          items.push({ type: 'iter', group: currentGroup, isStreaming: false })
          currentGroup = null
        }
        items.push({ type: 'bubble', msg })
      }
    }
    // Flush last group
    if (currentGroup) items.push({ type: 'iter', group: currentGroup, isStreaming: false })

    // Append streaming groups (only during generation, after all historical messages)
    if (isGenerating && streamGroups.length > 0) {
      for (const g of streamGroups) {
        items.push({ type: 'iter', group: g, isStreaming: true })
      }
    }

    return items
  }, [enrichedMessages, streamGroups, isGenerating])

  return (
    <div className={s.list}>
      {/* Plan progress card */}
      {planData && planData.steps.length > 0 && (
        <PlanProgressCard plan={planData} />
      )}

      {/* Unified render list: interleaved bubbles + iteration cards */}
      {renderList.map((item, idx) => {
        if (item.type === 'bubble') {
          return <MessageBubble key={item.msg.id} message={item.msg} />
        }
        // Iteration card
        const group = item.group
        return (
          <IterationCard
            key={`iter-${group.iteration}-${idx}`}
            group={group}
            open={isExpanded(group.iteration)}
            onToggle={() => toggleIteration(group.iteration)}
            isStreaming={item.isStreaming}
          />
        )
      })}

      {/* Streaming analysis text — always outside IterationCard (doc §4.2) */}
      {isGenerating && streamContent && (
        <StreamThoughtBlock
          content={streamContent}
          isGenerating={isGenerating}
          defaultOpen={true}
        />
      )}

      {/* Thinking indicator */}
      {isGenerating && !streamContent && streamSteps.length === 0 && renderList.length === 0 && (
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

// ── Iteration Card ──

function IterationCard({
  group, open, onToggle, isStreaming,
}: {
  group: IterationGroup
  open: boolean
  onToggle: () => void
  isStreaming: boolean
}) {
  const { t } = useTranslation()
  const summary = group.thought
    ? (group.thought.length > 30 ? group.thought.slice(0, 30) + '...' : group.thought)
    : ''
  const stepCount = group.actions.length

  return (
    <div className={s.iterCard}>
      <button className={s.iterHeader} onClick={onToggle}>
        <span className={`${s.chevron} ${open ? s.chevronOpen : ''}`}>▸</span>
        <span className={s.iterLabel}>Iter {group.iteration + 1}</span>
        {summary && <span className={s.iterSummary}>{summary}</span>}
        {stepCount > 0 && <span className={s.iterSteps}>{t('task.steps', { count: stepCount })}</span>}
        {group.status === 'error' && <span className={`${s.statusDot} ${s.statusError}`} />}
        {group.status === 'ok' && stepCount > 0 && !isStreaming && <span className={`${s.statusDot} ${s.statusOk}`} />}
        {isStreaming && group.status === 'running' && <span className={s.spinner} />}
      </button>
      {open && (
        <div className={s.iterBody}>
          {group.reasoning && (
            <ReasoningBlock content={group.reasoning} defaultOpen={isStreaming && open} />
          )}
          {group.thought && (
            <ThoughtBlock content={group.thought} defaultOpen={isStreaming && open} />
          )}
          {group.actions.map((action, i) => {
            const toolName = action.metadata?.tool_name || 'unknown'
            const toolInput = action.metadata?.tool_input || {}
            const toolOutput = action.metadata?.tool_output
            const diff = action.metadata?.diff
            const filePath = action.metadata?.file_path
            return (
              <ToolCard
                key={i}
                toolName={toolName}
                input={toolInput}
                output={toolOutput}
                diff={diff}
                filePath={filePath}
                done={!!toolOutput}
              />
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── Message Bubble ──

function MessageBubble({ message }: { message: TaskMessage }) {
  const { t, i18n } = useTranslation()
  const [copied, setCopied] = useState(false)
  const isUser = message.role === 'user'
  const time = new Date(message.created_at).toLocaleTimeString(i18n.language, {
    hour: '2-digit',
    minute: '2-digit',
  })

  const handleCopy = async (e: React.MouseEvent) => {
    e.stopPropagation()
    try {
      await navigator.clipboard.writeText(message.content)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch { /* ignore */ }
  }

  return (
    <div className={`${s.message} ${isUser ? s.user : s.assistant}`}>
      <div className={s.avatar}>{isUser ? <User size={18} /> : <Bot size={18} />}</div>
      <div className={s.bubbleWrap}>
        <div className={`${s.bubble} ${isUser ? s.userBubble : s.assistantBubble}`}>
          {isUser ? (
            <p>{message.content}</p>
          ) : (
            <Markdown content={message.content} className={s.markdown} />
          )}
        </div>
        <div className={`${s.timeRow} ${isUser ? s.timeRowUser : ''}`}>
          <span className={s.time}>{time}</span>
          <button
            className={s.copyBtn}
            onClick={handleCopy}
            title={copied ? t('chat.copied') : t('chat.copy')}
          >
            {copied ? <Check size={12} /> : <Copy size={12} />}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Collapsible blocks ──

function ReasoningBlock({ content, defaultOpen = false }: { content: string; defaultOpen?: boolean }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className={s.stepRow}>
      <button className={s.stepToggle} onClick={() => setOpen(!open)}>
        <span className={`${s.chevron} ${open ? s.chevronOpen : ''}`}></span>
        <Brain size={14} className={s.stepIcon} />
        <span className={s.stepLabel}>{t('task.reasoningProcess')}</span>
      </button>
      {open && (
        <div className={s.stepDetail}><Markdown content={content} className={s.markdown} /></div>
      )}
    </div>
  )
}

function ThoughtBlock({ content, defaultOpen = false }: { content: string; defaultOpen?: boolean }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className={s.stepRow}>
      <button className={s.stepToggle} onClick={() => setOpen(!open)}>
        <span className={`${s.chevron} ${open ? s.chevronOpen : ''}`}>▸</span>
        <span className={s.stepLabel}>{t('task.thoughtProcess')}</span>
      </button>
      {open && (
        <div className={s.stepDetail}><Markdown content={content} className={s.markdown} /></div>
      )}
    </div>
  )
}

function StreamThoughtBlock({ content, isGenerating, defaultOpen = true }: { content: string; isGenerating: boolean; defaultOpen?: boolean }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(defaultOpen)
  useEffect(() => { setOpen(defaultOpen) }, [defaultOpen])
  return (
    <div className={s.stepRow}>
      <button className={s.stepToggle} onClick={() => setOpen(!open)}>
        <span className={`${s.chevron} ${open ? s.chevronOpen : ''}`}>▸</span>
        <span className={s.stepLabel}>{t('task.thoughtProcess')}</span>
      </button>
      {open && (
        <div className={s.stepDetail}>
          <Markdown content={content} className={s.markdown} />
          {isGenerating && <span className={s.streamingCursor}>▍</span>}
        </div>
      )}
    </div>
  )
}

// ── Tool Card ──

function ToolCard({
  toolName, input, output, diff, filePath, done,
}: {
  toolName: string
  input: Record<string, unknown>
  output?: string
  diff?: string
  filePath?: string
  done: boolean
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const meta = getToolMeta(toolName)
  const summary = formatToolInput(input)

  useEffect(() => {
    if (done) { setElapsed(0); return }
    setElapsed(0)
    const interval = setInterval(() => setElapsed(prev => prev + 1), 1000)
    return () => clearInterval(interval)
  }, [done])

  let statusIcon: 'ok' | 'error' | null = null
  let fixSuggestion: string | null = null
  let lintWarning: string | null = null
  if (done && output) {
    try {
      const parsed = JSON.parse(output)
      if (typeof parsed === 'object' && parsed !== null) {
        const exitCode = parsed.exit_code
        if (exitCode !== undefined && exitCode !== 0) statusIcon = 'error'
        else if (parsed.error) statusIcon = 'error'
        else statusIcon = 'ok'
        if (parsed.fix_suggestion) fixSuggestion = parsed.fix_suggestion as string
        if (parsed.lint_warning) lintWarning = parsed.lint_warning as string
        if (parsed.auto_fix_hint) fixSuggestion = parsed.auto_fix_hint as string
      } else { statusIcon = 'ok' }
    } catch { statusIcon = 'ok' }
  }

  const formatElapsed = (s: number) => s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`

  return (
    <div className={s.stepRow}>
      <button className={s.stepToggle} onClick={() => setOpen(!open)}>
        <span className={`${s.chevron} ${open ? s.chevronOpen : ''}`}>▸</span>
        <span className={s.toolIcon}>
          {(() => { const Icon = getToolIcon(toolName); return Icon ? <Icon size={14} /> : <Wrench size={14} /> })()}
        </span>
        <span className={s.stepLabel}>{meta.labelKey ? t(meta.labelKey) : toolName}</span>
        <span className={s.toolSummary}>{summary}</span>
        {!done && (
          <>
            <span className={s.spinner}></span>
            {elapsed >= 3 && <span className={s.elapsed}>{formatElapsed(elapsed)}</span>}
          </>
        )}
        {statusIcon && <span className={`${s.statusDot} ${statusIcon === 'ok' ? s.statusOk : s.statusError}`} />}
      </button>
      {diff && <DiffCard diff={diff} filePath={filePath} />}
      {fixSuggestion && (
        <div className={s.fixBanner}><Lightbulb size={14} className={s.fixIcon} /> {fixSuggestion}</div>
      )}
      {lintWarning && !fixSuggestion && (
        <div className={s.warnBanner}><AlertTriangle size={14} className={s.warnIcon} /> {lintWarning}</div>
      )}
      {open && output && (
        <div className={s.stepDetail}><pre className={s.toolOutput}>{output}</pre></div>
      )}
    </div>
  )
}

// ─ Diff Card ──

function DiffCard({ diff, filePath }: { diff: string; filePath?: string }) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)
  const lines = diff.split('\n')
  let additions = 0, deletions = 0
  for (const line of lines) {
    if (line.startsWith('+') && !line.startsWith('+++')) additions++
    if (line.startsWith('-') && !line.startsWith('---')) deletions++
  }
  const preview = lines.slice(0, 8).join('\n')
  const hasMore = lines.length > 8

  return (
    <div className={s.diffCard}>
      <button className={s.diffHeader} onClick={() => setExpanded(!expanded)}>
        <span className={`${s.chevron} ${expanded ? s.chevronOpen : ''}`}>▸</span>
        <span className={s.diffTitle}>
          {filePath || t('task.diffChanges')}
          <span className={s.diffStats}>
            <span className={s.diffAdd}>+{additions}</span>
            <span className={s.diffDel}>-{deletions}</span>
          </span>
        </span>
      </button>
      {expanded ? (
        <pre className={s.diffBody}>
          {lines.map((line, i) => (
            <span key={i} className={
              line.startsWith('+') && !line.startsWith('+++') ? s.diffLineAdd :
              line.startsWith('-') && !line.startsWith('---') ? s.diffLineDel :
              line.startsWith('@@') ? s.diffLineHunk : s.diffLineCtx
            }>
              {line}{'\n'}
            </span>
          ))}
        </pre>
      ) : (
        <pre className={s.diffPreview}>{preview}{hasMore ? '\n...' : ''}</pre>
      )}
    </div>
  )
}

// ─ Plan Progress Card ──

function PlanProgressCard({ plan }: { plan: PlanData }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(true)
  return (
    <div className={s.planCard}>
      <button className={s.planHeader} onClick={() => setOpen(!open)}>
        <span className={`${s.chevron} ${open ? s.chevronOpen : ''}`}>▸</span>
        <span className={s.planTitle}>📋 {t('task.executionPlan')}</span>
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

// ── Helpers ──

function enrichMessages(msgs: TaskMessage[]): TaskMessage[] {
  // Merge observation metadata into action messages
  // Note: thought messages are kept separate (NOT merged into reasoning)
  const obsByAction = new Map<string, Record<string, unknown>>()
  for (const msg of msgs) {
    if (msg.role === 'observation' && msg.metadata?.action_id) {
      obsByAction.set(msg.metadata.action_id as string, msg.metadata as Record<string, unknown>)
    }
  }
  if (obsByAction.size === 0) {
    const actionIds: string[] = []
    const obsList: Record<string, unknown>[] = []
    for (const msg of msgs) {
      if (msg.role === 'action') actionIds.push(msg.id)
      if (msg.role === 'observation' && msg.metadata?.tool_output) obsList.push(msg.metadata as Record<string, unknown>)
    }
    for (let i = 0; i < actionIds.length && i < obsList.length; i++) {
      obsByAction.set(actionIds[i], obsList[i])
    }
  }
  if (obsByAction.size === 0) return msgs

  return msgs.map((msg) => {
    if (msg.role !== 'action') return msg
    const obs = obsByAction.get(msg.id)
    if (!obs) return msg
    return {
      ...msg,
      metadata: { ...msg.metadata, tool_output: obs.tool_output as string | undefined, diff: obs.diff as string | undefined, file_path: obs.file_path as string | undefined },
    }
  })
}
