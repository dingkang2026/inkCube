// ── Fetch pool & login windows (2.8browser-fetch-design §6.2) ──
// 常驻可见抓取窗口（CDP attach 目标，title 以 POOL_TITLE 开头标记）+ 按需
// 登录窗口，同一 persist:webfetch partition 共享登录 cookie；partition 级
// SSRF 字面拦截（§5.3 第 2 层）应用启动即挂，与窗口生命周期无关。
// 主窗口 default session 无涉（§10 隔离边界）。
const { BrowserWindow, session } = require('electron')
const path = require('path')
const { logElectron } = require('./log-utils')

const PARTITION = 'persist:webfetch'
// 池窗口对 Python 侧的匹配锚点是 preload 注入的 window.__INKCUBE_POOL__
// （每次导航后自动重注入，与 document.title / 窗口标题无关——抓取后
// 窗口停留在目标页，页面 title 会污染任何基于标题的锚点）。POOL_TITLE
// 系列仅驱动窗口标题可视化（待命 / 抓取中）。
const POOL_TITLE = 'inkcube-fetch-pool'
const FETCHING_TITLE = `${POOL_TITLE} · 抓取中…`
const MARK_PAGE =
  `data:text/html,<title>${POOL_TITLE}</title>` +
  '<body style="font-family:sans-serif;color:#888">InkCube 网页抓取待命</body>'

let poolWindow = null
let loginWindow = null

// ── SSRF 字面拦截（§5.3 第 2 层）──

/** IPv4 字面量命中内网段：0/8 · 10/8 · 127/8 · 172.16/12 · 192.168/16 · 169.254/16 */
function _isPrivateV4Literal(host) {
  const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(host)
  if (!m) return false
  const octets = m.slice(1).map(Number)
  if (octets.some((n) => n > 255)) return false
  const a = octets[0]
  const b = octets[1]
  return (
    a === 0 || a === 10 || a === 127 ||
    (a === 172 && b >= 16 && b <= 31) ||
    (a === 192 && b === 168) ||
    (a === 169 && b === 254)
  )
}

function _isPrivateLiteral(hostname) {
  const host = (hostname || '').toLowerCase()
  if (host === 'localhost' || host.endsWith('.local')) return true
  if (_isPrivateV4Literal(host)) return true
  // IPv6 字面量（URL.hostname 不含方括号）：回环 / 未指定 / 链路本地
  if (host === '::1' || host === '::' || host.startsWith('fe80:')) return true
  // IPv4-mapped IPv6：Chromium 将 [::ffff:127.0.0.1] 规范化为 ::ffff:7f00:1，
  // 不落在上面的回环/未指定/链路本地字面集合内，前缀一并拒绝
  if (host.startsWith('::ffff:')) return true
  return false
}

/** partition 级 onBeforeRequest：同步字面校验，封「探测后导航中重定向入内网」
 *  与「页面 JS fetch 内网」两条绕过路径（§5.3）。webRequest 只能同步决策，
 *  DNS 解析级校验由 Python 探测链承担（第 1 层）。data:/about: 放行——不产生
 *  网络请求，且池窗口标记页本身即 data: URL（spike 实测一刀切会误拦）。 */
function installSsrfInterceptor() {
  session.fromPartition(PARTITION).webRequest.onBeforeRequest((details, callback) => {
    const url = details.url
    if (url.startsWith('data:') || url.startsWith('about:')) return callback({})
    let parsed
    try {
      parsed = new URL(url)
    } catch {
      return callback({ cancel: true })
    }
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
      return callback({ cancel: true })
    }
    if (_isPrivateLiteral(parsed.hostname)) {
      logElectron('WARN', `[fetch-pool] SSRF blocked: ${url}`)
      return callback({ cancel: true })
    }
    callback({})
  })
}

// ── 抓取窗口（常驻可见，CDP attach 目标）──

