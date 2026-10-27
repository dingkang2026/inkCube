/**
 * tests/Markdown.test.tsx — 共享 Markdown 组件行级 HTML 管线回归（2026-10-27）。
 *
 * 背景：ReportPreview 视觉尾注（2.5 §1.3/§3.7）把正文 [n] 注入为 <sup>[n]</sup>，
 * 含 sup 的行走 InlineHtmlLine 管线。code review 修复两处并在此锁定：
 *   1. 纯段落行包 <p>——此前裸 fragment：相邻引用段落粘连 + Ctrl+F
 *      叶子块（FIND_BLOCK_SELECTOR 含 p）不可见；chat/wiki 内联 HTML 行同惠。
 *   2. <sup> 进入 token 管线前编码为私有区占位符——避免 sup 标签切开
 *      加粗与行内 code 的配对区间（此前字面星号）。
 */
import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { Markdown } from '../src/components/Markdown'

/** 与 ReportPreview.applyCitationSuperscript 产出一致的转换后形态 */
const cite = (n: number) => `<sup>[${n}]</sup>`

describe('Markdown citation paragraphs (visual endnote pipeline)', () => {
  it('wraps a citation paragraph in <p> so find leaf blocks can match it', () => {
    const { container } = render(<Markdown content={`结论依据材料${cite(5)}与${cite(6)}。`} />)
    const p = container.querySelector('.markdown > p')
    expect(p).toBeTruthy()
    expect(p!.querySelectorAll('sup')).toHaveLength(2)
    expect(p!.textContent).toContain('结论依据材料[5]与[6]。')
  })

  it('renders consecutive citation paragraphs as separate <p> (no merging)', () => {
    const { container } = render(
      <Markdown content={`段落一${cite(5)}。\n\n段落二${cite(6)}。`} />,
    )
    const ps = container.querySelectorAll('.markdown > p')
    expect(ps.length).toBe(2)
    expect(ps[0].querySelector('sup')?.textContent).toBe('[5]')
    expect(ps[1].querySelector('sup')?.textContent).toBe('[6]')
  })

  it('keeps bold pairing across an embedded citation (placeholder protocol)', () => {
    // 转换后形态：**核心<sup>[5]</sup>观点** —— sup 不得切开 ** 配对成字面星号
    const { container } = render(<Markdown content={`**核心${cite(5)}观点**陈述。`} />)
    const strong = container.querySelector('strong')
    expect(strong).toBeTruthy()
    expect(strong!.textContent).toBe('核心[5]观点')
    expect(strong!.querySelector('sup')).toBeTruthy()
    expect(container.textContent).not.toContain('**')
  })

  it('keeps inline code pairing and restores literal [n] inside code', () => {
    const { container } = render(<Markdown content={`型号\`A-${cite(5)}\`说明。`} />)
    const code = container.querySelector('code')
    expect(code).toBeTruthy()
    expect(code!.textContent).toBe('A-[5]')
    expect(code!.querySelector('sup')).toBeNull()
    expect(container.textContent).not.toContain('`')
  })

  it('renders headings with citations as h-tags (prefix branch unchanged)', () => {
    const { container } = render(<Markdown content={`## 结论${cite(5)}`} />)
    const h2 = container.querySelector('h2')
    expect(h2).toBeTruthy()
    expect(h2!.querySelector('sup')?.textContent).toBe('[5]')
  })
})

describe('Markdown existing behaviours (chat/wiki regression baseline)', () => {
  it('renders plain markdown paragraphs via ReactMarkdown unchanged', () => {
    const { container } = render(<Markdown content={'普通段落，无内联 HTML。'} />)
    const p = container.querySelector('.markdown > p')
    expect(p).toBeTruthy()
    expect(p!.textContent).toBe('普通段落，无内联 HTML。')
    expect(p!.querySelector('sup')).toBeNull()
  })

  it('keeps authored inline span with safe style rendering (now inside <p>)', () => {
    const { container } = render(
      <Markdown content={'<span style="color: red">警告文本</span>'} />,
    )
    const span = container.querySelector('span')
    expect(span).toBeTruthy()
    expect(span!.textContent).toBe('警告文本')
    // 包 <p> 后 span 位于段落内（段落语义修复的连带断言）
    expect(span!.closest('p')).toBeTruthy()
  })
})
