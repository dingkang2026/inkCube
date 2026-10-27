/** tests/OutputTab.test.tsx — 产出二级分区条仅在列表态显示（2026-10-27 用户裁决）
 *  报告/填表任一侧进入详情/向导视图即隐藏整条分区条（详情占据整个 tab 内容区），
 *  回到列表态恢复。子 Tab 打桩隔离（同 ReportsTab.test 约定），仅验证视图态上报接线。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { useEffect } from 'react'

const stub = vi.hoisted(() => ({ reportsMode: 'list', formsMode: 'list' }))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('../src/features/project/tabs/OutputTab.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

vi.mock('../src/features/project/tabs/ReportsTab', () => ({
  ReportsTab: ({ onViewModeChange }: { onViewModeChange?: (m: string) => void }) => {
    useEffect(() => { onViewModeChange?.(stub.reportsMode) }, [onViewModeChange, stub.reportsMode])
    return null
  },
}))

vi.mock('../src/features/project/tabs/FormsTab', () => ({
  FormsTab: ({ onViewModeChange }: { onViewModeChange?: (m: string) => void }) => {
    useEffect(() => { onViewModeChange?.(stub.formsMode) }, [onViewModeChange, stub.formsMode])
    return null
  },
}))

import { render, screen, cleanup, fireEvent } from '@testing-library/react'
import { OutputTab } from '../src/features/project/tabs/OutputTab'

beforeEach(() => {
  stub.reportsMode = 'list'
  stub.formsMode = 'list'
})

afterEach(() => {
  cleanup()
})

describe('OutputTab section bar visibility (list-only)', () => {
  it('shows the bar initially in list state', () => {
    render(<OutputTab projectId="p1" projectType="general" />)
    expect(screen.getByText('project.tab.reports')).toBeTruthy()
    expect(screen.getByText('project.tab.forms')).toBeTruthy()
    expect(screen.getByText('project.report.templateManagement')).toBeTruthy()
  })

  it('hides the whole bar while a report detail/wizard view is active', () => {
    const { rerender } = render(<OutputTab projectId="p1" projectType="general" />)
    stub.reportsMode = 'preview'
    rerender(<OutputTab projectId="p1" projectType="general" />)
    expect(screen.queryByText('project.tab.reports')).toBeNull()
    expect(screen.queryByText('project.tab.forms')).toBeNull()
    expect(screen.queryByText('project.report.templateManagement')).toBeNull()
  })

  it('restores the bar when the reports branch returns to list', () => {
    const { rerender } = render(<OutputTab projectId="p1" projectType="general" />)
    stub.reportsMode = 'preview'
    rerender(<OutputTab projectId="p1" projectType="general" />)
    expect(screen.queryByText('project.tab.reports')).toBeNull()
    stub.reportsMode = 'list'
    rerender(<OutputTab projectId="p1" projectType="general" />)
    expect(screen.getByText('project.tab.reports')).toBeTruthy()
    expect(screen.getByText('project.report.templateManagement')).toBeTruthy()
  })

  it('same contract for the forms branch (preview hides, back to list restores)', () => {
    const { rerender } = render(<OutputTab projectId="p1" projectType="general" />)
    fireEvent.click(screen.getByText('project.tab.forms'))
    // 切到填表分区：FormsTab 挂载上报 'list'，分区条保持可见
    expect(screen.getByText('project.report.templateManagement')).toBeTruthy()
    stub.formsMode = 'preview'
    rerender(<OutputTab projectId="p1" projectType="general" />)
    expect(screen.queryByText('project.tab.forms')).toBeNull()
    expect(screen.queryByText('project.report.templateManagement')).toBeNull()
    stub.formsMode = 'list'
    rerender(<OutputTab projectId="p1" projectType="general" />)
    expect(screen.getByText('project.tab.forms')).toBeTruthy()
  })
})
