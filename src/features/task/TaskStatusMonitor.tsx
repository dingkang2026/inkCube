/** src/features/task/TaskStatusMonitor.tsx — 任务系统状态轮询（0.4 §8.1 #2，v3.9 顺序 5 收编）
 *
 * 谓词全局派生：任务页激活 || 本地会话流式生成中 || useActivity('task') 有运行中任务
 * （后台任务经全局心跳驱动——非本窗口发起 / 崩溃恢复后重启的任务同样被轮询覆盖）。
 * 无渲染组件：App 自身渲染 ActivityProvider（useActivity 不可用），故抽本组件挂
 * Provider 内；can_start / running_task_ids 经 onStatus 回传 App（TaskList 消费）。
 */
import { useEffect, useRef } from 'react'
import { usePolling } from '../../hooks/usePolling'
import { useEdgeRefetch } from '../../hooks/useEdgeRefetch'
import { useActivity } from '../activity/ActivityContext'

interface TaskRuntimeStatus {
  can_start: boolean
  running_task_ids?: string[]
}

interface Props {
  /** 任务页处于激活态（页面可见性兜底——在页面时即时刷新，不依赖心跳粒度） */
  activeOnTasks: boolean
  /** 本地会话流式生成中（SSE done 先于后端 finally 清理 running 集，结束补拉带 500ms 延迟） */
  localGenerating: boolean
  /** 状态回传（App 持有 canStartTask / runningTaskIds） */
  onStatus: (status: TaskRuntimeStatus) => void
}

export function TaskStatusMonitor({ activeOnTasks, localGenerating, onStatus }: Props) {
  const { isActive: taskActive } = useActivity('task')
  // onStatus 经 ref 透传：回调引用变化不触发轮询循环重建
  const onStatusRef = useRef(onStatus)
  onStatusRef.current = onStatus

  const fetchStatus = async () => {
    // 抛错由 usePolling 计入连续失败并退避（0.4 §5.8）
    const status = await window.electronAPI.invoke('tasks:getStatus') as TaskRuntimeStatus
    onStatusRef.current(status)
  }
  const fetchStatusRef = useRef(fetchStatus)
  fetchStatusRef.current = fetchStatus

  // 谓词：页面激活 || 本地生成 || 后台运行中任务（全局心跳派生，禁手动布尔）
  const enabled = activeOnTasks || localGenerating || taskActive
  usePolling({ name: 'task-status', enabled, interval: 3000, task: fetchStatus })

  // 非轮询期重置派生标记（原 App shouldPoll=false 分支：无任务时 can_start 恒真、运行集恒空）
  useEffect(() => {
    if (!enabled) onStatusRef.current({ can_start: true, running_task_ids: [] })
  }, [enabled])

  // 本地流式结束补拉：SSE done 早于 run() finally 清理 running 集，立即拉会读到旧值
  useEffect(() => {
    if (localGenerating) return
    const timer = setTimeout(() => {
      void fetchStatusRef.current().catch(() => { /* best-effort，下轮心跳兜底 */ })
    }, 500)
    return () => clearTimeout(timer)
  }, [localGenerating])

  // 后台任务终态边沿（§6.3）：心跳翻 false 补拉一次，can_start 即时恢复
  useEdgeRefetch(taskActive, () => {
    void fetchStatusRef.current().catch(() => { /* best-effort */ })
  })

  return null
}
