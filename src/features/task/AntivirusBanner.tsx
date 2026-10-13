import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ShieldAlert, ShieldCheck, Loader2, ClipboardList } from 'lucide-react'
import { usePolling } from '../../hooks/usePolling'
import styles from './AntivirusBanner.module.css'

type Status = 'hidden' | 'prompt' | 'working' | 'fallback' | 'hint'

/** 状态轮询间隔：授权状态只在用户点授权后变化，低频足够 */
const POLL_INTERVAL_MS = 30_000

/**
 * 白名单授权横幅（2.8 §12 F4，前置阻断）。
 *
 * 信号：{dataDir}/uv-exclusion-verified.json 是否有效（Electron
 * system:getUvExclusionStatus 直读，无 Python 参与；提权会话内回读
 * 成功后落盘）。未验证时 Python 侧前置阻断一切 uv/pip 安装
 * （venv_manager UvExclusionRequired），横幅引导用户完成一次系统授权
 * （UAC 确认即完成，无需打开安全中心）；验证失败（UAC 取消 / 防篡改
 * 拦截）→ 深链直达 Defender 排除项页 + 剪贴板路径，降级为手动引导。
 * 用户关闭或完成授权后本会话不再打扰。
 */
export function AntivirusBanner() {
  const { t } = useTranslation()
  const [status, setStatus] = useState<Status>('hidden')
  const [thirdPartyAv, setThirdPartyAv] = useState('')
  const dismissedRef = useRef(false)

  usePolling({
    name: 'uv-exclusion-status',
    interval: POLL_INTERVAL_MS,
    enabled: status === 'hidden' || status === 'prompt',
    taskTimeoutMs: 20_000,
    task: async () => {
      if (dismissedRef.current) return
      const res = (await window.electronAPI.invoke('system:getUvExclusionStatus')) as {
        verified?: boolean
        thirdPartyAv?: string
      }
      // Electron 侧 WSC 检测异步（首查可能不带字段），带上即记录供文案分流
      if (res?.thirdPartyAv) setThirdPartyAv(res.thirdPartyAv)
      if (!res?.verified) {
        setStatus((prev) => (prev === 'hidden' ? 'prompt' : prev))
      } else {
        // 授权完成（其他窗口/会话早前完成）→ prompt 态横幅下线；
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
        defenderSkipped?: boolean
      }
      if (res?.added && res?.defenderSkipped) {
        // Defender 未运行被跳过（阻断已解除）：第三方杀软仍需手动加信任区，
        // 切手动指引态告知（路径已由 Electron 侧复制到剪贴板）；无第三方
        // 杀软时无需提示，直接消失
        setStatus(thirdPartyAv ? 'hint' : 'hidden')
      } else {
        setStatus(res?.added ? 'hidden' : 'fallback')
      }
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

  if (status === 'hint') {
    return (
      <div className={styles.banner}>
        <span className={styles.icon}><ClipboardList size={14} /></span>
        <span className={styles.text}>
          {t('task.avHint3p', {
            name: thirdPartyAv,
            defaultValue:
              `已跳过 Windows Defender（服务未运行）。检测到第三方杀毒软件（${thirdPartyAv}）仍在运行——建议手动将以下目录加入其信任区（路径已复制到剪贴板）`,
          })}
        </span>
        <button className={styles.btnGhost} onClick={dismiss}>
          {t('task.avDismiss', { defaultValue: '知道了' })}
        </button>
      </div>
    )
  }

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
        {thirdPartyAv
          ? t('task.avPrompt3p', {
              name: thirdPartyAv,
              defaultValue:
                `检测到第三方杀毒软件（${thirdPartyAv}）：一键授权仅写入 Windows Defender 白名单，可能不覆盖该杀软——建议同时将 uv-cache 目录手动加入其信任区`,
            })
          : t('task.avPrompt', {
              defaultValue:
                '代码执行与网页抓取环境需要一次系统授权（加入杀毒软件白名单），授权完成前依赖安装无法进行',
            })}
      </span>
      {status === 'working' ? (
        <span className={styles.workingText}>
          {t('task.avWorking', { defaultValue: '请在系统弹窗中确认…' })}
        </span>
      ) : (
        <>
          <button className={styles.btn} onClick={() => void handleOptimize()}>
            <ShieldCheck size={12} /> {t('task.avOptimize', { defaultValue: '立即授权' })}
          </button>
          <button className={styles.btnGhost} onClick={dismiss}>
            {t('task.avDismiss', { defaultValue: '暂不' })}
          </button>
        </>
      )}
    </div>
  )
}
