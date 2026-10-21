/** tests/BootstrapSplash.test.ts — 启动加载屏守卫（2026-10-10 白屏修复）
 *  白屏根因：index.html 的 #root 为空，「窗口已开 → JS bundle 加载解析 → React
 *  挂载」期间无任何内容。修复：#root 内置纯 CSS 加载屏，React 首渲随容器内容
 *  一起替换，无需清理代码。
 *  本测试守护：加载屏存在于 #root 内、双语静态提示（i18n 未加载期）、关键样式
 *  内联自足（global.css 随 bundle 加载，之前无样式可用）、CSP 合规（无内联脚本）。
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const html = readFileSync(resolve(process.cwd(), 'index.html'), 'utf-8')

describe('index.html 启动加载屏', () => {
  it('加载屏位于 #root 内（React 首渲自动替换，无需清理代码）', () => {
    const rootStart = html.indexOf('<div id="root">')
    // 用 DOM 标记定位：bootstrap-splash 首次出现是 <head> 内的 CSS 选择器
    const splashStart = html.indexOf('class="bootstrap-splash"')
    expect(rootStart).toBeGreaterThanOrEqual(0)
    expect(splashStart).toBeGreaterThan(rootStart)
  })

  it('包含品牌墨字、墨渍带与双语静态提示', () => {
    expect(html).toContain('bootstrap-splash-inkchar')
    expect(html).toContain('bootstrap-splash-ink')
    expect(html).toContain('墨方 InkCube')
    expect(html).toContain('正在启动')
    expect(html).toContain('Loading')
  })

  it('样式内联自足：视口撑满 + 品牌呼吸动画定义存在', () => {
    // global.css 随 bundle 加载，加载屏期间必须自带关键样式
    expect(html).toContain('html, body, #root { height: 100%;')
    expect(html).toContain('@keyframes bootstrap-splash-breathe')
    expect(html).toContain('animation: bootstrap-splash-breathe')
  })

  it('CSP 合规：无内联脚本（script-src 仅允许 self 外链）', () => {
    // <script> 缺 src= 即内联脚本，会被 CSP 拦截导致加载屏后无代码执行
    expect(html).not.toMatch(/<script\b(?![^>]*\bsrc=)[^>]*>/)
  })
})
