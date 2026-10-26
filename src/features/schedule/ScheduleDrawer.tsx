/** src/features/schedule/ScheduleDrawer.tsx — 期次三态右侧抽屉（0.6 §3.2 第二步
 *  三态统一，2026-10-25）。read（阅读页）/replay（执行过程）/archive（档案）从整页
 *  peer 替换改为 App 级遮罩抽屉：宿主页（IntelPage/首页）不卸载，订阅选中与滚动
 *  位置天然保持——原 from 来路字段与 restoreScheduleId 返回恢复链随整页态退役
 *  （0.6 §7 删除链）。
 *  形态对齐 TaskDrawer（3.0 §6.1.7 执行抽屉，复用 WikiTab 预览面板模式）：
 *  遮罩点击收起（mousedown 起于 panel 而松手落遮罩的合成 click 由 suppressClick
 *  守卫吞掉——拖宽/正文选字拖出均不误关）+ 左缘拖宽（默认 60vw 随视口，双向
 *  JS 夹：MIN_W 下限、92vw 随视口上限；CSS max-width 兜底）+ Esc 关闭；
 *  Esc 在输入框聚焦时忽略——文档内查找条（FindBar）Esc 先关查找，再按才关抽屉
 *  （两段式关闭，公共组件零改动）。抽屉内 read↔replay 互切不关抽屉（onViewChange）。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { ReadView } from './ReadView'
import { RunReplay } from './RunReplay'
import { ArchiveView } from './ArchiveView'
import s from './ScheduleDrawer.module.css'

/** 期次视图三态（App 级 scheduleView 状态形态；原 from/scheduleId 字段随抽屉化退役） */
export type ScheduleView =
  | { type: 'read'; runId: string }
  | { type: 'replay'; runId: string; taskId: string }
  | { type: 'archive'; targetId: string; fileId: string; scheduleName: string }

const MIN_W = 420
const MAX_W_RATIO = 0.92
const DEFAULT_W_RATIO = 0.6

/** 视口夹紧（与拖宽 move 同一口径）：下限 MIN_W、上限 92vw 随视口 */
const clampW = (w: number) =>
  Math.min(Math.max(MIN_W, w), Math.floor(window.innerWidth * MAX_W_RATIO))
/** 默认宽 = 视口 60%（惰性初始化，小视口夹至下限；2026-10-26 由固定 760px 改比例） */
const initialWidth = () => clampW(Math.floor(window.innerWidth * DEFAULT_W_RATIO))

interface ScheduleDrawerProps {
  view: ScheduleView
  /** 关闭抽屉（头部返回钮 / Esc / 点遮罩同一收口） */
  onClose: () => void
  /** 抽屉内三态互切（read↔replay），抽屉保持打开 */
  onViewChange: (view: ScheduleView) => void
}

export function ScheduleDrawer({ view, onClose, onViewChange }: ScheduleDrawerProps) {
  const [width, setWidth] = useState(initialWidth)
  const dragging = useRef(false)
  const startX = useRef(0)
  // 首渲染捕获初始宽作拖宽基准（onDragDown 时刷新为当时 width）
  const startW = useRef(width)
  // mousedown 起于 panel 内而 mouseup 落于遮罩时，浏览器把 click 合成到两者公共
  // 祖先（= overlay 本身，绕过 panel 的 stopPropagation）——手柄拖宽、正文选字后
  // 拖出松手均属此类非用户意图 click；松手后一个 tick 内吞掉，防误关抽屉
  const suppressClick = useRef(false)
  const mouseDownInPanel = useRef(false)

  const onDragDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    dragging.current = true
    startX.current = e.clientX
    startW.current = width
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }, [width])

  useEffect(() => {
    const move = (e: MouseEvent) => {
      if (!dragging.current) return
      const delta = startX.current - e.clientX
      // 双向夹紧：下限 MIN_W、上限随视口 92vw——只靠 CSS 上限会产生「拖过头后
      // 回拖纹丝不动」的死区（视觉被 CSS 夹住而 state 未降回）
      setWidth(clampW(startW.current + delta))
    }
    const up = () => {
      if (dragging.current) {
        dragging.current = false
        document.body.style.cursor = ''
        document.body.style.userSelect = ''
      }
      if (!mouseDownInPanel.current) return
      mouseDownInPanel.current = false
      // click 同步派发于 mouseup 之后、本定时器之前，此窗口内吞掉合成 click
      suppressClick.current = true
      setTimeout(() => { suppressClick.current = false }, 0)
    }
    document.addEventListener('mousemove', move)
    document.addEventListener('mouseup', up)
    return () => {
      document.removeEventListener('mousemove', move)
      document.removeEventListener('mouseup', up)
      // 拖宽中途卸载（如全局导航动作清 scheduleView 自动关抽屉）也复位 body 样式，
      // 防游标/选区卡死（TaskDrawer 同款防御）
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
  }, [])

  // Esc 关闭抽屉（模态面板语义 0.3 §5.2）。输入框/多行框聚焦时忽略：FindBar 的
  // Esc 先关查找条（焦点随其卸载散回 body），再按才关抽屉；提取/编辑等弹层打开时
  // 抽屉整体关闭属模态抽屉语义（TaskDrawer 同款，弹层非独立 Esc 域）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')) return
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div
      className={s.overlay}
      onClick={() => { if (!suppressClick.current) onClose() }}
      data-testid="schedule-drawer-overlay"
    >
      <div
        className={s.panel}
        style={{ width }}
        onMouseDown={() => { mouseDownInPanel.current = true }}
        onClick={(e) => e.stopPropagation()}
        data-testid="schedule-drawer"
      >
        <div className={s.dragHandle} onMouseDown={onDragDown} />
        <div className={s.body}>
          {view.type === 'read' ? (
            <ReadView
              runId={view.runId}
              onBack={onClose}
              onReplay={(taskId) => onViewChange({ type: 'replay', runId: view.runId, taskId })}
            />
          ) : view.type === 'replay' ? (
            <RunReplay
              runId={view.runId}
              taskId={view.taskId}
              onBack={onClose}
              onOpenReport={() => onViewChange({ type: 'read', runId: view.runId })}
            />
          ) : (
            <ArchiveView
              targetId={view.targetId}
              fileId={view.fileId}
              scheduleName={view.scheduleName}
              onBack={onClose}
            />
          )}
        </div>
      </div>
    </div>
  )
}
