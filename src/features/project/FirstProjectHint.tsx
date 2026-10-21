/** src/features/project/FirstProjectHint.tsx — 0.5 E1 T6:首个项目一次性引导提示条(0.5 §4.3 ③)。
 *  localStorage 键 inkcube.onboarding.firstProject:点击任一动作或关闭后写入,永不再现。 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { X } from 'lucide-react'
import s from './FirstProjectHint.module.css'

const STORAGE_KEY = 'inkcube.onboarding.firstProject'

interface FirstProjectHintProps {
  /** 上传第一批资料 → 项目素材 Tab */
  onUploadMaterials: () => void
  /** 发起第一个任务 → 任务页(空态即模板引导) */
  onStartTask: () => void
  /** 向项目助手提问 → 打开右侧助手面板 */
  onAskAssistant: () => void
}

export function FirstProjectHint({ onUploadMaterials, onStartTask, onAskAssistant }: FirstProjectHintProps) {
  const { t } = useTranslation()
  const [visible, setVisible] = useState(() => !window.localStorage.getItem(STORAGE_KEY))
  if (!visible) return null

  const dismiss = () => {
    window.localStorage.setItem(STORAGE_KEY, '1')
    setVisible(false)
  }

  return (
    <div className={s.hint}>
      <span className={s.hintTitle}>{t('guide.step3')}</span>
      <div className={s.hintActions}>
        <button className={s.hintAction} onClick={() => { onUploadMaterials(); dismiss() }}>
          {t('guide.afterWiki')}
        </button>
        <button className={s.hintAction} onClick={() => { onStartTask(); dismiss() }}>
          {t('guide.afterMaterials')}
        </button>
        <button className={s.hintAction} onClick={() => { onAskAssistant(); dismiss() }}>
          {t('guide.afterAsk')}
        </button>
      </div>
      <button className={s.hintClose} aria-label={t('window.close')} onClick={dismiss}><X size={14} /></button>
    </div>
  )
}
