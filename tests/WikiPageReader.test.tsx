/** tests/WikiPageReader.test.tsx — 阅读器页面动作回归（2.6 §5.3/§7.1/§7.2.4）
 *  契约（本轮孤儿通道接线）：
 *  - 删除（wiki:deletePage）：header「删除」→ 自建确认框（不可撤销警示）→ 确认 →
 *    后端级联清理 → onDeleted(page_key) 回调父级关预览 + toast 成功。
 *  - 富化（wiki:enrichWikilinks）：links_added>0 → 成功 toast + onRefresh（父级重拉
 *    pageDetail）；links_added=0 → info 提示且不触发刷新。
 *  - 版本历史（wiki:listVersions/wiki:getVersion）：details 展开懒加载列表，
 *    行内「查看」→ 只读快照渲染；再点收起。
 *  2026-10-16 review 修复轮：快照连点序号守卫（晚到的旧版本响应不覆盖新快照）；
 *  切页经父级 key=page_key 逐页重挂载（2.6 §10.2，版本/快照/确认框随实例复位）。
 *  2026-10-23：Ctrl+F 文档内查找接入（公共基座 useMarkdownBlockFind + FindBar，
 *  范围限正文；Markdown mock 同步改为 p 包裹使叶子块可命中。 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'

const toastMock = vi.hoisted(() => vi.fn())
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    // 简单插值：真实 i18next 会替换 {{var}}，mock 需同语义（toast 断言依赖插值结果）
    t: (key: string, opts?: Record<string, unknown>) => {
      const raw = (opts?.defaultValue as string) ?? key
      return Object.entries(opts ?? {}).reduce(
        (acc, [k, v]) => acc.split(`{{${k}}}`).join(String(v)),
        raw,
      )
    },
    i18n: { language: 'zh-CN' },
  }),
}))
vi.mock('../src/features/project/tabs/WikiTab.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))
vi.mock('../src/features/knowledge/Toast', () => ({
  useToast: () => toastMock,
}))
vi.mock('../src/components/Markdown', () => ({
  // p 叶子块包裹（FIND_BLOCK_SELECTOR 匹配 p/h1-h6/li/...；纯 div 无叶子块会使
  // 查找永远 0 命中）；textContent 不变，既有用例断言不受影响
  Markdown: ({ content }: { content: string }) =>
    React.createElement('p', { 'data-testid': 'md' }, content),
}))

import { render, fireEvent, cleanup, act } from '@testing-library/react'
import { WikiPageReader } from '../src/features/project/tabs/WikiPageReader'

const invokeMock = vi.fn()

const makePageDetail = () => ({
  page: {
    id: 'pg1', page_key: 'entity_acme', title: 'Acme 公司',
    content: '---\ntype: entity\n---\n\n# Acme\n正文内容',
    status: 'compiled', compile_version: 2, page_type: 'entity',
    refs: [], sources: [], tags: [],
    last_compiled_at: '2026-10-10T00:00:00Z', updated_at: '2026-10-10T00:00:00Z',
  },
  sources: [],
  change_log: [],
})

const versions = [
  { id: 'v2', version: 2, change_summary: '用户编辑', created_at: '2026-10-10T00:00:00Z' },
  { id: 'v1', version: 1, change_summary: '初始编译', created_at: '2026-10-09T00:00:00Z' },
]

/** 排空 microtask（backlinks/versions 等异步拉取 + 动作链多次 await） */
const flush = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => {
      await Promise.resolve()
    })
  }
}

const setup = (overrides: Partial<Record<string, unknown>> = {}) => {
  const onDeleted = vi.fn()
  const onRefresh = vi.fn()
  const utils = render(
    <WikiPageReader
      projectId="p1"
      pageDetail={makePageDetail() as never}
      onEdit={() => {}}
      onNavigateToPage={() => {}}
      onDeleted={onDeleted}
      onRefresh={onRefresh}
    />,
  )
  return { onDeleted, onRefresh, ...utils }
}

