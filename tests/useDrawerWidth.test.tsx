/**
 * tests/useDrawerWidth.test.tsx — 右侧遮罩抽屉统一宽度契约单测（2026-10-26 收编）。
 *
 * 覆盖：默认宽 = 视口 60%（小视口夹至下限）；拖宽双向夹紧（下限 min、上限 92vw）；
 * 防误关守卫（mousedown 起于 panel 内 → 松手后一个 tick 内吞合成 click，其后复位）；
 * min 变化自动重夹紧；卸载复位 body 游标/选区样式。
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook, act, fireEvent } from '@testing-library/react'
import { useDrawerWidth } from '../src/hooks/useDrawerWidth'
import type { MouseEvent as ReactMouseEvent } from 'react'

const setViewport = (w: number) => {
  Object.defineProperty(window, 'innerWidth', { value: w, configurable: true })
}

const fakeDown = (clientX: number) =>
  ({ clientX, preventDefault() {} }) as unknown as ReactMouseEvent

beforeEach(() => setViewport(1000))

describe('useDrawerWidth（抽屉统一宽度契约）', () => {
  it('默认宽 = 视口 60%（1000 → 600）', () => {
    const { result } = renderHook(() => useDrawerWidth({ min: 420 }))
    expect(result.current.width).toBe(600)
  })

  it('小视口默认宽夹至下限（视口 500、min 460 → 460）', () => {
    setViewport(500)
    const { result } = renderHook(() => useDrawerWidth({ min: 460 }))
    expect(result.current.width).toBe(460)
  })

  it('initial 覆盖默认 60vw（initial 500、min 420 → 500）', () => {
    const { result } = renderHook(() =>
      useDrawerWidth({ min: 420, initial: () => 500 }),
    )
    expect(result.current.width).toBe(500)
  })

  it('拖宽：向左移 100px → 宽 +100（600 → 700）', () => {
    const { result } = renderHook(() => useDrawerWidth({ min: 420 }))
    act(() => result.current.onHandleMouseDown(fakeDown(100)))
    fireEvent.mouseMove(document, { clientX: 0 })
    expect(result.current.width).toBe(700)
    fireEvent.mouseUp(document)
  })

  it('拖宽夹下限：右移超下限 → 夹在 min', () => {
    const { result } = renderHook(() => useDrawerWidth({ min: 420 }))
    act(() => result.current.onHandleMouseDown(fakeDown(500)))
    fireEvent.mouseMove(document, { clientX: 5000 })
    expect(result.current.width).toBe(420)
    fireEvent.mouseUp(document)
  })

  it('拖宽夹上限：左移超 92vw → 夹在 920（1000 视口）', () => {
    const { result } = renderHook(() => useDrawerWidth({ min: 420 }))
    act(() => result.current.onHandleMouseDown(fakeDown(0)))
    fireEvent.mouseMove(document, { clientX: -5000 })
    expect(result.current.width).toBe(920)
    fireEvent.mouseUp(document)
  })

  it('守卫：mousedown 于 panel 内 → 松手后 suppressClick 置位，一个 tick 后复位', async () => {
    const { result } = renderHook(() => useDrawerWidth({ min: 420 }))
    act(() => result.current.onPanelMouseDown())
    fireEvent.mouseUp(document)
    expect(result.current.suppressClick.current).toBe(true)
    await new Promise((r) => setTimeout(r, 0))
    expect(result.current.suppressClick.current).toBe(false)
  })

  it('mousedown 不在 panel 内（直接点遮罩）松手不设守卫，遮罩 click 照常关闭', () => {
    const { result } = renderHook(() => useDrawerWidth({ min: 420 }))
    fireEvent.mouseUp(document)
    expect(result.current.suppressClick.current).toBe(false)
  })

  it('min 变化自动重夹紧当前宽（460 → 680 模式切换）', () => {
    setViewport(1000)
    const { result, rerender } = renderHook(
      ({ min }) => useDrawerWidth({ min }),
      { initialProps: { min: 460 } },
    )
    // 初始 600，在 [460, 920] 内不动；min 提到 680 后重夹至 680
    expect(result.current.width).toBe(600)
    rerender({ min: 680 })
    expect(result.current.width).toBe(680)
  })

  it('卸载复位 body 游标/选区样式（拖宽中途卸载防卡死）', () => {
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    const { unmount } = renderHook(() => useDrawerWidth({ min: 420 }))
    unmount()
    expect(document.body.style.cursor).toBe('')
    expect(document.body.style.userSelect).toBe('')
  })
})
