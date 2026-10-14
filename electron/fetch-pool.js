// ── webfetch partition 共享设施（2.8 §5.3/§6.3；V2：池/登录窗口退役，
// 浏览器承载改为主窗口 BrowserPanel webview）——本模块仅存 partition 级
// SSRF 字面拦截（应用启动即挂，与 webview 生命周期无关）、cookie 清理、
// webview preload 路径。主窗口 default session 无涉（§10 隔离边界）。
const { session } = require('electron')
const path = require('path')
const { logElectron } = require('./log-utils')

const PARTITION = 'persist:webfetch'
// webview preload 绝对路径（渲染进程无法解析 electron/ 目录，经 IPC 下发；
// 标记语义见 fetch-pool-preload.js）
const PRELOAD_PATH = path.join(__dirname, 'fetch-pool-preload.js')

// ── SSRF 字面拦截（§5.3 第 2 层；覆盖面板 webview 内一切导航/重定向/fetch）──

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

module.exports = { installSsrfInterceptor, removeCookiesForDomain, PRELOAD_PATH }
