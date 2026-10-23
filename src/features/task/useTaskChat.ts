import { useState, useEffect, useCallback, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import type { TaskMessage, StreamStep, PlanData, UsageSummary } from '../../types'
import { apiErrorText } from '../../i18n/errorCodes'
import { browserPanelStore } from '../browser/browserPanelStore'

export interface ShellConfirmData {
  command: string
  taskId: string
  /** 倒计时截止时刻（ms，收到事件时由后端 timeout 字段推算；0 = 无倒计时，
   *  仅防御 timeout 缺失的异常载荷）。绝对时间戳：bgStates 保存/恢复后剩余时间
   *  自动正确，不因切换任务重置 */
  expiresAt: number
}

export interface SkillConfirmData {
  skillName: string
  taskId: string
  /** 同 ShellConfirmData.expiresAt */
  expiresAt: number
}

export function useTaskChat(activeTaskId: string | null, onDone?: () => void) {
  const { t } = useTranslation()
  const [messages, setMessages] = useState<TaskMessage[]>([])
  const [isGenerating, setIsGenerating] = useState(false)
  const [streamContent, setStreamContent] = useState('')
  const [streamSteps, setStreamSteps] = useState<StreamStep[]>([])
  const [streamReasoning, setStreamReasoning] = useState('')
  const [streamIteration, setStreamIteration] = useState(0)
  // 注意：streamXxxRef 是写侧真相（事件处理器同步维护），streamXxx state 仅是渲染投影；
  // 禁止用 state→ref 回写 effect 同步——高频流式下 effect 闭包里的 state 已过期，
  // 会把 ref 回滚到旧值，导致中间增量永久丢失（丢字 bug 根因，勿恢复）
  const streamIterationRef = useRef(0)
  const streamContentRef = useRef('')
  const streamReasoningRef = useRef('')
  const streamIterationReasoning = useRef<Map<number, string>>(new Map())
  const streamIterationThoughts = useRef<Map<number, string>>(new Map())
  const streamStepsRef = useRef<StreamStep[]>([])
  const handleStreamEventRef = useRef<(type: string, payload: string) => void>(() => {})
  const handleBgStreamEventRef = useRef<(taskId: string, type: string, payload: string) => void>(() => {})
  const [planData, setPlanData] = useState<PlanData | null>(null)
  // 轮级 usage（2.10 §4.5）：done 前到达的 SSE usage 事件暂存于此，done 转 assistant
  // 消息时附着 metadata.usage（历史回放由 DB 同数据兜底）；后台路径不消费
  const pendingUsageRef = useRef<UsageSummary | null>(null)
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
  /** override 发送后的首次 activeTaskId 切换抑制标记（0.5 §4.4 发送即建任务）：
   *  创建后立即发送已完成 messages/isGenerating/ref 全部状态设置，紧随的切换 effect
   *  （null → 新 id）若再跑，else 分支会重置 isGenerating（流式呈现/停止钮失效、
   *  sendingRef 提前释放），loadMessages 会用后端空列表覆盖乐观 user 消息——
   *  该切换是冗余回流，匹配即跳过并消费标记（一次性，不影响后续正常 bg 保存/恢复） */
  const suppressSwitchRef = useRef<string | null>(null)

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
    // Terminal/cleanup state must not leave stale confirm dialogs on screen
    // (backend already popped the pending confirm on timeout/done)
    setPendingShellConfirm(null)
    setPendingSkillConfirm(null)
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

    if (activeTaskId && activeTaskId === suppressSwitchRef.current) {
      suppressSwitchRef.current = null
      return
    }

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
      if (type === 'iteration') {
        // 显式迭代边界（后端每轮 LLM 调用开始时发送）：推进轮号、开新轮。
        // 兜底快照上一轮 reasoning（该轮无 content → 无 thought 事件时的丢失窗口）
        if (streamReasoningRef.current.trim() && !streamIterationReasoning.current.has(streamIterationRef.current)) {
          streamIterationReasoning.current.set(streamIterationRef.current, streamReasoningRef.current)
        }
        streamIterationRef.current = typeof parsed.iteration === 'number' ? parsed.iteration : 0
        setStreamIteration(streamIterationRef.current)
        streamReasoningRef.current = ''
        setStreamReasoning('')
        streamContentRef.current = ''
        setStreamContent('')
      } else if (type === 'reasoning_chunk') {
        // 推理逐字流式（1.2 §2）；DB 由后端在 flush 点存全量，前端只累积
        streamReasoningRef.current += parsed.delta || ''
        setStreamReasoning(streamReasoningRef.current)
      } else if (type === 'reasoning_reset') {
        // LLM 瞬时错误重试：清空已渲染的部分推理，重试流从头累积
        streamReasoningRef.current = ''
        setStreamReasoning('')
      } else if (type === 'plan') {
        setPlanData(parsed as PlanData)
      } else if (type === 'thought') {
        // 本轮 LLM 响应结束（有 tool_calls）：锁定该轮推理+分析快照，清空当前流。
        // 迭代推进由 iteration 事件负责；快照取流式内容，为空时回退载荷——
        // 提示型 thought（planner/恢复提示，P2）无前置流式内容，载荷即文案
        // （此前被静默丢弃，「流内提示」从未可见）；同一轮已有值时换行追加
        //（提示 + 随后到达的正常分析同卡可见）
        if (streamReasoningRef.current.trim()) {
          streamIterationReasoning.current.set(streamIterationRef.current, streamReasoningRef.current)
        }
        const incoming = streamContentRef.current.trim()
          || (typeof parsed.content === 'string' ? parsed.content.trim() : '')
        if (incoming) {
          const prevThought = streamIterationThoughts.current.get(streamIterationRef.current)
          streamIterationThoughts.current.set(
            streamIterationRef.current,
            prevThought ? `${prevThought}\n${incoming}` : incoming,
          )
        }
        streamReasoningRef.current = ''
        setStreamReasoning('')
        streamContentRef.current = ''
        setStreamContent('')
      } else if (type === 'action') {
        // Don't clear reasoning — it stays visible in the IterationCard during tool execution
        // V2 §8.2：browser-fetch 实时驱动 → 弹 Compact 面板（仅前台流；后台/历史回放不弹）。
        // skill_runner 备选路径（slash 调用 hint 允许 LLM 走 skill_runner）同样感知
        if (
          parsed.tool === 'browser-fetch' ||
          (parsed.tool === 'skill_runner' && (parsed.input as { skill?: string } | undefined)?.skill === 'browser-fetch')
        ) {
          browserPanelStore.showCompact()
          // type 输入可见性（V2.1-R10）：输入动作需要用户看清输入框——小卡
          // 360×480 里几乎不可见，自动放大；observation 到达回小卡
          // （collapseIfAuto 仅对 auto 放大生效，用户手动切换优先）
          const bfInput = parsed.input as
            | { command?: string; input?: { command?: string } }
            | undefined
          const cmd =
            (parsed.tool === 'browser-fetch' ? bfInput?.command : bfInput?.input?.command) || ''
          if (cmd.startsWith('type')) browserPanelStore.expandForInput()
        }
        setStreamSteps((prev) => {
          const next: StreamStep[] = [...prev, { type: 'action', tool: parsed.tool, input: parsed.input, iteration: streamIterationRef.current }]
          streamStepsRef.current = next
          return next
        })
      } else if (type === 'step_note') {
        // Skill 首用依赖安装进度（2.8 §5.5）：粗粒度两条（开始/终态），
        // 渲染在当前迭代卡内；不持久化——预装一次性，历史回放无需呈现
        setStreamSteps((prev) => {
          const next: StreamStep[] = [...prev, { type: 'note', text: parsed.text || '', iteration: streamIterationRef.current }]
          streamStepsRef.current = next
          return next
        })
      } else if (type === 'observation') {
        // type 自动放大随输入完成回小卡（V2.1-R10；仅 auto 触发的才回，
        // 用户手动放大不受影响）
        browserPanelStore.collapseIfAuto()
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
      } else if (type === 'answer_reset') {
        // LLM 瞬时错误重试/失败兜底：清空已渲染的部分内容，后续从头累积（与 reasoning_reset 对称）
        streamContentRef.current = ''
        setStreamContent('')
      } else if (type === 'answer_chunk') {
        // 分析/最终回答流式累积；快照职责在 thought / iteration / done 事件
        streamContentRef.current += parsed.content || ''
        setStreamContent(streamContentRef.current)
      } else if (type === 'confirm_shell') {
        setPendingShellConfirm({
          command: parsed.command,
          taskId: parsed.task_id || activeTaskIdRef.current || '',
          expiresAt: parsed.timeout ? Date.now() + parsed.timeout * 1000 : 0,
        })
      } else if (type === 'confirm_skill') {
        setPendingSkillConfirm({
          skillName: parsed.skill_name,
          taskId: parsed.task_id || activeTaskIdRef.current || '',
          expiresAt: parsed.timeout ? Date.now() + parsed.timeout * 1000 : 0,
        })
      } else if (type === 'confirm_expired') {
        // Backend confirm wait timed out: dismiss the stale dialog; the skip
        // reason is traced via the action's observation (1.1 §7.2)
        if (parsed.kind === 'shell') setPendingShellConfirm(null)
        else setPendingSkillConfirm(null)
      } else if (type === 'usage') {
        // 轮级 usage（done 前，2.10 §4.5）：暂存供 done 附着；同时通知 L2 呈现位
        // （状态栏/列表行/徽章）按去抖节奏重取（§5.3 事件路径）
        pendingUsageRef.current = parsed as UsageSummary
        window.dispatchEvent(new Event('app:usage-changed'))
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

        // Collect all iteration keys (maps + steps — a tool-only iteration
        // has no map entry and must not lose its actions, e.g. resume rounds)
        const allIters = new Set<number>([
          ...streamIterationReasoning.current.keys(),
          ...streamIterationThoughts.current.keys(),
          ...streamStepsRef.current.map((s) => s.iteration ?? 0),
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
          // note 步骤（skill 首用环境提示，2.8 §5.5）不持久化——预装一次性，
          // 历史回放无需呈现，与 useTaskChat 的流式期渲染策略一致
          const iterActions = streamStepsRef.current.filter(
            (s): s is Extract<StreamStep, { type: 'action' }> =>
              (s.iteration ?? 0) === iter && s.type === 'action',
          )
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
            content: streamContentRef.current,
            metadata: pendingUsageRef.current ? { usage: pendingUsageRef.current } : undefined,
            created_at: now,
          })
        }
        pendingUsageRef.current = null

        if (newMessages.length > 0) {
          setMessages((prev) => [...prev, ...newMessages])
        }

        // Clear streaming state
        bgStates.current.delete(taskId)
        clearStreamState()
        // done 不重载消息，canResume 原先只在 loadMessages（切任务）时查询——
        // 前台看着任务耗尽时恢复横幅不出现，兜底文案指路的「恢复」按钮缺失
        //（P2 CodeReview M2；后台任务不刷，切回时 loadMessages 自然覆盖）
        refreshCanResume(taskId)
        onDoneRef.current?.()
      }
    } catch {
      // partial data — malformed/incomplete payload, skip
    }
  }
  handleStreamEventRef.current = handleStreamEvent

  // ── Background task stream event handler ──
  const handleBgStreamEvent = (taskId: string, type: string, payload: string) => {
    try {
      const parsed = JSON.parse(payload)
      const bg = bgStates.current.get(taskId)
      if (!bg && type !== 'done') return

      if (type === 'iteration') {
        if (!bg) return
        if (bg.reasoning.trim() && !bg.iterationReasoning.has(bg.iteration)) {
          bg.iterationReasoning.set(bg.iteration, bg.reasoning)
        }
        bg.iteration = typeof parsed.iteration === 'number' ? parsed.iteration : 0
        bg.reasoning = ''
        bg.content = ''
      } else if (type === 'reasoning_chunk') {
        if (!bg) return
        bg.reasoning += parsed.delta || ''
      } else if (type === 'reasoning_reset') {
        if (!bg) return
        bg.reasoning = ''
      } else if (type === 'answer_reset') {
        // LLM 瞬时错误重试/失败兜底：清空已累积的部分内容（与 reasoning_reset 对称）
        if (!bg) return
        bg.content = ''
      } else if (type === 'thought') {
        if (!bg) return
        if (bg.reasoning.trim()) {
          bg.iterationReasoning.set(bg.iteration, bg.reasoning)
        }
        // 与前台路径对称：流式内容优先，空则取载荷（提示型 thought）
        const incoming = bg.content.trim()
          || (typeof parsed.content === 'string' ? parsed.content.trim() : '')
        if (incoming) {
          const prevThought = bg.iterationThoughts.get(bg.iteration)
          bg.iterationThoughts.set(
            bg.iteration,
            prevThought ? `${prevThought}\n${incoming}` : incoming,
          )
        }
        bg.reasoning = ''
        bg.content = ''
      } else if (type === 'action') {
        if (!bg) return
        bg.steps.push({ type: 'action', tool: parsed.tool, input: parsed.input, iteration: bg.iteration })
      } else if (type === 'observation') {
        if (!bg) return
        for (let i = bg.steps.length - 1; i >= 0; i--) {
          const step = bg.steps[i]
          if (step.type !== 'action') continue // note 不参与 action/observation 配对
          if (step.output) continue
          bg.steps[i] = { ...step, output: parsed.content, diff: parsed.diff, filePath: parsed.file_path }
          break
        }
      } else if (type === 'answer_chunk') {
        if (!bg) return
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
          expiresAt: parsed.timeout ? Date.now() + parsed.timeout * 1000 : 0,
        }
      } else if (type === 'confirm_skill') {
        // Background task needs skill confirmation — store in bgState
        if (!bg) return
        bg.pendingSkillConfirm = {
          skillName: parsed.skill_name,
          taskId: parsed.task_id || taskId,
          expiresAt: parsed.timeout ? Date.now() + parsed.timeout * 1000 : 0,
        }
      } else if (type === 'confirm_expired') {
        // Timeout dismissal must also clear the bgState copy (restored on
        // task switch — otherwise a dead dialog reappears, 1.1 §7.2)
        if (!bg) return
        if (parsed.kind === 'shell') bg.pendingShellConfirm = null
        else bg.pendingSkillConfirm = null
      } else if (type === 'done') {
        // Background task finished — clean up. DB already has the messages.
        bgStates.current.delete(taskId)
      }
      // type === 'usage' 后台路径不消费（2.10 §4.5）：气泡用 DB metadata.usage 兜底，
      // 全局呈现位的 app:usage-changed 信号由前台路径/活动终态发出
    } catch {
      // partial data — malformed/incomplete payload, skip
    }
  }
  handleBgStreamEventRef.current = handleBgStreamEvent

  // Check if task has saved state (for resume UI)；Boolean 归一化防 undefined 入 state
  const refreshCanResume = useCallback(async (taskId: string) => {
    try {
      const stateRes = await window.electronAPI.invoke('tasks:hasState', taskId) as { has_state: boolean }
      // 乱序守卫：响应返回时已切走 → 丢弃（canResume 只描述当前活跃任务，
      // 与 loadMessages 同一竞态窗口；done 路径的 taskId 恒等于活跃任务，不受影响）
      if (activeTaskIdRef.current !== taskId) return
      setCanResume(Boolean(stateRes.has_state))
    } catch {
      if (activeTaskIdRef.current === taskId) setCanResume(false)
    }
  }, [])

  const loadMessages = async (taskId: string) => {
    const res = await window.electronAPI.invoke('tasks:getMessages', taskId)
    // 乱序守卫（review 2026-10-20 T-3）：快速切换任务时，旧任务的慢响应
    // 不得覆盖新任务的消息列表（同文件流式路径已有归属判定，唯此加载路径缺失）
    if (activeTaskIdRef.current !== taskId) return
    setMessages((res as { messages: TaskMessage[] }).messages || [])
    await refreshCanResume(taskId)
  }

  const handleSendMessage = useCallback(
    async (content: string, overrideTaskId?: string) => {
      // overrideTaskId：欢迎页「发送即建任务」链路（0.5 §4.4）——创建任务后立即以新 id
      // 发送首条，此时 activeTaskId prop/ref 尚未经渲染回流，显式指定并在发送瞬间同步
      // activeTaskIdRef：流事件归属判定（L132）与 done 合成消息的 task_id（L273）都读它
      const tid = overrideTaskId ?? activeTaskId
      if (!tid || isGenerating) return
      if (overrideTaskId) {
        activeTaskIdRef.current = overrideTaskId
        suppressSwitchRef.current = overrideTaskId
      }

      clearStreamState()
      bgStates.current.delete(tid)

      const userMsg: TaskMessage = {
        id: crypto.randomUUID(),
        task_id: tid,
        role: 'user',
        content,
        created_at: new Date().toISOString(),
      }
      setMessages((prev) => [...prev, userMsg])
      setIsGenerating(true)

      try {
        const res = await window.electronAPI.invoke('tasks:chat', {
          taskId: tid,
          message: content,
        }) as { error?: string; code?: string } | null | undefined
        // 200+error body（错误桥合法形态二，review 2026-10-20 T-2）：tasks:chat
        // 桥把 200 响应体当 SSE 流解析，dict 错误不抛错——显式检查防 isGenerating 卡死
        if (res?.error) {
          setIsGenerating(false)
          setMessages((prev) => [...prev, {
            id: crypto.randomUUID(),
            task_id: tid,
            role: 'assistant',
            content: apiErrorText(res.code, res.error || t('task.startFailed'), t),
            created_at: new Date().toISOString(),
          }])
          return
        }
      } catch (err) {
        console.error('Task chat error:', err)
        setIsGenerating(false)
        const errMsg = err instanceof Error ? err.message : String(err)
        const errCode = (err as { errorCode?: string })?.errorCode
        // D1 错误契约：code 命中映射 → 当前语言文案；否则 fallback 后端原文
        const text = apiErrorText(errCode, `${t('task.startFailed')}${errMsg}`, t)
        const infoMsg: TaskMessage = {
          id: crypto.randomUUID(),
          task_id: tid,
          role: 'assistant',
          content: text,
          created_at: new Date().toISOString(),
        }
        setMessages((prev) => [...prev, infoMsg])
      }
    },
    [activeTaskId, isGenerating, clearStreamState, t],
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
      // 暂停成功后丢弃残留的确认弹窗：后端已把暂停后的「允许」视为拒绝，
      // 前端同步清理避免误导（弹窗看似还能放行）
      setPendingShellConfirm(null)
      setPendingSkillConfirm(null)
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

  /** 倒计时到 0 的本地清理（不发拒绝 POST）：真实等待终止由后端超时分支
   *  发 confirm_expired 留痕「确认超时，已跳过」——若此处发 confirmed=false，
   *  后端会把超时记成「用户拒绝」，observation 语义失真 */
  const dismissPendingConfirm = useCallback((kind: 'shell' | 'skill') => {
    if (kind === 'shell') setPendingShellConfirm(null)
    else setPendingSkillConfirm(null)
  }, [])

  const handleSkillConfirm = useCallback(async (confirmed: boolean) => {
    if (!activeTaskId) return
    setPendingSkillConfirm(null)
    try {
      const res = await window.electronAPI.invoke('tasks:confirmSkill', { taskId: activeTaskId, confirmed }) as { found?: boolean } | null
      if (res && res.found === false) {
        // Pending confirm already popped server-side (timeout): surface it
        // instead of silently swallowing the click (1.1 §7.2)
        setMessages((prev) => [...prev, {
          id: crypto.randomUUID(),
          task_id: activeTaskId,
          role: 'assistant',
          content: t('task.skillConfirmExpired'),
          created_at: new Date().toISOString(),
        }])
      }
    } catch (err) {
      console.error('Skill confirm error:', err)
    }
  }, [activeTaskId, t])

  /** Resume a task from saved state (crash recovery or pause). */
  const handleResumeTask = useCallback(async () => {
    if (!activeTaskId || isGenerating) return
    clearStreamState()
    bgStates.current.delete(activeTaskId)
    setIsGenerating(true)
    setCanResume(false)
    const tid = activeTaskId
    try {
      const res = await window.electronAPI.invoke('tasks:chat', {
        taskId: tid,
        message: '',
        resume: true,
      }) as { error?: string; code?: string } | null | undefined
      // 200+error body（T-2 同款）：不检查则 await 正常返回且无 SSE done 事件，
      // isGenerating 恒 true、输入框永久锁死（对齐 confirmSkill found===false 先例）
      if (res?.error) {
        setIsGenerating(false)
        setMessages((prev) => [...prev, {
          id: crypto.randomUUID(),
          task_id: tid,
          role: 'assistant',
          content: apiErrorText(res.code, res.error || t('task.startFailed'), t),
          created_at: new Date().toISOString(),
        }])
      }
    } catch (err) {
      console.error('Resume task error:', err)
      setIsGenerating(false)
    }
  }, [activeTaskId, isGenerating, clearStreamState, t])

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
    dismissPendingConfirm,
    handleResumeTask,
    canResume,
    resetGeneratingState,
    loadMessages,
  }
}
