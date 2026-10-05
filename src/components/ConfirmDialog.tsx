/** src/components/ConfirmDialog.tsx — Global promise-based confirmation dialog.
 *  Replaces native window.confirm() with a themed, i18n-aware modal.
 *  Mount <ConfirmProvider> once high in the tree (main.tsx); consume via useConfirm(). */
import { useState, useCallback, useEffect, useRef, createContext, useContext } from 'react'
import { useTranslation } from 'react-i18next'
import s from './ConfirmDialog.module.css'

export interface ConfirmOptions {
  /** Dialog title (optional; omit for a message-only dialog) */
  title?: string
  /** Body message (required) */
  message: string
  /** Confirm button label (defaults to common.confirm) */
  confirmText?: string
  /** Cancel button label (defaults to common.cancel) */
  cancelText?: string
  /** Style the confirm button as destructive/red. Defaults to true. */
  danger?: boolean
}

type ConfirmFn = (options: ConfirmOptions | string) => Promise<boolean>

interface PendingConfirm {
  options: ConfirmOptions
  resolve: (value: boolean) => void
}

const ConfirmContext = createContext<ConfirmFn | null>(null)

export function ConfirmProvider({ children }: { children: React.ReactNode }) {
  const [pending, setPending] = useState<PendingConfirm | null>(null)
  const pendingRef = useRef<PendingConfirm | null>(null)

  const confirm = useCallback<ConfirmFn>((options) => {
    const normalized: ConfirmOptions = typeof options === 'string' ? { message: options } : options
    return new Promise<boolean>((resolve) => {
      const entry = { options: normalized, resolve }
      pendingRef.current = entry
      setPending(entry)
    })
  }, [])

  // Resolve outside the state updater to avoid side effects during render
  const settle = useCallback((result: boolean) => {
    const entry = pendingRef.current
    pendingRef.current = null
    setPending(null)
    if (entry) entry.resolve(result)
  }, [])

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {pending && <ConfirmDialogView options={pending.options} onSettle={settle} />}
    </ConfirmContext.Provider>
  )
}

function ConfirmDialogView({ options, onSettle }: { options: ConfirmOptions; onSettle: (v: boolean) => void }) {
  const { t } = useTranslation()
  const confirmRef = useRef<HTMLButtonElement>(null)
  const danger = options.danger !== false

  useEffect(() => {
    confirmRef.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onSettle(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onSettle])

  return (
    <div className={s.backdrop} onClick={() => onSettle(false)}>
      <div className={s.dialog} role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        {options.title && <h3 className={s.title}>{options.title}</h3>}
        <p className={s.message}>{options.message}</p>
        <div className={s.actions}>
          <button className={s.cancelBtn} onClick={() => onSettle(false)}>
            {options.cancelText || t('common.cancel')}
          </button>
          <button
            ref={confirmRef}
            className={danger ? s.dangerBtn : s.primaryBtn}
            onClick={() => onSettle(true)}
          >
            {options.confirmText || t('common.confirm')}
          </button>
        </div>
      </div>
    </div>
  )
}

/** Hook returning a promise-based confirm function. Must be used within ConfirmProvider. */
export function useConfirm(): ConfirmFn {
  const ctx = useContext(ConfirmContext)
  if (!ctx) throw new Error('useConfirm must be used within ConfirmProvider')
  return ctx
}
