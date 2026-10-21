// @vitest-environment node
/**
 * V2.1-R3 回归（2.8 §6.2 bounds 桥）：webfetch:set-bounds 的 zoom 换算。
 * 渲染层 getBoundingClientRect 返回 CSS px（受视图缩放影响），WebContentsView
 * .setBounds 坐标是 DIP（物理 = DIP×S，dpr = S×z）。对齐条件 bounds_DIP =
 * CSS × z（与显示器缩放无关）。视图菜单 zoomOut 半步 → zoomLevel -0.5 →
 * factor 1.2^-0.5 ≈ 0.913，CSS 值直传 DIP 偏移 CSS×(1-z) 导致承载右移+右缘
 * 被窗口裁切（2026-10-14 用户截图实证）。修复：主进程按
 * event.sender.getZoomFactor() **乘**换算。被测：electron/fetch-pool-bounds.js（零依赖纯逻辑，
 * fetch-pool.js 的 ipcMain.on('webfetch:set-bounds') 一行接线到它）。
 */
import { describe, expect, it, vi } from 'vitest'

import { handleSetBounds } from '../electron/fetch-pool-bounds.js'

/** 与渲染层 BrowserPanel roundRect 输出同构的 compact host rect（CSS px） */
const COMPACT_HOST = { x: 1031, y: 85, width: 358, height: 442 }
/** 视图菜单「缩小」一次：zoomLevel 0 → -0.5（2026-10-14 实证值，精确 1.2^-0.5） */
const MENU_ZOOM_OUT_FACTOR = 1.2 ** -0.5

function makeView() {
  return { setBounds: vi.fn() }
}

describe('handleSetBounds（webfetch:set-bounds 纯逻辑，V2.1-R3）', () => {
  it('zoomFactor=1.2^-0.5（视图菜单缩小半步）时按 sender zoom 换算为 DIP', () => {
    const view = makeView()
    handleSetBounds(view, { getZoomFactor: () => MENU_ZOOM_OUT_FACTOR }, { ...COMPACT_HOST })
    expect(view.setBounds).toHaveBeenCalledWith({
      x: Math.round(1031 * MENU_ZOOM_OUT_FACTOR),
      y: Math.round(85 * MENU_ZOOM_OUT_FACTOR),
      width: Math.max(1, Math.round(358 * MENU_ZOOM_OUT_FACTOR)),
      height: Math.max(1, Math.round(442 * MENU_ZOOM_OUT_FACTOR)),
    })
  })

  it('zoomFactor=1.2^0.5（视图菜单放大半步）同样换算', () => {
    const view = makeView()
    handleSetBounds(view, { getZoomFactor: () => 1.2 ** 0.5 }, { ...COMPACT_HOST })
    expect(view.setBounds).toHaveBeenCalledWith({
      x: Math.round(1031 * (1.2 ** 0.5)),
      y: Math.round(85 * (1.2 ** 0.5)),
      width: Math.max(1, Math.round(358 * (1.2 ** 0.5))),
      height: Math.max(1, Math.round(442 * (1.2 ** 0.5))),
    })
  })

  it('zoomFactor=1 时保持原值（等价修复前行为）', () => {
    const view = makeView()
    handleSetBounds(view, { getZoomFactor: () => 1 }, { ...COMPACT_HOST })
    expect(view.setBounds).toHaveBeenCalledWith({ x: 1031, y: 85, width: 358, height: 442 })
  })

  it('sender 缺 getZoomFactor 时按 1 兜底（不抛错）', () => {
    const view = makeView()
    expect(() => handleSetBounds(view, {}, { ...COMPACT_HOST })).not.toThrow()
    expect(view.setBounds).toHaveBeenCalledWith({ x: 1031, y: 85, width: 358, height: 442 })
  })

  it('非法 rect/view 仍被忽略（既有守卫不回退）', () => {
    const view = makeView()
    handleSetBounds(view, { getZoomFactor: () => MENU_ZOOM_OUT_FACTOR }, null)
    handleSetBounds(view, { getZoomFactor: () => MENU_ZOOM_OUT_FACTOR }, { y: 1 })
    handleSetBounds(null, { getZoomFactor: () => 1 }, { ...COMPACT_HOST })
    expect(view.setBounds).not.toHaveBeenCalled()
  })
})
