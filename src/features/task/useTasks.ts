import { useState, useEffect, useCallback, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { useToast } from '../knowledge/Toast'
import { apiErrorText } from '../../i18n/errorCodes'
import type { Task } from '../../types'

export type TaskTab = 'active' | 'archived'

/** 「加载更多」分页步长（后端 list_tasks 同款 limit+1 探测 has_more） */
const PAGE_SIZE = 50

export function useTasks(defaultTaskTitle = 'New Task') {
  const { t } = useTranslation()
  const showToast = useToast()
  const [tasks, setTasks] = useState<Task[]>([])
  const [archivedTasks, setArchivedTasks] = useState<Task[]>([])
  const [activeTab, setActiveTab] = useState<TaskTab>('active')
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  // 每个 tab 独立分页：ref 保存当前已加载量——刷新按原量重拉（外部持久化闭包如
  // useTaskChat 的 loadTasks 回调也读到最新值，不会退回第一页）
  const limitsRef = useRef<Record<TaskTab, number>>({ active: PAGE_SIZE, archived: PAGE_SIZE })
  const [hasMore, setHasMore] = useState<Record<TaskTab, boolean>>({ active: false, archived: false })

  const fetchPage = useCallback(async (tab: TaskTab, limit: number) => {
    const res = await window.electronAPI.invoke('tasks:listFiltered', { status: tab, limit }) as {
      tasks?: Task[]
      has_more?: boolean
    }
    const list = res?.tasks ?? []
    if (tab === 'active') setTasks(list)
    else setArchivedTasks(list)
    setHasMore((prev) => ({ ...prev, [tab]: !!res?.has_more }))
  }, [])

  const loadTasks = useCallback(async () => {
    setLoading(true)
    try {
      await Promise.all([
        fetchPage('active', limitsRef.current.active),
        fetchPage('archived', limitsRef.current.archived),
      ])
    } finally {
      setLoading(false)
    }
  }, [fetchPage])

  const loadMore = useCallback(async (tab: TaskTab) => {
    const next = limitsRef.current[tab] + PAGE_SIZE
    limitsRef.current = { ...limitsRef.current, [tab]: next }
    await fetchPage(tab, next)
  }, [fetchPage])

  useEffect(() => {
    loadTasks()
  }, [loadTasks])

  /** 返回新任务 id：欢迎页「发送即建任务」链路（0.5 §4.4）需立即以新 id
   *  发送首条消息，不等渲染回流的 activeTaskId；失败返回 undefined（toast 已反馈） */
  const handleCreateTask = useCallback(async (mode: 'coding' | 'general' = 'general'): Promise<string | undefined> => {
    if (creating) return
    setCreating(true)
    try {
      const res = await window.electronAPI.invoke('tasks:create', { title: defaultTaskTitle, mode }) as Task & { error?: string; code?: string }
      if (res.error) {
        // 兼容 200 + error dict 的旧形态；配额错误现走 catch 分支的 409 结构化错误
        showToast(t('task.createFailed') + '：' + apiErrorText(res.code, res.error, t), 'error')
        return
      }
      setTasks((prev) => [res, ...prev])
      setActiveTaskId(res.id)
      setActiveTab('active')
      return res.id
    } catch (err) {
      console.error('Create task failed:', err)
      const code = (err as { errorCode?: string })?.errorCode
      showToast(apiErrorText(code, t('task.createFailed'), t), 'error')
    } finally {
      setCreating(false)
    }
  }, [creating, defaultTaskTitle, showToast, t])

  const handleCreateTaskWithDir = useCallback(async (dirPath: string, mode: 'coding' | 'general' = 'general') => {
    if (creating) return
    // Extract folder name as default title
    const parts = dirPath.replace(/[\/\\]+$/, '').split(/[\/\\]/)
    const folderName = parts[parts.length - 1] || defaultTaskTitle
    setCreating(true)
    try {
      const res = await window.electronAPI.invoke('tasks:create', { title: folderName, workspacePath: dirPath, mode }) as Task & { error?: string; code?: string }
      if (res.error) {
        showToast(t('task.createFailed') + '：' + apiErrorText(res.code, res.error, t), 'error')
        return
      }
      setTasks((prev) => [res, ...prev])
      setActiveTaskId(res.id)
      setActiveTab('active')
    } catch (err) {
      console.error('Create task with dir failed:', err)
      const code = (err as { errorCode?: string })?.errorCode
      showToast(apiErrorText(code, t('task.createFailed'), t), 'error')
    } finally {
      setCreating(false)
    }
  }, [creating, defaultTaskTitle, showToast, t])

  const handleDeleteTask = useCallback(
    async (id: string) => {
      await window.electronAPI.invoke('tasks:delete', id)
      setTasks((prev) => prev.filter((t) => t.id !== id))
      setArchivedTasks((prev) => prev.filter((t) => t.id !== id))
      if (activeTaskId === id) {
        setActiveTaskId(null)
      }
    },
    [activeTaskId],
  )

  const handleRenameTask = useCallback(
    async (id: string, title: string) => {
      try {
        await window.electronAPI.invoke('tasks:rename', { taskId: id, title })
      } catch (err) {
        // 后端 404（任务已删）/ 400（空标题）走 handlePythonResponse 抛错通道；
        // 失败时不更新本地状态，避免 UI 与 DB 不一致
        console.error('Rename task failed:', err)
        showToast(t('task.renameFailed'), 'error')
        return
      }
      setTasks((prev) =>
        prev.map((t) => (t.id === id ? { ...t, title } : t)),
      )
      setArchivedTasks((prev) =>
        prev.map((t) => (t.id === id ? { ...t, title } : t)),
      )
    },
    [showToast, t],
  )

  const handleArchiveTask = useCallback(
    async (id: string) => {
      const res = await window.electronAPI.invoke('tasks:archive', id) as { error?: string }
      if (res?.error) return
      setTasks((prev) => {
        const task = prev.find((t) => t.id === id)
        if (task) {
          setArchivedTasks((ap) => [{ ...task, status: 'archived' }, ...ap])
          return prev.filter((t) => t.id !== id)
        }
        return prev
      })
      if (activeTaskId === id) {
        setActiveTaskId(null)
      }
    },
    [activeTaskId],
  )

  const handleUnarchiveTask = useCallback(
    async (id: string) => {
      const res = await window.electronAPI.invoke('tasks:unarchive', id) as { error?: string }
      if (res?.error) return
      setArchivedTasks((prev) => {
        const task = prev.find((t) => t.id === id)
        if (task) {
          setTasks((ap) => [{ ...task, status: 'active' }, ...ap])
          return prev.filter((t) => t.id !== id)
        }
        return prev
      })
    },
    [],
  )

  const handleExportTask = useCallback(
    async (id: string) => {
      return await window.electronAPI.invoke('tasks:export', id)
    },
    [],
  )

  const handleSetTaskMode = useCallback(
    async (id: string, mode: 'coding' | 'general') => {
      await window.electronAPI.invoke('tasks:setMode', { taskId: id, mode })
      setTasks((prev) =>
        prev.map((t) => (t.id === id ? { ...t, mode } : t)),
      )
      setArchivedTasks((prev) =>
        prev.map((t) => (t.id === id ? { ...t, mode } : t)),
      )
    },
    [],
  )

  const currentTasks = activeTab === 'active' ? tasks : archivedTasks

  return {
    tasks,
    archivedTasks,
    currentTasks,
    activeTab,
    setActiveTab,
    activeTaskId,
    setActiveTaskId,
    loadTasks,
    loadMore,
    hasMore,
    loading,
    creating,
    handleCreateTask,
    handleCreateTaskWithDir,
    handleDeleteTask,
    handleRenameTask,
    handleArchiveTask,
    handleUnarchiveTask,
    handleExportTask,
    handleSetTaskMode,
  }
}
