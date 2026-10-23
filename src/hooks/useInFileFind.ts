/** src/hooks/useInFileFind.ts — 文件/文档内查找状态机（场景无关）。
 *  职责：查找开关、关键词、当前命中索引（越界渲染期夹紧 + 循环回绕）、命中变化时定位。
 *  场景差异全部注入：compute 由调用方把关键词映射为目标索引序列（源码场景=行号，
 *  渲染文档场景=内容块索引），locate 由调用方实现滚动定位。
 *  UI 呈现统一走 <FindBar>（src/components/FindBar.tsx）。 */
import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import type { RefObject } from 'react'

export interface UseInFileFindOptions {
  /** 关键词 → 目标索引序列（每个命中一项；空关键词返回空数组） */
  compute: (query: string) => number[]
  /** 定位到指定目标（滚动），由调用方实现 */
  locate: (target: number) => void
}

export function useInFileFind({ compute, locate }: UseInFileFindOptions) {
  const [findOpen, setFindOpen] = useState(false)
  const [findQuery, setFindQuery] = useState('')
  const [currentMatch, setCurrentMatch] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  const matches = useMemo(() => compute(findQuery), [compute, findQuery])

  // 渲染期派生安全索引：effect 夹紧晚于 paint，避免 matches 缩短时计数/高亮闪现越界帧
  const safeIdx = matches.length === 0 ? 0 : Math.min(currentMatch, matches.length - 1)

  // 命中集变化：夹紧写回 + 定位当前命中（相同值 setState 由 React bailout，无死循环）
  useEffect(() => {
    if (matches.length === 0) {
      setCurrentMatch(0)
      return
    }
    if (currentMatch >= matches.length) {
      setCurrentMatch(matches.length - 1)
      return
    }
    locate(matches[currentMatch])
  }, [matches, currentMatch, locate])

  const openFind = useCallback(() => {
    setFindOpen(true)
    requestAnimationFrame(() => inputRef.current?.focus())
  }, [])

  const closeFind = useCallback(() => {
    setFindOpen(false)
    setFindQuery('')
    setCurrentMatch(0)
  }, [])

  // 循环回绕：负索引与越界均取模落回有效区间
  const gotoMatch = useCallback((i: number) => {
    if (matches.length === 0) return
    setCurrentMatch(((i % matches.length) + matches.length) % matches.length)
  }, [matches.length])

  return { findOpen, findQuery, setFindQuery, matches, safeIdx, inputRef, openFind, closeFind, gotoMatch }
}

/** 容器内居中滚动目标元素（不用 scrollIntoView：避免连带滚动外层页面） */
export function scrollElementToCenter(container: HTMLElement, el: Element) {
  const cRect = container.getBoundingClientRect()
  const eRect = el.getBoundingClientRect()
  container.scrollTop += eRect.top - cRect.top - container.clientHeight / 2 + eRect.height / 2
}

/** ── Markdown 块级查找预设（2026-10-23 自 ReportPreview / ReadView / ArchiveView
 *  三处同构实现收编为公共基座，WikiPageReader 同批接入）──
 *  叶子块文本匹配 → 居中滚动 + find-flash 闪烁。containerRef 同时是扫描根与
 *  滚动容器（内容自身 overflow 滚动的容器，如 .report / .markdownBody）；
 *  content 变化（或 enabled 翻转）时重扫叶子块。 */
export const FIND_BLOCK_SELECTOR = 'p, h1, h2, h3, h4, h5, h6, li, pre, blockquote, td, th'

/** 命中块闪烁类（global.css 统一定义，不经 css module——hook 直接 classList 操作） */
const FIND_FLASH_CLASS = 'find-flash'

export function useMarkdownBlockFind(
  containerRef: RefObject<HTMLElement | null>,
  /** 正文内容键：变化时重扫叶子块（渲染文档场景 = 正文串） */
  content: string | null | undefined,
  enabled: boolean,
) {
  // 叶子块元素与文本缓存：元素供定位，文本供匹配
  const blocksRef = useRef<HTMLElement[]>([])
  const [blockTexts, setBlockTexts] = useState<string[]>([])
  useEffect(() => {
    const container = containerRef.current
    if (!enabled || !container) {
      blocksRef.current = []
      setBlockTexts([])
      return
    }
    const leaves = Array.from(container.querySelectorAll<HTMLElement>(FIND_BLOCK_SELECTOR))
      .filter((el) => !el.querySelector(FIND_BLOCK_SELECTOR))
    blocksRef.current = leaves
    setBlockTexts(leaves.map((el) => (el.textContent || '').toLowerCase()))
  }, [containerRef, enabled, content])

  // 闪烁 timer 托管：同块 1.5s 内重复定位先取消旧 timer（避免提前掉断新动画）；
  // 1500ms = CSS 动画 1.4s + 100ms 余量（global.css 调整时长须同步此值）；卸载清理
  const flashTimerRef = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(flashTimerRef.current), [])

  const locate = useCallback((idx: number) => {
    const container = containerRef.current
    const el = blocksRef.current[idx]
    if (!container || !el) return
    scrollElementToCenter(container, el)
    window.clearTimeout(flashTimerRef.current)
    el.classList.remove(FIND_FLASH_CLASS)
    void el.offsetWidth
    el.classList.add(FIND_FLASH_CLASS)
    flashTimerRef.current = window.setTimeout(() => el.classList.remove(FIND_FLASH_CLASS), 1500)
  }, [containerRef])

  const compute = useCallback((q: string) => {
    if (!q || blockTexts.length === 0) return []
    const needle = q.toLowerCase()
    const out: number[] = []
    blockTexts.forEach((txt, i) => { if (txt.includes(needle)) out.push(i) })
    return out
  }, [blockTexts])

  return useInFileFind({ compute, locate })
}
