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

// navigator.webdriver 置 false（2.8 §6.2 V2.1-R6）：--remote-debugging-port
// 开启后 Chromium 自动置位 W3C 自动化标志（getter 定义在原型上），滑块/
// 拼图风控头号检测项——2026-10-14 天眼查登录页 CDP 实测 true。覆盖原型
// getter 保证风控沿原型链探测也读到 false；preload 每次导航自动重执行，
// 用户手动登录场景同样生效（不依赖 agent attach）。边界说明：这是
// 「消除明确的自动化标志」，非全量反指纹——get.toString() 仍是普通函数
// 源码，Kasada 级原生函数特征检测不在覆盖范围（当前站点族未观测到）。
try {
  Object.defineProperty(Object.getPrototypeOf(navigator), 'webdriver', {
    get: () => false,
    configurable: true,
  })
} catch { /* 极端冻结环境：与池标记同理，该页本就不适合人机交互 */ }
