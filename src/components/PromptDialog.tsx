/** src/components/PromptDialog.tsx — Global promise-based input dialog.
 *  Replaces native window.prompt() (unsupported in Electron — throws
 *  "prompt() is and will not be supported") with a themed, i18n-aware modal.
 *  Mount <PromptProvider> once high in the tree (main.tsx); consume via usePrompt(). */
import { useState, useCallback, useEffect, useRef, createContext, useContext } from 'react'
import { useTranslation } from 'react-i18next'
import s from './PromptDialog.module.css'

export interface PromptOptions {
  /** Dialog title (optional) */
  title?: string
  /** Hint message above the input (optional) */
  message?: string
  /** Initial input value */
  defaultValue?: string
  /** Input placeholder */
  placeholder?: string
  /** Confirm button label (defaults to common.confirm) */
  confirmText?: string
  /** Cancel button label (defaults to common.cancel) */
  cancelText?: string
}

type PromptFn = (options: PromptOptions | string) => Promise<string | null>

interface PendingPrompt {
  options: PromptOptions
  resolve: (value: string | null) => void
}

const PromptContext = createContext<PromptFn | null>(null)

export function PromptProvider({ children }: { children: React.ReactNode }) {
  const [pending, setPending] = useState<PendingPrompt | null>(null)
  const pendingRef = useRef<PendingPrompt | null>(null)

  const prompt = useCallback<PromptFn>((options) => {
    const normalized: PromptOptions = typeof options === 'string' ? { message: options } : options
    return new Promise<string | null>((resolve) => {
      const entry = { options: normalized, resolve }
      pendingRef.current = entry
      setPending(entry)
    })
  }, [])

  // Resolve outside the state updater to avoid side effects during render
  const settle = useCallback((result: string | null) => {
    const entry = pendingRef.current
    pendingRef.current = null
    setPending(null)
    if (entry) entry.resolve(result)
  }, [])

  return (
    <PromptContext.Provider value={prompt}>
      {children}
      {pending && <PromptDialogView options={pending.options} onSettle={settle} />}
    </PromptContext.Provider>
  )
}

function PromptDialogView({ options, onSettle }: { options: PromptOptions; onSettle: (v: string | null) => void }) {
  const { t } = useTranslation()
  const inputRef = useRef<HTMLInputElement>(null)
  const [value, setValue] = useState(options.defaultValue ?? '')

  useEffect(() => {
    inputRef.current?.focus()
    inputRef.current?.select()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onSettle(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onSettle])

  return (
    <div className={s.backdrop} onClick={() => onSettle(null)}>
      <div className={s.dialog} role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        {options.title && <h3 className={s.title}>{options.title}</h3>}
        {options.message && <p className={s.message}>{options.message}</p>}
        <input
          ref={inputRef}
          className={s.input}
          value={value}
          placeholder={options.placeholder}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') onSettle(value)
          }}
        />
        <div className={s.actions}>
          <button className={s.cancelBtn} onClick={() => onSettle(null)}>
            {options.cancelText || t('common.cancel')}
          </button>
          <button className={s.primaryBtn} onClick={() => onSettle(value)}>
            {options.confirmText || t('common.confirm')}
          </button>
        </div>
      </div>
    </div>
  )
}

/** Hook returning a promise-based prompt function (resolves null on cancel).
 *  Must be used within PromptProvider. */
export function usePrompt(): PromptFn {
  const ctx = useContext(PromptContext)
  if (!ctx) throw new Error('usePrompt must be used within PromptProvider')
  return ctx
}
