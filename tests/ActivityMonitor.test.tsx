/** tests/ActivityMonitor.test.tsx — ActivityMonitor 回归用例（0.4lifecycle-design.md §5.5 / §6.4 E3）
 *  核心回归：底层 IPC 挂起（invoke 永不 settle，如 Node fetch 复用半关闭
 *  keep-alive 连接且无超时）时，轮询循环不得死亡——INVOKE_TIMEOUT 超时判定
 *  失败 → 指数退避 → 继续轮询。防止「任务完成后首页/徽章快照冻结」复发。
 *  另覆盖正常路径：成功拉取后按空闲间隔调度下一次。
 *  另覆盖可见性：窗口不可见（Windows 遮挡检测 hidden=true）时仍拉取且降频 45s、
 *  恢复可见立即拉取（0.4 附录 A 决策表「禁止 hidden 早退跳过拉取」）。
 *  E3：activity:stream 事件到达 → 200ms 节流后立即心跳（Toast 即时化）。
 *  navigateToNotification 导航分流：form_template → 模板管理页（全局资产，
 *  无 project_id 也可导航）、form_fill → 项目 forms Tab（2.4 填表接入）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, act } from '@testing-library/react'
import { ActivityMonitor, navigateToNotification } from '../src/features/activity/ActivityMonitor'
import type { ActivityStatus, NotificationItem } from '../src/features/activity/types'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

// R4 用例需断言 showToast / markRead 调用 → 共享实例（每 render 新建无法引用）
const sharedMocks = vi.hoisted(() => ({
  showToast: vi.fn(),
  markRead: vi.fn().mockResolvedValue(undefined),
}))

vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => sharedMocks.showToast,
}))

/** summary 可变对象：useActivity mock 直接引用，用例内改字段驱动 currentInterval 判定 */
const activitySummary = {
  active_files: 0,
  active_compiles: 0,
  generating_reports: 0,
  running_tasks: 0,
  downloading_models: 0,
  active_ppts: 0,
}

vi.mock('../src/features/activity/ActivityContext', () => ({
  useActivity: () => ({
    applyStatus: vi.fn(),
    markRead: sharedMocks.markRead,
    summary: activitySummary,
  }),
}))

const IDLE_STATUS = {
  notifications: { unread: [], recent: [] },
} as unknown as ActivityStatus

/** invoke 按 channel 分流：activities:getStatus 返回状态，其余（subscribeStream）返回 ok */
const wireInvoke = () =>
  vi.fn((channel: string) =>
    channel === 'activities:getStatus'
      ? Promise.resolve(IDLE_STATUS)
      : Promise.resolve({ ok: true }),
  )

/** activities:getStatus 通道的调用次数（SSE 订阅 invoke 不计入心跳计数） */
const heartbeatCalls = (invoke: ReturnType<typeof wireInvoke>) =>
  invoke.mock.calls.filter((c) => c[0] === 'activities:getStatus').length

/** 排空 microtask（poll 为 async，fake timers 不会自动推进） */
const flush = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => {
      await Promise.resolve()
    })
  }
}

beforeEach(() => {
  vi.useFakeTimers()
  activitySummary.active_ppts = 0
  sharedMocks.showToast.mockClear()
  sharedMocks.markRead.mockClear()
  sharedMocks.markRead.mockResolvedValue(undefined)
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
  delete (window as { electronAPI?: unknown }).electronAPI
  // 移除用例内定义的实例属性，恢复 jsdom 原型上的 hidden/hasFocus getter
  delete (document as { hidden?: unknown }).hidden
  delete (document as { hasFocus?: unknown }).hasFocus
})

