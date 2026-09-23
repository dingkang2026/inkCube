import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Play, Terminal, Folder, Search, Globe, Sparkles, Package, Wrench, BookOpen,
  FileText, Map, Puzzle, FolderOpen, Bot, User, Brain, Lightbulb, AlertTriangle, Copy, Check,
} from 'lucide-react'
import { Markdown } from '../../components/Markdown'
import type { TaskMessage, StreamStep, PlanData } from '../../types'
import s from './TaskMessageList.module.css'

interface TaskMessageListProps {
  taskId: string | null
  messages: TaskMessage[]
  streamContent: string
  streamSteps: StreamStep[]
  streamIsThought?: boolean // Whether streamContent is a thought (analysis) or final answer
  planData?: PlanData | null
  isGenerating: boolean
}

// Tool display names and icons
const TOOL_META: Record<string, { icon: string; label: string }> = {
  code_executor: { icon: '▶', label: '执行代码' },
  shell: { icon: '⌘', label: 'Shell' },
  file_ops: { icon: '📁', label: '文件操作' },
  code_search: { icon: '🔍', label: '搜索' },
  web_fetch: { icon: '🌐', label: '网页' },
  web_search: { icon: '🔎', label: '网络搜索' },
  code_lint: { icon: '🔎', label: '代码检查' },
  code_format: { icon: '✨', label: '格式化' },
  package_manager: { icon: '📦', label: '依赖管理' },
  project_scaffold: { icon: '🏗', label: '项目骨架' },
  knowledge_search: { icon: '📚', label: '知识库' },
  doc_generator: { icon: '📄', label: '文档转换' },
  repo_map: { icon: '🗺', label: '代码结构' },
  skill_runner: { icon: '🧩', label: 'Skill' },
  git_ops: { icon: '📂', label: 'Git' },
  lsp_client: { icon: '🧠', label: '代码分析' },
}

// Lucide icon mapping for tool cards
const TOOL_ICONS: Record<string, React.ComponentType<{ size?: number }>> = {
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
  repo_map: Map,
  skill_runner: Puzzle,
  git_ops: FolderOpen,
  lsp_client: Brain,
}

function getToolMeta(toolName: string) {
  return TOOL_META[toolName] || { icon: '🔧', label: toolName }
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
  if (input.command && typeof input.command === 'string') {
    return input.command
  }
  if (input.action && input.path) {
    return `${input.action}: ${input.path}`
  }
  if (input.pattern) {
    return `/${input.pattern}/`
  }
  if (input.url) {
    return input.url as string
  }
  const [, val] = entries[0]
  const valStr = typeof val === 'string' ? val : JSON.stringify(val)
  return valStr.length > 60 ? valStr.slice(0, 60) + '...' : valStr
}

