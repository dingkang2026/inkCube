import { useState, useEffect, useCallback, useRef } from 'react'
import type { TaskMessage, StreamStep, PlanData } from '../../types'

export interface ShellConfirmData {
  command: string
  taskId: string
}

export interface SkillConfirmData {
  skillName: string
  taskId: string
}

export function useTaskChat(activeTaskId: string | null, onDone?: () => void) {
  const [messages, setMessages] = useState<TaskMessage[]>([])
  const [isGenerating, setIsGenerating] = useState(false)
  const [streamContent, setStreamContent] = useState('')
  const [streamSteps, setStreamSteps] = useState<StreamStep[]>([])
  const [streamReasoning, setStreamReasoning] = useState('')
  const [streamIteration, setStreamIteration] = useState(0)
  const streamIterationRef = useRef(0)
  useEffect(() => { streamIterationRef.current = streamIteration }, [streamIteration])
  const streamContentRef = useRef('')
  useEffect(() => { streamContentRef.current = streamContent }, [streamContent])
  const streamReasoningRef = useRef('')
  useEffect(() => { streamReasoningRef.current = streamReasoning }, [streamReasoning])
  const streamIterationReasoning = useRef<Map<number, string>>(new Map())
  const streamIterationThoughts = useRef<Map<number, string>>(new Map())
  const streamStepsRef = useRef<StreamStep[]>([])
  useEffect(() => { streamStepsRef.current = streamSteps }, [streamSteps])
  const handleStreamEventRef = useRef<(type: string, payload: string) => void>(() => {})
  const handleBgStreamEventRef = useRef<(taskId: string, type: string, payload: string) => void>(() => {})
  const [planData, setPlanData] = useState<PlanData | null>(null)
  const [pendingShellConfirm, setPendingShellConfirm] = useState<ShellConfirmData | null>(null)
  const [pendingSkillConfirm, setPendingSkillConfirm] = useState<SkillConfirmData | null>(null)
  const [canResume, setCanResume] = useState(false)
  const activeTaskIdRef = useRef(activeTaskId)
  useEffect(() => { activeTaskIdRef.current = activeTaskId }, [activeTaskId])
  const onDoneRef = useRef(onDone)
  useEffect(() => { onDoneRef.current = onDone }, [onDone])

  // ── Background task state (Phase 2: per-task streaming) ──
  // Stores streaming state for non-active tasks so switching preserves progress.
  const bgStates = useRef<Map<string, {
    content: string; reasoning: string; steps: StreamStep[]; iteration: number;
    isGenerating: boolean; planData: PlanData | null;
    iterationReasoning: Map<number, string>; iterationThoughts: Map<number, string>;
    pendingShellConfirm: ShellConfirmData | null; pendingSkillConfirm: SkillConfirmData | null;
  }>>(new Map())
  const prevActiveTaskIdRef = useRef<string | null>(null)

  const clearStreamState = useCallback(() => {
    setStreamContent('')
    setStreamSteps([])
    setStreamReasoning('')
    setStreamIteration(0)
    streamIterationReasoning.current = new Map()
    streamIterationThoughts.current = new Map()
    streamIterationRef.current = 0
    streamContentRef.current = ''
    streamReasoningRef.current = ''
    if (activeTaskIdRef.current) {
      bgStates.current.delete(activeTaskIdRef.current)
    }
  }, [])

  // Save/restore streaming state when switching tasks
  useEffect(() => {
    const prevId = prevActiveTaskIdRef.current
    if (prevId && prevId !== activeTaskId) {
      // Save current task's streaming state to background
      bgStates.current.set(prevId, {
        content: streamContentRef.current,
        reasoning: streamReasoningRef.current,
        steps: streamStepsRef.current,
        iteration: streamIterationRef.current,
        isGenerating,
        planData,
        iterationReasoning: new Map(streamIterationReasoning.current),
        iterationThoughts: new Map(streamIterationThoughts.current),
        pendingShellConfirm,
        pendingSkillConfirm,
      })
    }
    prevActiveTaskIdRef.current = activeTaskId

    if (activeTaskId) {
      const bg = bgStates.current.get(activeTaskId)
      if (bg) {
        // Restore background task's streaming state
        streamContentRef.current = bg.content
        setStreamContent(bg.content)
        streamReasoningRef.current = bg.reasoning
        setStreamReasoning(bg.reasoning)
        streamStepsRef.current = bg.steps
        setStreamSteps(bg.steps)
        streamIterationRef.current = bg.iteration
        setStreamIteration(bg.iteration)
        setIsGenerating(bg.isGenerating)
        setPlanData(bg.planData)
        streamIterationReasoning.current = new Map(bg.iterationReasoning)
        streamIterationThoughts.current = new Map(bg.iterationThoughts)
        setPendingShellConfirm(bg.pendingShellConfirm)
        setPendingSkillConfirm(bg.pendingSkillConfirm)
        bgStates.current.delete(activeTaskId)
      } else {
        // New task (no cached bg state) — reset all streaming state including isGenerating
        clearStreamState()
        setIsGenerating(false)
      }
      loadMessages(activeTaskId)
    } else {
      setMessages([])
      clearStreamState()
      setIsGenerating(false)
    }
  }, [activeTaskId])

  useEffect(() => {
    const off = window.electronAPI.on('task:stream', (data: unknown) => {
      const { taskId, type, data: payload } = data as { taskId: string; type: string; data: string }
      if (taskId === activeTaskIdRef.current) {
        handleStreamEventRef.current(type, payload)
      } else {
        handleBgStreamEventRef.current(taskId, type, payload)
      }
    })
    return () => {
      off()
    }
  }, [])

  const handleStreamEvent = (type: string, payload: string) => {
    try {
      const parsed = JSON.parse(payload)
      if (type === 'reasoning') {
        // Save previous iteration's reasoning snapshot before new one arrives
        if (streamReasoningRef.current.trim()) {
          streamIterationReasoning.current.set(streamIterationRef.current, streamReasoningRef.current)
        }
        streamReasoningRef.current = parsed.content || ''
        setStreamReasoning(parsed.content || '')
      } else if (type === 'plan') {
        setPlanData(parsed as PlanData)
      } else if (type === 'thought') {
        // thought = content 的完整版本，已由 answer_chunk 流式显示在 streamContent 中
        // 将 streamContent（中间分析）独立保存为当前迭代的 thought（不与 reasoning 混合）
        if (streamContentRef.current.trim()) {
          streamIterationThoughts.current.set(streamIterationRef.current, streamContentRef.current)
        }
        // 清除 reasoning 和 streamContent，推进迭代计数器
        streamReasoningRef.current = ''
        setStreamReasoning('')
        streamContentRef.current = ''
        setStreamContent('')
        setStreamIteration((prev) => { streamIterationRef.current = prev + 1; return prev + 1 })
        setStreamSteps((prev) => {
          const next = prev.filter((s) => s.type === 'action')
          streamStepsRef.current = next
          return next
        })
      } else if (type === 'action') {
        // Don't clear reasoning — it stays visible in the IterationCard during tool execution
        setStreamSteps((prev) => {
          const next: StreamStep[] = [...prev, { type: 'action', tool: parsed.tool, input: parsed.input, iteration: streamIterationRef.current }]
          streamStepsRef.current = next
          return next
        })
      } else if (type === 'observation') {
        setStreamSteps((prev) => {
          const steps = [...prev]
          for (let i = steps.length - 1; i >= 0; i--) {
            const step = steps[i]
            if (step.type === 'action' && !step.output) {
              steps[i] = {
                ...step,
                output: parsed.content,
                diff: parsed.diff,
                filePath: parsed.file_path,
              }
              if (step.tool === 'file_ops' || step.tool === 'doc_generator') {
                setTimeout(() => window.dispatchEvent(new Event('task:refresh-files')), 0)
              }
              break
            }
          }
          streamStepsRef.current = steps
          return steps
        })
      } else if (type === 'answer_chunk') {
        // Before clearing reasoning, save it to the map (prevents loss when
        // reasoning was flushed during streaming and no subsequent reasoning event arrives)
        if (streamReasoningRef.current.trim()) {
          streamIterationReasoning.current.set(streamIterationRef.current, streamReasoningRef.current)
        }
        streamReasoningRef.current = ''
        setStreamReasoning('')
        setStreamContent((prev) => prev + (parsed.content || ''))
      } else if (type === 'confirm_shell') {
        setPendingShellConfirm({ command: parsed.command, taskId: parsed.task_id || activeTaskIdRef.current || '' })
      } else if (type === 'confirm_skill') {
        setPendingSkillConfirm({ skillName: parsed.skill_name, taskId: parsed.task_id || activeTaskIdRef.current || '' })
      } else if (type === 'done') {
        setIsGenerating(false)
        // Convert ALL streaming state to permanent messages (no DB reload)
        const newMessages: TaskMessage[] = []
        const now = new Date().toISOString()
        const taskId = activeTaskIdRef.current || ''

        // Save current reasoning to map
        if (streamReasoningRef.current.trim()) {
          streamIterationReasoning.current.set(streamIterationRef.current, streamReasoningRef.current)
        }

        // Collect all iteration keys (from both reasoning and thoughts)
        const allIters = new Set<number>([
          ...streamIterationReasoning.current.keys(),
          ...streamIterationThoughts.current.keys(),
        ])

        // Build messages per iteration: reasoning → thought → actions
        for (const iter of allIters) {
          const reasoning = streamIterationReasoning.current.get(iter)
          if (reasoning?.trim()) {
            newMessages.push({ id: crypto.randomUUID(), task_id: taskId, role: 'reasoning', content: reasoning, created_at: now })
          }
          const thought = streamIterationThoughts.current.get(iter)
          if (thought?.trim()) {
            newMessages.push({ id: crypto.randomUUID(), task_id: taskId, role: 'thought', content: thought, created_at: now })
          }
          const iterActions = streamStepsRef.current.filter((s) => (s.iteration ?? 0) === iter)
          for (const step of iterActions) {
            const actionId = crypto.randomUUID()
            newMessages.push({
              id: actionId, task_id: taskId, role: 'action', content: '',
              metadata: { tool_name: step.tool, tool_input: step.input },
              created_at: now,
            })
            if (step.output) {
              newMessages.push({
                id: crypto.randomUUID(), task_id: taskId, role: 'observation', content: '',
                metadata: {
                  tool_name: step.tool, tool_input: step.input,
                  tool_output: step.output as string, diff: step.diff,
                  file_path: step.filePath, action_id: actionId,
                },
                created_at: now,
              })
            }
          }
        }

        // Convert final answer (streamContent) to assistant message bubble
        if (streamContentRef.current.trim()) {
          newMessages.push({
            id: crypto.randomUUID(), task_id: taskId, role: 'assistant',
            content: streamContentRef.current, created_at: now,
          })
        }

        if (newMessages.length > 0) {
          setMessages((prev) => [...prev, ...newMessages])
        }

        // Clear streaming state
        bgStates.current.delete(taskId)
        clearStreamState()
        onDoneRef.current?.()
      }
    } catch {
      // partial data
    }
  }
  handleStreamEventRef.current = handleStreamEvent

  // ── Background task stream event handler ──
  const handleBgStreamEvent = (taskId: string, type: string, payload: string) => {
    try {
      const parsed = JSON.parse(payload)
      const bg = bgStates.current.get(taskId)
      if (!bg && type !== 'done') return

      if (type === 'reasoning') {
        if (!bg) return
        if (bg.reasoning.trim()) {
          bg.iterationReasoning.set(bg.iteration, bg.reasoning)
        }
        bg.reasoning = parsed.content || ''
      } else if (type === 'thought') {
        if (!bg) return
        if (bg.content.trim()) {
          bg.iterationThoughts.set(bg.iteration, bg.content)
        }
        bg.reasoning = ''
        bg.content = ''
        bg.iteration++
        bg.steps = bg.steps.filter((s) => s.type === 'action')
      } else if (type === 'action') {
        if (!bg) return
        bg.steps.push({ type: 'action', tool: parsed.tool, input: parsed.input, iteration: bg.iteration })
      } else if (type === 'observation') {
        if (!bg) return
        for (let i = bg.steps.length - 1; i >= 0; i--) {
          if (bg.steps[i].type === 'action' && !bg.steps[i].output) {
            bg.steps[i] = { ...bg.steps[i], output: parsed.content, diff: parsed.diff, filePath: parsed.file_path }
            break
          }
        }
      } else if (type === 'answer_chunk') {
        if (!bg) return
        if (bg.reasoning.trim()) {
          bg.iterationReasoning.set(bg.iteration, bg.reasoning)
        }
        bg.reasoning = ''
        bg.content += (parsed.content || '')
      } else if (type === 'plan') {
        if (!bg) return
        bg.planData = parsed as PlanData
      } else if (type === 'confirm_shell') {
        // Background task needs shell confirmation — store in bgState
        // so it shows when user switches to this task
        if (!bg) return
        bg.pendingShellConfirm = {
          command: parsed.command,
          taskId: parsed.task_id || taskId,
        }
      } else if (type === 'confirm_skill') {
        // Background task needs skill confirmation — store in bgState
        if (!bg) return
        bg.pendingSkillConfirm = {
          skillName: parsed.skill_name,
          taskId: parsed.task_id || taskId,
        }
      } else if (type === 'done') {
        // Background task finished — clean up. DB already has the messages.
        bgStates.current.delete(taskId)
      }
    } catch {
      // partial data
    }
  }
  handleBgStreamEventRef.current = handleBgStreamEvent

  const loadMessages = async (taskId: string) => {
    const res = await window.electronAPI.invoke('tasks:getMessages', taskId)
    setMessages((res as { messages: TaskMessage[] }).messages || [])
    // Check if task has saved state (for resume UI)
    try {
      const stateRes = await window.electronAPI.invoke('tasks:hasState', taskId) as { has_state: boolean }
      setCanResume(stateRes.has_state)
    } catch {
      setCanResume(false)
    }
  }

  const handleSendMessage = useCallback(
    async (content: string) => {
      if (!activeTaskId || isGenerating) return

      clearStreamState()
      bgStates.current.delete(activeTaskId)

      const userMsg: TaskMessage = {
        id: crypto.randomUUID(),
        task_id: activeTaskId,
        role: 'user',
        content,
        created_at: new Date().toISOString(),
      }
      setMessages((prev) => [...prev, userMsg])
      setIsGenerating(true)

      try {
        await window.electronAPI.invoke('tasks:chat', {
          taskId: activeTaskId,
          message: content,
        })
      } catch (err) {
        console.error('Task chat error:', err)
        setIsGenerating(false)
        const errMsg = err instanceof Error ? err.message : String(err)
        if (errMsg.includes('TASK_LIMIT_REACHED') || errMsg.includes('并发')) {
          const limitMsg: TaskMessage = {
            id: crypto.randomUUID(),
            task_id: activeTaskId,
            role: 'assistant',
            content: '已达到最大并发任务数限制，请等待当前任务完成后再试。',
            created_at: new Date().toISOString(),
          }
          setMessages((prev) => [...prev, limitMsg])
        } else if (errMsg.includes('TASK_RUNNING') || errMsg.includes('执行中')) {
          const runningMsg: TaskMessage = {
            id: crypto.randomUUID(),
            task_id: activeTaskId,
            role: 'assistant',
            content: '该任务正在执行中，请等待完成或先停止。',
            created_at: new Date().toISOString(),
          }
          setMessages((prev) => [...prev, runningMsg])
        } else {
          const errorMsg: TaskMessage = {
            id: crypto.randomUUID(),
            task_id: activeTaskId,
            role: 'assistant',
            content: `任务启动失败：${errMsg}`,
            created_at: new Date().toISOString(),
          }
          setMessages((prev) => [...prev, errorMsg])
        }
      }
    },
    [activeTaskId, isGenerating, clearStreamState],
  )

  const handleStopGenerate = useCallback(() => {
    if (activeTaskId) {
      window.electronAPI.invoke('tasks:cancelChat', activeTaskId)
    }
    setIsGenerating(false)
  }, [activeTaskId])

  const handlePauseTask = useCallback(async () => {
    if (!activeTaskId) return
    try {
      await window.electronAPI.invoke('tasks:pause', { taskId: activeTaskId })
      setIsGenerating(false)
    } catch (err) {
      console.error('Pause task error:', err)
    }
  }, [activeTaskId])

  const handleShellConfirm = useCallback(async (confirmed: boolean, allowSession: boolean = false) => {
    if (!activeTaskId) return
    setPendingShellConfirm(null)
    try {
      await window.electronAPI.invoke('tasks:confirmShell', { taskId: activeTaskId, confirmed, allow_session: allowSession })
    } catch (err) {
      console.error('Shell confirm error:', err)
    }
  }, [activeTaskId])

  const handleSkillConfirm = useCallback(async (confirmed: boolean) => {
    if (!activeTaskId) return
    setPendingSkillConfirm(null)
    try {
      await window.electronAPI.invoke('tasks:confirmSkill', { taskId: activeTaskId, confirmed })
    } catch (err) {
      console.error('Skill confirm error:', err)
    }
  }, [activeTaskId])

  /** Resume a task from saved state (crash recovery or pause). */
  const handleResumeTask = useCallback(async () => {
    if (!activeTaskId || isGenerating) return
    clearStreamState()
    bgStates.current.delete(activeTaskId)
    setIsGenerating(true)
    setCanResume(false)
    try {
      await window.electronAPI.invoke('tasks:chat', {
        taskId: activeTaskId,
        message: '',
        resume: true,
      })
    } catch (err) {
      console.error('Resume task error:', err)
      setIsGenerating(false)
    }
  }, [activeTaskId, isGenerating, clearStreamState])

  /** Reset generating state after Python process crash (Phase 1 crash recovery). */
  const resetGeneratingState = useCallback(() => {
    setIsGenerating(false)
    clearStreamState()
  }, [clearStreamState])

  return {
    messages,
    isGenerating,
    streamContent,
    streamSteps,
    streamReasoning,
    streamIteration,
    streamIterationThoughts: streamIterationThoughts.current,
    streamIterationReasoning: streamIterationReasoning.current,
    planData,
    pendingShellConfirm,
    pendingSkillConfirm,
    handleSendMessage,
    handleStopGenerate,
    handlePauseTask,
    handleShellConfirm,
    handleSkillConfirm,
    handleResumeTask,
    canResume,
    resetGeneratingState,
  }
}
