import { useState, useEffect, useCallback } from 'react'
import type { Task } from '../../types'

export type TaskTab = 'active' | 'archived'

export function useTasks() {
  const [tasks, setTasks] = useState<Task[]>([])
  const [archivedTasks, setArchivedTasks] = useState<Task[]>([])
  const [activeTab, setActiveTab] = useState<TaskTab>('active')
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const loadTasks = async () => {
    setLoading(true)
    try {
      const [activeRes, archivedRes] = await Promise.all([
        window.electronAPI.invoke('tasks:listFiltered', { status: 'active' }),
        window.electronAPI.invoke('tasks:listFiltered', { status: 'archived' }),
      ])
      setTasks(activeRes as Task[])
      setArchivedTasks(archivedRes as Task[])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadTasks()
  }, [])

  const handleCreateTask = useCallback(async (mode: 'coding' | 'general' = 'coding') => {
    try {
      const res = await window.electronAPI.invoke('tasks:create', { title: '新任务', mode }) as Task & { error?: string }
      if (res.error) {
        console.error('Create task error:', res.error)
        return
      }
      setTasks((prev) => [res, ...prev])
      setActiveTaskId(res.id)
      setActiveTab('active')
    } catch (err) {
      console.error('Create task failed:', err)
    }
  }, [])

  const handleCreateTaskWithDir = useCallback(async (dirPath: string, mode: 'coding' | 'general' = 'coding') => {
    // Extract folder name as default title
    const parts = dirPath.replace(/[\/\\]+$/, '').split(/[\/\\]/)
    const folderName = parts[parts.length - 1] || '新任务'
    try {
      const res = await window.electronAPI.invoke('tasks:create', { title: folderName, workspacePath: dirPath, mode }) as Task & { error?: string }
      if (res.error) {
        console.error('Create task with dir error:', res.error)
        return
      }
      setTasks((prev) => [res, ...prev])
      setActiveTaskId(res.id)
      setActiveTab('active')
    } catch (err) {
      console.error('Create task with dir failed:', err)
    }
  }, [])

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
      await window.electronAPI.invoke('tasks:rename', { taskId: id, title })
      setTasks((prev) =>
        prev.map((t) => (t.id === id ? { ...t, title } : t)),
      )
      setArchivedTasks((prev) =>
        prev.map((t) => (t.id === id ? { ...t, title } : t)),
      )
    },
    [],
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
    loading,
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
