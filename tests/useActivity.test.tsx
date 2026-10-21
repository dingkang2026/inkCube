/** tests/useActivity.test.tsx — useActivity 重载单测（0.4lifecycle-design.md §6.2）。
 *  无参：全量 Context；有参：按 kind 过滤 + isActive 派生。 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
import { ActivityProvider, useActivity } from '../src/features/activity/ActivityContext'
import type { ActivityStatus } from '../src/features/activity/types'

vi.stubGlobal('electronAPI', {
  invoke: vi.fn().mockResolvedValue({}),
  on: vi.fn(() => () => {}),
})

afterEach(cleanup)

function makeStatus(): ActivityStatus {
  return {
    active: [
      { entity_type: 'model_download', entity_id: 'bge-m3', progress: 40 },
      { entity_type: 'model_download', entity_id: 'tableformer-v2', progress: 10 },
      { entity_type: 'file', project_id: 'p1', count: 2 },
      { entity_type: 'form_fill', project_id: 'p1' },
      { entity_type: 'form_fill', project_id: 'p1' },
    ],
    notifications: { unread: [], recent: [] },
    summary: {
      active_files: 2,
      active_compiles: 0,
      generating_reports: 0,
      running_tasks: 0,
      downloading_models: 2,
      analyzing_form_templates: 1,
      filling_forms: 2,
      active_ppts: 0,
    },
  }
}

/** 测试探针：渲染 hook 值，触发 applyStatus，最后展示序列化结果 */
function Probe({ kind }: { kind?: 'model_download' | 'file' }) {
  const value = useActivity(kind as never)
  return <div data-testid="probe">{JSON.stringify('items' in value ? { items: value.items, isActive: value.isActive } : { summary: value.summary, byProject: [...value.byProject.values()], activeProjectIds: [...value.activeProjectIds] })}</div>
}

function setup(kind?: 'model_download' | 'file') {
  let apply!: (s: ActivityStatus) => void
  function Harness() {
    const ctx = useActivity()
    apply = ctx.applyStatus
    return <Probe kind={kind} />
  }
  const utils = render(
    <ActivityProvider>
      <Harness />
    </ActivityProvider>,
  )
  return { utils, apply: (s: ActivityStatus) => act(() => apply(s)) }
}

describe('useActivity() no-arg overload', () => {
  it('returns full context after applyStatus', () => {
    const { apply } = setup()
    apply(makeStatus())
    const text = screen.getByTestId('probe').textContent!
    expect(JSON.parse(text).summary.downloading_models).toBe(2)
  })

  it('aggregates form_fill into byProject and activeProjectIds (2.4 填表接入)', () => {
    const { apply } = setup()
    apply(makeStatus())
    const parsed = JSON.parse(screen.getByTestId('probe').textContent!)
    const p1 = parsed.byProject.find((p: { projectId: string }) => p.projectId === 'p1')
    expect(p1.forms).toBe(2)
    expect(parsed.activeProjectIds).toContain('p1')
  })
})

describe('useActivity(kind) overload', () => {
  it('filters items by kind and derives isActive', () => {
    const { apply } = setup('model_download')
    apply(makeStatus())
    const parsed = JSON.parse(screen.getByTestId('probe').textContent!)
    expect(parsed.isActive).toBe(true)
    expect(parsed.items.map((i: { entity_id?: string }) => i.entity_id)).toEqual(['bge-m3', 'tableformer-v2'])
  })

  it('returns empty items when kind has no active entries', () => {
    const { apply } = setup('model_download')
    const empty: ActivityStatus = {
      active: [{ entity_type: 'file', project_id: 'p1', count: 1 }],
      notifications: { unread: [], recent: [] },
      summary: {
        active_files: 1,
        active_compiles: 0,
        generating_reports: 0,
        running_tasks: 0,
        downloading_models: 0,
        active_ppts: 0,
      },
    }
    apply(empty)
    const parsed = JSON.parse(screen.getByTestId('probe').textContent!)
    expect(parsed.isActive).toBe(false)
    expect(parsed.items).toEqual([])
  })

  it('throws outside ActivityProvider', () => {
    // 独立 container + 手动移除：render 抛错时 RTL 无法自动登记清理，避免残留污染后续查询
    const div = document.createElement('div')
    expect(() => render(<Probe kind="file" />, { container: div })).toThrow(/ActivityProvider/)
    div.remove()
  })
})
