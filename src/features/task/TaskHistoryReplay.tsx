/** src/features/task/TaskHistoryReplay.tsx — 任务历史回放渲染（1.2 §4.1 渲染资产共享出口）。
 *  把持久化消息（tasks:getMessages 全量行，含 metadata）按「缩小的任务 agent」形态回放：
 *  user/assistant 气泡与迭代卡片（reasoning → 工具行 → 分析）交错，全部折叠态起步。
 *  TaskMessageList 的历史段与 PPT 模板任务卡（JobCard）/ PPT 详情时间线共用本组件；
 *  迭代卡片/气泡渲染资产（IterationCard 等）由 TaskMessageList 的流式段继续复用。
 *  样式共用 TaskMessageList.module.css（同类名同一份映射，无复制）。
 */
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Play, Terminal, Folder, Search, Globe, Sparkles, Package, Wrench, BookOpen,
  FileText, Map as MapIcon, Puzzle, FolderOpen, Bot, User, Brain, Lightbulb, AlertTriangle, Copy, Check,
} from 'lucide-react'
import { Markdown } from '../../components/Markdown'
import type { TaskMessage } from '../../types'
import { UsageText } from '../usage/UsageText'
import { translateStoredError } from '../../i18n/errorCodes'
import { browserPanelStore } from '../browser/browserPanelStore'
import s from './TaskMessageList.module.css'

// Tool display i18n label keys (icons from TOOL_ICONS)
const TOOL_META: Record<string, { labelKey: string }> = {
  code_executor: { labelKey: 'task.tools.code_executor' },
  shell: { labelKey: 'task.tools.shell' },
  file_ops: { labelKey: 'task.tools.file_ops' },
  code_search: { labelKey: 'task.tools.code_search' },
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

export interface IterationGroup {
  iteration: number
  reasoning: string | null
  thought: string | null
  /** skill 首用环境准备提示行（仅流式期，2.8 §5.5）；历史回放无此内容 */
  notes: string[]
  actions: TaskMessage[]
  status: 'ok' | 'error' | 'running'
  /** true while the field is still streaming live (current iteration, not yet locked by thought/done) */
  liveReasoning?: boolean
  liveThought?: boolean
}

export type RenderItem = { type: 'bubble'; msg: TaskMessage } | { type: 'iter'; group: IterationGroup; isStreaming: boolean }

/** Group persisted messages (DB replay / done-converted) into bubbles + iteration
 *  cards, preserving message order. Iteration numbering restarts at 0 after each
 *  user message so history matches live rendering (each dialogue round shows
 *  Iter 1..N regardless of earlier rounds). Exported for tests (1.2 §4.1). */
export function buildHistoryItems(msgs: TaskMessage[]): RenderItem[] {
  const items: RenderItem[] = []
  let currentGroup: IterationGroup | null = null
  let iterCounter = 0

  const flushGroup = () => {
    if (currentGroup) {
      items.push({ type: 'iter', group: currentGroup, isStreaming: false })
      currentGroup = null
      iterCounter++
    }
  }

  for (const msg of msgs) {
    if (msg.role === 'reasoning') {
      // Start new iteration group
      flushGroup()
      currentGroup = { iteration: iterCounter, reasoning: msg.content, thought: null, notes: [], actions: [], status: 'ok' }
    } else if (msg.role === 'thought') {
      // Defensive: create group if no preceding reasoning
      if (!currentGroup) {
        currentGroup = { iteration: iterCounter, reasoning: null, thought: msg.content, notes: [], actions: [], status: 'ok' }
      } else {
        currentGroup.thought = msg.content
      }
    } else if (msg.role === 'action') {
      // Defensive: persisted history often lacks reasoning/thought (models
      // that emit tool_calls directly store no preceding text), so an action
      // may arrive with no open group — create one instead of silently
      // dropping the step, keeping history replay aligned with the
      // streaming view (matches the thought branch above).
      if (!currentGroup) {
        currentGroup = { iteration: iterCounter, reasoning: null, thought: null, notes: [], actions: [], status: 'ok' }
      }
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
    } else if (msg.role === 'observation') {
      // Skip — output already merged into action by enrichMessages
    } else {
      // user or assistant message bubble
      flushGroup()
      items.push({ type: 'bubble', msg })
      if (msg.role === 'user') iterCounter = 0 // numbering restarts per dialogue round
    }
  }
  // Flush last group
  flushGroup()

  return items
}

// ── History replay component ──

/** 持久化消息 → 历史回放。全折叠起步（与任务面板历史段一致），点击迭代卡展开；
 *  容器高度与滚动由外部控制（.replay 只负责纵向排布）。 */
export function TaskHistoryReplay({ messages, className }: { messages: TaskMessage[]; className?: string }) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const items = useMemo(() => buildHistoryItems(enrichMessages(messages)), [messages])

  const toggleIteration = (iter: number) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(iter)) next.delete(iter)
      else next.add(iter)
      return next
    })
  }

  return (
    <div className={className ? `${s.replay} ${className}` : s.replay}>
      {items.map((item, idx) => {
        if (item.type === 'bubble') {
          return <MessageBubble key={item.msg.id} message={item.msg} />
        }
        const group = item.group
        return (
          <IterationCard
            key={`iter-${group.iteration}-${idx}`}
            group={group}
            open={expanded.has(group.iteration)}
            onToggle={() => toggleIteration(group.iteration)}
            isStreaming={false}
          />
        )
      })}
    </div>
  )
}

