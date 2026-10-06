import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import s from './AboutDialog.module.css'

interface AboutDialogProps {
  open: boolean
  onClose: () => void
}

export function AboutDialog({ open, onClose }: AboutDialogProps) {
  const { t } = useTranslation()
  const [version, setVersion] = useState('')
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    if (open) {
      window.electronAPI.invoke('app:version').then((v) => setVersion(v as string))
      requestAnimationFrame(() => setVisible(true))
    } else {
      setVisible(false)
    }
  }, [open])

  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if (e.key === 'Escape') onClose()
  }, [onClose])

  useEffect(() => {
    if (!open) return
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [open, handleKeyDown])

  if (!open) return null

  return (
    <div className={`${s.overlay} ${visible ? s.visible : ''}`} onClick={onClose}>
      <div className={`${s.card} ${visible ? s.visible : ''}`} onClick={(e) => e.stopPropagation()}>
        <div className={s.inkTop} />
        <div className={s.inkBottom} />

        <div className={s.content}>
          <div className={s.logoArea}>
            <span className={s.inkChar}>墨</span>
            <span className={s.appName}>{t('app.title')}</span>
          </div>

          <div className={s.version}>InkCube v{version}</div>
          <div className={s.tagline}>{t('about.tagline')}</div>
        </div>
      </div>
    </div>
  )
}
