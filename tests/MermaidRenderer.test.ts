// @vitest-environment node
import { describe, expect, it } from 'vitest'
import {
  prepareSvgSource,
  resolveSvgSize,
  sanitizeSvgForRaster,
} from '../electron/mermaid-renderer'

// mermaid-renderer 纯函数层（无 electron 依赖）：内联 SVG 光栅化前的
// 黑名单剥离 / 宽高解析 / 根标签规整。入口区经 require.main guard 跳过。

describe('sanitizeSvgForRaster — 截图前黑名单剥离', () => {
  it('配对 script 块整块删除', () => {
    const src = '<svg><script>alert(1)</script><rect width="1" height="1"/></svg>'
    const out = sanitizeSvgForRaster(src)
    expect(out).not.toContain('script')
    expect(out).toContain('<rect')
  })

  it('foreignObject（HTML 逃逸面）整块删除', () => {
    const src =
      '<svg><foreignObject><iframe src="https://evil.example"></iframe></foreignObject><rect/></svg>'
    const out = sanitizeSvgForRaster(src)
    expect(out).not.toContain('foreignObject')
    expect(out).not.toContain('iframe')
  })

  it('未闭合的孤立黑名单标签也拆除（畸形 LLM 输出）', () => {
    const src = '<svg><script src="https://x/y.js"><rect/></svg>'
    expect(sanitizeSvgForRaster(src)).not.toContain('<script')
  })

  it('on* 事件属性剥离', () => {
    const src = '<svg onload="alert(1)"><rect onclick="alert(2)" width="1" height="1"/></svg>'
    const out = sanitizeSvgForRaster(src)
    expect(out).not.toContain('onload')
    expect(out).not.toContain('onclick')
  })

  it('外链 href/xlink:href（javascript:/http(s):/file:）剥离，内部锚点保留', () => {
    const src =
      '<svg>' +
      '<a href="javascript:alert(1)"><text>x</text></a>' +
      '<image xlink:href="https://evil.example/x.png"/>' +
      '<use href="#internal-shape"/>' +
      '</svg>'
    const out = sanitizeSvgForRaster(src)
    expect(out).not.toContain('javascript:')
    expect(out).not.toContain('https://')
    expect(out).toContain('href="#internal-shape"')
  })

  it('data: 内嵌图片保留（静态无脚本面）', () => {
    const src = '<svg><image href="data:image/png;base64,AAAA"/></svg>'
    expect(sanitizeSvgForRaster(src)).toContain('data:image/png')
  })

  it('正常矢量元素不受影响', () => {
    const src =
      '<svg viewBox="0 0 10 10"><rect fill="blue"/><text font-size="12">层</text></svg>'
    expect(sanitizeSvgForRaster(src)).toBe(src)
  })

  it('非字符串输入原样返回（防御）', () => {
    expect(sanitizeSvgForRaster(undefined as never)).toBe(undefined)
    expect(sanitizeSvgForRaster(null as never)).toBe(null)
  })
})

describe('resolveSvgSize — 宽高解析（显式尺寸优先，viewBox 回退）', () => {
  it('显式 width/height 优先', () => {
    const src = '<svg width="320" height="240" viewBox="0 0 900 980">'
    expect(resolveSvgSize(src)).toEqual({ width: 320, height: 240 })
  })

  it('px 单位后缀可解析', () => {
    expect(resolveSvgSize('<svg width="320px" height="240px">')).toEqual({
      width: 320,
      height: 240,
    })
  })

  it('viewBox-only（LLM 常见形态）按 viewBox 宽高', () => {
    expect(resolveSvgSize('<svg viewBox="0 0 900 980" xmlns="...">')).toEqual({
      width: 900,
      height: 980,
    })
  })

  it('百分比尺寸无 viewBox → null（调用方降级）', () => {
    expect(resolveSvgSize('<svg width="100%" height="100%">')).toBeNull()
  })

  it('无根 svg 标签 → null', () => {
    expect(resolveSvgSize('not an svg')).toBeNull()
  })
})

describe('prepareSvgSource — 根标签规整（注入显示尺寸）', () => {
  it('替换 mermaid 产物的 width="100%" 并删根 style（max-width 会缩回光栅）', () => {
    const src =
      '<svg xmlns="..." width="100%" style="max-width:500px;background:white;"><rect/></svg>'
    const out = prepareSvgSource(src, 600, 300)
    expect(out).toContain('width="600"')
    expect(out).toContain('height="300"')
    expect(out).not.toContain('100%')
    expect(out).not.toContain('max-width')
  })

  it('根无尺寸时注入显式尺寸（viewBox-only 形态）', () => {
    const out = prepareSvgSource('<svg viewBox="0 0 900 980">', 1800, 1960)
    expect(out).toContain('width="1800"')
    expect(out).toContain('height="1960"')
    expect(out).toContain('viewBox="0 0 900 980"') // viewBox 保留
  })

  it('只动第一个根标签，子元素不受影响', () => {
    const src = '<svg viewBox="0 0 2 2"><svg viewBox="0 0 3 3"/></svg>'
    const out = prepareSvgSource(src, 4, 4)
    expect(out).toContain('<svg viewBox="0 0 2 2" width="4" height="4">')
    expect(out).toContain('<svg viewBox="0 0 3 3"/>')
  })
})
