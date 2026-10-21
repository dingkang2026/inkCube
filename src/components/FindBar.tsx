/** src/components/FindBar.tsx — 文件/文档内查找条（纯展示）。
 *  状态机在 useInFileFind（src/hooks/useInFileFind.ts），本组件只负责
 *  输入框 + n/m 计数 + 上一个/下一个/关闭的呈现与键位（Enter=下一个、
 *  Shift+Enter=上一个、Esc=关闭）。 */
import { useTranslation } from 'react-i18next'
import { ChevronDown, ChevronUp, X } from 'lucide-react'
import type { Ref, KeyboardEvent } from 'react'
import styles from './FindBar.module.css'

interface FindBarProps {
  inputRef: Ref<HTMLInputElement>
  query: string
  /** 命中总数 */
  count: number
  /** 当前命中序号（0-based，调用方保证已夹紧） */
  current: number
  onQueryChange: (v: string) => void
  onPrev: () => void
  onNext: () => void
  onClose: () => void
}

export function FindBar({ inputRef, query, count, current, onQueryChange, onPrev, onNext, onClose }: FindBarProps) {
  const { t } = useTranslation()
  const handleKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      if (e.shiftKey) onPrev()
      else onNext()
    } else if (e.key === 'Escape') {
      e.preventDefault()
      onClose()
    }
  }
  return (
    <div className={styles.findBar}>
      <input
        ref={inputRef}
        className={styles.findInput}
        value={query}
        placeholder={t('find.placeholder')}
        onChange={(e) => onQueryChange(e.target.value)}
        onKeyDown={handleKeyDown}
        autoFocus
      />
      <span className={styles.findCount}>
        {query ? `${count === 0 ? 0 : current + 1}/${count}` : ''}
      </span>
      <button className={styles.findBtn} disabled={count === 0} onClick={onPrev} title={t('find.prev')}>
        <ChevronUp size={12} />
      </button>
      <button className={styles.findBtn} disabled={count === 0} onClick={onNext} title={t('find.next')}>
        <ChevronDown size={12} />
      </button>
      <button className={styles.findBtn} onClick={onClose} title={t('find.close')}>
        <X size={12} />
      </button>
    </div>
  )
}