// ── Iteration Card ──

export function IterationCard({
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
          {/* Fixed three-section layout: reasoning → tools → analysis (1.2 §4.1);
              live streams keep their cursor inside the card — analysis streams below
              a still-running tool is expected (user-approved interaction) */}
          {group.reasoning && (
            <ReasoningBlock content={group.reasoning} streaming={group.liveReasoning} defaultOpen={isStreaming && open} />
          )}
          {/* 环境准备提示行（2.8 §5.5）：最后一条仍为 ⏳（开始态）时带 spinner，
              终态 note（✅/❌）入列后自然停转 */}
          {group.notes.map((text, i) => (
            <div key={`note-${i}`} className={s.noteRow}>
              {isStreaming && i === group.notes.length - 1 && text.startsWith('⏳') && <span className={s.spinner} />}
              <span className={s.noteText}>{text}</span>
            </div>
          ))}
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
          {group.thought && (
            <ThoughtBlock content={group.thought} streaming={group.liveThought} defaultOpen={isStreaming && open} />
          )}
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
      // D1 错误契约：复制前还原落库协议串，剪贴板不出 INKCUBE_CODE 原串
      await navigator.clipboard.writeText(isUser ? message.content : translateStoredError(message.content, t))
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
            <Markdown content={translateStoredError(message.content, t)} className={s.markdown} />
          )}
        </div>
        <div className={`${s.timeRow} ${isUser ? s.timeRowUser : ''}`}>
          <span className={s.time}>{time}</span>
          {/* 该轮用量小字（2.10 §6.1/线框 2）：仅 assistant 气泡；历史消息无记录不渲染 */}
          {!isUser && <UsageText usage={message.metadata?.usage} />}
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

function ReasoningBlock({ content, streaming = false, defaultOpen = false }: { content: string; streaming?: boolean; defaultOpen?: boolean }) {
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
        <div className={s.stepDetail}>
          <Markdown content={content} className={s.markdown} />
          {streaming && <span className={s.streamingCursor}>▍</span>}
        </div>
      )}
    </div>
  )
}

function ThoughtBlock({ content, streaming = false, defaultOpen = false }: { content: string; streaming?: boolean; defaultOpen?: boolean }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className={s.stepRow}>
      <button className={s.stepToggle} onClick={() => setOpen(!open)}>
        <span className={`${s.chevron} ${open ? s.chevronOpen : ''}`}>▸</span>
        <span className={s.stepLabel}>{t('task.thoughtProcess')}</span>
      </button>
      {open && (
        <div className={s.stepDetail}>
          <Markdown content={content} className={s.markdown} />
          {streaming && <span className={s.streamingCursor}>▍</span>}
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
  // browser-fetch 撞登录墙（2.8 §4.1）：observation 元数据携带 needs_login/domain
  let loginDomain: string | null = null
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
        if (parsed.needs_login && parsed.domain) loginDomain = parsed.domain as string
      } else { statusIcon = 'ok' }
    } catch { statusIcon = 'ok' }
  }

  // 登录主路径（2.8 §8.3 V2）：幂等自动登记（失败不阻塞）后经 BrowserPanel
  // 导航到该站点登录页；用户在面板登录完回对话说「重试」由 agent 重试抓取。
  // 导航被内网防线拒时面板呈现 blocked 页，无需独立错误通道。
  const handleLogin = async () => {
    if (!loginDomain) return
    try {
      await window.electronAPI.invoke('weblogin:add', { domain: loginDomain })
    } catch { /* 登记失败仍可登录 */ }
    browserPanelStore.navigate(`https://${loginDomain}`)
  }

  const formatElapsed = (secs: number) => secs < 60 ? `${secs}s` : `${Math.floor(secs / 60)}m ${secs % 60}s`

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
      {loginDomain && (
        <div className={s.loginBanner}>
          <Globe size={14} className={s.fixIcon} />
          <span className={s.loginText}>{t('task.loginRequired', { domain: loginDomain })}</span>
          <button className={s.loginBtn} onClick={handleLogin}>{t('task.loginNow')}</button>
        </div>
      )}
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

// ── Helpers ──

export function enrichMessages(msgs: TaskMessage[]): TaskMessage[] {
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
