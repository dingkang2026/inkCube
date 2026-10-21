/** tests/ScheduleFormModal.test.tsx — 新建/编辑订阅弹层向导（2.11 E3 T7）
 *  计划 Step 1 要点：从模板选模板 → 出现去重后的占位符输入（同名占位符只一个输入框，
 *  填一次全量替换，0.5 §4.4）；填齐下一步可用；initial.taskId → refinePrompt
 *  预填并跳过第①步；提交 schedules:create payload intervalValue 格式
 *  （weekly: MON,THU 09:00）。 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { ScheduleFormModal } from '../src/features/schedule/ScheduleFormModal'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}))

vi.mock('../src/features/knowledge/Toast', () => ({ useToast: () => vi.fn() }))

const invokeMock = vi.fn()

beforeEach(() => {
  invokeMock.mockReset()
  invokeMock.mockImplementation(async (channel: string) => {
    if (channel === 'taskTemplates:list') {
      return {
        templates: [
          {
            id: 'tpl_custom_1', scope: 'global', project_id: null,
            category: 'monitor', title: '企业舆情扫描', prompt: '扫描{企业名}的舆情，输出{企业名}的报告',
            pinned: 0, created_at: '', updated_at: '',
          },
        ],
        builtinPinned: [],
      }
    }
    if (channel === 'knowledge:listProjects') {
      return { projects: [{ id: 'p1', name: '演示项目' }] }
    }
    if (channel === 'baseLib:list') return { base_libs: [] }
    return {}
  })
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

describe('ScheduleFormModal', () => {
  it('template tab: picking a template surfaces ONE deduped {企业名} placeholder input', async () => {
    render(<ScheduleFormModal open onClose={() => {}} onSaved={() => {}} />)
    await waitFor(() => expect(screen.getByText('企业舆情扫描')).toBeTruthy())
    fireEvent.click(screen.getByText('企业舆情扫描'))
    await waitFor(() =>
      // {企业名} 在模板中出现 2 次，去重后只渲染一个输入框
      expect(screen.getAllByLabelText('schedule.ph.企业名')).toHaveLength(1))
  })

  it('next disabled until placeholders filled; filled → step 2 reachable', async () => {
    render(<ScheduleFormModal open onClose={() => {}} onSaved={() => {}} />)
    await waitFor(() => expect(screen.getByText('企业舆情扫描')).toBeTruthy())
    fireEvent.click(screen.getByText('企业舆情扫描'))
    const input = await waitFor(() =>
      screen.getByLabelText('schedule.ph.企业名')) as HTMLInputElement
    const next = screen.getByText('schedule.form.next') as HTMLButtonElement
    expect(next.disabled).toBe(true)
    fireEvent.change(input, { target: { value: '华润' } })
    expect(next.disabled).toBe(false)
    fireEvent.click(next)
    expect(screen.getByText('schedule.form.stepPeriod')).toBeTruthy()
  })

  it('fills the variable once → all occurrences replaced in schedules:create payload', async () => {
    render(<ScheduleFormModal open onClose={() => {}} onSaved={() => {}} />)
    await waitFor(() => expect(screen.getByText('企业舆情扫描')).toBeTruthy())
    fireEvent.click(screen.getByText('企业舆情扫描'))
    const input = await waitFor(() =>
      screen.getByLabelText('schedule.ph.企业名')) as HTMLInputElement
    fireEvent.change(input, { target: { value: '华润' } })
    fireEvent.click(screen.getByText('schedule.form.next'))
    fireEvent.change(screen.getByLabelText('schedule.form.timeLabel'), {
      target: { value: '09:00' },
    })
    fireEvent.click(screen.getByText('schedule.form.next'))
    fireEvent.click(screen.getByText('schedule.form.save'))
    await waitFor(() => {
      const create = invokeMock.mock.calls.find((c) => c[0] === 'schedules:create')
      expect(create).toBeTruthy()
      // 两处 {企业名} 一次填写全部替换
      expect(create![1]).toMatchObject({
        prompt: '扫描华润的舆情，输出华润的报告',
      })
    })
  })

  it('initial.taskId: refinePrompt prefills and skips step 1', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'schedules:refinePrompt') {
        return { name: '华润舆情周报', prompt: '每周扫描华润舆情', outputHint: 'md 报告' }
      }
      if (channel === 'knowledge:listProjects') {
        return { projects: [{ id: 'p1', name: '演示项目' }] }
      }
      return {}
    })
    render(<ScheduleFormModal open initial={{ taskId: 't1' }} onClose={() => {}} onSaved={() => {}} />)
    await waitFor(() => expect(invokeMock).toHaveBeenCalledWith('schedules:refinePrompt', { taskId: 't1' }))
    await waitFor(() => expect(screen.getByText('schedule.form.stepPeriod')).toBeTruthy())
    // 跳过第①步：来源页签不存在
    expect(screen.queryByText('schedule.form.fromTemplate')).toBeNull()
  })

  it('weekly multi-select submits intervalValue "MON,THU 09:00" via schedules:create', async () => {
    render(<ScheduleFormModal open onClose={() => {}} onSaved={() => {}} />)
    fireEvent.click(screen.getByText('schedule.form.freeInput'))
    fireEvent.change(screen.getByLabelText('schedule.form.promptLabel'), {
      target: { value: '每周扫描华润舆情' },
    })
    fireEvent.click(screen.getByText('schedule.form.next'))
    // 第②步：weekly + 周一/周四 + 09:00
    fireEvent.click(screen.getByRole('tab', { name: 'schedule.period.weekly' }))
    fireEvent.click(screen.getByLabelText('schedule.wd.MON'))
    fireEvent.click(screen.getByLabelText('schedule.wd.THU'))
    fireEvent.change(screen.getByLabelText('schedule.form.timeLabel'), {
      target: { value: '09:00' },
    })
    fireEvent.click(screen.getByText('schedule.form.next'))
    fireEvent.click(screen.getByText('schedule.form.save'))
    await waitFor(() => {
      const create = invokeMock.mock.calls.find((c) => c[0] === 'schedules:create')
      expect(create).toBeTruthy()
      expect(create![1]).toMatchObject({
        prompt: '每周扫描华润舆情',
        intervalType: 'weekly',
        intervalValue: 'MON,THU 09:00',
      })
    })
  })
})
