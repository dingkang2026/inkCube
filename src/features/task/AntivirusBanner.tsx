import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ShieldAlert, ShieldCheck, Loader2, ClipboardList, Copy, Check, X } from 'lucide-react'
import { usePolling } from '../../hooks/usePolling'
import styles from './AntivirusBanner.module.css'

type DefenderStatus = 'hidden' | 'prompt' | 'working' | 'fallback'

/** 状态轮询间隔：授权/确认状态只在用户动作后变化，低频足够 */
const POLL_INTERVAL_MS = 30_000

/**
 * 杀软引导横幅（2.8 §12 F4 前置阻断 + F6 检测分流 + F8 逐 AV 确认）。
 *
 * 条列表结构，各条独立出现/消失：
 * - Defender 条：verified=false 时引导一键授权（UAC）；成功/服务停用跳过
 *   （defenderSkipped）后消失；失败降级 fallback 手动指引（深链 + 剪贴板）。
 * - 第三方条 ×N：WSC 检测到的每个非 Defender AV 一条。确认是用户声明制——
 *   第三方信任区无通用写入 API、WSC 状态位实测不可信，唯一可行方式是用户
 *   处理后点「我已处理」写台账（marker third_party_ack.acked_names）；×
 *   仅本次会话隐藏，重启再现；新装杀软重启后出现（停轮后不再轮询 WSC）；
 *   cache 挪位 marker 失效 → 台账一并作废重新引导。
 * 全部条消失（Defender verified + 每个在场第三方已确认）后轮询停止。
 */
