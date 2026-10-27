/** src/features/schedule/ScheduleDrawer.tsx — 期次三态右侧抽屉（0.6 §3.2 第二步
 *  三态统一，2026-10-25）。read（阅读页）/replay（执行过程）/archive（档案）从整页
 *  peer 替换改为 App 级遮罩抽屉：宿主页（IntelPage/首页）不卸载，订阅选中与滚动
 *  位置天然保持——原 from 来路字段与 restoreScheduleId 返回恢复链随整页态退役
 *  （0.6 §7 删除链）。
 *  形态对齐 TaskDrawer（3.0 §6.1.7 执行抽屉，复用 WikiTab 预览面板模式）：
 *  遮罩点击收起 + 左缘拖宽 + Esc 关闭（宽度契约统一走 useDrawerWidth：默认 60vw
 *  随视口，双向 JS 夹：MIN_W 下限、92vw 随视口上限；CSS max-width 兜底）；
 *  Esc 在输入框聚焦时忽略——文档内查找条（FindBar）Esc 先关查找，再按才关抽屉
 *  （两段式关闭，公共组件零改动）。抽屉内 read↔replay 互切不关抽屉（onViewChange）。
 */
import { useEffect } from 'react'
import { ReadView } from './ReadView'
import { RunReplay } from './RunReplay'
import { ArchiveView } from './ArchiveView'
import { useDrawerWidth } from '../../hooks/useDrawerWidth'
import s from './ScheduleDrawer.module.css'

/** 期次视图三态（App 级 scheduleView 状态形态；原 from/scheduleId 字段随抽屉化退役） */
export type ScheduleView =
  | { type: 'read'; runId: string }
  | { type: 'replay'; runId: string; taskId: string }
  | { type: 'archive'; targetId: string; fileId: string; scheduleName: string }

/** 宽度下限（上限 92vw 随视口、默认 60vw 随视口，见 useDrawerWidth） */
const MIN_W = 420

interface ScheduleDrawerProps {
  view: ScheduleView
  /** 关闭抽屉（头部返回钮 / Esc / 点遮罩同一收口） */
  onClose: () => void
  /** 抽屉内三态互切（read↔replay），抽屉保持打开 */
  onViewChange: (view: ScheduleView) => void
}

export function ScheduleDrawer({ view, onClose, onViewChange }: ScheduleDrawerProps) {
  // 宽度/拖宽/防误关守卫统一契约（抽屉六处收编，2026-10-26）
  const dr = useDrawerWidth({ min: MIN_W })

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
      onClick={() => { if (!dr.suppressClick.current) onClose() }}
      data-testid="schedule-drawer-overlay"
    >
      <div
        className={s.panel}
        style={{ width: dr.width }}
        onMouseDown={dr.onPanelMouseDown}
        onClick={(e) => e.stopPropagation()}
        data-testid="schedule-drawer"
      >
        <div className={s.dragHandle} onMouseDown={dr.onHandleMouseDown} />
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
