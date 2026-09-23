/** src/features/knowledge/SearchBar.tsx */
import { useState, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import s from './SearchBar.module.css'

interface SearchBarProps {
  onSearch: (query: string, topK: number) => void
  isSearching: boolean
  placeholder?: string
}

export function SearchBar({ onSearch, isSearching, placeholder }: SearchBarProps) {
  const { t } = useTranslation()
  const [query, setQuery] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  // Ctrl+K / Cmd+K shortcut to focus search
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault()
        inputRef.current?.focus()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (query.trim() && !isSearching) {
      onSearch(query.trim(), 5)
    }
  }

  const handleClear = () => {
    setQuery('')
    inputRef.current?.focus()
  }

  return (
    <form className={s.form} onSubmit={handleSubmit}>
      <div className={s.inputWrap}>
        <span className={s.searchIcon}>🔍</span>
        <input
          ref={inputRef}
          type="text"
          className={s.input}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={placeholder || t('knowledge.searchPlaceholder')}
          disabled={isSearching}
        />
        {query && (
          <button type="button" className={s.clearBtn} onClick={handleClear}>
            ×
          </button>
        )}
        <span className={s.shortcutHint} title={t('knowledge.searchShortcut')}>
          Ctrl+K
        </span>
      </div>
      <button
        type="submit"
        className={s.searchBtn}
        disabled={isSearching || !query.trim()}
      >
        {isSearching ? t('knowledge.searching') : t('knowledge.search')}
      </button>
    </form>
  )
}
