/** BrowserPanel（2.8 §8）：内嵌浏览器面板，主窗口右上角三态。
 *  hidden（移出视口，保布局——§8.3 hidden 态保持布局）/ compact / expanded。
 *  承载为主进程 WebContentsView（V2.1：playwright 仅识别 CDP type=page 的
 *  target，<webview> guest 报 type=webview 不可见——2026-10-14 运行时实证 +
 *  driver 源码）。渲染层只负责面板 chrome：矩形经 ResizeObserver → rAF 合帧
 *  上报（hidden 不上报，防移出视口的 -10000 污染），可见性经
 *  webfetch:set-visible，页面事件经 webfetch:event 推回（crash 自愈在主进程），
 *  导航经 webfetch:navigate。通道语义：set-bounds/set-visible 注册在
 *  ipcMain.on → 渲染层必须 send（invoke 会因无 handler 必 reject 且被吞——
 *  2026-10-14 dev 日志实证，承载永不可见）；navigate 是 ipcMain.handle → invoke。关闭 ≠ 销毁：占位 div 恒渲染，承载常驻。 */
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { browserPanelStore, useBrowserPanel } from './browserPanelStore'
import styles from './BrowserPanel.module.css'

/** 矩形整数化为 DIP（WCV setBounds 接受小数，整数化避免亚像素抖动载荷） */
function roundRect(r: DOMRect) {
  return {
    x: Math.round(r.x),
    y: Math.round(r.y),
    width: Math.round(r.width),
    height: Math.round(r.height),
  }
}

export function BrowserPanel() {
  const { t } = useTranslation()
  const panel = useBrowserPanel()
  const hostRef = useRef<HTMLDivElement | null>(null)
  const [pageTitle, setPageTitle] = useState('')
  const [crashed, setCrashed] = useState(false)

  // 可见性同步（含首挂载显式对齐主进程态）：显示前先推矩形（防闪旧位）
  useEffect(() => {
    const host = hostRef.current
    if (panel.visible && host) {
      window.electronAPI.send('webfetch:set-bounds', roundRect(host.getBoundingClientRect()))
    }
    window.electronAPI.send('webfetch:set-visible', panel.visible)
  }, [panel.visible])

  // 矩形跟随（transition/窗口 resize 连续回调 → rAF 合帧）；hidden 不上报。
  // window resize 补报（2026-10-14）：compact 面板 fixed 360×480 固定尺寸，
  // 窗口 resize 时 host 尺寸不变 → ResizeObserver 不触发，而 chrome 的
  // right 锚点已随窗口移动 → bounds 停旧值 = WCV 错位（最大化往返实证）
  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    let raf = 0
    const push = () => {
      cancelAnimationFrame(raf)
      raf = requestAnimationFrame(() => {
        if (!browserPanelStore.getState().visible) return
        window.electronAPI.send('webfetch:set-bounds', roundRect(host.getBoundingClientRect()))
      })
    }
    const ro = new ResizeObserver(push)
    ro.observe(host)
    window.addEventListener('resize', push)
    return () => {
      cancelAnimationFrame(raf)
      ro.disconnect()
      window.removeEventListener('resize', push)
    }
  }, [])

  // 主进程事件桥（title 跟随 / crashed 提示；render-process-gone 的 reload
  // 自愈由主进程执行，渲染层仅呈现提示，title 恢复即清除）
  useEffect(() => {
    return window.electronAPI.on('webfetch:event', (payload) => {
      const p = payload as { type: string; title?: string }
      if (p.type === 'title') {
        setPageTitle(String(p.title ?? ''))
        setCrashed(false)
      } else if (p.type === 'crashed') {
        setCrashed(true)
      }
    })
  }, [])

  // store.currentUrl 变化 → 主进程导航（登录入口 / 任务横幅）
  useEffect(() => {
    if (panel.currentUrl) {
      window.electronAPI.invoke('webfetch:navigate', panel.currentUrl).catch(() => {})
    }
  }, [panel.currentUrl])

  return (
    <div
      data-panel=""
      className={`${styles.panel} ${panel.expanded ? styles.expanded : styles.compact}`}
      data-visible={panel.visible ? 'true' : 'false'}
      data-expanded={panel.expanded ? 'true' : 'false'}
    >
      <div className={styles.header}>
        <span className={styles.drivingDot} title={t('browserPanel.driving')} />
        <span className={styles.pageTitle}>{pageTitle || t('browserPanel.title')}</span>
        {crashed && <span className={styles.crashHint}>{t('browserPanel.crashReload')}</span>}
        <button
          className={styles.iconBtn}
          title={panel.expanded ? t('browserPanel.collapse') : t('browserPanel.expand')}
          onClick={() => (panel.expanded ? browserPanelStore.collapse() : browserPanelStore.expand())}
        >
          {panel.expanded ? '⤡' : '▢'}
        </button>
        <button className={styles.iconBtn} title={t('browserPanel.hide')} onClick={() => browserPanelStore.hide()}>
          ✕
        </button>
      </div>
      {/* WCV 承载视区：主进程按此矩形 setBounds（header 不被覆盖，保持可点） */}
      <div ref={hostRef} data-pool-host="" className={styles.poolHost} />
    </div>
  )
}
