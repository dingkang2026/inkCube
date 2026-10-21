// window-open 收编决策（2.8 §6.2 V2.1-R5）：零依赖、独立于 electron 可单测
// （与 fetch-pool-bounds.js 同模式——主进程逻辑一律提纯函数再测）。
//
// deny 弹窗 + 同承载导航：V2 快照循环按「单页面状态机」建模（2.8 §5.2），
// target=_blank / window.open 若放行为独立窗口：① agent 的 POOL page 快照
// 看不到任何变化（2026-10-14 天眼查任务实证：点击详情页后 LLM 误判
// 「未跳转」连走 4 步弯路直至登录墙）② 新窗口脱离面板承载区，用户看到
// 面板外独立窗口。收编后点击语义 = 当前承载页跳转，agent 视野（POOL page
// 的 url/title/正文变化）与用户视野（面板内）同时一致。
//
// 导航失败（含 SSRF 拦截层 cancel）静默消化：快照层天然如实反映
// （url/title 不变），LLM 据此重试或换策略——失败可见性由快照循环提供，
// 不在此叠加日志。SSRF 拦截命中时拦截器自身已有 WARN（installSsrfInterceptor）。
// about: 前缀（about:blank 占位弹窗）不导航：把承载导去空白页会破坏原页面。
const DENY = { action: 'deny' }

function handleWindowOpen(view, url) {
  if (view && typeof url === 'string' && url !== '' && !url.startsWith('about:')) {
    view.webContents.loadURL(url).catch(() => {})
  }
  return DENY
}

// UA 清洗（2.8 §6.2 V2.1-R6）：剥除 inkcube/<v> 与 Electron/<v> 两段。
// 2026-10-14 天眼查登录页 CDP 实测：拼图验证不放行的 UA 根因——
// Electron 默认 UA 明写框架身份，滑块/拼图风控 UA 黑名单直接命中。
// 只消除错误暴露，不伪造版本号：Chrome 段保留真实内核版本（四段）
// 与 client hints（sec-ch-ua 主版本）天然一致，改写反引入冲突风险。
const STRIP_RE = / (?:inkcube|Electron)\/[^\s]+/g

function sanitizeUserAgent(ua) {
  if (typeof ua !== 'string') return ua
  return ua.replace(STRIP_RE, '')
}

module.exports = { handleWindowOpen, sanitizeUserAgent }