export function TaskMessageList({ taskId, messages, streamContent, streamSteps, streamIsThought, planData, isGenerating }: TaskMessageListProps) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamContent, streamSteps])

  return (
    <div className={s.list}>
      {messages.filter((msg) => msg.role !== 'observation').map((msg) => (
        <MessageBubble key={msg.id} taskId={taskId} message={msg} />
      ))}

      {/* Plan progress card (Phase 18) */}
      {planData && planData.steps.length > 0 && (
        <PlanProgressCard plan={planData} />
      )}

      {streamSteps.map((step, i) => (
        <StreamStepView key={i} step={step} />
      ))}

      {streamContent && streamIsThought ? (
        // 流式期间默认显示在分析块，有后续步骤时收起
        <StreamThoughtBlock
          content={streamContent}
          isGenerating={isGenerating}
          defaultOpen={streamSteps.length === 0}
        />
      ) : streamContent ? (
        // 无工具调用时，最终回答显示为消息泡
        <div className={`${s.message} ${s.assistant}`}>
          <div className={s.avatar}><Bot size={18} /></div>
          <div className={`${s.bubble} ${s.assistantBubble}`}>
            <Markdown content={streamContent} className={s.markdown} />
            {isGenerating && <span className={s.streamingCursor}>▍</span>}
          </div>
        </div>
      ) : null}

      {isGenerating && !streamContent && streamSteps.length === 0 && (
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

function MessageBubble({ taskId, message }: { taskId: string | null; message: TaskMessage }) {
  const { t, i18n } = useTranslation()
  const [copied, setCopied] = useState(false)
  const isUser = message.role === 'user'
  const time = new Date(message.created_at).toLocaleTimeString(i18n.language, {
    hour: '2-digit',
    minute: '2-digit',
  })

  // Reasoning: collapsible block at top
  if (message.role === 'reasoning') {
    return <ReasoningBlock content={message.content} />
  }

  // Thought (now called "Analysis"): compact collapsible row inside tool context
  if (message.role === 'thought') {
    return <ThoughtBlock content={message.content} />
  }

  // Action: compact tool card
  if (message.role === 'action') {
    const toolName = message.metadata?.tool_name || 'unknown'
    const toolInput = message.metadata?.tool_input || {}
    const toolOutput = message.metadata?.tool_output
    return <ToolCard taskId={taskId} messageId={message.id} toolName={toolName} input={toolInput} output={toolOutput} done />
  }

  const handleCopy = async (e: React.MouseEvent) => {
    e.stopPropagation()
    try {
      await navigator.clipboard.writeText(message.content)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch { /* ignore */ }
  }

  // User / Assistant message
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

function StreamStepView({ step }: { step: StreamStep }) {
  if (step.type === 'reasoning') {
    return <ReasoningBlock content={step.content} defaultOpen />
  }
  if (step.type === 'thought') {
    return <ThoughtBlock content={step.content} defaultOpen />
  }
  return (
    <ToolCard
      taskId={null}
      toolName={step.tool}
      input={step.input}
      output={step.output}
      diff={step.diff}
      filePath={step.filePath}
      done={!!step.output}
    />
  )
}

/** Collapsible reasoning block — model's internal thinking */
function ReasoningBlock({ content, defaultOpen = false }: { content: string; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className={s.stepRow}>
      <button className={s.stepToggle} onClick={() => setOpen(!open)}>
        <span className={`${s.chevron} ${open ? s.chevronOpen : ''}`}></span>
        <Brain size={14} className={s.stepIcon} />
        <span className={s.stepLabel}>推理</span>
      </button>
      {open && (
        <div className={s.stepDetail}><Markdown content={content} className={s.markdown} /></div>
      )}
    </div>
  )
}

/** Collapsible thought block — Claude Code style (now labeled as "分析") */
function ThoughtBlock({ content, defaultOpen = false }: { content: string; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className={s.stepRow}>
      <button className={s.stepToggle} onClick={() => setOpen(!open)}>
        <span className={`${s.chevron} ${open ? s.chevronOpen : ''}`}>▸</span>
        <span className={s.stepLabel}>分析</span>
      </button>
      {open && (
        <div className={s.stepDetail}><Markdown content={content} className={s.markdown} /></div>
      )}
    </div>
  )
}

/** Streaming thought block — same as ThoughtBlock but with streaming cursor support */
function StreamThoughtBlock({ content, isGenerating, defaultOpen = true }: { content: string; isGenerating: boolean; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen)

  // Auto-collapse when moving to next stage (e.g., tool execution)
  useEffect(() => {
    setOpen(defaultOpen)
  }, [defaultOpen])

  return (
    <div className={s.stepRow}>
      <button className={s.stepToggle} onClick={() => setOpen(!open)}>
        <span className={`${s.chevron} ${open ? s.chevronOpen : ''}`}>▸</span>
        <span className={s.stepLabel}>分析</span>
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

/** Compact tool call card — Cursor / Claude Code style */
function ToolCard({
  taskId,
  messageId,
  toolName,
  input,
  output,
  diff,
  filePath,
  done,
}: {
  taskId: string | null
  messageId?: string
  toolName: string
  input: Record<string, unknown>
  output?: string
  diff?: string
  filePath?: string
  done: boolean
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [rerunning, setRerunning] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const meta = getToolMeta(toolName)
  const summary = formatToolInput(input)

  // Elapsed time counter for running tools
  useEffect(() => {
    if (done) {
      setElapsed(0)
      return
    }
    setElapsed(0)
    const interval = setInterval(() => {
      setElapsed(prev => prev + 1)
    }, 1000)
    return () => clearInterval(interval)
  }, [done])

  // Determine success/failure from output
  let statusIcon: 'ok' | 'error' | null = null
  let fixSuggestion: string | null = null
  let lintWarning: string | null = null
  if (done && output) {
    try {
      const parsed = JSON.parse(output)
      if (typeof parsed === 'object' && parsed !== null) {
        const exitCode = parsed.exit_code
        if (exitCode !== undefined && exitCode !== 0) {
          statusIcon = 'error'
        } else if (parsed.error) {
          statusIcon = 'error'
        } else {
          statusIcon = 'ok'
        }
        // Extract fix suggestion and lint warning
        if (parsed.fix_suggestion) {
          fixSuggestion = parsed.fix_suggestion as string
        }
        if (parsed.lint_warning) {
          lintWarning = parsed.lint_warning as string
        }
        if (parsed.auto_fix_hint) {
          fixSuggestion = parsed.auto_fix_hint as string
        }
      } else {
        statusIcon = 'ok'
      }
    } catch {
      // Non-JSON output = success (e.g. plain text output)
      statusIcon = 'ok'
    }
  }

  const handleRerun = async (e: React.MouseEvent) => {
    e.stopPropagation()
    if (!taskId || !messageId || rerunning) return
    setRerunning(true)
    try {
      await window.electronAPI.invoke('tasks:rerun', { taskId, msgId: messageId })
      // Refresh messages to show the new observation
      window.dispatchEvent(new Event('task:rerun-done'))
    } catch (err) {
      console.error('Rerun failed:', err)
    } finally {
      setRerunning(false)
    }
  }

  // Format elapsed time
  const formatElapsed = (s: number) => {
    if (s < 60) return `${s}s`
    return `${Math.floor(s / 60)}m ${s % 60}s`
  }

  return (
    <div className={s.stepRow}>
      <div className={s.stepRowInner}>
        <button className={s.stepToggle} onClick={() => setOpen(!open)}>
          <span className={`${s.chevron} ${open ? s.chevronOpen : ''}`}>▸</span>
          <span className={`${s.toolIcon}`}>
            {(() => { const Icon = getToolIcon(toolName); return Icon ? <Icon size={14} /> : meta.icon })()}
          </span>
          <span className={s.stepLabel}>{meta.label}</span>
          <span className={s.toolSummary}>{summary}</span>
          {!done && (
            <>
              <span className={s.spinner}></span>
              {elapsed >= 3 && <span className={s.elapsed}>{formatElapsed(elapsed)}</span>}
            </>
          )}
          {statusIcon && <span className={`${s.statusDot} ${statusIcon === 'ok' ? s.statusOk : s.statusError}`} />}
        </button>
        {taskId && messageId && done && (
          <button
            className={s.rerunBtn}
            onClick={handleRerun}
            disabled={rerunning}
            title={t('task.rerun')}
          >
            {rerunning ? '⏳' : '↻'}
          </button>
        )}
      </div>
      {/* Diff viewer for file edits (Phase 18) */}
      {diff && <DiffCard diff={diff} filePath={filePath} />}
      {/* Fix suggestion banner */}
      {fixSuggestion && (
        <div className={s.fixBanner}>
          <Lightbulb size={14} className={s.fixIcon} /> {fixSuggestion}
        </div>
      )}
      {/* Lint warning banner */}
      {lintWarning && !fixSuggestion && (
        <div className={s.warnBanner}>
          <AlertTriangle size={14} className={s.warnIcon} /> {lintWarning}
        </div>
      )}
      {open && output && (
        <div className={s.stepDetail}>
          <pre className={s.toolOutput}>{output}</pre>
        </div>
      )}
    </div>
  )
}

/** Inline diff viewer for file edits (Phase 18) */
function DiffCard({ diff, filePath }: { diff: string; filePath?: string }) {
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
          {filePath || '变更'}
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

/** Plan progress card — shows execution plan steps (Phase 18) */
function PlanProgressCard({ plan }: { plan: PlanData }) {
  const [open, setOpen] = useState(true)
  return (
    <div className={s.planCard}>
      <button className={s.planHeader} onClick={() => setOpen(!open)}>
        <span className={`${s.chevron} ${open ? s.chevronOpen : ''}`}>▸</span>
        <span className={s.planTitle}>📋 执行计划</span>
        <span className={s.planBadge}>{plan.total} 步</span>
      </button>
      {open && (
        <div className={s.planBody}>
          {plan.steps.map((step) => (
            <div key={step.id} className={s.planStep}>
              <span className={s.planStepNum}>{step.id}</span>
              <span className={s.planStepDesc}>{step.desc}</span>
              {step.files.length > 0 && (
                <span className={s.planStepFiles}>{step.files.join(', ')}</span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
