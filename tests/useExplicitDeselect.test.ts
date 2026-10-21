import { describe, it, expect } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useExplicitDeselect } from '../src/hooks/useExplicitDeselect'

describe('useExplicitDeselect (0.5 E1 T5)', () => {
  it('markDeselected 后 shouldAutoSelect 为 false，reset 后恢复 true', () => {
    const { result } = renderHook(() => useExplicitDeselect())
    expect(result.current.shouldAutoSelect()).toBe(true)
    act(() => result.current.markDeselected())
    expect(result.current.shouldAutoSelect()).toBe(false)
    act(() => result.current.reset())
    expect(result.current.shouldAutoSelect()).toBe(true)
  })

  it('blocked 守卫位随两侧动作翻转', () => {
    const { result } = renderHook(() => useExplicitDeselect())
    expect(result.current.blocked).toBe(false)
    act(() => result.current.markDeselected())
    expect(result.current.blocked).toBe(true)
    act(() => result.current.reset())
    expect(result.current.blocked).toBe(false)
  })
})
