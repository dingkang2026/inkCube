/** src/hooks/useConfirmDelete.ts — Two-step inline delete confirmation. */
import { useState, useCallback, useRef } from 'react'

export function useConfirmDelete(timeoutMs = 3000) {
  const [confirming, setConfirming] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const requestDelete = useCallback(() => {
    if (confirming) {
      // Second click — confirmed
      if (timerRef.current) clearTimeout(timerRef.current)
      setConfirming(false)
      return true
    }
    // First click — show confirmation
    setConfirming(true)
    timerRef.current = setTimeout(() => setConfirming(false), timeoutMs)
    return false
  }, [confirming, timeoutMs])

  const cancel = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current)
    setConfirming(false)
  }, [])

  return { confirming, requestDelete, cancel }
}
