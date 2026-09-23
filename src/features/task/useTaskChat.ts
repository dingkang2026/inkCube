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
  const [streamIsThought, setStreamIsThought] = useState(false) // true = 分析块，false = 消息泡
  const [streamHasAction, setStreamHasAction] = useState(false) // 是否有工具调用
  const streamHasActionRef = useRef(streamHasAction)
  useEffect(() => { streamHasActionRef.current = streamHasAction }, [streamHasAction])
  const [planData, setPlanData] = useState<PlanData | null>(null)
  const [pendingShellConfirm, setPendingShellConfirm] = useState<ShellConfirmData | null>(null)
  const [pendingSkillConfirm, setPendingSkillConfirm] = useState<SkillConfirmData | null>(null)
  const activeTaskIdRef = useRef(activeTaskId)
  useEffect(() => { activeTaskIdRef.current = activeTaskId }, [activeTaskId])
  const onDoneRef = useRef(onDone)
  useEffect(() => { onDoneRef.current = onDone }, [onDone])

  useEffect(() => {
    if (activeTaskId) {
      loadMessages(activeTaskId)
      // Clear streaming state from previous task to avoid content mixing
      setStreamContent('')
      setStreamSteps([])
    } else {
      setMessages([])
      setStreamContent('')
      setStreamSteps([])
      setIsGenerating(false)
    }
  }, [activeTaskId])

  useEffect(() => {
    const off = window.electronAPI.on('task:stream', (data: unknown) => {
      const { taskId, type, data: payload } = data as { taskId: string; type: string; data: string }
      // Only process events for the active task
      if (taskId !== activeTaskIdRef.current) return
      handleStreamEvent(type, payload)
    })
    // Listen for rerun completion to refresh messages
    const onRerunDone = () => {
      if (activeTaskIdRef.current) {
        loadMessages(activeTaskIdRef.current)
      }
    }
    window.addEventListener('task:rerun-done', onRerunDone)
    return () => {
      off()
      window.removeEventListener('task:rerun-done', onRerunDone)
    }
  }, [])

  const handleStreamEvent = (type: string, payload: string) => {
    try {
      const parsed = JSON.parse(payload)
      if (type === 'reasoning') {
        setStreamSteps((prev) => [...prev, { type: 'reasoning', content: parsed.content }])
      } else if (type === 'plan') {
        // Structured execution plan from planner (Phase 18)
        setPlanData(parsed as PlanData)
      } else if (type === 'thought') {
        // Content is analysis (thought event received)
        setStreamIsThought(true)
      } else if (type === 'action') {
        // Tool call detected - content is analysis, keep in 分析 block
        setStreamIsThought(true)
        setStreamHasAction(true)
        setStreamSteps((prev) => [...prev, { type: 'action', tool: parsed.tool, input: parsed.input }])
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
              // Refresh file tree after file-related tool operations
              if (step.tool === 'file_ops' || step.tool === 'doc_generator') {
                window.dispatchEvent(new Event('task:refresh-files'))
              }
              break
            }
          }
          return steps
        })
      } else if (type === 'answer_chunk') {
        // Default to 分析 block during streaming
        // Will be changed to message bubble if no tool calls after done
        setStreamIsThought(true)
        setStreamContent((prev) => prev + (parsed.content || ''))
      } else if (type === 'confirm_shell') {
        setPendingShellConfirm({ command: parsed.command, taskId: parsed.task_id || activeTaskIdRef.current || '' })
      } else if (type === 'confirm_skill') {
        setPendingSkillConfirm({ skillName: parsed.skill_name, taskId: parsed.task_id || activeTaskIdRef.current || '' })
      } else if (type === 'done') {
        setIsGenerating(false)
        // If no tool calls, content is final answer → show as message bubble
        // Use ref to avoid stale closure in event listener
        if (!streamHasActionRef.current) {
          setStreamIsThought(false)
        }
        // Keep streaming state visible - no need to re-read from DB
        onDoneRef.current?.()
      }
    } catch {
      // partial data
    }
  }

  const loadMessages = async (taskId: string) => {
    const res = await window.electronAPI.invoke('tasks:getMessages', taskId)
    setMessages((res as { messages: TaskMessage[] }).messages || [])
  }

  const handleSendMessage = useCallback(
    async (content: string) => {
      if (!activeTaskId || isGenerating) return

      // Clear previous stream state before starting new conversation
      setStreamContent('')
      setStreamSteps([])
      setStreamIsThought(false)
      setStreamHasAction(false)

      const userMsg: TaskMessage = {
        id: crypto.randomUUID(),
        task_id: activeTaskId,
        role: 'user',
        content,
        created_at: new Date().toISOString(),
      }
      setMessages((prev) => [...prev, userMsg])
      setIsGenerating(true)
      setStreamContent('')
      setStreamSteps([])
      setStreamIsThought(false)
      setStreamHasAction(false)

      try {
        await window.electronAPI.invoke('tasks:chat', {
          taskId: activeTaskId,
          message: content,
        })
      } catch (err) {
        console.error('Task chat error:', err)
        setIsGenerating(false)
        // Handle 429 concurrent limit
        const errMsg = err instanceof Error ? err.message : String(err)
        if (errMsg.includes('TASK_LIMIT_REACHED') || errMsg.includes('并发')) {
          const limitMsg: TaskMessage = {
            id: crypto.randomUUID(),
            task_id: activeTaskId,
            role: 'assistant',
            content: '⚠️ 已达到最大并发任务数限制，请等待当前任务完成后再试。',
            created_at: new Date().toISOString(),
          }
          setMessages((prev) => [...prev, limitMsg])
        }
      }
    },
    [activeTaskId, isGenerating],
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

  return {
    messages,
    isGenerating,
    streamContent,
    streamSteps,
    streamIsThought,
    planData,
    pendingShellConfirm,
    pendingSkillConfirm,
    handleSendMessage,
    handleStopGenerate,
    handlePauseTask,
    handleShellConfirm,
    handleSkillConfirm,
  }
}
