// ── webfetch partition 共享设施（2.8 §5.3/§6.3；V2.1：池/登录窗口退役，
// 浏览器承载为主进程 WebContentsView 常驻池）——本模块含 partition 级
// SSRF 字面拦截（应用启动即挂，与承载生命周期无关）、cookie 清理、
// 承载创建/挂载/IPC。主窗口 default session 无涉（§10 隔离边界）。
const { session, WebContentsView, ipcMain } = require('electron')
const path = require('path')
const { logElectron } = require('./log-utils')
const { handleSetBounds } = require('./fetch-pool-bounds')
const { handleWindowOpen, sanitizeUserAgent } = require('./fetch-pool-window')

const PARTITION = 'persist:webfetch'
// 承载 preload 绝对路径（构造参数直传；标记语义见 fetch-pool-preload.js）
const PRELOAD_PATH = path.join(__dirname, 'fetch-pool-preload.js')
// 待命页（与 python _fetch_core.MARK_PAGE 同值字符串；close 归位由
// Python 侧权威执行。裸 # 会被 URL 解析截为 fragment，颜色用关键字；
// charset 必须显式：无 charset 的 data: URL 中文按 fallback 编码乱码，
// 2026-10-14 面板截图实证）
const MARK_PAGE_URL =
  'data:text/html;charset=utf-8,<title>inkcube-fetch-pool</title>' +
  '<body style="font-family:sans-serif;color:gray">InkCube 网页抓取待命</body>'

// ── 浏览器承载（2.8 §6.2 V2.1）：主进程 WebContentsView 常驻池 ──
// playwright 仅识别 CDP type=page 的 target（connect_over_cdp 建 CRPage 的
// 白名单硬编码），<webview> guest 报 type=webview 完全不可见——2026-10-14
// 运行时 /json/list 实证 + playwright driver 源码（session2.detach() 分支）。
// WCV 的 webContents 是标准 page target（spike 实证 type=page），POOL 标记
// 经构造参数 preload 直传（main world，_find_pool_page 锚点语义不变）。
let poolView = null
let poolEventWindow = null
let poolIpcRegistered = false

function createPoolView() {
  const view = new WebContentsView({
    webPreferences: {
      preload: PRELOAD_PATH,
      partition: PARTITION,
      // 池标记须对 CDP Runtime.evaluate（main world）可见 → contextIsolation
      // 必须关闭；preload 仅设 window 布尔标记、无 Node API，目标页可见/
      // 可篡改该标记的攻击增量≈0（SSRF 防护在 partition 网络层）。
      contextIsolation: false,
      nodeIntegration: false,
      // 面板隐藏/窗口最小化时 LLM 驱动不被节流（§8.2）
      backgroundThrottling: false,
    },
  })
  // UA 清洗（§6.2 V2.1-R6）：承载页对外暴露 Chrome 标准 UA——2026-10-14
  // 天眼查登录页 CDP 实测，inkcube/Electron 段是拼图验证不放行的根因之一
  view.webContents.setUserAgent(sanitizeUserAgent(view.webContents.getUserAgent()))
  view.webContents.loadURL(MARK_PAGE_URL)

  // window-open 收编（§6.2 V2.1-R5）：deny 弹窗 + 同承载导航（决策逻辑见
  // fetch-pool-window.js）；SSRF 拦截器是 partition 级，收编导航照常罩住
  view.webContents.setWindowOpenHandler(({ url }) => handleWindowOpen(view, url))

  // 事件桥（挂一次；attachPoolView 重复调用不重挂）→ 渲染层 header 呈现
  view.webContents.on('page-title-updated', (_e, title) => {
    poolEventWindow?.webContents.send('webfetch:event', { type: 'title', title })
  })
  // §6.2 崩溃自愈：主进程立即 reload（不等渲染层，隐藏态也自愈）→ preload
  // 重注入 → POOL 标记恢复；crashed 提示推给渲染层，title 恢复即清除
  view.webContents.on('render-process-gone', (_e, details) => {
    logElectron('WARN', `[fetch-pool] renderer gone (${details.reason}), reloading`)
    try {
      view.webContents.reload()
    } catch { /* gone 后短暂不可 reload，下次 navigate 重建 */ }
    poolEventWindow?.webContents.send('webfetch:event', { type: 'crashed' })
  })
  return view
}

/** 挂载承载到主窗口（createWindow 调用；窗口重建时 re-attach，承载复用） */
function attachPoolView(win) {
  if (!poolView) {
    poolView = createPoolView()
  }
  win.contentView.addChildView(poolView)
  poolView.setBounds({ x: 0, y: 0, width: 360, height: 480 })
  // 面板可见性由渲染层 webfetch:set-visible 驱动，初始隐藏
  poolView.setVisible(false)
  poolEventWindow = win
  logElectron('INFO', '[fetch-pool] WebContentsView pool attached')
  return poolView
}

function getLivePoolView() {
  if (!poolView) return null
  try {
    return poolView.webContents.isDestroyed() ? null : poolView
  } catch {
    return null
  }
}

/** 面板控制通道（幂等：createWindow 在 activate 重建时可能重入） */
function registerFetchPoolIpc() {
  if (poolIpcRegistered) return
  poolIpcRegistered = true
  ipcMain.on('webfetch:set-bounds', (_e, rect) => {
    handleSetBounds(getLivePoolView(), _e.sender, rect)
  })
  ipcMain.on('webfetch:set-visible', (_e, visible) => {
    getLivePoolView()?.setVisible(Boolean(visible))
  })
  ipcMain.handle('webfetch:navigate', (_e, url) => {
    const v = getLivePoolView()
    if (!v || typeof url !== 'string' || !url) return false
    return v.webContents.loadURL(url).then(
      () => true,
      (err) => {
        logElectron('WARN', `[fetch-pool] navigate failed: ${err?.message || err}`)
        return false
      },
    )
  })
}

// ── SSRF 字面拦截（§5.3 第 2 层；覆盖面板承载内一切导航/重定向/fetch）──

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
 *  网络请求，且待命页本身即 data: URL（spike 实测一刀切会误拦）。V2：LLM
 *  click 引发的跳转导航同在此罩内——Python 第 1 层仅把关 open 的初始 URL。 */
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

// ── 真·全清（设置页「重置全部数据」联动）：清 partition 全部网页数据。
// 登录态（cookies/localStorage/IndexedDB）存 userData 下的 partition，
// Python 侧够不到——reset-all 成功后由 IPC 层调用本函数收尾。
async function clearAllWebData() {
  const ses = session.fromPartition(PARTITION)
  await ses.clearStorageData({})
  await ses.clearCache()
  logElectron('INFO', '[fetch-pool] cleared all web data (reset-all)')
  return { cleared: true }
}

module.exports = {
  installSsrfInterceptor,
  removeCookiesForDomain,
  clearAllWebData,
  attachPoolView,
  registerFetchPoolIpc,
  PRELOAD_PATH,
}
