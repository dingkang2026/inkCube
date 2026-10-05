/** src/features/project/BaseLibFooter.tsx — Sidebar base library list (API-driven) */
import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, Factory, DollarSign, Library, X, FileText } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useToast } from '../knowledge/Toast'
import { useActivity } from '../activity/ActivityContext'
import type { ProjectActivity } from '../activity/types'
import s from './BaseLibFooter.module.css'
import { useConfirm } from '../../components/ConfirmDialog'

interface BaseLibRecord {
  id: string
  name: string
  description: string
  lib_type: string | null
  file_count: number
}

/** Icon mapping for known preset base lib IDs */
const PRESET_ICONS: Record<string, LucideIcon> = {
  'bl-recruitment': Factory,
  'bl-investment': DollarSign,
}

function isPreset(libType: string | null): boolean {
  return !!libType && libType.startsWith('preset_')
}

function getIcon(id: string): LucideIcon {
  return PRESET_ICONS[id] || Library
}

interface BaseLibFooterProps {
  selectedBaseLib: string | null
  onSelectBaseLib: (baseLibId: string) => void
  onOpenTemplateManagement?: () => void
  /** Whether the template management page is currently shown (peer highlight) */
  templateManagementActive?: boolean
}

export function BaseLibFooter({ selectedBaseLib, onSelectBaseLib, onOpenTemplateManagement, templateManagementActive }: BaseLibFooterProps) {
  const { t } = useTranslation()
  const confirmDialog = useConfirm()
  const showToast = useToast()
  const { byProject } = useActivity()
  const [baseLibs, setBaseLibs] = useState<BaseLibRecord[]>([])
  const [loading, setLoading] = useState(true)

  /** 活跃点 tooltip（0.4 §5.7）：与项目行对齐，复用 ProjectList 同一组 i18n */
  const activityTooltip = (pa: ProjectActivity): string => {
    const parts: string[] = []
    if (pa.files > 0) parts.push(t('activity.tipFiles', { count: pa.files }))
    if (pa.compiles > 0) parts.push(t('activity.tipCompiles'))
    if (pa.reports > 0) parts.push(t('activity.tipReports', { count: pa.reports }))
    if (pa.waitingReviews > 0) parts.push(t('activity.tipWaiting', { count: pa.waitingReviews }))
    return parts.join(' / ')
  }

  const loadBaseLibs = async () => {
    try {
      const result: any = await window.electronAPI.invoke('baseLib:list')
      setBaseLibs(result.base_libs || [])
    } catch (err) {
      console.error('Failed to load base libs:', err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadBaseLibs() }, [])

  const handleCreate = async () => {
    const name = window.prompt(t('baseLib.createPrompt'))
    if (!name?.trim()) return
    try {
      await window.electronAPI.invoke('baseLib:create', { name: name.trim(), description: '' })
      await loadBaseLibs()
      showToast(t('baseLib.createSuccess'), 'success')
    } catch (err) {
      console.error('Failed to create base lib:', err)
      showToast(t('baseLib.createFailed'), 'error')
    }
  }

  const handleDelete = async (e: React.MouseEvent, libId: string) => {
    e.stopPropagation()
    if (!(await confirmDialog({ title: t('baseLib.delete'), message: t('baseLib.confirmDelete') }))) return
    try {
      await window.electronAPI.invoke('baseLib:delete', libId)
      await loadBaseLibs()
      showToast(t('baseLib.deleteSuccess'), 'success')
    } catch (err) {
      console.error('Failed to delete base lib:', err)
      showToast(t('baseLib.deleteFailed'), 'error')
    }
  }

  return (
    <div className={s.footer}>
      <div className={s.header}>
        <span className={s.title}>{t('baseLib.title')}</span>
      </div>
      <div className={s.list}>
        {!loading && baseLibs.map((lib) => {
          const Icon = getIcon(lib.id)
          const preset = isPreset(lib.lib_type)
          // 活跃点与项目行对齐（0.4 §5.7）：全局心跳 byProject 派生，仅基础库容器行先小动作
          const pa = byProject.get(lib.id)
          const hasOngoing = !!pa && pa.container === 'baselib' && pa.files + pa.compiles + pa.reports > 0
          const needsReview = !!pa && pa.container === 'baselib' && pa.waitingReviews > 0
          return (
            <div
              key={lib.id}
              className={`${s.item} ${selectedBaseLib === lib.id ? s.itemSelected : ''}`}
              onClick={() => onSelectBaseLib(lib.id)}
            >
              <Icon size={15} className={s.icon} />
              <span className={s.name}>{lib.name}</span>
              {(hasOngoing || needsReview) && pa && (
                <span
                  className={hasOngoing ? s.activityDot : s.reviewDot}
                  title={activityTooltip(pa)}
                />
              )}
              {preset && <span className={s.badge}>{t('baseLib.preset')}</span>}
              {!preset && (
                <button
                  className={s.deleteBtn}
                  onClick={(e) => handleDelete(e, lib.id)}
                  title={t('baseLib.delete')}
                >
                  <X size={12} />
                </button>
              )}
            </div>
          )
        })}
      </div>
      <button className={s.addBtn} onClick={handleCreate}>
        <Plus size={13} />
        <span>{t('baseLib.addCustom')}</span>
      </button>
      {onOpenTemplateManagement && (
        <>
          <div className={s.header} style={{ marginTop: '8px' }}>
            <span className={s.title}>{t('baseLib.templateManagement')}</span>
          </div>
          <div
            className={`${s.item} ${templateManagementActive ? s.itemSelected : ''}`}
            onClick={onOpenTemplateManagement}
            style={{ cursor: 'pointer', paddingLeft: '18px' }}
          >
            <FileText size={15} className={s.icon} />
            <span className={s.name}>{t('project.report.templateManagement')}</span>
          </div>
        </>
      )}
    </div>
  )
}
