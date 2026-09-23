/** src/features/project/BaseLibFooter.tsx — Permanent base library footer in sidebar */
import { useTranslation } from 'react-i18next'
import { Plus, Factory, DollarSign, Library } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import s from './BaseLibFooter.module.css'

export interface BaseLibItem {
  id: string
  name: string
  Icon: LucideIcon
  preset: boolean
}

export const PRESET_BASE_LIBS: BaseLibItem[] = [
  { id: 'bl-recruitment', name: 'baseLib.recruitmentLib', Icon: Factory, preset: true },
  { id: 'bl-investment', name: 'baseLib.investmentLib', Icon: DollarSign, preset: true },
]

export const CUSTOM_BASE_LIBS: BaseLibItem[] = [
  { id: 'bl-custom-1', name: 'baseLib.myLib', Icon: Library, preset: false },
]

interface BaseLibFooterProps {
  selectedBaseLib: string | null
  onSelectBaseLib: (baseLibId: string) => void
}

export function BaseLibFooter({ selectedBaseLib, onSelectBaseLib }: BaseLibFooterProps) {
  const { t } = useTranslation()
  const allBaseLibs = [...PRESET_BASE_LIBS, ...CUSTOM_BASE_LIBS]

  return (
    <div className={s.footer}>
      <div className={s.header}>
        <span className={s.title}>{t('baseLib.title')}</span>
      </div>
      <div className={s.list}>
        {allBaseLibs.map((lib) => (
          <div
            key={lib.id}
            className={`${s.item} ${selectedBaseLib === lib.id ? s.itemSelected : ''}`}
            onClick={() => onSelectBaseLib(lib.id)}
          >
            <lib.Icon size={15} className={s.icon} />
            <span className={s.name}>{t(lib.name)}</span>
            {lib.preset && <span className={s.badge}>{t('baseLib.preset')}</span>}
          </div>
        ))}
      </div>
      <button className={s.addBtn}>
        <Plus size={13} />
        <span>{t('baseLib.addCustom')}</span>
      </button>
    </div>
  )
}