beforeEach(() => {
  toastMock.mockReset()
  invokeMock.mockReset()
  invokeMock.mockImplementation(async (channel: string) => {
    if (channel === 'wiki:getBacklinks') return { backlinks: [] }
    if (channel === 'wiki:listVersions') return { versions, current_version: 2 }
    if (channel === 'wiki:getVersion') {
      return { version: { ...versions[0], content: '# Acme v2 历史快照' } }
    }
    if (channel === 'wiki:enrichWikilinks') return { success: true, links_added: 2 }
    return {}
  })
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  cleanup()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

describe('WikiPageReader delete page (§5.3)', () => {
  it('confirms, invokes deletePage and notifies the parent', async () => {
    const { onDeleted, getByText, container } = setup()
    await flush()

    // header「删除」→ 确认框出现（含页面标题警示文案）
    fireEvent.click(getByText('删除'))
    expect(container.querySelector('.confirmOverlay')).toBeTruthy()
    expect(getByText('删除页面')).toBeTruthy()

    // 确认框内 danger 按钮 → 级联删除 + 回调
    fireEvent.click(container.querySelector('.confirmDangerBtn')!)
    await flush()

    expect(invokeMock).toHaveBeenCalledWith('wiki:deletePage', { projectId: 'p1', pageKey: 'entity_acme' })
    expect(onDeleted).toHaveBeenCalledWith('entity_acme')
    expect(toastMock).toHaveBeenLastCalledWith('页面已删除', 'success')
  })

  it('closes the dialog without any write when cancelled', async () => {
    const { onDeleted, getByText, container } = setup()
    await flush()

    fireEvent.click(getByText('删除'))
    fireEvent.click(container.querySelector('.confirmCancelBtn')!)
    await flush()

    expect(invokeMock).not.toHaveBeenCalledWith('wiki:deletePage', expect.anything())
    expect(onDeleted).not.toHaveBeenCalled()
  })
})

describe('WikiPageReader enrich wikilinks (§7.2.4)', () => {
  it('toasts the added count and refreshes the detail when links were added', async () => {
    const { onRefresh, getByText } = setup()
    await flush()

    fireEvent.click(getByText('补全链接'))
    await flush()

    expect(invokeMock).toHaveBeenCalledWith('wiki:enrichWikilinks', { projectId: 'p1', pageKey: 'entity_acme' })
    expect(toastMock).toHaveBeenLastCalledWith('已补全 2 个链接', 'success')
    expect(onRefresh).toHaveBeenCalledTimes(1)
  })

  it('shows an info toast without refreshing when nothing was added', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'wiki:getBacklinks') return { backlinks: [] }
      if (channel === 'wiki:enrichWikilinks') return { success: true, links_added: 0 }
      return {}
    })
    const { onRefresh, getByText } = setup()
    await flush()

    fireEvent.click(getByText('补全链接'))
    await flush()

    expect(toastMock).toHaveBeenLastCalledWith('未发现可补全的链接', 'info')
    expect(onRefresh).not.toHaveBeenCalled()
  })
})