export function AntivirusBanner() {
  const { t } = useTranslation()
  const [defenderStatus, setDefenderStatus] = useState<DefenderStatus>('hidden')
  const [verified, setVerified] = useState(false)
  const [cacheDir, setCacheDir] = useState('')
  const [avList, setAvList] = useState<string[]>([])
  // WSC 查中标记（H1）：首查进行中响应不带 thirdPartyAv 字段，此时不能
  // 判定「无第三方」——否则已验证机器首轮即停轮，第三方条永不出现
  const [avChecked, setAvChecked] = useState(false)
  const [ackedNames, setAckedNames] = useState<string[]>([])
  const [dismissed3p, setDismissed3p] = useState<string[]>([])
  const [copied, setCopied] = useState('')
  const dismissedRef = useRef(false)

  const pending3p = avList.filter((n) => !ackedNames.includes(n))
  const visible3p = pending3p.filter((n) => !dismissed3p.includes(n))
  // 停轮 = 全部条消且 WSC 已查中：字段缺省（首查进行中）不停轮（H1）
  const allDone = verified && avChecked && pending3p.length === 0

  usePolling({
    name: 'uv-exclusion-status',
    interval: POLL_INTERVAL_MS,
    // 停轮条件 = 全部条消且 WSC 查中（F8 前 verified 即停，第三方确认态
    // 永远出不来；首查进行中缺字段不停轮）；
    // dismissedRef 只挡 Defender 条状态机，不挡台账/新 AV 跟踪
    enabled: !allDone,
    taskTimeoutMs: 20_000,
    task: async () => {
      const res = (await window.electronAPI.invoke('system:getUvExclusionStatus')) as {
        verified?: boolean
        cacheDir?: string
        thirdPartyAv?: string[]
        ackedNames?: string[]
      }
      setVerified(!!res?.verified)
      if (res?.cacheDir) setCacheDir(res.cacheDir)
      // Electron 侧查中后恒给确定值（含 []，卸载杀软后名单变空也能正确清条）；
      // 缺字段 = 首查进行中，不停轮
      if (Array.isArray(res?.thirdPartyAv)) {
        setAvChecked(true)
        setAvList(res.thirdPartyAv)
      }
      if (res?.ackedNames) setAckedNames(res.ackedNames)
      if (!dismissedRef.current) {
        if (!res?.verified) {
          setDefenderStatus((prev) => (prev === 'hidden' ? 'prompt' : prev))
        } else {
          // 授权完成（其他窗口/会话早前完成）→ prompt 态下线；
          // 经函数式更新，不会覆盖进行中的 working/fallback 态
          setDefenderStatus((prev) => (prev === 'prompt' ? 'hidden' : prev))
        }
      }
    },
  })

  const handleOptimize = async () => {
    setDefenderStatus('working')
    dismissedRef.current = true
    try {
      const res = (await window.electronAPI.invoke('system:addUvCacheExclusion')) as {
        added?: boolean
        defenderSkipped?: boolean
      }
      if (res?.added) {
        // 成功（含 defenderSkipped——服务停用即 Defender 非扫描器，跳过即放行）：
        // Defender 条下线；第三方条（若有）独立在场逐条引导（F8 取代原 hint 态）
        setVerified(true)
        setDefenderStatus('hidden')
      } else {
        setDefenderStatus('fallback')
      }
    } catch {
      setDefenderStatus('fallback')
    }
  }

  // 第三方确认（用户声明制）：写台账后乐观更新，轮询兜底
  const handleAck = async (name: string) => {
    try {
      const res = (await window.electronAPI.invoke('system:ackThirdPartyAv', name)) as {
        acked?: boolean
        ackedNames?: string[]
      }
      if (res?.acked) {
        setAckedNames(res.ackedNames ?? ((prev) => [...prev, name]))
      }
    } catch { /* 静默：下一轮轮询重试 */ }
  }

  const handleCopy = async (text: string) => {
    try {
      const res = (await window.electronAPI.invoke('system:copyText', text)) as { copied?: boolean }
      if (res?.copied) {
        setCopied(text)
        setTimeout(() => setCopied(''), 2000)
      }
    } catch { /* 静默：路径文本可手动选中复制 */ }
  }

  // 用户主动关闭：Defender 条会话内不再打扰；第三方条仅本条隐藏（重启再现）
  const dismissDefender = () => {
    dismissedRef.current = true
    setDefenderStatus('hidden')
  }

  if (defenderStatus === 'hidden' && visible3p.length === 0) return null

  return (
    <>
      {defenderStatus === 'fallback' && (
        <div className={styles.banner}>
          <span className={styles.icon}><ClipboardList size={14} /></span>
          <span className={styles.text}>
            {t('task.avFallback', {
              defaultValue:
                '未能自动写入排除项。已打开 Windows 安全中心的排除项页，路径已复制到剪贴板，粘贴添加即可；若装有火绒，请在火绒「信任区」添加同一路径',
            })}
          </span>
          <button className={styles.btnGhost} onClick={dismissDefender}>
            {t('task.avDismiss', { defaultValue: '知道了' })}
          </button>
        </div>
      )}
      {(defenderStatus === 'prompt' || defenderStatus === 'working') && (
        <div className={styles.banner}>
          <span className={styles.icon}>
            {defenderStatus === 'working' ? <Loader2 size={14} className={styles.spin} /> : <ShieldAlert size={14} />}
          </span>
          <span className={styles.text}>
            {t('task.avPrompt', {
              defaultValue:
                '代码执行与网页抓取环境需要一次系统授权（加入杀毒软件白名单），授权完成前依赖安装无法进行',
            })}
          </span>
          {defenderStatus === 'working' ? (
            <span className={styles.workingText}>
              {t('task.avWorking', { defaultValue: '请在系统弹窗中确认…' })}
            </span>
          ) : (
            <>
              <button className={styles.btn} onClick={() => void handleOptimize()}>
                <ShieldCheck size={12} /> {t('task.avOptimize', { defaultValue: '立即授权' })}
              </button>
              <button className={styles.btnGhost} onClick={dismissDefender}>
                {t('task.avDismiss', { defaultValue: '暂不' })}
              </button>
            </>
          )}
        </div>
      )}
      {visible3p.map((name) => (
        <div key={name} className={styles.banner}>
          <span className={styles.icon}><ClipboardList size={14} /></span>
          <span className={styles.text}>
            {t('task.av3pBanner', {
              name,
              defaultValue: `检测到 ${name}：请将以下目录加入其信任区（代码执行环境白名单）`,
            })}
            <span className={styles.path}>{cacheDir}</span>
          </span>
          <button
            className={styles.btnGhost}
            onClick={() => void handleCopy(cacheDir)}
            disabled={!cacheDir}
          >
            {copied === cacheDir ? <Check size={12} /> : <Copy size={12} />}
            {t(copied === cacheDir ? 'task.avCopied' : 'task.avCopyPath', {
              defaultValue: copied === cacheDir ? '已复制' : '复制路径',
            })}
          </button>
          <button className={styles.btn} onClick={() => void handleAck(name)}>
            <Check size={12} /> {t('task.avAck', { defaultValue: '我已处理' })}
          </button>
          <button
            className={styles.btnClose}
            aria-label={t('task.avDismiss', { defaultValue: '暂不' })}
            onClick={() => setDismissed3p((prev) => [...prev, name])}
          >
            <X size={14} />
          </button>
        </div>
      ))}
    </>
  )
}
