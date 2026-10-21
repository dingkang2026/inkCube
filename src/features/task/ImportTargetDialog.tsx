/** src/features/task/ImportTargetDialog.tsx — 0.5 E2 T5:任务产出「导入到项目/基础库」目标弹层。
 *  双 tab（项目 = knowledge:listProjects（后端已滤 kind='project'）/ 基础库 = baseLib:list
 *  懒加载）+ 单选列表 + 确认导入；导入走 importTaskFile 纯逻辑；duplicate 仍算完成
 *  （toast「已存在同名内容文件」）。上传响应触发 file 活动，目标侧素材 Tab 自然出现。 */
import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { X } from 'lucide-react'
import { useToast } from '../knowledge/Toast'
import { apiErrorText } from '../../i18n/errorCodes'
import { importTaskFile } from './importTaskFile'
import s from './ImportTargetDialog.module.css'

interface TargetItem {
  id: string
  name: string
}

interface ImportTargetDialogProps {
  taskId: string
  /** 任务工作区内相对路径 */
  path: string
  filename: string
  onDone: (fileId: string) => void
  onClose: () => void
}

type Tab = 'project' | 'baselib'

export function ImportTargetDialog({ taskId, path, filename, onDone, onClose }: ImportTargetDialogProps) {
  const { t } = useTranslation()
  const showToast = useToast()
  const [tab, setTab] = useState<Tab>('project')
  const [projects, setProjects] = useState<TargetItem[]>([])
  const [libs, setLibs] = useState<TargetItem[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [importing, setImporting] = useState(false)

  const errCode = (err: unknown) => (err as { errorCode?: string })?.errorCode

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    // 选择器形态：limit=0 全量（目标列表不得截断）
    window.electronAPI.invoke('knowledge:listProjects', { limit: 0 })
      .then((res) => {
        if (!cancelled) setProjects(((res as { projects?: TargetItem[] })?.projects ?? []))
      })
      .catch((err) => console.error('Import dialog: list projects failed:', err))
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  const loadLibs = useCallback(() => {
    setLoading(true)
    window.electronAPI.invoke('baseLib:list', { limit: 0 })
      .then((res) => setLibs(((res as { base_libs?: TargetItem[] })?.base_libs ?? [])))
      .catch((err) => console.error('Import dialog: list base libs failed:', err))
      .finally(() => setLoading(false))
  }, [])

  const switchTab = (next: Tab) => {
    if (next === tab) return
    setTab(next)
    setSelectedId(null)
    if (next === 'baselib' && libs.length === 0) loadLibs()
  }

  const items = tab === 'project' ? projects : libs

  const handleConfirm = async () => {
    if (!selectedId || importing) return
    const targetName = items.find((i) => i.id === selectedId)?.name ?? ''
    setImporting(true)
    try {
      const out = await importTaskFile({
        taskId, path, filename,
        targetType: tab, targetId: selectedId,
      })
      if (out.duplicate) {
        showToast(t('task.import.duplicate'), 'warning')
      } else {
        showToast(t('task.import.success', { name: targetName }), 'success')
      }
      onDone(out.fileId)
    } catch (err) {
      console.error('Import task file failed:', err)
      showToast(apiErrorText(errCode(err), t('task.import.failed'), t), 'error')
    } finally {
      setImporting(false)
    }
  }

  return (
    <div className={s.backdrop} onClick={onClose}>
      <div className={s.dialog} role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <div className={s.header}>
          <h3 className={s.title}>{t('task.import.title')}</h3>
          <button className={s.iconBtn} aria-label={t('common.close')} onClick={onClose}>
            <X size={16} />
          </button>
        </div>
        <div className={s.fileRow}>
          <span className={s.fileLabel}>{filename}</span>
        </div>
        <div className={s.tabs} role="tablist">
          <button
            role="tab"
            aria-selected={tab === 'project'}
            className={`${s.tab} ${tab === 'project' ? s.tabActive : ''}`}
            onClick={() => switchTab('project')}
          >
            {t('task.import.tabProject')}
          </button>
          <button
            role="tab"
            aria-selected={tab === 'baselib'}
            className={`${s.tab} ${tab === 'baselib' ? s.tabActive : ''}`}
            onClick={() => switchTab('baselib')}
          >
            {t('task.import.tabBaseLib')}
          </button>
        </div>
        <div className={s.list}>
          {loading ? (
            <div className={s.empty}>{t('task.loadingFile')}</div>
          ) : items.length === 0 ? (
            <div className={s.empty}>{t('task.import.emptyTargets')}</div>
          ) : (
            items.map((item) => (
              <button
                key={item.id}
                className={`${s.item} ${selectedId === item.id ? s.itemSelected : ''}`}
                onClick={() => setSelectedId(item.id)}
              >
                {item.name}
              </button>
            ))
          )}
        </div>
        <div className={s.actions}>
          <button className={s.cancelBtn} onClick={onClose}>{t('common.cancel')}</button>
          <button className={s.confirmBtn} disabled={!selectedId || importing} onClick={handleConfirm}>
            {importing ? t('task.import.importing') : t('task.import.confirm')}
          </button>
        </div>
      </div>
    </div>
  )
}
