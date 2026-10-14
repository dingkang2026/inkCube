/** BrowserPanel（2.8 §8）：内嵌浏览器面板，主窗口右上角三态。
 *  hidden（移出视口，保布局——§8.3 hidden 态保持布局）/ compact / expanded。
 *  webview 恒渲染（关闭 ≠ 销毁，导航状态保留）；crash → reload 自愈（§6.2）。
 *  webview 事件用 ref + addEventListener（React 对未知标签的 on<hyphen>
 *  属性绑定不可靠，2026-10-13 Ruling 4）。 */
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { browserPanelStore, useBrowserPanel } from './browserPanelStore'
import styles from './BrowserPanel.module.css'

// 待命页（与 _fetch_core.MARK_PAGE 同值字符串；close 归位由 Python 侧权威执行）
const STANDBY_URL =
  'data:text/html,<title>inkcube-fetch-pool</title>' +
  // 裸 # 会被 URL 解析截为 fragment，颜色用关键字（同 _fetch_core.MARK_PAGE）
  '<body style="font-family:sans-serif;color:gray">InkCube 网页抓取待命</body>'
const WEBFETCH_PARTITION = 'persist:webfetch'

export function BrowserPanel() {
  const { t } = useTranslation()
  const panel = useBrowserPanel()
  const wvRef = useRef<Electron.WebviewTag | null>(null)
  const [pageTitle, setPageTitle] = useState('')
  const [crashed, setCrashed] = useState(false)

  // webview 事件（crash 自愈 / 标题跟随）：webview 恒渲染，ref 稳定，挂一次即可
  useEffect(() => {
    const wv = wvRef.current
    if (!wv) return
    const handleCrash = () => {
      // §6.2 崩溃自愈：reload → preload 重注入 → skill 下次 attach 自动恢复
      setCrashed(true)
      try { wv.reload() } catch { /* 已销毁则下次导航自然重建 */ }
    }
    // Electron 真实签名 (event, title)；jsdom 测试以 CustomEvent.detail 等价驱动
    const handleTitle = (e: Event, title?: string) => {
      setPageTitle(String(title ?? (e as CustomEvent<string>).detail ?? ''))
      setCrashed(false)
    }
    wv.addEventListener('crash', handleCrash)
    wv.addEventListener('page-title-updated', handleTitle)
    return () => {
      wv.removeEventListener('crash', handleCrash)
      wv.removeEventListener('page-title-updated', handleTitle)
    }
  }, [])

  // store.currentUrl 变化 → 命令式导航（webview 的 src 属性回写不可靠）
  useEffect(() => {
    if (panel.currentUrl && wvRef.current) {
      try { wvRef.current.loadURL(panel.currentUrl) } catch { /* webview 未就绪，src 兜底 */ }
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
      <webview
        ref={wvRef}
        src={STANDBY_URL}
        partition={WEBFETCH_PARTITION}
        className={styles.webview}
      />
    </div>
  )
}
