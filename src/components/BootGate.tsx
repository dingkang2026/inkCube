import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

// BootGate — 启动门(「应用启动时序」窗口先行改造,2026-10-24):
// 主进程窗口先行创建后,python 后端仍在后台启动——boot 期本组件挡住
// children(App),呈现「阶段 + 计时」loading;阶段由主进程经 app:boot-stage
// 推送(app:boot-status 拉取初始态)。boot 期业务 hook 不挂载、不向未就绪
// 后端发请求——窗口先行时序的隐含约定由此组件承载。
// 阶段流:tools(准备运行环境)→ python(启动本地服务)→ ready(主进程
// /health 已通过)→ 本地 connect(连接数据服务:boot-status invoke 兼作
// IPC 链连通确认)→ 放行 children。任一失败路径(主进程推 error /
// python:process-exit / 确认 invoke 失败)转错误屏(重试 = app:relaunch)。
type BootStage = 'tools' | 'python' | 'connect' | 'error'

interface BootStatus {
  stage: 'tools' | 'python' | 'ready' | 'error'
  message: string | null
  elapsedMs: number
}

const STAGE_I18N_KEY: Record<BootStage, string> = {
  tools: 'boot.stage.tools',
  python: 'boot.stage.python',
  connect: 'boot.stage.connect',
  error: 'boot.errorTitle',
}

export default function BootGate({ children }: { children: React.ReactNode }) {
  const { t } = useTranslation()
  const [stage, setStage] = useState<BootStage>('tools')
  const [status, setStatus] = useState<BootStatus | null>(null)
  const [elapsed, setElapsed] = useState(0)
  const [passed, setPassed] = useState(false)
  const passedRef = useRef(false)

  useEffect(() => {
    let cancelled = false

    // 就绪放行:「连接数据服务」确认——boot-status invoke 通 = Electron IPC
    // 链通,主进程侧 /health 已在发 ready 前验证,双条件即端到端就绪。
    const enterReady = () => {
      if (passedRef.current || cancelled) return
      setStage('connect')
      window.electronAPI
        .invoke('app:boot-status')
        .then((s) => {
          if (cancelled || passedRef.current) return
          const st = s as BootStatus
          if (st.stage === 'error') {
            setStage('error')
            setStatus({ ...st, elapsedMs: 0 })
            return
          }
          passedRef.current = true
          setPassed(true)
        })
        .catch((err) => {
          if (cancelled) return
          setStage('error')
          setStatus({
            stage: 'error',
            message: err instanceof Error ? err.message : String(err),
            elapsedMs: 0,
          })
        })
    }

    // 先订阅后拉取:避免「事件先到、invoke 后返」的竞态覆盖新阶段
    const offStage = window.electronAPI.on('app:boot-stage', (payload: unknown) => {
      const s = payload as BootStatus
      if (s.stage === 'ready') {
        enterReady()
        return
      }
      if (passedRef.current || cancelled) return
      setStage(s.stage)
      // 新阶段从 0 本地计时(事件里的 elapsedMs 是上一阶段耗时,不适用)
      setStatus({ ...s, elapsedMs: 0 })
    })

    // 启动期 python 进程退出(主进程 boot 链 catch 置 error 前的另一路信号;
    // App 挂载后本组件已卸载退订,与 App.tsx 运行期消费不重叠)
    const offExit = window.electronAPI.on('python:process-exit', () => {
      if (passedRef.current || cancelled) return
      setStage('error')
      setStatus({ stage: 'error', message: null, elapsedMs: 0 })
    })

    window.electronAPI
      .invoke('app:boot-status')
      .then((s) => {
        if (cancelled || passedRef.current) return
        const st = s as BootStatus
        if (st.stage === 'ready') {
          enterReady()
        } else if (st.stage === 'error') {
          setStage('error')
          setStatus({ ...st, elapsedMs: 0 })
        } else {
          setStage(st.stage)
          setStatus(st)
        }
      })
      .catch(() => {
        // invoke 挂了 = IPC 链异常,转错误屏(重试走 relaunch)
        if (!cancelled) {
          setStage('error')
          setStatus({ stage: 'error', message: null, elapsedMs: 0 })
        }
      })

    return () => {
      cancelled = true
      offStage?.()
      offExit?.()
    }
  }, [])

  // 阶段计时:初始值对齐 boot-status 返回的本阶段已耗时,此后本地每秒递增
  useEffect(() => {
    if (stage === 'error') return
    const baseSec = Math.max(0, Math.floor((status?.elapsedMs ?? 0) / 1000))
    setElapsed(baseSec)
    const iv = setInterval(() => setElapsed((e) => e + 1), 1000)
    return () => clearInterval(iv)
  }, [stage])

  const handleRetry = () => {
    void window.electronAPI.invoke('app:relaunch')
  }

  const handleQuit = () => {
    void window.electronAPI.invoke('window:close')
  }

  if (passed) return <>{children}</>

  if (stage === 'error') {
    return (
      <div className="boot-gate boot-gate-error" role="alert">
        <div className="boot-gate-inkchar" aria-hidden>墨</div>
        <div className="boot-gate-title">{t('boot.errorTitle')}</div>
        <div className="boot-gate-msg">
          {status?.message || t('boot.errorHint')}
        </div>
        <div className="boot-gate-actions">
          <button type="button" className="boot-gate-btn boot-gate-btn-primary" onClick={handleRetry}>
            {t('boot.retry')}
          </button>
          <button type="button" className="boot-gate-btn" onClick={handleQuit}>
            {t('boot.quit')}
          </button>
        </div>
        <div className="boot-gate-ink" aria-hidden />
      </div>
    )
  }

  return (
    <div className="boot-gate">
      <div className="boot-gate-inkchar" aria-hidden>墨</div>
      <div className="boot-gate-brand">墨方 InkCube</div>
      <div className="boot-gate-stage">{t(STAGE_I18N_KEY[stage])}</div>
      <div className="boot-gate-elapsed">{t('boot.elapsed', { seconds: elapsed })}</div>
      <div className="boot-gate-ink" aria-hidden />
    </div>
  )
}
