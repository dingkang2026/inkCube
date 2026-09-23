import { useState, useCallback } from 'react'

export function useResizeHandle(defaultWidth: number, min?: number, max?: number) {
  const [width, setWidth] = useState(defaultWidth)

  const handleResize = useCallback(
    (delta: number) => {
      setWidth((prev) => {
        let next = prev + delta
        if (min !== undefined) next = Math.max(min, next)
        if (max !== undefined) next = Math.min(max, next)
        return next
      })
    },
    [min, max],
  )

  const resetWidth = useCallback(() => {
    setWidth(defaultWidth)
  }, [defaultWidth])

  return { width, handleResize, resetWidth }
}
