import { useCallback, useRef, useEffect } from 'react'
import styles from './ResizeHandle.module.css'

interface ResizeHandleProps {
  onResize: (delta: number) => void
  onDoubleClick?: () => void
}

export function ResizeHandle({ onResize, onDoubleClick }: ResizeHandleProps) {
  const onResizeRef = useRef(onResize)
  onResizeRef.current = onResize

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    let prevX = e.clientX
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'

    const onMove = (ev: MouseEvent) => {
      const delta = ev.clientX - prevX
      prevX = ev.clientX
      onResizeRef.current(delta)
    }
    const onUp = () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }, [])

  useEffect(() => {
    return () => {
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
  }, [])

  return (
    <div
      className={styles.resizeHandle}
      onMouseDown={handleMouseDown}
      onDoubleClick={onDoubleClick}
    />
  )
}
