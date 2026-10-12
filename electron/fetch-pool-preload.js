// ── Fetch pool preload (2.8 §6.2)：池窗口运行时标记 ──
// Python 侧 find_pool_page 的匹配锚点。不能依赖 page.title()：抓取完成
// 后窗口停留在目标页，document.title 是页面内容（不可控），首次成功抓
// 取即污染锚点（曾致后续所有抓取「未找到抓取窗口」，2026-10-12 实证）。
// preload 在每次导航后自动重新执行（含跨域），window 标记与导航/标题
// 无关、恒存在。仅池窗口挂载本 preload（主窗口/登录窗口无），不误标；
// 第三方页面伪造该标记的攻击增量≈0（Python 对选中窗口仅 goto+读取，
// SSRF 防护在网络层，与窗口选择无关）。
try {
  window.__INKCUBE_POOL__ = true
} catch { /* 冻结 window 的页面忽略：本就不可作抓取载体 */ }
