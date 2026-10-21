/** tests/useRecentOutputs.test.ts — 0.5 E2 T7:卡片墙「新产出」角标派生。
 *  纯函数口径:kind==='completed' && entity_type ∈ 产出类 && project_id 非空 && 距今 <24h;
 *  hook 从 ActivityContext 全局心跳快照(status.notifications.unread)派生,零新增轮询。 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, act, cleanup } from '@testing-library/react'
import { deriveRecentOutputs, useRecentOutputs } from '../src/features/home/useRecentOutputs'
import { ActivityProvider, useActivity } from '../src/features/activity/ActivityContext'
import type { ActivityContextValue } from '../src/features/activity/ActivityContext'
import type { NotificationItem } from '../src/features/activity/types'

const NOW = new Date('2026-10-19T12:00:00').getTime()

function n(over: Partial<NotificationItem>): NotificationItem {
  return {
    id: 1, entity_type: 'report', entity_id: 'e1', project_id: 'p1', container: 'project',
    kind: 'completed', entity_title: '报告', detail: null, read_at: null,
    created_at: new Date(NOW - 3600_000).toISOString(), // 默认 1h 前
    ...over,
  }
}

describe('deriveRecentOutputs 纯派生口径 (0.5 E2 T7)', () => {
  it('合格条目按项目计数', () => {
    const unread = [
      n({ id: 1, entity_type: 'report', project_id: 'p1' }),
      n({ id: 2, entity_type: 'task', project_id: 'p1' }),
    ]
    expect(deriveRecentOutputs(unread, NOW)).toEqual({ p1: 2 })
  })

  it('仅统计 24h 内:超龄条目排除(23h 计入 / 25h 排除)', () => {
    const unread = [
      n({ id: 1, created_at: new Date(NOW - 23 * 3600_000).toISOString() }),
      n({ id: 2, created_at: new Date(NOW - 25 * 3600_000).toISOString() }),
    ]
    expect(deriveRecentOutputs(unread, NOW)).toEqual({ p1: 1 })
  })

  it('failed / 信息类 kind 排除', () => {
    const unread = [
      n({ id: 1, kind: 'failed' }),
      n({ id: 2, kind: 'due' }),
      n({ id: 3, kind: 'file_changed' }),
    ]
    expect(deriveRecentOutputs(unread, NOW)).toEqual({})
  })

  it('无 project_id / entity_type 不在产出类排除', () => {
    const unread = [
      n({ id: 1, project_id: null }),
      n({ id: 2, entity_type: 'affair' }),
      n({ id: 3, entity_type: 'model_download' }),
    ]
    expect(deriveRecentOutputs(unread, NOW)).toEqual({})
  })

  it('wiki_compile 计入产出(独立项目)', () => {
    const unread = [n({ id: 1, entity_type: 'wiki_compile', project_id: 'p2' })]
    expect(deriveRecentOutputs(unread, NOW)).toEqual({ p2: 1 })
  })

  it('多项目分组计数', () => {
    const unread = [
      n({ id: 1, project_id: 'p1' }),
      n({ id: 2, entity_type: 'ppt', project_id: 'p2' }),
      n({ id: 3, entity_type: 'form_fill', project_id: 'p2' }),
    ]
    expect(deriveRecentOutputs(unread, NOW)).toEqual({ p1: 1, p2: 2 })
  })
})

describe('useRecentOutputs hook(ActivityContext 派生,零新增轮询)', () => {
  let captured: ActivityContextValue | null = null
  let latest: Record<string, number> | undefined

  function Probe() {
    captured = useActivity()
    // 注入 NOW：hook 缺省走真实时钟，mock 条目 created_at=NOW-1h 会在真实时间
    // 跨过 24h 窗口后（10-20 12:00 起）被口径排除而假红
    latest = useRecentOutputs(NOW)
    return null
  }

  beforeEach(() => {
    captured = null
    latest = undefined
  })
  afterEach(() => cleanup())

  it('未拉取时为空对象;applyStatus 注入后按口径派生', () => {
    render(
      <ActivityProvider>
        <Probe />
      </ActivityProvider>,
    )
    expect(latest).toEqual({})
    act(() => {
      captured!.applyStatus({
        active: [],
        summary: {
          active_files: 0, active_compiles: 0, generating_reports: 0, running_tasks: 0,
          downloading_models: 0, analyzing_form_templates: 0, filling_forms: 0, active_ppts: 0,
        },
        notifications: {
          unread: [
            n({ id: 1, project_id: 'p1' }),
            n({ id: 2, kind: 'failed', project_id: 'p1' }),
          ],
          recent: [],
        },
      })
    })
    expect(latest).toEqual({ p1: 1 })
  })
})
