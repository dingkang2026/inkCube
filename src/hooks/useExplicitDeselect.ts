/** src/hooks/useExplicitDeselect.ts — 0.5 E1 T5:项目自动选中的显式取消守卫。
 *  背景:进项目页 effect 会自动选中首个项目;用户点当前项目取消(toggle→null)后,
 *  该 effect 的依赖变化会把首个项目顶回来,项目欢迎页永远不可达——守卫位让
 *  「用户显式取消」优先于自动选中;切导航或重新选择项目时复位。 */
import { useCallback, useMemo, useState } from 'react'

export interface ExplicitDeselect {
  /** 守卫位:true = 用户刚显式取消,自动选中应让路 */
  blocked: boolean
  /** 用户点已选项目取消时调用 */
  markDeselected: () => void
  /** 切 NavTab 或重新选择项目时调用 */
  reset: () => void
  /** 自动选中 effect 内判断:返回 !blocked */
  shouldAutoSelect: () => boolean
}

export function useExplicitDeselect(): ExplicitDeselect {
  const [blocked, setBlocked] = useState(false)
  const markDeselected = useCallback(() => setBlocked(true), [])
  const reset = useCallback(() => setBlocked(false), [])
  const shouldAutoSelect = useCallback(() => !blocked, [blocked])
  return useMemo(
    () => ({ blocked, markDeselected, reset, shouldAutoSelect }),
    [blocked, markDeselected, reset, shouldAutoSelect],
  )
}
