/** src/features/knowledge/Toast.tsx */
import { useState, useEffect, useCallback, createContext, useContext } from 'react'
import s from './Toast.module.css'

type ToastType = 'success' | 'error' | 'info' | 'warning'

interface ToastAction {
  label: string
  onClick: () => void
}

interface ToastItem {
  id: number
  message: string
  type: ToastType
  suggestion?: string
  action?: ToastAction
  detail?: string
}

interface ToastContextValue {
  showToast: (message: string, type?: ToastType, options?: Omit<ToastItem, 'id' | 'message' | 'type'>) => void
}

const ToastContext = createContext<ToastContextValue | null>(null)

let nextId = 0

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([])

  const showToast = useCallback(
    (message: string, type: ToastType = 'info', options?: Omit<ToastItem, 'id' | 'message' | 'type'>) => {
      const id = nextId++
      setToasts(prev => [...prev, { id, message, type, ...options }])
    },
    [],
  )

  const removeToast = useCallback((id: number) => {
    setToasts(prev => prev.filter(t => t.id !== id))
  }, [])

  return (
    <ToastContext.Provider value={{ showToast }}>
      {children}
      <div className={s.container}>
        {toasts.map(toast => (
          <ToastItemView key={toast.id} toast={toast} onRemove={removeToast} />
        ))}
      </div>
    </ToastContext.Provider>
  )
}

function ToastItemView({ toast, onRemove }: { toast: ToastItem; onRemove: (id: number) => void }) {
  const [exiting, setExiting] = useState(false)
  const [showDetail, setShowDetail] = useState(false)

  // Longer duration for errors with actions
  const duration = toast.action || toast.detail ? 6000 : 2500
  const removeDelay = duration + 500

  useEffect(() => {
    const timer = setTimeout(() => setExiting(true), duration)
    const removeTimer = setTimeout(() => onRemove(toast.id), removeDelay)
    return () => {
      clearTimeout(timer)
      clearTimeout(removeTimer)
    }
  }, [toast.id, onRemove, duration, removeDelay])

  const icons: Record<ToastType, string> = {
    success: '✓',
    error: '✕',
    info: 'ℹ',
    warning: '!',
  }

  return (
    <div className={`${s.toast} ${s[toast.type]} ${exiting ? s.exiting : ''}`}>
      <span className={s.icon}>{icons[toast.type]}</span>
      <div className={s.body}>
        <span className={s.message}>{toast.message}</span>
        {toast.suggestion && (
          <span className={s.suggestion}>{toast.suggestion}</span>
        )}
        {(toast.action || toast.detail) && (
          <div className={s.actions}>
            {toast.action && (
              <button
                className={s.actionBtn}
                onClick={() => {
                  toast.action!.onClick()
                  onRemove(toast.id)
                }}
              >
                {toast.action.label}
              </button>
            )}
            {toast.detail && (
              <button
                className={s.detailToggle}
                onClick={() => setShowDetail(!showDetail)}
              >
                {showDetail ? '收起详情' : '查看详情'}
              </button>
            )}
          </div>
        )}
        {showDetail && toast.detail && (
          <pre className={s.detail}>{toast.detail}</pre>
        )}
      </div>
    </div>
  )
}

/** Hook to show toast notifications */
export function useToast() {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used within ToastProvider')
  return ctx.showToast
}
