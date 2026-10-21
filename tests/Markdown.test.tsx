/**
 * Markdown.tsx sanitized inline HTML + block SVG (A+) 回归测试。
 *
 * 背景：_PROMPT_RICH_TEXT 引导 LLM 输出 <svg> 色卡与 <span style> 上色，
 * 但 react-markdown 默认不解析原始 HTML（XSS 安全默认），标签被当纯文本显示。
 * A+ 方案：块级 SVG 走 DOMParser 白名单清洗；行内标签走受控 React 元素
 * （文本永远经 React 转义，不拼接 HTML 字符串）。
 */
import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import React from 'react'
import { Markdown } from '../src/components/Markdown'

function renderToString(content: string): string {
  const { container } = render(<Markdown content={content} />)
  return container.innerHTML
}

describe('Markdown block SVG (sanitized)', () => {
  it('renders a whitelisted svg color card', () => {
    const html = renderToString(
      '前文\n<svg width="300" height="80" xmlns="http://www.w3.org/2000/svg">'
      + '<rect width="100" height="80" fill="#663399"/><text x="50" y="45" fill="white">rebeccapurple</text></svg>\n后文',
    )
    expect(html).toContain('<svg')
    expect(html).toContain('rect')
    expect(html).toContain('#663399')
    expect(html).toContain('rebeccapurple')
    expect(html).toContain('前文')
    expect(html).toContain('后文')
  })

  it('strips script elements, event handlers and foreignObject', () => {
    const html = renderToString(
      '<svg width="10" height="10"><script>alert(1)</script>'
      + '<rect width="10" height="10" fill="red" onclick="alert(2)"/>'
      + '<foreignObject><body>x</body></foreignObject></svg>',
    )
    expect(html).not.toContain('<script')
    expect(html).not.toContain('onclick')
    expect(html).not.toContain('alert')
    expect(html).not.toContain('foreignObject')
    expect(html).toContain('rect') // 图形本体保留
  })

  it('keeps whitelisted gradients and drops unknown elements', () => {
    const html = renderToString(
      '<svg width="10" height="10"><defs><linearGradient id="g">'
      + '<stop offset="0" stop-color="#663399"/></linearGradient></defs>'
      + '<iframe src="https://evil.example"></iframe>'
      + '<rect width="10" height="10" fill="url(#g)"/></svg>',
    )
    expect(html).toContain('linearGradient')
    expect(html).toContain('stop-color="')
    expect(html).toContain('id="g"') // 渐变引用 url(#g) 的锚点必须保留
    expect(html).not.toContain('iframe')
    expect(html).not.toContain('evil.example')
  })

  it('falls back to raw source for unclosed mid-stream svg', () => {
    const html = renderToString('正在输出 <svg><rect width="5"')
    // 未闭合 → 清洗失败 → 降级显示源码（innerHTML 中 < 被转义为 &lt;）而非静默丢失
    expect(html).toContain('&lt;svg&gt;&lt;rect')
  })
})