describe('ActivityMonitor', () => {
  it('polls on success and schedules next run at idle interval', async () => {
    const invoke = wireInvoke()
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
    render(<ActivityMonitor />)
    await flush()
    expect(heartbeatCalls(invoke)).toBe(1)
    // 空闲状态无进行中活动 → 15s 后下一次
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15000)
    })
    await flush()
    expect(heartbeatCalls(invoke)).toBe(2)
  })

  it('keeps polling when invoke hangs forever (regression: poll loop death)', async () => {
    // 模拟 IPC 永久挂起：getStatus 返回永不 settle 的 promise（其余通道正常）
    const invoke = vi.fn((channel: string) =>
      channel === 'activities:getStatus'
        ? new Promise(() => {})
        : Promise.resolve({ ok: true }),
    )
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
    render(<ActivityMonitor />)
    await flush()
    expect(heartbeatCalls(invoke)).toBe(1)
    // 8s 超时判定失败 → 退避首档 5s → 13s 处第二次 invoke（循环未死）
    await act(async () => {
      await vi.advanceTimersByTimeAsync(8000)
    })
    await flush()
    expect(heartbeatCalls(invoke)).toBe(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4999)
    })
    await flush()
    expect(heartbeatCalls(invoke)).toBe(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })
    await flush()
    expect(heartbeatCalls(invoke)).toBe(2)
    // 第二次仍挂起 → 超时 → 退避 10s → 第三次 invoke
    await act(async () => {
      await vi.advanceTimersByTimeAsync(8000)
    })
    await flush()
    expect(heartbeatCalls(invoke)).toBe(2)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000)
    })
    await flush()
    expect(heartbeatCalls(invoke)).toBe(3)
  })

  it('still polls when document.hidden (regression: occluded-window heartbeat stall)', async () => {
    // Windows Chromium 遮挡检测：被完全遮挡的窗口 document.hidden=true，
    // 旧实现早退跳过拉取 → 心跳断流（进度冻结 0% / 呼吸点消失 / Toast 延迟到置前）
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true })
    const invoke = wireInvoke()
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
    render(<ActivityMonitor />)
    await flush()
    // hidden 时挂载仍立即首拉（不跳过）
    expect(heartbeatCalls(invoke)).toBe(1)
    // 不可见降频 45s（IDLE 15s ×3）：44.9s 不拉，45s 整点拉
    await act(async () => {
      await vi.advanceTimersByTimeAsync(44999)
    })
    await flush()
    expect(heartbeatCalls(invoke)).toBe(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })
    await flush()
    expect(heartbeatCalls(invoke)).toBe(2)
  })

  it('polls immediately when window becomes visible again', async () => {
    // 遮挡期降频 45s；恢复可见立即拉取（visibilitychange），不等 timer 到点
    let hidden = true
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden })
    const invoke = wireInvoke()
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
    render(<ActivityMonitor />)
    await flush()
    expect(heartbeatCalls(invoke)).toBe(1)
    // 置前：hidden→false + visibilitychange → 立即再拉（不推进 timer）
    hidden = false
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'))
    })
    await flush()
    expect(heartbeatCalls(invoke)).toBe(2)
  })

  it('subscribes activity stream and kicks immediate heartbeat on event (E3)', async () => {
    // 终态事件到达 → 200ms 节流 → 立即一次聚合拉取（Toast 即时化，不等 15s 心跳）
    let streamHandler: (() => void) | null = null
    const invoke = wireInvoke()
    ;(window as { electronAPI?: unknown }).electronAPI = {
      invoke,
      on: (channel: string, cb: () => void) => {
        if (channel === 'activity:stream') streamHandler = cb
        return () => {}
      },
    }
    render(<ActivityMonitor />)
    await flush()
    // 挂载即订阅 SSE（E3）+ 首次心跳
    expect(invoke).toHaveBeenCalledWith('activities:subscribeStream')
    expect(heartbeatCalls(invoke)).toBe(1)

    // 事件到达：200ms 节流窗口内不拉，窗口结束后立即拉
    await act(async () => {
      streamHandler!()
      await vi.advanceTimersByTimeAsync(199)
    })
    await flush()
    expect(heartbeatCalls(invoke)).toBe(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })
    await flush()
    expect(heartbeatCalls(invoke)).toBe(2)

    // 事件风暴（batch 完成）在节流窗口内合并为一次拉取
    await act(async () => {
      streamHandler!()
      streamHandler!()
      streamHandler!()
      await vi.advanceTimersByTimeAsync(200)
    })
    await flush()
    expect(heartbeatCalls(invoke)).toBe(3)
  })

  it('dispatches app:usage-changed when new terminal notifications arrive (2.10 §5.3)', async () => {
    // 第三发信点回归：新终态条目 → 全局用量刷新信号（StatusBar/项目行/统计页
    // 经 useUsageSummary 1.5s 去抖消费）。首拉建基线不发（启动历史不轰炸）；
    // 无新条目轮不发；同批多条合并为一次信号
    let call = 0
    const withNotice = {
      notifications: {
        unread: [],
        recent: [{ id: 7, entity_type: 'report', kind: 'completed' }],
      },
    } as unknown as ActivityStatus
    const invoke = vi.fn((channel: string) => {
      if (channel !== 'activities:getStatus') return Promise.resolve({ ok: true })
      call += 1
      return Promise.resolve(call === 1 ? IDLE_STATUS : withNotice)
    })
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
    const seen: Event[] = []
    window.addEventListener('app:usage-changed', (e) => seen.push(e))
    render(<ActivityMonitor />)
    await flush()
    expect(heartbeatCalls(invoke)).toBe(1)
    expect(seen).toHaveLength(0) // 首拉仅建基线
    // 第二轮出现新终态 → 恰一次信号（批量终态由消费端去抖合并）
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15000)
    })
    await flush()
    expect(heartbeatCalls(invoke)).toBe(2)
    expect(seen).toHaveLength(1)
    // 第三轮无新条目 → 不再发
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15000)
    })
    await flush()
    expect(heartbeatCalls(invoke)).toBe(3)
    expect(seen).toHaveLength(1)
  })

  it('does not fork polling chain when kick arrives during in-flight poll (regression: chain fork)', async () => {
    // P1-1 回归（v3.9 修复）：poll 无在飞守卫时，kick / visibilitychange 在 poll
    // 在飞期间 void poll() 会分叉出第二条链式调度链（尾部无条件 setTimeout，
    // clearTimeout 只能清到最后一条），频率只增不减；修复后在飞触发仅标记补拉，
    // 完成后 0ms 补一轮，单链语义不变
    let resolveFirst!: (v: unknown) => void
    const first = new Promise((r) => { resolveFirst = r })
    let call = 0
    const invoke = vi.fn((channel: string) => {
      if (channel !== 'activities:getStatus') return Promise.resolve({ ok: true })
      call += 1
      return call === 1 ? first : Promise.resolve(IDLE_STATUS)
    })
    let streamHandler: (() => void) | null = null
    ;(window as { electronAPI?: unknown }).electronAPI = {
      invoke,
      on: (channel: string, cb: () => void) => {
        if (channel === 'activity:stream') streamHandler = cb
        return () => {}
      },
    }
    render(<ActivityMonitor />)
    await flush()
    expect(heartbeatCalls(invoke)).toBe(1) // 首拉在飞（pending）

    // SSE 事件 → 200ms 后 kick，此刻首拉仍在飞：不并发启动第二条链
    await act(async () => {
      streamHandler!()
      await vi.advanceTimersByTimeAsync(200)
    })
    expect(heartbeatCalls(invoke)).toBe(1)

    // 首拉完成 → 在飞期间标记的补拉立即执行（0ms，终态即时性不丢）
    await act(async () => {
      resolveFirst(IDLE_STATUS)
      await Promise.resolve()
    })
    await flush() // finally 完成，0ms 补拉 timer 已注册
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    await flush()
    expect(heartbeatCalls(invoke)).toBe(2)

    // 推进一个空闲周期（15s）：单链恰 +1（分叉实现会双链各拉一次 → 4）
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15000)
    })
    await flush()
    expect(heartbeatCalls(invoke)).toBe(3)
  })
  it('transfer active → 5s heartbeat (2.9 §7：导出/导入纳入统一活动模型，不降为二等活动)', async () => {
    const invoke = vi.fn((channel: string) =>
      channel === 'activities:getStatus'
        ? Promise.resolve({
            active: [{ entity_type: 'project_import', project_id: 'p1' }],
            notifications: { unread: [], recent: [] },
          } as unknown as ActivityStatus)
        : Promise.resolve({ ok: true }),
    )
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
    render(<ActivityMonitor />)
    await flush()
    expect(heartbeatCalls(invoke)).toBe(1)
    // summary 七字段全零但 active 含 transfer → 活跃间隔 5s（4.9s 不拉，5s 整点拉）
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4999)
    })
    await flush()
    expect(heartbeatCalls(invoke)).toBe(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })
    await flush()
    expect(heartbeatCalls(invoke)).toBe(2)
  })

  it('ppt awaiting_input → 5s heartbeat（2.3 §7：summary.active_ppts 参与活跃判定）', async () => {
    // PPT 门禁待确认时用户需尽快看到：active_ppts 计数独立参与判定（纵深：
    // 即使 active 列表瞬时为空，计数不为零仍保持快轮询）
    activitySummary.active_ppts = 1
    const invoke = wireInvoke()
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
    render(<ActivityMonitor />)
    await flush()
    expect(heartbeatCalls(invoke)).toBe(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4999)
    })
    await flush()
    expect(heartbeatCalls(invoke)).toBe(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })
    await flush()
    expect(heartbeatCalls(invoke)).toBe(2)
  })

  /** OS 通知用例（2.11 §4）专用构造：第二轮拉取时注入新终态条目 */
  const wireWithNotice = (notice: Record<string, unknown>, settings?: Record<string, unknown>) => {
    let call = 0
    return vi.fn((channel: string) => {
      if (channel === 'settings:get') return Promise.resolve({ ok: true, ...settings })
      if (channel !== 'activities:getStatus') return Promise.resolve({ ok: true })
      call += 1
      return Promise.resolve(
        call === 1
          ? IDLE_STATUS
          : { notifications: { unread: [], recent: [notice] } } as unknown as ActivityStatus,
      )
    })
  }
  const osCalls = (invoke: ReturnType<typeof vi.fn>) =>
    invoke.mock.calls.filter((c) => c[0] === 'app:osNotify')
  /** 推进到第二轮拉取；hidden 用例注意：不可见降频 45s，需传 45000 */
  const secondRound = async (delay = 15000) => {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(delay)
    })
    await flush()
  }

  it('sends OS notification for fresh notices when window hidden (2.11 §4)', async () => {
    // 不可见/失焦 = 消息黑洞 → OS 通知补位；title 走 i18n 渲染、body=detail、
    // nav 原样携带（main 点击后回流 navigateToNotification）、默认带声
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true })
    const notice = {
      id: 7, entity_type: 'report', kind: 'completed', entity_title: 'R',
      detail: 'd', project_id: 'p1', container: 'project',
    }
    const invoke = wireWithNotice(notice)
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
    render(<ActivityMonitor />)
    await flush()
    expect(osCalls(invoke)).toHaveLength(0) // 首拉仅建基线
    await secondRound(45000) // hidden → 不可见降频 45s
    const calls = osCalls(invoke)
    expect(calls).toHaveLength(1)
    expect(calls[0][1]).toMatchObject({
      title: 'activity.report.completed',
      body: 'd',
      silent: false,
      nav: notice,
    })
  })

  it('skips OS notification when window focused (Toast only, 2.11 D3)', async () => {
    // jsdom 无焦点语义（hasFocus() 恒 false），显式 mock 聚焦态：
    // 前台可见 = Toast 足够，不重复弹 OS 通知
    Object.defineProperty(document, 'hasFocus', { configurable: true, value: () => true })
    const notice = { id: 7, entity_type: 'report', kind: 'completed', entity_title: 'R' }
    const invoke = wireWithNotice(notice)
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
    render(<ActivityMonitor />)
    await flush()
    await secondRound()
    expect(osCalls(invoke)).toHaveLength(0)
  })

  it('respects os_notify_enabled=false (2.11 §2.3)', async () => {
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true })
    const notice = { id: 7, entity_type: 'report', kind: 'completed', entity_title: 'R' }
    const invoke = wireWithNotice(notice, { os_notify_enabled: false })
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
    render(<ActivityMonitor />)
    await flush()
    await secondRound(45000)
    expect(osCalls(invoke)).toHaveLength(0)
  })

  it('respects os_notify_sound=false → silent OS notification (2.11 §2.3)', async () => {
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true })
    const notice = { id: 7, entity_type: 'report', kind: 'completed', entity_title: 'R' }
    const invoke = wireWithNotice(notice, { os_notify_sound: false })
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
    render(<ActivityMonitor />)
    await flush()
    await secondRound(45000)
    expect(osCalls(invoke)).toHaveLength(1)
    expect(osCalls(invoke)[0][1].silent).toBe(true)
  })

  it('re-reads os prefs on app:settings-changed DOM event (review C-1: 保存后立即生效)', async () => {
    // 设置保存 → SettingsPage doSave 派发的是 window DOM 事件：监听端必须同在
    // window 事件系统（曾误用 electronAPI.on 监听永不触发的 IPC 通道，开关
    // 保存后不生效直到重启）
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true })
    let enabled = true
    let call = 0
    const notice = { id: 7, entity_type: 'report', kind: 'completed', entity_title: 'R' }
    const invoke = vi.fn((channel: string) => {
      if (channel === 'settings:get') return Promise.resolve({ ok: true, os_notify_enabled: enabled })
      if (channel !== 'activities:getStatus') return Promise.resolve({ ok: true })
      call += 1
      return Promise.resolve(
        call === 1
          ? IDLE_STATUS
          : { notifications: { unread: [], recent: [notice] } } as unknown as ActivityStatus,
      )
    })
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
    render(<ActivityMonitor />)
    await flush()
    // 用户在设置页关闭 OS 通知并保存 → DOM 事件到达 → 重读开关
    await act(async () => {
      enabled = false
      window.dispatchEvent(new Event('app:settings-changed'))
    })
    await flush()
    expect(invoke.mock.calls.filter((c) => c[0] === 'settings:get')).toHaveLength(2)
    // 之后新通知到达不再发 OS 通知（无需重启应用）
    await secondRound(45000)
    expect(osCalls(invoke)).toHaveLength(0)
  })

  it('batches >5 fresh notices into one summary OS notification (2.11 §6.4)', async () => {
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true })
    let call = 0
    const notices = Array.from({ length: 6 }, (_, i) => ({
      id: i + 1, entity_type: 'report', kind: 'completed',
      entity_title: `R${i + 1}`, detail: null, project_id: 'p1', container: 'project',
    }))
    const invoke = vi.fn((channel: string) => {
      if (channel === 'settings:get') return Promise.resolve({ ok: true })
      if (channel !== 'activities:getStatus') return Promise.resolve({ ok: true })
      call += 1
      return Promise.resolve(
        call === 1
          ? IDLE_STATUS
          : { notifications: { unread: [], recent: notices } } as unknown as ActivityStatus,
      )
    })
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
    render(<ActivityMonitor />)
    await flush()
    await secondRound(45000)
    const calls = osCalls(invoke)
    expect(calls).toHaveLength(1)
    expect(calls[0][1]).toMatchObject({ title: 'activity.batch', body: '', nav: null })
  })

  it('governance notice → 广播 app:governance-done（M-2 信号链）', async () => {
    // 治理完成通知 → 广播给已打开的 Wiki 体检视图/编译详情面板重载（通知
    // 点击导航只是第二路径）；此前无用例，与批量补口用例成对锁定两条路径
    const notice = {
      id: 21, entity_type: 'wiki_compile', kind: 'governance', entity_title: '知识册',
      detail: null, project_id: 'p1', container: 'project',
    }
    const invoke = wireWithNotice(notice)
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
    const events: CustomEvent[] = []
    vi.spyOn(window, 'dispatchEvent').mockImplementation((e) => {
      if ((e as CustomEvent).type === 'app:governance-done') events.push(e as CustomEvent)
      return true
    })
    render(<ActivityMonitor />)
    await flush()
    await secondRound()
    expect(events).toHaveLength(1)
    expect(events[0].detail).toEqual({ projectId: 'p1' })
  })

  it('批量通知含治理条目 → batch 分支提前 return 仍广播（复审补口 2026-10-27）', async () => {
    // 回归：治理广播原本挂在逐条 Toast 循环里，>5 条批量时提前 return 被跳过
    // ——信号不依赖 Toast，必须先于批量分支派发，否则开着体检视图看不到治理结果
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true })
    const notices = [
      ...Array.from({ length: 5 }, (_, i) => ({
        id: i + 1, entity_type: 'report', kind: 'completed',
        entity_title: `R${i + 1}`, detail: null, project_id: 'p1', container: 'project',
      })),
      {
        id: 6, entity_type: 'wiki_compile', kind: 'governance', entity_title: '知识册',
        detail: null, project_id: 'p9', container: 'project',
      },
    ]
    let call = 0
    const invoke = vi.fn((channel: string) => {
      if (channel === 'settings:get') return Promise.resolve({ ok: true })
      if (channel !== 'activities:getStatus') return Promise.resolve({ ok: true })
      call += 1
      return Promise.resolve(
        call === 1
          ? IDLE_STATUS
          : { notifications: { unread: [], recent: notices } } as unknown as ActivityStatus,
      )
    })
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
    const events: CustomEvent[] = []
    vi.spyOn(window, 'dispatchEvent').mockImplementation((e) => {
      if ((e as CustomEvent).type === 'app:governance-done') events.push(e as CustomEvent)
      return true
    })
    render(<ActivityMonitor />)
    await flush()
    await secondRound(45000)
    // batch 汇总 Toast 照发；治理广播不缺席（项目 id 透传）
    expect(sharedMocks.showToast).toHaveBeenCalledWith('activity.batch', 'info')
    expect(events).toHaveLength(1)
    expect(events[0].detail).toEqual({ projectId: 'p9' })
  })

  it('R4: schedule_run completed → 静默（不弹 Toast/OS，仅 markRead）', async () => {
    // 2.11 §5.5 R4：订阅完成默认 silent——通知中心仍可见，静默已读；
    // 管理台/收件箱红点与徽章计数是完成态的消费入口，不靠 Toast
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true })
    const notice = {
      id: 7, entity_type: 'schedule_run', kind: 'completed',
      entity_title: '订阅甲', detail: null,
    }
    const invoke = wireWithNotice(notice)
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
    render(<ActivityMonitor />)
    await flush()
    await secondRound(45000)
    expect(sharedMocks.showToast).not.toHaveBeenCalled()
    expect(osCalls(invoke)).toHaveLength(0)
    expect(sharedMocks.markRead).toHaveBeenCalledWith([7]) // 静默已读不缺席
  })

  it('R4: schedule_notify=true → completed 照弹（review P2-7 设置项提前落地）', async () => {
    // 用户在设置页开启「订阅完成通知」→ 默认静默解除：completed 走正常
    // Toast/OS 通道（success 绿）；保存后 app:settings-changed 重读即生效
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true })
    const notice = {
      id: 9, entity_type: 'schedule_run', kind: 'completed',
      entity_title: '订阅乙', detail: null,
    }
    const invoke = wireWithNotice(notice, { schedule_notify: true })
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
    render(<ActivityMonitor />)
    await flush()
    await secondRound(45000)
    expect(sharedMocks.showToast).toHaveBeenCalledTimes(1)
    expect(sharedMocks.showToast.mock.calls[0][0]).toBe('activity.schedule_run.completed')
    expect(sharedMocks.showToast.mock.calls[0][1]).toBe('success')
    expect(osCalls(invoke)).toHaveLength(1)
    expect(sharedMocks.markRead).toHaveBeenCalledWith([9])
  })

  it('R4: schedule_run failed → 照弹 Toast（失败/熔断必弹）', async () => {
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true })
    const notice = {
      id: 8, entity_type: 'schedule_run', kind: 'failed',
      entity_title: '订阅甲', detail: '连续 3 次失败已自动暂停',
    }
    const invoke = wireWithNotice(notice)
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
    render(<ActivityMonitor />)
    await flush()
    await secondRound(45000)
    expect(sharedMocks.showToast).toHaveBeenCalledTimes(1)
    expect(sharedMocks.showToast.mock.calls[0][0]).toBe('activity.schedule_run.failed')
    expect(sharedMocks.showToast.mock.calls[0][1]).toBe('error')
    expect(osCalls(invoke)).toHaveLength(1)
  })

  it('file completed (project) → action「整理进知识页」直达 wiki 编译（无培训整改）', async () => {
    // 项目文件解析完成 Toast 的 action 从「查看」升级：label 改 completedAction，
    // 点击派发 navigate-project-tab 携 wikiFocus='compile'，与脉搏带待编译 chip 同链；
    // 缝合「上传→解析完成→不知道下一步」动线断点
    const notice = {
      id: 11, entity_type: 'file', kind: 'completed', entity_title: '招股书.pdf',
      detail: null, project_id: 'p1', container: 'project',
    }
    const invoke = wireWithNotice(notice)
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
    // 只捕获导航事件（新终态同时派发 app:usage-changed 第三发信点，非本用例关注）
    const events: CustomEvent[] = []
    vi.spyOn(window, 'dispatchEvent').mockImplementation((e) => {
      if ((e as CustomEvent).type === 'app:navigate-project-tab') events.push(e as CustomEvent)
      return true
    })
    render(<ActivityMonitor />)
    await flush()
    await secondRound()
    expect(sharedMocks.showToast).toHaveBeenCalledTimes(1)
    const opts = sharedMocks.showToast.mock.calls[0][2]
    expect(opts.action.label).toBe('activity.file.completedAction')
    opts.action.onClick()
    expect(events).toHaveLength(1)
    expect(events[0].detail).toEqual({ projectId: 'p1', tab: 'wiki', container: 'project', wikiFocus: 'compile' })
  })

  it('file completed (baselib) → 保持「前往查看」落素材 Tab（基础库不参与编译）', async () => {
    // 基础库文件无 Wiki 编译场景，特例不命中：通用导航语义不变
    const notice = {
      id: 12, entity_type: 'file', kind: 'completed', entity_title: '政策.pdf',
      detail: null, project_id: 'b1', container: 'baselib',
    }
    const invoke = wireWithNotice(notice)
    ;(window as { electronAPI?: unknown }).electronAPI = { invoke, on: () => () => {} }
    // 只捕获导航事件（同上，过滤 app:usage-changed）
    const events: CustomEvent[] = []
    vi.spyOn(window, 'dispatchEvent').mockImplementation((e) => {
      if ((e as CustomEvent).type === 'app:navigate-project-tab') events.push(e as CustomEvent)
      return true
    })
    render(<ActivityMonitor />)
    await flush()
    await secondRound()
    expect(sharedMocks.showToast).toHaveBeenCalledTimes(1)
    const opts = sharedMocks.showToast.mock.calls[0][2]
    expect(opts.action.label).toBe('activity.view')
    opts.action.onClick()
    expect(events).toHaveLength(1)
    expect(events[0].detail).toEqual({ projectId: 'b1', tab: 'materials', container: 'baselib' })
  })
})

