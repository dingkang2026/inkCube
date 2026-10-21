/** src/hooks/useQueuePause.ts — 后台队列停止/恢复（0.4 §队列暂停，UI 呈现为停止/继续）
 *
 * 统一封装两类长时活动（文件解析/图片描述）的停止开关：
 *  - 初始态从 settings 读取（停止态持久化于 settings，重启保持）；
 *  - toggle 经 knowledge:queuePause 写 settings + 通知后端 worker 闸门；
 *  - 语义：停止 = 不再开始新任务 + 清空内存队列积压（DB pending 不动），
 *    在飞任务自然跑完；恢复后 Scanner 按 DB pending 补投递，断点续传。
 * 基础库与项目共用同一队列，天然同停止。
 * wiki 编译无队列暂停（0.4 §6.7 修订）：其任务级「停止」+ 断点续传已覆盖
 * 暂停的全部价值，双出口只会增加语义混淆。
 * 报告生成同无队列暂停（2.5 §3.1 修订）：报告级「暂停」（章节边界协作生效
 * + checkpoint 续跑）与「重新生成」双出口已覆盖全部价值。
 */
import { useCallback, useEffect, useState } from 'react'

export type QueuePauseKind = 'parsing' | 'describe'

const SETTINGS_CHANNEL = 'settings:get'
const PAUSE_CHANNEL = 'knowledge:queuePause'

export function useQueuePause(kind: QueuePauseKind) {
  const [paused, setPaused] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const settings = (await window.electronAPI.invoke(SETTINGS_CHANNEL)) as Record<string, unknown> | null
        if (!cancelled) setPaused(!!settings?.[`${kind}_paused`])
      } catch (err) {
        console.error('Failed to load queue pause state:', err)
      }
    })()
    return () => { cancelled = true }
  }, [kind])

  const toggle = useCallback(async () => {
    if (busy) return
    const next = !paused
    setBusy(true)
    try {
      await window.electronAPI.invoke(PAUSE_CHANNEL, { kind, paused: next })
      setPaused(next)
    } catch (err) {
      console.error('Failed to toggle queue pause:', err)
    } finally {
      setBusy(false)
    }
  }, [busy, paused, kind])

  return { paused, toggle, busy }
}