describe('Markdown inline HTML (sanitized)', () => {
  it('renders span with whitelisted color style', () => {
    const html = renderToString(
      '它比 <span style="color:#663399">紫罗兰色</span> 更深沉',
    )
    expect(html).toContain('紫罗兰色')
    // jsdom/浏览器 CSSOM 会把 hex 规范化为 rgb()，两种形式均视为命中
    expect(html).toMatch(/<span[^>]*style[^>]*color:\s*(#663399|rgb\(102,\s*51,\s*153\))/)
    // 渲染产物是受控 React 元素：style 由 style 对象序列化，文本是裸文本节点
    expect(html).toMatch(/<span[^>]*color[^>]*>紫罗兰色<\/span>/)
  })

  it('drops layout-capable style props, keeps visual ones', () => {
    const html = renderToString(
      '<span style="position:fixed;top:0;z-index:999;color:red;background-color:blue">x</span>',
    )
    expect(html).toContain('color')
    expect(html).toContain('red')
    expect(html).not.toContain('position')
    expect(html).not.toContain('fixed')
    expect(html).not.toContain('z-index')
  })

  it('rejects url() injection inside style', () => {
    const html = renderToString(
      '<span style="background:url(https://evil.example);color:green">x</span>',
    )
    expect(html).not.toContain('evil.example')
    expect(html).toContain('green')
  })

  it('converts b/strong/i/em/code/mark/u/sub/sup to React elements', () => {
    const html = renderToString(
      '<b>粗</b> <i>斜</i> <code>码</code> <mark>亮</mark> <u>下</u> H<sub>2</sub>O x<sup>2</sup>',
    )
    expect(html).toContain('<strong>粗</strong>')
    expect(html).toContain('<em>斜</em>')
    expect(html).toContain('<code>码</code>')
    expect(html).toContain('<mark>亮</mark>')
    expect(html).toContain('<u>下</u>')
    expect(html).toContain('<sub>')
    expect(html).toContain('<sup>')
  })

  it('renders inline markdown inside tagged lines (bold/code)', () => {
    const html = renderToString('前缀 <b>加粗</b> 与 **markdown粗** 及 `code`')
    expect(html).toContain('<strong>加粗</strong>')
    expect(html).toContain('<strong>markdown粗</strong>')
    expect(html).toContain('<code>code</code>')
  })

  it('leaves plain text and math comparisons untouched', () => {
    // 无成对闭合标签 → 不走行内管线（防 x<y 等数学式误伤）
    const html = renderToString('if a < b then **bold** works')
    expect(html).toContain('<strong>bold</strong>')
    expect(html).not.toContain('<b>') // 未被当作行内 HTML 解析
  })

  it('keeps table lines on the ReactMarkdown path (html in table not supported)', () => {
    const html = renderToString('| a | <span>b</span> |\n| --- | --- |\n| 1 | 2 |')
    expect(html).toContain('<table>')
    expect(html).toContain('<td>')
  })

  it('supports list prefix lines containing inline html', () => {
    const html = renderToString('- 项一 <span style="color:red">红</span>\n- 项二 普通')
    expect(html).toContain('<li>')
    expect(html).toContain('红')
    expect(html).toContain('项二')
  })
})

describe('Markdown regression (existing behaviour)', () => {
  it('standard markdown still renders via ReactMarkdown', () => {
    const html = renderToString('# 标题\n\n- 列表项\n\n| t | |\n| --- | --- |\n| a | b |')
    expect(html).toContain('<h1>标题</h1>')
    expect(html).toContain('<li>列表项</li>')
    expect(html).toContain('<table>')
  })

  it('wiki links still preprocess to safe anchors', () => {
    const html = renderToString('见 [[page_key]]')
    expect(html).toMatch(/wiki-link/)
  })
})

describe('Markdown resolveImage（工作区图片注入点，FilePreview b64 通道）', () => {
  const MD = '前文\n\n![架构图](imgs/p6-000.png)\n\n后文'

  it('string return takes over the img src (data:/inkcube: loadable URL)', () => {
    const { container } = render(
      <Markdown content={MD} resolveImage={() => 'data:image/png;base64,QUJD'} />,
    )
    const img = container.querySelector('img')
    expect(img?.getAttribute('src')).toBe('data:image/png;base64,QUJD')
    expect(img?.getAttribute('alt')).toBe('架构图')
    expect(container.innerHTML).toContain('前文')
  })

  it('undefined renders pending placeholder with original src in title', () => {
    const { container } = render(<Markdown content={MD} resolveImage={() => undefined} />)
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('span[title="imgs/p6-000.png"]')).toBeTruthy()
  })

  it('null falls back to CSP rules: external blocked; data: src stripped upstream', () => {
    const { container } = render(
      <Markdown
        content={'![a](https://a.b/c.png)\n\n![b](data:image/png;base64,QUJD)'}
        resolveImage={() => null}
      />,
    )
    expect(container.querySelector('a[href="https://a.b/c.png"]')).toBeTruthy()
    // data: 协议在 react-markdown v9 defaultUrlTransform 被剥空 → SafeImage 收到空 src 返回 null；
    // resolver 接管返回的 data: URI 在组件层生成 img，不经该转换（FilePreview b64 用例覆盖）
    expect(container.querySelector('img')).toBeNull()
  })

  it('without resolver behaviour is unchanged (external still blocked)', () => {
    const { container } = render(<Markdown content={'![a](https://a.b/c.png)'} />)
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('a[href="https://a.b/c.png"]')).toBeTruthy()
  })
})