describe('navigateToNotification', () => {
  /** 构造最小通知对象（导航仅读 entity_type/project_id/container） */
  const notice = (over: Partial<NotificationItem>): NotificationItem => ({
    id: 1,
    entity_type: 'report',
    entity_id: 'r1',
    project_id: 'p1',
    kind: 'completed',
    entity_title: 't',
    detail: null,
    read_at: null,
    created_at: '',
    ...over,
  })
  /** 拦截 window.dispatchEvent，记录派发的 CustomEvent */
  const captureEvents = () => {
    const events: CustomEvent[] = []
    vi.spyOn(window, 'dispatchEvent').mockImplementation((e) => {
      events.push(e as CustomEvent)
      return true
    })
    return events
  }

  it('form_template → 模板管理页（全局资产，无 project_id 也可导航）', () => {
    const events = captureEvents()
    navigateToNotification(notice({ entity_type: 'form_template', project_id: null }))
    expect(events).toHaveLength(1)
    expect(events[0].type).toBe('app:navigate-template-management')
  })

  it('form_fill → 项目 forms Tab（app:navigate-project-tab）', () => {
    const events = captureEvents()
    navigateToNotification(notice({ entity_type: 'form_fill', project_id: 'p9' }))
    expect(events).toHaveLength(1)
    expect(events[0].type).toBe('app:navigate-project-tab')
    expect(events[0].detail).toEqual({ projectId: 'p9', tab: 'forms', container: 'project' })
  })

  it('project_export → 项目 materials Tab（2.9 §8，落点同首页进行中卡片）', () => {
    const events = captureEvents()
    navigateToNotification(notice({ entity_type: 'project_export', project_id: 'p1' }))
    expect(events).toHaveLength(1)
    expect(events[0].type).toBe('app:navigate-project-tab')
    expect(events[0].detail).toEqual({ projectId: 'p1', tab: 'materials', container: 'project' })
  })

  it('project_import → 新项目 materials Tab（导入完成查看素材；project_id 为空时不导航）', () => {
    const events = captureEvents()
    navigateToNotification(notice({ entity_type: 'project_import', project_id: 'p-new' }))
    expect(events).toHaveLength(1)
    expect(events[0].type).toBe('app:navigate-project-tab')
    expect(events[0].detail).toEqual({ projectId: 'p-new', tab: 'materials', container: 'project' })
    // 失败导入（新项目行未建成，project_id 为空）：无落点可跳，不误导
    const noTarget = captureEvents()
    navigateToNotification(notice({ entity_type: 'project_import', project_id: null }))
    expect(noTarget).toHaveLength(0)
  })

  it('affair → 项目 affairs Tab 并携带 taskId（2.11 §3.2；project_id 为空时不导航）', () => {
    const events = captureEvents()
    navigateToNotification(notice({ entity_type: 'affair', entity_id: 'pt1', project_id: 'p1', kind: 'due' }))
    expect(events).toHaveLength(1)
    expect(events[0].type).toBe('app:navigate-project-tab')
    expect(events[0].detail).toEqual({ projectId: 'p1', tab: 'affairs', container: 'project', taskId: 'pt1' })
    // 无落点（project_id 为空）：不误导导航
    const noTarget = captureEvents()
    navigateToNotification(notice({ entity_type: 'affair', entity_id: 'pt2', project_id: null }))
    expect(noTarget).toHaveLength(0)
  })

  it('schedule_run → 订阅管理台（app:navigate-schedules，全局资产无项目）', () => {
    // 2.11 §5.5：订阅是全局资产不隶属项目，点击完成/失败通知固定开管理台
    const events = captureEvents()
    navigateToNotification(notice({ entity_type: 'schedule_run', project_id: null }))
    expect(events).toHaveLength(1)
    expect(events[0].type).toBe('app:navigate-schedules')
  })
})