function ensurePoolWindow() {
  if (poolWindow && !poolWindow.isDestroyed()) return poolWindow
  poolWindow = new BrowserWindow({
    show: false,          // 初始不可见，首次真实抓取导航时显示
    width: 1100,
    height: 800,
    title: POOL_TITLE,
    webPreferences: {
      partition: PARTITION,
      // 匹配锚点：每次导航重注入 window.__INKCUBE_POOL__（见文件头注释）。
      // contextIsolation 必须 false：Electron 12+ 默认 true，preload 跑在
      // isolated world，标记对 CDP evaluate（main world）不可见（2026-10-12
      // 实证 mark=False）。本窗口无 nodeIntegration、preload 仅设标记，
      // 关闭隔离无安全损失
      preload: path.join(__dirname, 'fetch-pool-preload.js'),
      contextIsolation: false,
      backgroundThrottling: false,  // 最小化不影响抓取（§2 可视化策略）
    },
  })
  poolWindow.loadURL(MARK_PAGE)

  // 可视化：真实抓取导航开始 → 显示窗口（不夺焦点）+ 抓取中标题。
  // 用 did-start-navigation（携带目标 url）而非 did-start-loading：后者在
  // 加载 data: 标记页时也触发，会让窗口创建即显示。
  poolWindow.webContents.on('did-start-navigation', (_e, url) => {
    if (!url || url.startsWith('data:')) return
    if (poolWindow.isDestroyed()) return
    if (!poolWindow.isVisible()) {
      // 可见性三连（2.8 §6.2）：showInactive 不抢焦点，但主窗口最大化时
      // 池窗口被完全遮挡、任务栏也无提示——用户感知就是「没看到窗口」
      // （2026-10-12 实测）。moveTop 提到最前（不夺焦点）+ flashFrame
      // 任务栏闪烁（Windows transient），显示链路同步落日志便于排查。
      poolWindow.showInactive()
      poolWindow.moveTop()
      poolWindow.flashFrame(true)
      logElectron('INFO', `[fetch-pool] pool window shown (nav: ${url.slice(0, 120)})`)
    }
    poolWindow.setTitle(FETCHING_TITLE)
  })
  // 加载结束恢复标题（抓取是否真正完成主进程不可感知——CDP 会话对主进程
  // 透明，恢复只表示网络加载完成）
  poolWindow.webContents.on('did-stop-loading', () => {
    if (!poolWindow.isDestroyed()) poolWindow.setTitle(POOL_TITLE)
  })

  // 页面 <title> 不得覆盖窗口标题（否则任务栏显示网站标题、「抓取中…」
  // 状态语义丢失）；窗口标题完全由上面两个状态机的 setTitle 驱动
  poolWindow.on('page-title-updated', (event) => event.preventDefault())

  // 用户点 ✕ → 仅隐藏：池窗口存活，CDP target 不销毁（§6.2）
  poolWindow.on('close', (event) => {
    event.preventDefault()
    if (!poolWindow.isDestroyed()) poolWindow.hide()
  })
  // 闭包校验：destroy()（崩溃自愈/退出清理路径）会异步触发 closed；若期间
  // render-process-gone 已同步重建新窗口，旧窗口的 closed 不得把新引用抹掉
  // （否则新窗口失去模块级引用被 GC，且池窗口数量漂移、CDP 匹配不确定）
  const win = poolWindow
  win.on('closed', () => {
    if (poolWindow === win) poolWindow = null
  })

  // 崩溃自愈（§6.2）：恶意页面致渲染进程崩溃只影响本窗口；重建后 preload
  // 标记照常注入，skill 下次 attach 自动恢复。不重建则后续所有抓取失败。
  poolWindow.webContents.on('render-process-gone', (_e, details) => {
    logElectron('ERROR', `[fetch-pool] pool renderer gone (${details.reason}), recreating window`)
    if (poolWindow && !poolWindow.isDestroyed()) poolWindow.destroy()
    poolWindow = null
    ensurePoolWindow()
  })
  return poolWindow
}

// ── 登录窗口（按需，人工登录载体，系统默认框、无 preload 零注入）──

function openLoginWindow(domain) {
  const raw = typeof domain === 'string' ? domain.trim().toLowerCase() : ''
  let target
  try {
    target = new URL(`https://${raw}`)
  } catch {
    return { error: 'invalid domain' }
  }
  // 二次防御：Python 路由层已拒 localhost/.local，此处拦内网 IPv4/IPv6 字面量
  if (_isPrivateLiteral(target.hostname)) {
    return { error: 'invalid domain' }
  }

  if (loginWindow && !loginWindow.isDestroyed()) {
    // 单实例复用：导航到新 domain（§6.2）
    loginWindow.loadURL(target.href)
    if (loginWindow.isMinimized()) loginWindow.restore()
    loginWindow.show()
    return { ok: true }
  }

  loginWindow = new BrowserWindow({
    width: 1100,
    height: 800,
    title: raw,
    webPreferences: { partition: PARTITION },
  })
  loginWindow.loadURL(target.href)
  loginWindow.on('closed', () => {
    loginWindow = null
  })
  return { ok: true }
}

// ── 登录态清理（§6.3）：与 DELETE /web-logins/{domain} 配对 ──

async function removeCookiesForDomain(domain) {
  const raw = typeof domain === 'string' ? domain.trim().toLowerCase() : ''
  if (!raw) return { removed: 0, matched: 0 }
  const ses = session.fromPartition(PARTITION)
  const all = await ses.cookies.get({})
  const matched = all.filter((c) => {
    // cookie domain 可能带前导点（.example.com 为父域 cookie），一并清除
    const d = (c.domain || '').replace(/^\./, '').toLowerCase()
    return d === raw || d.endsWith(`.${raw}`)
  })
  let removed = 0
  for (const c of matched) {
    try {
      const host = (c.domain || '').replace(/^\./, '').toLowerCase()
      const scheme = c.secure ? 'https' : 'http'
      await ses.cookies.remove({ url: `${scheme}://${host}${c.path || '/'}`, name: c.name })
      removed += 1
    } catch (e) {
      logElectron('WARN', `[fetch-pool] cookie remove failed (${c.name}@${c.domain}): ${e.message}`)
    }
  }
  logElectron('INFO', `[fetch-pool] removed ${removed}/${matched.length} cookies for ${raw}`)
  return { removed, matched: matched.length }
}

/** 应用退出清理（before-quit 调用）：池窗口 close 被 preventDefault，
 *  退出前必须强制销毁，否则 window-all-closed 永不触发、进程驻留后台。
 *  destroy() 跳过 close 事件，无需解除监听。 */
function shutdown() {
  if (poolWindow && !poolWindow.isDestroyed()) poolWindow.destroy()
  poolWindow = null
  if (loginWindow && !loginWindow.isDestroyed()) loginWindow.destroy()
  loginWindow = null
}

module.exports = {
  installSsrfInterceptor,
  ensurePoolWindow,
  openLoginWindow,
  removeCookiesForDomain,
  shutdown,
}
