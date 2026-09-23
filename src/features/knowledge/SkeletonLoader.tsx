/** src/features/knowledge/SkeletonLoader.tsx */
import s from './SkeletonLoader.module.css'

interface SkeletonLineProps {
  width?: string | number
  height?: number
  className?: string
}

/** Single shimmer line placeholder */
export function SkeletonLine({ width = '100%', height = 14, className }: SkeletonLineProps) {
  const style: React.CSSProperties = {
    width: typeof width === 'number' ? `${width}px` : width,
    height: `${height}px`,
  }
  return <div className={`${s.line} ${className || ''}`} style={style} />
}

/** Simulates a file list row: icon + 2 lines of text */
export function SkeletonFileRow() {
  return (
    <div className={s.fileRow}>
      <div className={s.fileIcon} />
      <div className={s.fileBody}>
        <SkeletonLine width="60%" height={13} />
        <SkeletonLine width="35%" height={11} />
      </div>
      <SkeletonLine width={48} height={18} />
    </div>
  )
}

/** Simulates a search result card */
export function SkeletonSearchCard() {
  return (
    <div className={s.searchCard}>
      <div className={s.cardHeader}>
        <SkeletonLine width={24} height={14} />
        <SkeletonLine width="40%" height={14} />
        <SkeletonLine width={48} height={14} />
      </div>
      <SkeletonLine width="80%" height={11} />
      <div className={s.cardBody}>
        <SkeletonLine height={12} />
        <SkeletonLine width="92%" height={12} />
        <SkeletonLine width="70%" height={12} />
      </div>
    </div>
  )
}

/** Simulates an image thumbnail in the gallery grid */
export function SkeletonImageThumb() {
  return (
    <div className={s.imageThumb}>
      <div className={s.imageSquare} />
      <SkeletonLine width="70%" height={11} />
    </div>
  )
}

/* ── Composite loaders ── */

export function SkeletonFileList({ rows = 4 }: { rows?: number }) {
  return (
    <div className={s.container}>
      {Array.from({ length: rows }, (_, i) => (
        <SkeletonFileRow key={i} />
      ))}
    </div>
  )
}

export function SkeletonSearchResults({ count = 3 }: { count?: number }) {
  return (
    <div className={s.container}>
      {Array.from({ length: count }, (_, i) => (
        <SkeletonSearchCard key={i} />
      ))}
    </div>
  )
}

export function SkeletonImageGrid({ count = 8 }: { count?: number }) {
  return (
    <div className={s.imageGrid}>
      {Array.from({ length: count }, (_, i) => (
        <SkeletonImageThumb key={i} />
      ))}
    </div>
  )
}
