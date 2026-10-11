import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ShieldAlert, ShieldCheck, Loader2, ClipboardList } from 'lucide-react'
import { usePolling } from '../../hooks/usePolling'
import styles from './AntivirusBanner.module.css'

type Status = 'hidden' | 'prompt' | 'working' | 'fallback'

/** 信号轮询间隔：信号变化慢（tmp 残留只在安装失败时新增），低频足够 */
const POLL_INTERVAL_MS = 30_000

/**
 * 一键加白名单横幅（2.8 §12 F3）。
 *
 * 信号：uv-cache 的陈旧 .tmp 解压残留（杀软阻断/安装中断的持久物证，
 * Python /system/uv-cache-blocked 返回，Electron 非 win32 短路）。blocked
 * 时展示「一键优化」：提权 Add-MpPreference（UAC 由系统弹窗，用户一次
 * 确认）；回读验证失败（UAC 取消 / 防篡改拦截）→ 深链直达 Defender 排
 * 除项页 + 剪贴板路径，降级为手动引导。信号归零（如手动清理后）prompt
 * 随之隐藏；用户关闭或完成优化后本会话不再打扰。
 */
export function AntivirusBanner() {
  const { t } = useTranslation()
  const [status, setStatus] = useState<Status>('hidden')
  const dismissedRef = useRef(false)

  usePolling({
    name: 'uv-cache-blocked',
    interval: POLL_INTERVAL_MS,
    enabled: status === 'hidden' || status === 'prompt',
    taskTimeoutMs: 20_000,
    task: async () => {
      if (dismissedRef.current) return
      const res = (await window.electronAPI.invoke('system:getUvCacheBlocked')) as {
        blocked?: boolean
      }
      if (res?.blocked) {
        setStatus((prev) => (prev === 'hidden' ? 'prompt' : prev))
      } else {
        // 阻断信号可逆：prompt 态信号归零（残留被清理等）→ 横幅下线；
        // 经函数式更新，不会覆盖进行中的 working/fallback 态
        setStatus((prev) => (prev === 'prompt' ? 'hidden' : prev))
      }
    },
  })

  const handleOptimize = async () => {
    setStatus('working')
    dismissedRef.current = true
    try {
      const res = (await window.electronAPI.invoke('system:addUvCacheExclusion')) as {
        added?: boolean
      }
      setStatus(res?.added ? 'hidden' : 'fallback')
    } catch {
      setStatus('fallback')
    }
  }

  // 用户主动关闭（prompt 可关，fallback 引导读完可关）：会话内不再打扰
  const dismiss = () => {
    dismissedRef.current = true
    setStatus('hidden')
  }

  if (status === 'hidden') return null

  if (status === 'fallback') {
    return (
      <div className={styles.banner}>
        <span className={styles.icon}><ClipboardList size={14} /></span>
        <span className={styles.text}>
          {t('task.avFallback', {
            defaultValue:
              '未能自动写入排除项。已打开 Windows 安全中心的排除项页，路径已复制到剪贴板，粘贴添加即可；若装有火绒，请在火绒「信任区」添加同一路径',
          })}
        </span>
        <button className={styles.btnGhost} onClick={dismiss}>
          {t('task.avDismiss', { defaultValue: '知道了' })}
        </button>
      </div>
    )
  }

  return (
    <div className={styles.banner}>
      <span className={styles.icon}>
        {status === 'working' ? <Loader2 size={14} className={styles.spin} /> : <ShieldAlert size={14} />}
      </span>
      <span className={styles.text}>
        {t('task.avPrompt', {
          defaultValue: '检测到杀毒软件实时扫描拖慢依赖安装，建议将依赖缓存加入白名单',
        })}
      </span>
      {status === 'working' ? (
        <span className={styles.workingText}>
          {t('task.avWorking', { defaultValue: '请在系统弹窗中确认…' })}
        </span>
      ) : (
        <>
          <button className={styles.btn} onClick={() => void handleOptimize()}>
            <ShieldCheck size={12} /> {t('task.avOptimize', { defaultValue: '一键优化' })}
          </button>
          <button className={styles.btnGhost} onClick={dismiss}>
            {t('task.avDismiss', { defaultValue: '知道了' })}
          </button>
        </>
      )}
    </div>
  )
}
