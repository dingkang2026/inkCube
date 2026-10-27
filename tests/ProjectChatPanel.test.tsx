/**
 * ProjectChatPanel markdown 渲染回归测试。
 *
 * 背景：chat 面板曾使用手写逐行渲染器（features/knowledge/markdown.ts，
 * 已删除），仅支持表格/无序列表/标题/段落/粗体/斜体/行内代码，且父容器
 * white-space: pre-wrap 被继承导致双倍行距；LLM 高频输出（围栏代码块、
 * 有序列表、链接、引用块）全部退化为纯文本、``` 标记裸露。
 * 现统一到 components/Markdown（react-markdown + remark-gfm），
 * 本测试锁定 chat 场景的渲染能力不回退。
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent, act } from '@testing-library/react'
import React from 'react'
import { ProjectChatPanel } from '../src/features/project/ProjectChatPanel'

const invokeMock = vi.fn()

// jsdom 未实现 scrollIntoView（messagesEndRef 自动滚动依赖）
beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn()
  invokeMock.mockReset()
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
})

afterEach(() => {
  cleanup()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

/** 打开面板 → 输入问题 → Enter 发送 → 等待回答渲染，返回渲染 HTML */
async function askAndRender(answer: string): Promise<string> {
  invokeMock.mockResolvedValue({ answer, sources: [] })
  const { container } = render(
    <ProjectChatPanel projectId="p1" projectName="测试项目" isOpen={true} />,
  )
  const input = screen.getByPlaceholderText('knowledge.chatInputPlaceholder')
  await act(async () => {
    fireEvent.change(input, { target: { value: '测试问题' } })
  })
  await act(async () => {
    fireEvent.keyDown(input, { key: 'Enter' })
  })
  await act(async () => {})
  return container.innerHTML
}

describe('ProjectChatPanel assistant markdown rendering', () => {
  it('renders fenced code blocks as <pre><code> without bare ``` markers', async () => {
    const html = await askAndRender('示例：\n```python\nprint("hi")\n```\n完毕')
    expect(html).toContain('<pre>')
    expect(html).toContain('<code')
    expect(html).toContain('print("hi")')
    expect(html).not.toContain('```')
  })

  it('renders ordered lists as <ol><li>', async () => {
    const html = await askAndRender('步骤：\n\n1. 第一步\n2. 第二步')
    expect(html).toContain('<ol>')
    expect(html).toContain('<li>第一步</li>')
    expect(html).toContain('<li>第二步</li>')
  })

  it('renders links as clickable <a>', async () => {
    const html = await askAndRender('参考 [文档](https://example.com/doc)')
    expect(html).toContain('<a href="https://example.com/doc"')
    expect(html).toContain('文档</a>')
  })

  it('renders blockquotes as <blockquote>', async () => {
    const html = await askAndRender('引用：\n\n> 原文内容')
    expect(html).toContain('<blockquote>')
    expect(html).toContain('原文内容')
  })

  it('keeps tables and bold rendering after unification (regression)', async () => {
    const html = await askAndRender(
      '| 名称 | 值 |\n| --- | --- |\n| a | 1 |\n\n**加粗** 与 *斜体*',
    )
    expect(html).toContain('<table>')
    expect(html).toContain('<th>名称</th>')
    expect(html).toContain('<td>1</td>')
    expect(html).toContain('<strong>加粗</strong>')
    expect(html).toContain('<em>斜体</em>')
  })
})

describe('ProjectChatPanel backdrop close', () => {
  /** 模板管理 drawer 同款：展开时点击面板外任意处（backdrop）收起 */

  it('reports close when clicking the backdrop (controlled mode)', () => {
    const onClose = vi.fn()
    const { container } = render(
      <ProjectChatPanel projectId="p1" projectName="P" isOpen={true} onClose={onClose} />,
    )
    const backdrop = container.querySelector('[class*="backdrop"]')
    expect(backdrop).toBeTruthy()
    fireEvent.click(backdrop as Element)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('closes the uncontrolled panel from backdrop click and restores the toggle', () => {
    const { container } = render(<ProjectChatPanel projectId="p1" projectName="P" />)
    // toggle 打开 → backdrop 出现；点 backdrop → 面板与 backdrop 消失
    fireEvent.click(container.querySelector('[class*="toggleTab"]') as Element)
    expect(container.querySelector('[class*="backdrop"]')).toBeTruthy()
    fireEvent.click(container.querySelector('[class*="backdrop"]') as Element)
    expect(container.querySelector('[class*="panel"]')).toBeNull()
    expect(container.querySelector('[class*="backdrop"]')).toBeNull()
    expect(container.querySelector('[class*="toggleTab"]')).toBeTruthy()
  })
})
