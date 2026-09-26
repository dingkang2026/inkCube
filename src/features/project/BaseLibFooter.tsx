/** src/features/project/BaseLibFooter.tsx — Sidebar base library list (API-driven) */
import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, Factory, DollarSign, Library, X } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useToast } from '../knowledge/Toast'
import s from './BaseLibFooter.module.css'

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
}

export function BaseLibFooter({ selectedBaseLib, onSelectBaseLib }: BaseLibFooterProps) {
  const { t } = useTranslation()
  const showToast = useToast()
  const [baseLibs, setBaseLibs] = useState<BaseLibRecord[]>([])
  const [loading, setLoading] = useState(true)

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
    if (!window.confirm(t('baseLib.confirmDelete'))) return
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
          return (
            <div
              key={lib.id}
              className={`${s.item} ${selectedBaseLib === lib.id ? s.itemSelected : ''}`}
              onClick={() => onSelectBaseLib(lib.id)}
            >
              <Icon size={15} className={s.icon} />
              <span className={s.name}>{lib.name}</span>
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
    </div>
  )
}
