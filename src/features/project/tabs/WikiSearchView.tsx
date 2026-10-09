/** src/features/project/tabs/WikiSearchView.tsx — Wiki search view with hybrid retrieval */
import { useState, useEffect, useRef, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { Search, Loader2, FileText, Link2 } from 'lucide-react'
import s from './WikiTab.module.css'

interface WikiSearchViewProps {
  projectId: string
  onNavigateToPage: (pageKey: string) => void
}

interface SearchResult {
  id: string
  page_key: string
  title: string
  content: string | null
  page_type: string
  _score: number
  _source: string
  graph_related_to?: string[]
}

export function WikiSearchView({ projectId, onNavigateToPage }: WikiSearchViewProps) {
  const { t } = useTranslation()
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const [searching, setSearching] = useState(false)
  const [searched, setSearched] = useState(false)
  const [searchFailed, setSearchFailed] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Focus input on mount
  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  // Debounced search
  const doSearch = useCallback(async (q: string) => {
    if (!q.trim()) {
      setResults([])
      setSearched(false)
      setSearchFailed(false)
      return
    }
    setSearching(true)
    try {
      const result: any = await window.electronAPI.invoke('wiki:search', {
        projectId,
        query: q.trim(),
        topK: 20,
      })
      setResults(result.results || [])
      setSearchFailed(false)
      setSearched(true)
    } catch (err) {
      console.error('Wiki search failed:', err)
      setResults([])
      // 故障与「无结果」是两种语义：错误态单独提示（后端 raise_on_error 透出）
      setSearchFailed(true)
      setSearched(true)
    } finally {
      setSearching(false)
    }
  }, [projectId])

  // Handle input change with debounce
  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value
    setQuery(val)
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => doSearch(val), 300)
  }

  // Handle Enter key
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      if (debounceRef.current) clearTimeout(debounceRef.current)
      doSearch(query)
    }
  }

  // Cleanup debounce on unmount
  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [])

  // Truncate content for preview
  const truncateContent = (content: string | null, maxLen = 200) => {
    if (!content) return ''
    if (content.length <= maxLen) return content
    return content.slice(0, maxLen) + '…'
  }

  return (
    <div className={s.searchView}>
      {/* Search header */}
      <div className={s.searchHeader}>
        <div className={s.searchInputWrap}>
          <Search size={16} className={s.searchIcon} />
          <input
            ref={inputRef}
            type="text"
            className={s.searchInput}
            placeholder={t('project.wiki.searchPlaceholder', { defaultValue: '搜索 Wiki 页面...' })}
            value={query}
            onChange={handleInputChange}
            onKeyDown={handleKeyDown}
          />
          {searching && <Loader2 size={14} className={s.spin} />}
        </div>
      </div>

      {/* Results */}
      <div className={s.searchResults}>
        {!searched && !searching && (
          <div className={s.searchHint}>
            <Search size={20} className={s.searchHintIcon} />
            <p>{t('project.wiki.searchHint', { defaultValue: '输入关键词搜索 Wiki 页面' })}</p>
          </div>
        )}

        {searched && results.length === 0 && !searching && (
          <div className={s.searchHint}>
            <p>
              {searchFailed
                ? t('project.wiki.searchFailed', { defaultValue: '搜索失败，请稍后重试' })
                : t('project.wiki.noResults', { defaultValue: '未找到相关页面' })}
            </p>
          </div>
        )}

        {results.map((r) => (
          <div
            key={r.page_key}
            className={s.searchResultItem}
            onClick={() => onNavigateToPage(r.page_key)}
          >
            <div className={s.searchResultHeader}>
              <FileText size={14} className={s.searchResultIcon} />
              <span className={s.searchResultTitle}>{r.title || r.page_key}</span>
              <span className={s.searchResultScore}>
                {(r._score || 0).toFixed(1)}
              </span>
            </div>
            <p className={s.searchResultContent}>
              {truncateContent(r.content)}
            </p>
            <div className={s.searchResultMeta}>
              <span className={s.searchResultKey}>{r.page_key}</span>
              {r._source === 'graph' && r.graph_related_to && r.graph_related_to.length > 0 && (
                <span className={s.searchResultGraph}>
                  <Link2 size={11} />
                  {t('project.wiki.graphRelated', { defaultValue: '图谱关联' })}: {r.graph_related_to.join(', ')}
                </span>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
