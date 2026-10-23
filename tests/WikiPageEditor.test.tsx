/** tests/WikiPageEditor.test.tsx — Wiki 页面编辑器回归（2026-10-23 查找接入 + i18n 化）
 *  契约：
 *  - 保存（wiki:editPage）：空内容 → 错误提示不发请求；有内容 → invoke 携带
 *    projectId/pageKey/content/title（标题未改传 null）→ onSaved 回调。
 *  - Ctrl+F 编辑器内查找（textarea 选中式）：打开查找条 → 输入关键词 → textarea
 *    选区定位到首个匹配（selectionStart/End 为字符偏移）+ 命中计数正确。
 *  - i18n：标题/正文 placeholder 经 t() 渲染（原硬编码中文清理）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    // 简单插值：真实 i18next 会替换 {{var}}，mock 需同语义（字数计数断言依赖插值）
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

import { render, fireEvent, cleanup, act } from '@testing-library/react'
import { WikiPageEditor } from '../src/features/project/tabs/WikiPageEditor'

const invokeMock = vi.fn()

const makePage = () => ({
  page_key: 'entity_acme',
  title: 'Acme 公司',
  content: '---\ntype: entity\n---\n\n# Acme\n第一段正文\n\n第二段正文',
})

/** 排空 microtask（保存链路 await invoke） */
const flush = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => {
      await Promise.resolve()
    })
  }
}

const setup = () => {
  const onSaved = vi.fn()
  const onBack = vi.fn()
  const utils = render(
    <WikiPageEditor projectId="p1" page={makePage()} onBack={onBack} onSaved={onSaved} />,
  )
  return { onSaved, onBack, ...utils }
}

beforeEach(() => {
  invokeMock.mockReset()
  invokeMock.mockResolvedValue({ ok: true })
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  cleanup()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

describe('WikiPageEditor save (wiki:editPage)', () => {
  it('空内容保存显示错误且不发请求；i18n placeholder 渲染', () => {
    const { container, getByText, getByPlaceholderText } = setup()

    // i18n 化：标题/正文 placeholder 经 t() 渲染（mock 返回 key 本身）
    expect(getByPlaceholderText('project.wiki.editorTitlePlaceholder')).toBeTruthy()
    const ta = container.querySelector('textarea') as HTMLTextAreaElement
    expect(ta).toBeTruthy()
    expect(ta.getAttribute('placeholder')).toBe('project.wiki.editorContentPlaceholder')

    // 清空 → 保存按钮激活（hasChanges）→ 点击 → 错误提示、无 invoke
    fireEvent.change(ta, { target: { value: '' } })
    fireEvent.click(getByText('project.wiki.save'))
    expect(getByText('project.wiki.editorEmptyContent')).toBeTruthy()
    expect(invokeMock).not.toHaveBeenCalledWith('wiki:editPage', expect.anything())
  })

  it('有内容保存调用 editPage（标题未改传 null）并回调 onSaved', async () => {
    const { container, getByText, onSaved } = setup()
    const ta = container.querySelector('textarea') as HTMLTextAreaElement

    fireEvent.change(ta, { target: { value: '# 新正文' } })
    fireEvent.click(getByText('project.wiki.save'))
    await flush()

    expect(invokeMock).toHaveBeenCalledWith('wiki:editPage', {
      projectId: 'p1',
      pageKey: 'entity_acme',
      content: '# 新正文',
      title: null,
    })
    expect(onSaved).toHaveBeenCalledTimes(1)
  })
})

describe('WikiPageEditor in-page find (Ctrl+F, textarea 选中式, 2026-10-23)', () => {
  it('Ctrl+F 打开查找条，输入关键词后 textarea 选区定位首个匹配', () => {
    const { container, getByPlaceholderText, getByText } = setup()

    fireEvent.keyDown(window, { ctrlKey: true, key: 'f' })
    const input = getByPlaceholderText('find.placeholder')
    // fixture content 中「第二段」出现 1 次 → 选区定位 + 计数 1/1
    fireEvent.change(input, { target: { value: '第二段' } })

    const ta = container.querySelector('textarea') as HTMLTextAreaElement
    const start = ta.value.indexOf('第二段')
    expect(ta.selectionStart).toBe(start)
    expect(ta.selectionEnd).toBe(start + '第二段'.length)
    expect(getByText('1/1')).toBeTruthy()
  })

  it('无命中时计数为 0/0 且 textarea 选区不变', () => {
    const { container, getByPlaceholderText, getByText } = setup()

    fireEvent.keyDown(window, { ctrlKey: true, key: 'f' })
    const input = getByPlaceholderText('find.placeholder')
    fireEvent.change(input, { target: { value: '不存在的词' } })

    const ta = container.querySelector('textarea') as HTMLTextAreaElement
    expect(ta.selectionStart).toBe(0)
    expect(ta.selectionEnd).toBe(0)
    expect(getByText('0/0')).toBeTruthy()
  })
})
