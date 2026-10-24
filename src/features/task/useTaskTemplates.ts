/** src/features/task/useTaskTemplates.ts — 0.5 E2 T4:任务模板数据 hook（0.5 §5/§6）。
 *  消费 taskTemplates:list/save/delete/togglePin 四通道；内置模板以 i18n 常量为事实源
 *  （不落库），与后端自定义行经 mergeTemplates 合并排序；builtins 可换数据源
 *  （V2-E1a T12 模板分家：ScheduleFormModal 传 INTEL_TEMPLATES，默认 TASK_TEMPLATES）。
 *  动作只做 invoke（错误沿 D1 契约抛出，errorCode 由调用方 toast），写操作后由
 *  调用方 refresh 重拉——面板持单实例 hook，弹层动作刷新即面板刷新。 */
import { useState, useEffect, useCallback, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { TASK_TEMPLATES, BACKEND_CATEGORY, mergeTemplates } from './promptTemplate'
import type { CustomRow, MergedTemplate, TaskTemplateDef, TemplateDraft } from './promptTemplate'

export type TemplateScope = 'global' | 'project'

export function useTaskTemplates(scope: TemplateScope, projectId?: string, builtins: TaskTemplateDef[] = TASK_TEMPLATES) {
  const { t } = useTranslation()
  const [custom, setCustom] = useState<CustomRow[]>([])
  const [builtinPinned, setBuiltinPinned] = useState<string[]>([])
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const res = await window.electronAPI.invoke('taskTemplates:list', { scope, projectId }) as {
        templates?: CustomRow[]
        builtinPinned?: string[]
      }
      setCustom(res?.templates ?? [])
      setBuiltinPinned(res?.builtinPinned ?? [])
    } catch (err) {
      console.error('Load task templates failed:', err)
    } finally {
      setLoading(false)
    }
  }, [scope, projectId])

  useEffect(() => {
    refresh()
  }, [refresh])

  const templates = useMemo(
    () => mergeTemplates(builtins, t, custom, builtinPinned),
    [builtins, t, custom, builtinPinned],
  )

  const save = useCallback(async (draft: TemplateDraft) => {
    await window.electronAPI.invoke('taskTemplates:save', {
      id: draft.id ?? null,
      scope,
      projectId: scope === 'project' ? projectId : null,
      category: BACKEND_CATEGORY[draft.category],
      title: draft.title.trim(),
      prompt: draft.prompt.trim(),
      pinned: draft.pinned ?? false,
    })
  }, [scope, projectId])

  const remove = useCallback(async (id: string) => {
    await window.electronAPI.invoke('taskTemplates:delete', id)
  }, [])

  const togglePin = useCallback(async (id: string, pinned: boolean) => {
    await window.electronAPI.invoke('taskTemplates:togglePin', { id, pinned })
  }, [])

  return { templates, loading, refresh, save, remove, togglePin } as {
    templates: MergedTemplate[]
    loading: boolean
    refresh: () => Promise<void>
    save: (draft: TemplateDraft) => Promise<void>
    remove: (id: string) => Promise<void>
    togglePin: (id: string, pinned: boolean) => Promise<void>
  }
}
