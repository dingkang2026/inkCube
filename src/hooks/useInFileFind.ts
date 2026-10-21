/** src/hooks/useInFileFind.ts — 文件/文档内查找状态机（场景无关）。
 *  职责：查找开关、关键词、当前命中索引（越界渲染期夹紧 + 循环回绕）、命中变化时定位。
 *  场景差异全部注入：compute 由调用方把关键词映射为目标索引序列（源码场景=行号，
 *  渲染文档场景=内容块索引），locate 由调用方实现滚动定位。
 *  UI 呈现统一走 <FindBar>（src/components/FindBar.tsx）。 */
import { useState, useEffect, useCallback, useMemo, useRef } from 'react'

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