describe('WikiPageReader version history (§7.1)', () => {
  it('lazy-loads versions on expand and renders a read-only snapshot on view', async () => {
    const { getByText, getAllByText, container } = setup()
    await flush()

    expect(invokeMock).not.toHaveBeenCalledWith('wiki:listVersions', expect.anything())

    // jsdom 点击 summary 切换 open 但不派发 toggle 事件——手动置 open + 派发，
    // 与真实浏览器行为对齐（面板展开 → onToggle → 懒加载）
    const details = container.querySelector('details') as HTMLDetailsElement
    details.open = true
    fireEvent(details, new Event('toggle'))
    await flush()

    expect(invokeMock).toHaveBeenCalledWith('wiki:listVersions', {
      projectId: 'p1', pageKey: 'entity_acme', limit: 20,
    })
    expect(container.textContent).toContain('用户编辑')
    expect(container.textContent).toContain('初始编译')

    // 「查看」→ getVersion 全行 → 快照只读渲染（多行同名按钮，取首行 v2）
    fireEvent.click(getAllByText('查看')[0]!)
    await flush()

    expect(invokeMock).toHaveBeenCalledWith('wiki:getVersion', { projectId: 'p1', versionId: 'v2' })
    expect(container.querySelector('.versionSnapshot')).toBeTruthy()
    expect(container.textContent).toContain('历史快照')
  })

  it('shows the empty hint when the page has no versions', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'wiki:getBacklinks') return { backlinks: [] }
      if (channel === 'wiki:listVersions') return { versions: [], current_version: 0 }
      return {}
    })
    const { getByText, container } = setup()
    await flush()

    const details = container.querySelector('details') as HTMLDetailsElement
    details.open = true
    fireEvent(details, new Event('toggle'))
    await flush()

    expect(getByText('暂无版本记录')).toBeTruthy()
    expect(container.querySelector('.versionSnapshot')).toBeNull()
  })

  it('keeps the last clicked version when responses arrive out of order (连点守卫)', async () => {
    let resolveV1: (v: unknown) => void = () => {}
    const pendingV1 = new Promise((res) => { resolveV1 = res })
    invokeMock.mockImplementation(async (channel: string, args: { versionId?: string }) => {
      if (channel === 'wiki:getBacklinks') return { backlinks: [] }
      if (channel === 'wiki:listVersions') return { versions, current_version: 2 }
      if (channel === 'wiki:getVersion') {
        if (args?.versionId === 'v1') return pendingV1
        return { version: { ...versions[0], content: '# Acme v2 历史快照' } }
      }
      return {}
    })
    const { getAllByText, container } = setup()
    await flush()

    const details = container.querySelector('details') as HTMLDetailsElement
    details.open = true
    fireEvent(details, new Event('toggle'))
    await flush()

    // 先点 v1（挂起）再点 v2（立即返回）：以最后一次点击为准
    fireEvent.click(getAllByText('查看')[1]!) // v1 行
    await flush()
    fireEvent.click(getAllByText('查看')[0]!) // v2 行
    await flush()

    expect(container.querySelector('.versionSnapshot')).toBeTruthy()
    expect(container.textContent).toContain('v2 历史快照')

    // v1 响应晚到：序号守卫丢弃，不得覆盖 v2 快照
    resolveV1({ version: { ...versions[1], content: '# Acme v1 历史快照' } })
    await flush()
    expect(container.textContent).not.toContain('v1 历史快照')
    expect(container.textContent).toContain('v2 历史快照')
  })
})

describe('WikiPageReader in-page find (Ctrl+F, 2026-10-23)', () => {
  it('Ctrl+F 打开查找条，正文块命中计数与 Esc 关闭', async () => {
    const { container, queryByPlaceholderText, getByPlaceholderText, getByText } = setup()
    await flush()

    expect(queryByPlaceholderText('find.placeholder')).toBeNull()
    fireEvent.keyDown(window, { ctrlKey: true, key: 'f' })
    const input = getByPlaceholderText('find.placeholder')
    // fixture 正文 body = '# Acme\n正文内容'（Markdown mock 渲染为单一 p 叶子块），
    // 「正文」命中 1 块 → FindBar 计数 1/1；版本快照面板未展开不参与
    fireEvent.change(input, { target: { value: '正文' } })
    expect(getByText('1/1')).toBeTruthy()
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(queryByPlaceholderText('find.placeholder')).toBeNull()
    expect(container.querySelector('.versionSnapshot')).toBeNull()
  })

  it('无命中时计数为 0/0', async () => {
    const { queryByPlaceholderText, getByPlaceholderText, getByText } = setup()
    await flush()

    fireEvent.keyDown(window, { ctrlKey: true, key: 'f' })
    const input = getByPlaceholderText('find.placeholder')
    fireEvent.change(input, { target: { value: '不存在的词' } })
    expect(getByText('0/0')).toBeTruthy()
  })
})
