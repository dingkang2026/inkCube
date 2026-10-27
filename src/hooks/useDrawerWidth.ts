/** src/hooks/useDrawerWidth.ts — 右侧遮罩抽屉统一宽度契约（2026-10-26 收编）。
 *  此前 6 处抽屉各自实现宽度与拖宽（ScheduleDrawer 60vw 可拖、TaskDrawer/Chat/Wiki
 *  固定 420/720px 可拖、模板四抽屉 CSS 46% 不可拖），本 hook 统一为：
 *  默认 60vw 随视口（小视口夹至下限 min）+ 左缘手柄拖宽 + 双向 JS 夹紧
 *  （下限 min、上限 92vw 随视口——只靠 CSS 上限会产生「拖过头后回拖纹丝不动」
 *  的死区，视觉被 CSS 夹住而 state 未降回，故上限也走 JS）。
 *  附带防误关守卫：mousedown 起于 panel 内而 mouseup 落于遮罩时，浏览器把 click
 *  合成到两者公共祖先（= overlay，绕过 panel 的 stopPropagation）——手柄拖宽、
 *  正文选字后拖出松手均属此类非用户意图 click；松手后一个 tick 内吞掉
 *  （suppressClick ref 供遮罩 onClick 判断），防误关抽屉。
 *  消费方：ScheduleDrawer / TaskDrawer / ProjectChatPanel / WikiTab previewPanel /
 *  模板四抽屉（TemplateManagement / PptTemplatePanel / PptTemplateWizard /
 *  FormTemplatePanel）。
 */
import { useCallback, useEffect, useRef, useState } from 'react'

/** 默认宽 = 视口 60% */
export const DRAWER_DEFAULT_RATIO = 0.6
/** 拖宽上限 = 视口 92%（与 ScheduleDrawer 原口径一致；CSS max-width 仅作兜底） */
export const DRAWER_MAX_RATIO = 0.92

interface UseDrawerWidthOptions {
  /** 宽度下限（px，各抽屉按内容密度自定；变化时自动重夹紧当前宽） */
  min: number
  /** 初始宽（px）；缺省 = 视口 60% 夹紧（惰性求值） */
  initial?: () => number
}

export function useDrawerWidth({ min, initial }: UseDrawerWidthOptions) {
  // 视口夹紧（与拖宽 move 同一口径）：下限 min、上限 92vw 随视口
  const clamp = useCallback(
    (w: number) =>
      Math.min(Math.max(min, w), Math.floor(window.innerWidth * DRAWER_MAX_RATIO)),
    [min],
  )
  const [width, setWidth] = useState(() =>
    clamp(initial ? initial() : Math.floor(window.innerWidth * DRAWER_DEFAULT_RATIO)),
  )
  const [dragging, setDragging] = useState(false)
  const draggingRef = useRef(false)
  const startX = useRef(0)
  // 每次手柄 mousedown 刷新为当时 width 作拖宽基准
  const startW = useRef(width)
  // 防误关守卫：见文件头注释
  const suppressClick = useRef(false)
  const mouseDownInPanel = useRef(false)

  const onHandleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault()
      draggingRef.current = true
      setDragging(true)
      startX.current = e.clientX
      startW.current = width
      document.body.style.cursor = 'col-resize'
      document.body.style.userSelect = 'none'
    },
    [width],
  )

  // panel onMouseDown 绑定：标记拖宽/选字起于 panel 内（守卫判定依据）
  const onPanelMouseDown = useCallback(() => {
    mouseDownInPanel.current = true
  }, [])

  // min 变化（如 detail↔edit 模式切换）时重夹紧当前宽，防 state 低于新下限
  useEffect(() => {
    setWidth((w) => clamp(w))
  }, [clamp])

  useEffect(() => {
    const move = (e: MouseEvent) => {
      if (!draggingRef.current) return
      setWidth(clamp(startW.current + (startX.current - e.clientX)))
    }
    const up = () => {
      if (draggingRef.current) {
        draggingRef.current = false
        setDragging(false)
        document.body.style.cursor = ''
        document.body.style.userSelect = ''
      }
      if (!mouseDownInPanel.current) return
      mouseDownInPanel.current = false
      // click 同步派发于 mouseup 之后、本定时器之前，此窗口内吞掉合成 click
      suppressClick.current = true
      setTimeout(() => {
        suppressClick.current = false
      }, 0)
    }
    document.addEventListener('mousemove', move)
    document.addEventListener('mouseup', up)
    return () => {
      document.removeEventListener('mousemove', move)
      document.removeEventListener('mouseup', up)
      // 拖宽中途卸载（如抽屉被全局导航自动关闭）也复位 body 样式，防游标/选区卡死
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
  }, [clamp])

  return { width, dragging, onHandleMouseDown, onPanelMouseDown, suppressClick }
}
