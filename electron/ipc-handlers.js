const { logElectron, readLogFile, getLogPath, getLogInfo } = require('./log-utils')
const fetchPool = require('./fetch-pool')
const { clipboardImageName } = require('./clipboard-image-name')

/**
 * Unified Python response handler — bridges InkCubeError to frontend.
 * On success (res.ok): returns parsed JSON.
 * On error (!res.ok): throws an InkCubeIPCError with structured fields
 *   so that useErrorHandler can classify and display appropriate UI.
 */
async function handlePythonResponse(res) {
  if (res.ok) return res.json()
  const body = await res.json().catch(() => null)
  // D1 错误契约：统一 {error, code} 体（FastAPI HTTPException 的 {detail} 与
  // pydantic 422 的 {detail: [...]} 依次降级），中文原文仅作未知 code 的 fallback
  const detailText = typeof body?.detail === 'string' ? body.detail : ''
  const err = new Error(body?.error || body?.message || detailText || `Python backend error (${res.status})`)
  err.errorCode = body?.code || 'UNKNOWN_ERROR'
  err.errorCategory = body?.category || 'INTERNAL'
  err.errorSuggestion = body?.suggestion || ''
  err.errorDetail = body?.detail || ''
  throw err
}

/** 需回传 payload 的动作通道（PPT 增强/归档三通道等）：ipcMain.handle 抛出的异常跨进程
 *  仅保留 message（Electron #24427），err.errorCode 等结构化字段全部丢失，渲染层
 *  apiErrorText(code, ...) 恒走 fallback——错误码改经返回值透传，渲染层判 res.ok
 *  后自行取 payload 与映射文案。语义同 handlePythonResponse，以返回值代替 throw。
 *  成功时 body 展开进返回值（{ ok: true, ...body }），仅判 ok 的消费方不受影响。 */
async function structuredPythonResponse(res) {
  if (res.ok) return { ok: true, ...(await res.json().catch(() => ({}))) }
  const body = await res.json().catch(() => null)
  return {
    ok: false,
    code: body?.code || 'UNKNOWN_ERROR',
    error: body?.error || body?.message || `Python backend error (${res.status})`,
  }
}

/** 默认超时：全部 IPC handler 的 Python HTTP 调用统一强制超时。
 *  根因背景：Uvicorn 默认 5s 关闭空闲 keep-alive 连接，Node fetch 复用半关闭
 *  socket 时会永久等待且无默认超时，曾导致 ActivityMonitor 轮询循环死亡
 *  （UI 快照冻结，0.4lifecycle-design）。慢操作（文件上传/LLM 测试连接）显式传更大值。 */
const PY_FETCH_DEFAULT_TIMEOUT = 15000

function fetchWithTimeout(url, options = {}, timeoutMs = PY_FETCH_DEFAULT_TIMEOUT) {
  return fetch(url, {
    ...options,
    signal: AbortSignal.timeout(timeoutMs),
  })
}

/** 保存对话框默认文件名净化（非法字符→_、去结尾点/空格、截 80），
 *  与 Python 后端 _sanitize_filename 同规则——reports:exportReport 与
 *  form-fill:download 共用，避免双实现漂移。 */
function sanitizeFilename(title, fallback) {
  return String(title || fallback)
    .replace(/[\\/:*?"<>|\x00-\x1f]/g, '_')
    .replace(/[. ]+$/g, '')
    .slice(0, 80)
    .replace(/[. ]+$/g, '') || fallback
}

/**
 * Upload files to a task workspace using manual multipart construction.
 * Avoids FormData/Blob which are unreliable in Electron main process.
 * 磁盘路径版：读文件后转调 _postMultipartFiles（与剪贴板截图字节直传共用
 * 同一份 multipart 拼接逻辑，避免两份实现漂移）。
 */
async function _uploadFilesToTask(pythonManager, taskId, filePaths) {
  const fs = require('fs')
  const path = require('path')
  const files = filePaths.map((fp) => ({
    name: path.basename(fp),
    data: fs.readFileSync(fp),
  }))
  return _postMultipartFiles(pythonManager, taskId, files)
}

/**
 * Shared multipart uploader: files = [{ name, data(Buffer) }].
 * 剪贴板截图（内存字节，无磁盘路径）与磁盘文件上传共用同一构造与 python 端点。
 */
async function _postMultipartFiles(pythonManager, taskId, files) {
  const path = require('path')
  const baseUrl = pythonManager.getBaseUrl()
  const boundary = '----InkCubeBoundary' + Date.now()

  const parts = []
  for (const { name, data } of files) {
    const fileName = path.basename(name).replace(/"/g, '\\"')
    parts.push(
      Buffer.from(
        `--${boundary}\r\n` +
        `Content-Disposition: form-data; name="files"; filename="${fileName}"\r\n` +
        `Content-Type: application/octet-stream\r\n\r\n`
      )
    )
    parts.push(data)
    parts.push(Buffer.from('\r\n'))
  }
  parts.push(Buffer.from(`--${boundary}--\r\n`))

  const body = Buffer.concat(parts)

  const res = await fetchWithTimeout(`${baseUrl}/tasks/${taskId}/files/upload`, {
    method: 'POST',
    headers: { 'Content-Type': `multipart/form-data; boundary=${boundary}` },
    body: body,
  }, 120000) // 大文件上传，放宽超时
  return handlePythonResponse(res)
}

/** 全局终态事件流 SSE 转发（0.4 §6.4 E3）：/activities/stream → 'activity:stream' IPC。
 *
 *  每窗口一条；重复 invoke（StrictMode 双挂载 / 组件重挂载）先 abort 旧流防双流；
 *  Python 后端重启窗口期退避重连；sender 销毁时 abort 上游 fetch（同 tasks:chat M2 语义）。
 *  SSE 事件体不在主进程解析，仅作为「立即心跳」信号转发给渲染进程。 */
const _activityStreamCtl = new Map() // sender.id -> AbortController

function streamActivities(pythonManager, sender) {
  const prev = _activityStreamCtl.get(sender.id)
  if (prev) prev.abort()
  const controller = new AbortController()
  _activityStreamCtl.set(sender.id, controller)
  const onDestroyed = () => controller.abort()
  sender.once('destroyed', onDestroyed)

  void (async () => {
    let backoffMs = 2000
    try {
      while (!controller.signal.aborted && !sender.isDestroyed()) {
        try {
          // SSE 长连接：不适用 fetchWithTimeout，生命周期由 controller.signal 管理
          const response = await fetch(`${pythonManager.getBaseUrl()}/activities/stream`, { signal: controller.signal })
          if (!response.ok || !response.body) throw new Error(`activities/stream HTTP ${response.status}`)
          backoffMs = 2000
          const reader = response.body.getReader()
          const decoder = new TextDecoder()
          let buffer = ''
          let currentEvent = 'message'
          while (true) {
            const { done, value } = await reader.read()
            if (done) break
            buffer += decoder.decode(value, { stream: true })
            const lines = buffer.split('\n')
            buffer = lines.pop() || ''
            for (const line of lines) {
              if (line.startsWith('event: ')) {
                currentEvent = line.slice(7).trim()
              } else if (line.startsWith('data: ')) {
                const data = line.slice(6)
                if (!sender.isDestroyed()) {
                  sender.send('activity:stream', { type: currentEvent, data })
                }
                currentEvent = 'message'
              } else {
                currentEvent = 'message'
              }
            }
          }
          // 流正常结束（后端关闭）→ 退避后重连
        } catch (err) {
          if (controller.signal.aborted) return
          logElectron('WARN', `activity stream reconnect in ${backoffMs}ms: ${err.message}`)
        }
        if (controller.signal.aborted) return
        await new Promise((resolve) => setTimeout(resolve, backoffMs))
        backoffMs = Math.min(backoffMs * 2, 15000)
      }
    } finally {
      sender.removeListener('destroyed', onDestroyed)
      if (_activityStreamCtl.get(sender.id) === controller) {
        _activityStreamCtl.delete(sender.id)
      }
    }
  })()
}

function registerIpcHandlers(ipcMain, pythonManager) {
  // ── System ──

  ipcMain.handle('system:getHealth', async () => {
    try {
      const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/health`)
      if (!res.ok) {
        return { status: 'degraded', services: {}, error: `Health check failed (${res.status})` }
      }
      return res.json()
    } catch (e) {
      logElectron('WARN', `Health check failed: ${e.message}`)
      // Return error code, let frontend handle i18n
      return { status: 'down', services: {}, errorCode: 'PYTHON_SERVICE_DOWN' }
    }
  })

  // Onboarding wizard flag (persisted in backend settings kv table, 0.4lifecycle §7.3)
  ipcMain.handle('system:getOnboarding', async () => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/onboarding`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('system:setOnboardingDone', async () => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/onboarding/done`, {
      method: 'POST',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('system:getLogs', async () => {
    return readLogFile()
  })

  ipcMain.handle('system:getLogInfo', async () => {
    return getLogInfo()
  })

  ipcMain.handle('system:exportLogs', async () => {
    const { dialog, BrowserWindow } = require('electron')
    const fs = require('fs')
    const logContent = readLogFile()
    const logPath = getLogPath()

    // Build export content with system context
    let healthInfo = 'N/A'
    try {
      const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/health`)
      if (res.ok) {
        const health = await res.json()
        healthInfo = Object.entries(health.services || {})
          .map(([k, v]) => `${k}: ${v.status}${v.code ? ` (${v.code})` : ''}`)
          .join('\n')
      }
    } catch { /* ignore */ }

    const exportContent = [
      '=== InkCube Log Export ===',
      `Time: ${new Date().toISOString()}`,
      `Log file: ${logPath}`,
      '',
      '--- Health Status ---',
      healthInfo,
      '',
      '--- Logs ---',
      logContent,
    ].join('\n')

    const win = BrowserWindow.getFocusedWindow()
    const { canceled, filePath } = await dialog.showSaveDialog(win || undefined, {
      title: 'Export Logs',
      defaultPath: `inkcube-logs-${new Date().toISOString().slice(0, 10)}.txt`,
      filters: [{ name: 'Text', extensions: ['txt', 'log'] }],
    })

    if (!canceled && filePath) {
      fs.writeFileSync(filePath, exportContent, 'utf-8')
      logElectron('INFO', `Logs exported to ${filePath}`)
    }
  })

  // ── Data Management ──

  ipcMain.handle('system:getDataUsage', async () => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/system/data/usage`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('system:cleanData', async (_event, category) => {
    // 大目录慢删除（chroma/files 累计 GB 级）会超默认 15s：超时会让前端
    // 循环中断、后续类别不执行（2026-10-20 实证）；60s 是慢删的纵深防御
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/system/data/${category}`, {
      method: 'DELETE',
    }, 60000)
    return handlePythonResponse(res)
  })

  // 真·全清（2026-10-20 审计 B3+B6）：后端单事务清全表 + data_dir 磁盘；
  // 网页登录态（cookies/storage）存 Electron partition，python 够不到——
  // 后端成功后联动 clearAllWebData 收尾。cookies 清理失败不阻断（后端
  // 已完成的部分不回滚），返回体标 partial 让前端 warning。
  ipcMain.handle('system:resetAllData', async () => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/system/data/reset-all`, {
      method: 'POST',
    }, 120000)
    const body = handlePythonResponse(res)
    if (body && body.success) {
      try {
        const { clearAllWebData } = require('./fetch-pool')
        await clearAllWebData()
      } catch (e) {
        logElectron('WARN', `resetAllData: web data cleanup failed: ${e.message}`)
        return {
          ...body,
          partial: true,
          message: body.message
            ? `${body.message}; web data cleanup failed`
            : 'Web data cleanup failed',
        }
      }
    }
    return body
  })

  // 数据目录三态解析（环境变量覆盖 > dev=python/data > 打包=userData/data）——
  // system:openDataDir 与 file:openOriginal（知识库/Wiki 打开原文件，2.2 §10.4）共用
  const getDataDir = () => {
    if (process.env.INKCUBE_DATA_DIR) return process.env.INKCUBE_DATA_DIR
    if (process.env.NODE_ENV === 'development') {
      return require('path').join(__dirname, '..', 'python', 'data')
    }
    const { app } = require('electron')
    return require('path').join(app.getPath('userData'), 'data')
  }

  ipcMain.handle('system:openDataDir', async () => {
    const { shell } = require('electron')
    const fs = require('fs')
    const dataDir = getDataDir()
    if (!fs.existsSync(dataDir)) {
      fs.mkdirSync(dataDir, { recursive: true })
    }
    // openPath 返回非空字符串即失败信息（同 file:openOriginal 模式），
    // 丢弃返回值会让渲染层误以为打开成功
    const openErr = await shell.openPath(dataDir)
    if (openErr) {
      logElectron('WARN', `openDataDir failed: ${openErr}`)
      return { success: false, message: openErr }
    }
    return { success: true }
  })

  // ── 杀毒软件白名单（2.8 §12 F4，前置阻断）──
  // 语义：未验证加白前，Python 侧任何 uv/pip 安装被禁止（venv_manager
  // is_uv_exclusion_verified 闸门）；验证状态落盘在
  // dirname(uvCacheDir)/uv-exclusion-verified.json，与 Python 侧推导
  // （venv_manager _verified_marker_path = dirname(UV_CACHE_DIR)）对称，
  // 两侧约定一致（Python 校验时做 abspath 归一，防相对/绝对路径差异误判）。
  // 阻断是 Windows 杀软特性，非 win32 恒已验证，前端零平台逻辑。
  const fs = require('fs')
  const path = require('path')
  // uv-cache 路径与 Python main.py 的 UV_CACHE_DIR 语义对称：显式 env 优先；
  // dev(Windows) 外置项目树（IDE 文件监视器锁 rename——2026-10-12 根因实测，
  // 规则与 Python dev 分支一致：项目盘根\InkCube-dev-cache\uv-cache，盘符取
  // 项目根不硬编码），生产回落 data 目录（与 venv 同盘硬链接，布局不变）。
  const uvCacheDir = () => {
    if (process.env.UV_CACHE_DIR) return process.env.UV_CACHE_DIR
    // INKCUBE_DATA_DIR 在场 = 生产 spawn（review C1 对称加固）：dev 下显式
    // 设该变量时 Electron 侧跟随生产口径，与 Python main.py 判据同源
    if (process.platform === 'win32' && !process.env.INKCUBE_DATA_DIR &&
        process.env.NODE_ENV === 'development') {
      return path.join(path.parse(path.join(__dirname, '..')).root, 'InkCube-dev-cache', 'uv-cache')
    }
    return path.join(getDataDir(), 'uv-cache')
  }
  const uvVerifiedMarker = () => path.join(path.dirname(uvCacheDir()), 'uv-exclusion-verified.json')
  // marker 读辅助（H2）：授权两落盘点读改写保留 third_party_ack 时共用；
  // 读不到（不存在/损坏/权限）返回空对象——与「未验证」同义，不抛错
  const readMarkerData = () => {
    try { return JSON.parse(fs.readFileSync(uvVerifiedMarker(), 'utf-8')) } catch { return {} }
  }

  // 第三方杀软检测（2.8 §12 F6）：WSC（SecurityCenter2）注册名单中过滤
  // Defender 后的非 Defender AV，用于横幅分流——一键授权仅写入 Defender
  // 白名单，第三方杀软（如实测本机的「Lenovo Anti-Virus powered by
  // Huorong Security」）需用户手动加信任区。
  // productState 不解析：实测联想 262144 按常见位域解码为 disabled 但实际
  // 在拦截（2026-10-12 归零测试），状态位不可信——注册即上报。
  // 进程内缓存一次：WMI 查询约 1s，不能进 30s 轮询热路径；杀软安装/卸载
  // 是低频事件。首查进行中返回 undefined（本次响应不带字段），查询失败
  // /WSC 服务异常归 null（等同无第三方，行为与 F4 现状一致）。
  let thirdPartyAvCache
  const detectThirdPartyAv = () => {
    if (thirdPartyAvCache !== undefined) return thirdPartyAvCache
    const { spawn } = require('child_process')
    const p = spawn('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command',
      '[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; ' +
      'Get-CimInstance -Namespace root/SecurityCenter2 ' +
      '-ClassName AntiVirusProduct | Select-Object -ExpandProperty displayName'],
    { timeout: 15000 })
    let out = ''
    p.stdout.on('data', (d) => { out += d })
    p.on('error', () => { thirdPartyAvCache = null })
    p.on('close', () => {
      const names = out.split(/\r?\n/).map((l) => l.trim()).filter(Boolean)
        .filter((n) => !n.toLowerCase().includes('defender'))
      // 数组逐名返回（F8）：每个第三方 AV 独立一条引导横幅 + 独立确认台账
      thirdPartyAvCache = names.length ? names : null
    })
    return undefined // 首查进行中，不阻塞本次响应
  }

  ipcMain.handle('system:getUvExclusionStatus', async () => {
    if (process.platform !== 'win32') {
      return { verified: true, notApplicable: true }
    }
    const cacheDir = uvCacheDir()
    // ackedNames：第三方确认台账（F8，用户声明制——信任区无通用写入 API，
    // WSC 状态位不可信，唯一可行确认方式是用户点「我已处理」后落盘）
    const readAcked = (data) =>
      (data && data.third_party_ack && Array.isArray(data.third_party_ack.acked_names))
        ? data.third_party_ack.acked_names : []
    // WSC 查中后恒给确定值（null→[]），仅首查进行中（undefined）省略字段——
    // 否则渲染端首响应缺字段无法区分「查中无」与「没查完」，会误判已完毕停轮，
    // 第三方条在已验证机器上永不出现（review H1）
    const withAv = (base) => {
      const tp = detectThirdPartyAv()
      return tp === undefined ? base : { ...base, thirdPartyAv: tp ?? [] }
    }
    try {
      const data = readMarkerData()
      const verified = !!(data && data.ok === true &&
        String(data.cache_dir || '').toLowerCase() === cacheDir.toLowerCase())
      const ackedNames = readAcked(data)
      return withAv({ verified, cacheDir, ackedNames })
    } catch {
      return withAv({ verified: false, cacheDir, ackedNames: [] })
    }
  })

  // 第三方杀软确认（F8）：逐名写台账，marker 读改写（保留 ok/cache_dir）；
  // cache 挪位时 marker 随目录失效 → 台账一并作废，重新引导（语义正确：
  // 信任区里的旧路径已无效）。新装杀软重启后出现（停轮后不再轮询 WSC，
  // 且名单进程内缓存一次）
  ipcMain.handle('system:ackThirdPartyAv', async (_e, name) => {
    if (process.platform !== 'win32' || typeof name !== 'string' || !name.trim()) {
      return { acked: false }
    }
    let data = {}
    try {
      data = JSON.parse(fs.readFileSync(uvVerifiedMarker(), 'utf-8'))
    } catch { /* marker 不存在/损坏 → 新建 */ }
    const ack = (data.third_party_ack && Array.isArray(data.third_party_ack.acked_names))
      ? data.third_party_ack : { acked_names: [] }
    if (!ack.acked_names.includes(name)) ack.acked_names.push(name)
    ack.ts = new Date().toISOString()
    data.third_party_ack = ack
    try {
      fs.mkdirSync(path.dirname(uvVerifiedMarker()), { recursive: true })
      fs.writeFileSync(uvVerifiedMarker(), JSON.stringify(data, null, 2), 'utf-8')
      return { acked: true, ackedNames: ack.acked_names }
    } catch (e) {
      logElectron('WARN', `third-party ack write failed: ${e.message}`)
      return { acked: false }
    }
  })

  // 通用剪贴板（F8）：渲染端 file:// 下 navigator.clipboard 不可靠，
  // 剪贴板操作统一走主进程（同 defenderSkipped 分支的 clipboard.writeText）
  ipcMain.handle('system:copyText', async (_e, text) => {
    if (typeof text !== 'string' || !text) return { copied: false }
    try {
      require('electron').clipboard.writeText(text)
      return { copied: true }
    } catch {
      return { copied: false }
    }
  })

  // 加白：单次提权会话内完成「Add-MpPreference + 提权内回读」——普通权限
  // 下 Get-MpPreference 读 ExclusionPath 直接报 Must be an administrator
  // （2026-10-12 实测），回读必须放进提权上下文，否则加白成功也判失败。
  // 结果回传用内层自写临时文件：PowerShell 5.1 的 Start-Process 参数集
  // 冲突（AmbiguousParameterSet），-Verb RunAs 与 -RedirectStandardOutput
  // 不能同用——曾致 Start-Process 根本不执行（UAC 弹窗都不出现）、stdout
  // 恒空、永远误判失败走降级（2026-10-12 实测根因）。主进程据结果文件
  // 落盘 verified.json（写自己的 data 目录无需提权）。验证失败 → 深链
  // 直达 Defender 排除项页 + 剪贴板路径，降级为手动引导。
  // PowerShell 异步 spawn：UAC 等待最长 2 分钟，同步 spawnSync 会冻结主
  // 进程事件循环（全应用「未响应」）；'error'/'close' 双路 resolve 永不
  // reject，成败统一交由回读结果裁决。
  ipcMain.handle('system:addUvCacheExclusion', async () => {
    if (process.platform !== 'win32') {
      return { added: false, notApplicable: true }
    }
    const cacheDir = uvCacheDir()
    // Defender 服务未运行（被第三方安全中心接管禁用，如实测联想贴牌
    // 火绒停用 WinDefend → Add-MpPreference 报 0x800106ba）时，「验证
    // Defender 排除项」成死锁——而服务停用即 Defender 本就非扫描器，
    // 加白无意义。跳过加白直接落盘裁决解除阻断；第三方杀软指引由横幅
    // 分流文案（F6 thirdPartyAv）承担。查询失败按运行中处理（保守，
    // 不放松阻断，走原提权链路由回读裁决）。
    const defenderRunning = await new Promise((resolve) => {
      const { spawn: spawnPs } = require('child_process')
      const p = spawnPs('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command',
        "(Get-Service WinDefend -ErrorAction SilentlyContinue).Status -eq 'Running'"],
      { timeout: 15000 })
      let out = ''
      p.stdout.on('data', (d) => { out += d })
      p.on('error', () => resolve(true))
      p.on('close', () => resolve(out.trim() === 'True'))
    })
    if (!defenderRunning) {
      try {
        // dev 外置布局下 cache 父目录（如 D:\InkCube-dev-cache）首次授权时
        // 尚不存在（uv 首跑才建 cache），marker 写入前先建，否则 ENOENT 假失败
        // 读改写保留 third_party_ack（review H2：盲写会抹掉用户已确认台账；
        // cache_dir 变化时按设计有意作废台账——信任区旧路径已无效）
        fs.mkdirSync(path.dirname(uvVerifiedMarker()), { recursive: true })
        const prev = readMarkerData()
        const keepAck = String(prev.cache_dir || '').toLowerCase() === cacheDir.toLowerCase()
          ? prev.third_party_ack : undefined
        fs.writeFileSync(uvVerifiedMarker(), JSON.stringify({
          ok: true, cache_dir: cacheDir, note: 'defender-not-running',
          ts: new Date().toISOString(),
          ...(keepAck ? { third_party_ack: keepAck } : {}),
        }, null, 2), 'utf-8')
      } catch (e) {
        logElectron('WARN', `uv-exclusion marker write failed (defender skipped): ${e.message}`)
        return { added: false, fallback: true, cacheDir }
      }
      // 路径引导由第三方横幅条承担（F8：逐 AV 一条 + 复制按钮），不再预复制
      logElectron('INFO', `Defender service not running, exclusion skipped, gate released: ${cacheDir}`)
      return { added: true, defenderSkipped: true }
    }
    const { spawn } = require('child_process')
    const { shell, clipboard } = require('electron')
    const os = require('os')
    // 单引号形式（'' 转义）：路径段含 $ 时双引号会被 PowerShell 插值，
    // 静默写错排除路径（回读 False → 误入 fallback）
    const psPath = cacheDir.replace(/'/g, "''")
    // 结果文件：外层预建（无需提权写 %TEMP%），内层 Set-Content 覆盖写入
    const outPath = path.join(os.tmpdir(), `uv-excl-result-${Date.now()}.txt`)
    const psOut = outPath.replace(/'/g, "''")
    // EncodedCommand（Base64 UTF-16LE）完全避开引号嵌套地狱；两步同会话：
    // 先加白，回读在提权上下文内执行（可信）。Add/Read 分别捕获异常：
    // 防篡改拦截（如火绒拦 Add-MpPreference）、服务异常等都会以
    // ADD_ERR/READ_ERR 行留痕，失败时主进程记入日志（否则失败细节全丢）
    const inner = Buffer.from(
      `$addMsg = ''; ` +
      `try { Add-MpPreference -ExclusionPath '${psPath}' -ErrorAction Stop } catch { $addMsg = $_.Exception.Message }; ` +
      `$ok = $false; $readMsg = ''; ` +
      `try { $ok = (Get-MpPreference).ExclusionPath -contains '${psPath}' } catch { $readMsg = $_.Exception.Message }; ` +
      `$lines = @(); ` +
      `if ($addMsg) { $lines += ('ADD_ERR: ' + $addMsg) }; ` +
      `if ($readMsg) { $lines += ('READ_ERR: ' + $readMsg) }; ` +
      `$lines += ('RESULT=' + $ok); ` +
      `Set-Content -LiteralPath '${psOut}' -Value $lines -Encoding UTF8`,
      'utf16le').toString('base64')
    // 外层仅用 -Verb 参数集合法参数（-Wait -WindowStyle -PassThru）；
    // -EncodedCommand/-NoProfile 是 powershell.exe 的 CLI 参数，须经 -ArgumentList
    // 传入（直写在 Start-Process 上会 NamedParameterNotFound）；
    // UTF8 控制台编码让 Node 端读到的错误消息不乱码
    const elevate = (
      '[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; ' +
      `New-Item -Path '${psOut}' -ItemType File -Force | Out-Null; ` +
      `Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden -PassThru -ArgumentList '-NoProfile','-EncodedCommand','${inner}' | Out-Null; ` +
      `Get-Content -LiteralPath '${psOut}' -Encoding UTF8; ` +
      `Remove-Item -LiteralPath '${psOut}' -ErrorAction SilentlyContinue`
    )
    // 异步包 Promise：'error'（spawn 失败）与 'close'（含 timeout 强杀）
    // 双路 resolve，永不 reject——成败统一交由回读 stdout 裁决
    const result = await new Promise((resolve) => {
      const p = spawn('powershell.exe',
        ['-NoProfile', '-NonInteractive', '-Command', elevate],
        { timeout: 120000 })
      let out = ''
      p.stdout.on('data', (d) => { out += d })
      p.stderr.on('data', (d) => { out += '\n[spawn stderr] ' + d })
      p.on('error', (e) => {
        logElectron('WARN', `uv-cache powershell spawn failed: ${e.message}`)
        resolve(out)
      })
      p.on('close', () => resolve(out))
    })

    // 判定以 RESULT= 行为准；全程（EXIT/ADD_ERR/READ_ERR/STDERR）留痕可查
    const verdict = result.split(/\r?\n/).find((l) => l.startsWith('RESULT='))
    if (verdict === 'RESULT=True') {
      logElectron('INFO', `uv-cache exclusion added & verified: ${cacheDir}`)
      try {
        // 同上：marker 父目录先建（dev 外置布局首次授权时不存在）；
        // 读改写保留 third_party_ack（review H2，cache_dir 变化时作废）
        fs.mkdirSync(path.dirname(uvVerifiedMarker()), { recursive: true })
        const prev = readMarkerData()
        const keepAck = String(prev.cache_dir || '').toLowerCase() === cacheDir.toLowerCase()
          ? prev.third_party_ack : undefined
        fs.writeFileSync(uvVerifiedMarker(), JSON.stringify({
          ok: true, cache_dir: cacheDir, ts: new Date().toISOString(),
          ...(keepAck ? { third_party_ack: keepAck } : {}),
        }, null, 2), 'utf-8')
      } catch (e) {
        // 裁决文件写失败 = 前置阻断无法解除（Python 读不到），视为失败
        logElectron('WARN', `uv-exclusion marker write failed: ${e.message}`)
        return { added: false, fallback: true, cacheDir }
      }
      return { added: true, fallback: false }
    }
    logElectron('WARN', `uv-cache exclusion verify failed, elevation transcript:\n${result}`)

    // 兜底：深链直达排除项页，路径进剪贴板供粘贴
    try {
      clipboard.writeText(cacheDir)
      await shell.openExternal('windowsdefender://exclusions')
    } catch (e) {
      logElectron('WARN', `uv-cache fallback deep-link failed: ${e.message}`)
    }
    return { added: false, fallback: true, cacheDir }
  })

  // 打开知识库原文件（2.2 §10.4）：素材列表 / Wiki 来源面板 / src 页三入口共用。
  // 安全约束（渲染层参数不可信）：filename 取 basename 削平路径成分，
  // projectId/fileId 仅允许安全字符——可打开路径被锁死在 {dataDir}/files/ 子树内。
  ipcMain.handle('file:openOriginal', async (_event, { projectId, fileId, filename }) => {
    const { shell } = require('electron')
    const fs = require('fs')
    const path = require('path')
    const safeSegment = (seg) => (typeof seg === 'string' && /^[A-Za-z0-9_-]+$/.test(seg) ? seg : null)
    const pid = safeSegment(projectId)
    const fid = safeSegment(fileId)
    if (!pid || !fid || typeof filename !== 'string' || !filename) {
      return { success: false, reason: 'invalid_args' }
    }
    const filePath = path.join(getDataDir(), 'files', pid, fid, path.basename(filename))
    if (!fs.existsSync(filePath)) {
      return { success: false, reason: 'not_found' }
    }
    // openPath 返回非空字符串即失败信息（报告产物打开同模式）
    const openErr = await shell.openPath(filePath)
    if (openErr) {
      logElectron('WARN', `openOriginal failed: ${openErr}`)
      return { success: false, reason: 'open_failed', message: openErr }
    }
    return { success: true }
  })

  // 读取容器内文本文件内容（2.11 §8.4 档案阅读页，2026-10-23）：与 file:openOriginal
  // 同构的安全约束（safeSegment + basename 削平，路径锁死 {dataDir}/files/ 子树）。
  // 系统打开边界 = 用户主动上传的文件（素材列表 / Wiki 来源，见上）；应用生成物
  // （订阅档案）应用内渲染——本通道仅限 utf-8 文本、2MB 上限（防误读二进制/超大
  // 文件拖垮渲染进程）。消费方：ArchiveView（订阅档案阅读页）。
  ipcMain.handle('file:readText', async (_event, { projectId, fileId, filename }) => {
    const fs = require('fs')
    const path = require('path')
    const safeSegment = (seg) => (typeof seg === 'string' && /^[A-Za-z0-9_-]+$/.test(seg) ? seg : null)
    const pid = safeSegment(projectId)
    const fid = safeSegment(fileId)
    if (!pid || !fid || typeof filename !== 'string' || !filename) {
      return { success: false, reason: 'invalid_args' }
    }
    const filePath = path.join(getDataDir(), 'files', pid, fid, path.basename(filename))
    if (!fs.existsSync(filePath)) {
      return { success: false, reason: 'not_found' }
    }
    try {
      if (fs.statSync(filePath).size > 2 * 1024 * 1024) {
        return { success: false, reason: 'too_large' }
      }
      const content = fs.readFileSync(filePath, 'utf-8')
      // abs_path 供「在文件夹中显示」（shell:showItemInFolder）定位逃生门
      return { success: true, content, abs_path: filePath }
    } catch (err) {
      logElectron('WARN', `readText failed: ${err}`)
      return { success: false, reason: 'read_failed', message: String(err) }
    }
  })

  ipcMain.handle('settings:get', async () => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/settings`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('settings:save', async (_event, settings) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/settings`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('settings:personal_test', async (_event, settings) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/settings/personal_test`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    }, 60000) // LLM 测试连接，放宽超时
    return handlePythonResponse(res)
  })

  ipcMain.handle('models:status', async () => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/models/status`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('models:download', async (_event, name) => {
    // 模型名进 URL path：统一 encodeURIComponent（与文件内其它通道的编码惯例
    // 一致；当前 name 来自后端受控列表，防未来开放自定义名时的路径注入）
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/models/${encodeURIComponent(name)}/download`, {
      method: 'POST',
    })
    return handlePythonResponse(res)
  })

  // 单模型删除（2026-10-20 删除功能审计补齐）：业务失败（捆绑/下载中/删失败）
  // 为 200 + ok:false，随响应体透传
  ipcMain.handle('models:delete', async (_event, name) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/models/${encodeURIComponent(name)}`, {
      method: 'DELETE',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('models:download-all', async () => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/models/download-all`, {
      method: 'POST',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('models:libreoffice-status', async () => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/models/libreoffice/status`)
    return handlePythonResponse(res)
  })

  // ── Knowledge Base ──

  ipcMain.handle('knowledge:listProjects', async (_event, { archived, limit, offset } = {}) => {
    const params = new URLSearchParams()
    if (archived) params.set('archived', 'true')
    if (limit !== undefined && limit !== null) params.set('limit', String(limit))
    if (offset) params.set('offset', String(offset))
    const qs = params.toString()
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/projects${qs ? `?${qs}` : ''}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('knowledge:createProject', async (_event, { name, description, type, stage, companyName, creditCode }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/projects`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, description, type: type || 'general', stage: stage || 'none', company_name: companyName || '', credit_code: creditCode || '' }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('knowledge:getFiles', async (_event, { projectId, limit, offset, status, fileType, filenameSearch }) => {
    const params = new URLSearchParams({ limit: String(limit ?? 20) })
    if (offset) params.set('offset', String(offset))
    if (status && status !== 'all') params.set('status', status)
    if (fileType) params.set('file_type', fileType)
    if (filenameSearch) params.set('filename_search', filenameSearch)
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/files?${params}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('knowledge:getActiveFiles', async (_event, projectId) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/files/active`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('knowledge:uploadFile', async (_event, { projectId, filename, fileType, data }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/upload`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ projectId, filename, fileType, data }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('knowledge:deleteFile', async (_event, { projectId, fileId }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/files/${fileId}`, {
      method: 'DELETE',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('knowledge:retryFile', async (_event, { projectId, fileId }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/files/${fileId}/retry`, {
      method: 'POST',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('knowledge:retryFailedFiles', async (_event, projectId) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/files/retry-failed`, {
      method: 'POST',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('knowledge:recentChunks', async (_event, { projectId, limit, fileId }) => {
    const params = new URLSearchParams({ limit: String(limit ?? 20) })
    if (fileId) params.set('file_id', fileId)
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/recent-chunks?${params}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('knowledge:search', async (_event, { projectId, query, topK, contentTypeFilter, fileId }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/search`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ projectId, query, topK, contentTypeFilter, fileId }),
    })
    return handlePythonResponse(res)
  })

  // 删除准入 = 已归档；409 PROJECT_NOT_ARCHIVED 经返回值透传（structuredPythonResponse）
  ipcMain.handle('knowledge:deleteProject', async (_event, projectId) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}`, {
      method: 'DELETE',
    })
    return structuredPythonResponse(res)
  })

  ipcMain.handle('knowledge:renameProject', async (_event, { projectId, name }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    })
    return handlePythonResponse(res)
  })

  // 2026-10-23 编辑补全（todo：项目创建后不能修改）：透传企业字段（PATCH 语义
  // 不传不改，与后端 ProjectUpdate 对齐）。消费方：详情页头部「编辑」弹层 +
  // type/stage 下拉/脉搏带；侧栏重命名走 knowledge:renameProject（仅名称，独立通道）
  ipcMain.handle('knowledge:updateProject', async (_event, { projectId, name, description, type, stage, company_name, credit_code }) => {
    const body = {}
    if (name !== undefined) body.name = name
    if (description !== undefined) body.description = description
    if (type !== undefined) body.type = type
    if (stage !== undefined) body.stage = stage
    if (company_name !== undefined) body.company_name = company_name
    if (credit_code !== undefined) body.credit_code = credit_code
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    return handlePythonResponse(res)
  })

  // 项目归档/恢复（生命周期软关闭；409 守门 code 经返回值透传，见 structuredPythonResponse）
  ipcMain.handle('knowledge:archiveProject', async (_event, projectId) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/archive`, {
      method: 'POST',
    })
    return structuredPythonResponse(res)
  })

  ipcMain.handle('knowledge:unarchiveProject', async (_event, projectId) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/unarchive`, {
      method: 'POST',
    })
    return structuredPythonResponse(res)
  })

  // Image management
  ipcMain.handle('knowledge:getProjectImages', async (_event, { projectId, limit, offset, fileId, status, category, search, size }) => {
    const params = new URLSearchParams({ limit: String(limit ?? 40), offset: String(offset ?? 0) })
    if (fileId) params.set('file_id', fileId)
    if (status) params.set('status', status)
    if (category) params.set('category', category)
    if (search) params.set('search', search)
    if (size) params.set('size', size)
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/images?${params}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('knowledge:getImages', async (_event, { projectId, fileId }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/files/${fileId}/images`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('knowledge:deleteImage', async (_event, { projectId, imageId }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/images/${imageId}`, {
      method: 'DELETE',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('knowledge:retryImage', async (_event, { projectId, imageId, force }) => {
    const url = `${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/images/${imageId}/retry`
    const params = force ? '?force=true' : ''
    const res = await fetchWithTimeout(url + params, { method: 'POST' })
    return handlePythonResponse(res)
  })

  ipcMain.handle('knowledge:retryFailedImages', async (_event, { projectId, fileId }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/files/${fileId}/images/retry-failed`, {
      method: 'POST',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('knowledge:updateImageCategory', async (_event, { projectId, imageId, category }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/images/${imageId}/category`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ category }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('knowledge:archiveImage', async (_event, { projectId, imageId }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/images/${imageId}/archive`, {
      method: 'POST',
    })
    return handlePythonResponse(res)
  })

  // 队列暂停/恢复（0.4 §队列暂停）：kind = parsing | describe | compile | report
  ipcMain.handle('knowledge:queuePause', async (_event, { kind, paused }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/queue/pause`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind, paused }),
    })
    return handlePythonResponse(res)
  })

  // Batch operations
  ipcMain.handle('knowledge:batchDeleteFiles', async (_event, { projectId, fileIds }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/files/batch-delete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_ids: fileIds }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('knowledge:batchReprocessFiles', async (_event, { projectId, fileIds }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/files/batch-reprocess`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_ids: fileIds }),
    })
    return handlePythonResponse(res)
  })

  // Knowledge base chat (RAG Q&A) — independent from agent chat
  ipcMain.handle('knowledge:chat', async (_event, { projectId, message, fileId }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, fileId }),
    })
    return handlePythonResponse(res)
  })

  // ── Base Library ──

  ipcMain.handle('baseLib:list', async (_event, { limit, offset } = {}) => {
    const params = new URLSearchParams()
    if (limit !== undefined && limit !== null) params.set('limit', String(limit))
    if (offset) params.set('offset', String(offset))
    const qs = params.toString()
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/base-libs${qs ? `?${qs}` : ''}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('baseLib:getFiles', async (_event, { libId, projectId, limit, offset, status, fileType, filenameSearch }) => {
    const id = libId || projectId
    const params = new URLSearchParams({ limit: String(limit ?? 20) })
    if (offset) params.set('offset', String(offset))
    if (status && status !== 'all') params.set('status', status)
    if (fileType) params.set('file_type', fileType)
    if (filenameSearch) params.set('filename_search', filenameSearch)
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/base-libs/${id}/files?${params}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('baseLib:getActiveFiles', async (_event, libId) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/base-libs/${libId}/files/active`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('baseLib:upload', async (_event, { libId, projectId, filename, fileType, data }) => {
    const id = libId || projectId
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/base-libs/${id}/upload`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ projectId: id, filename, fileType, data }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('baseLib:deleteFile', async (_event, { libId, projectId, fileId }) => {
    const id = libId || projectId
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/base-libs/${id}/files/${fileId}`, {
      method: 'DELETE',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('baseLib:retryFile', async (_event, { libId, projectId, fileId }) => {
    const id = libId || projectId
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/base-libs/${id}/files/${fileId}/retry`, {
      method: 'POST',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('baseLib:search', async (_event, { libId, projectId, query, topK, contentTypeFilter, fileId }) => {
    const id = libId || projectId
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/base-libs/${id}/search`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ projectId: id, query, topK, contentTypeFilter, fileId }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('baseLib:recentChunks', async (_event, { libId, projectId, limit, fileId }) => {
    const id = libId || projectId
    const params = new URLSearchParams({ limit: String(limit ?? 20) })
    if (fileId) params.set('file_id', fileId)
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/base-libs/${id}/recent-chunks?${params}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('baseLib:retryFailedFiles', async (_event, libId) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/base-libs/${libId}/files/retry-failed`, {
      method: 'POST',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('baseLib:batchDeleteFiles', async (_event, { libId, projectId, fileIds }) => {
    const id = libId || projectId
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/base-libs/${id}/files/batch-delete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_ids: fileIds }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('baseLib:batchReprocessFiles', async (_event, { libId, projectId, fileIds }) => {
    const id = libId || projectId
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/base-libs/${id}/files/batch-reprocess`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_ids: fileIds }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('baseLib:getImages', async (_event, { libId, projectId, limit, offset, fileId, status, category, search, size }) => {
    const id = libId || projectId
    const params = new URLSearchParams({ limit: String(limit ?? 40), offset: String(offset ?? 0) })
    if (fileId) params.set('file_id', fileId)
    if (status) params.set('status', status)
    if (category) params.set('category', category)
    if (search) params.set('search', search)
    if (size) params.set('size', size)
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/base-libs/${id}/images?${params}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('baseLib:chat', async (_event, { libId, projectId, message, fileId }) => {
    const id = libId || projectId
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/base-libs/${id}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, fileId }),
    })
    return handlePythonResponse(res)
  })

  // ── Project-BaseLib Association ──

  ipcMain.handle('project:getBaseLibs', async (_event, projectId) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/base-libs`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('project:linkBaseLib', async (_event, { projectId, baseLibId }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/base-libs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ base_lib_id: baseLibId }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('project:unlinkBaseLib', async (_event, { projectId, baseLibId }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/base-libs/${baseLibId}`, {
      method: 'DELETE',
    })
    return handlePythonResponse(res)
  })

  // ── Task Templates (0.5 §5, E2) ──

  ipcMain.handle('taskTemplates:list', async (_event, { scope, projectId } = {}) => {
    const params = new URLSearchParams()
    if (scope) params.set('scope', scope)
    if (projectId) params.set('projectId', projectId)
    const qs = params.toString()
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/task-templates${qs ? `?${qs}` : ''}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('taskTemplates:save', async (_event, body) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/task-templates`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('taskTemplates:delete', async (_event, id) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/task-templates/${encodeURIComponent(String(id))}`, {
      method: 'DELETE',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('taskTemplates:togglePin', async (_event, { id, pinned }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/task-templates/${encodeURIComponent(String(id))}/pin`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pinned }),
    })
    return handlePythonResponse(res)
  })

  // ── Tasks ──

  ipcMain.handle('tasks:create', async (_event, { title, workspacePath, mode }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/tasks`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title: title || 'Untitled',
        workspace_path: workspacePath || '',
        mode: mode || 'coding',
      }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('tasks:delete', async (_event, taskId) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/tasks/${taskId}`, {
      method: 'DELETE',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('tasks:rename', async (_event, { taskId, title }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/tasks/${taskId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('tasks:setMode', async (_event, { taskId, mode }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/tasks/${taskId}/mode`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('tasks:getMessages', async (_event, taskId) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/tasks/${taskId}/messages`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('tasks:chat', async (_event, { taskId, message, resume }) => {
    const baseUrl = pythonManager.getBaseUrl()
    // Abort the upstream SSE fetch when the renderer goes away (window
    // closed / app quit) — otherwise this loop keeps streaming into a
    // destroyed webContents until the task finishes (M2).
    const controller = new AbortController()
    const onSenderDestroyed = () => controller.abort()
    _event.sender.once('destroyed', onSenderDestroyed)

    try {
      const baseUrl = pythonManager.getBaseUrl()
      // SSE 流转发（任务运行可能持续分钟级）：不适用 fetchWithTimeout，
      // 生命周期由 controller.signal 管理（渲染进程销毁时 abort）
      const response = await fetch(`${baseUrl}/tasks/${taskId}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task_id: taskId, message, resume: resume || false }),
        signal: controller.signal,
      })

      if (!response.ok) {
        const body = await response.json().catch(() => null)
        // Backend errors use {error, code} (e.g. 409 TASK_RUNNING / 429
        // TASK_LIMIT_REACHED); surface them so the frontend can react.
        const err = new Error(body?.message || body?.error || `Python backend error (${response.status})`)
        err.errorCode = body?.code || 'UNKNOWN_ERROR'
        err.errorCategory = body?.category || 'INTERNAL'
        err.errorSuggestion = body?.suggestion || ''
        err.errorDetail = body?.detail || ''
        throw err
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let currentEvent = 'message'

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7).trim()
          } else if (line.startsWith('data: ')) {
            const data = line.slice(6)
            if (!_event.sender.isDestroyed()) {
              _event.sender.send('task:stream', { taskId, type: currentEvent, data })
            }
            currentEvent = 'message'
          } else if (line === '') {
            currentEvent = 'message'
          }
        }
      }
    } catch (err) {
      // Aborting is an expected exit (renderer gone), not an error
      if (controller.signal.aborted) return { aborted: true }
      throw err
    } finally {
      _event.sender.removeListener('destroyed', onSenderDestroyed)
    }
  })

  ipcMain.handle('tasks:cancelChat', async (_event, taskId) => {
    const baseUrl = pythonManager.getBaseUrl()
    await fetchWithTimeout(`${baseUrl}/tasks/${taskId}/chat/cancel`, { method: 'POST' })
  })

  ipcMain.handle('tasks:confirmShell', async (_event, { taskId, confirmed, allow_session }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/tasks/${taskId}/confirm-shell`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirmed, allow_session: allow_session || false }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('tasks:confirmSkill', async (_event, { taskId, confirmed }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/tasks/${taskId}/confirm-skill`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirmed }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('tasks:getFiles', async (_event, taskId) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/tasks/${taskId}/files`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('tasks:readFile', async (_event, { taskId, path, b64 }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/tasks/${taskId}/files/content?path=${encodeURIComponent(path)}${b64 ? '&b64=1' : ''}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('tasks:getStatus', async () => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/tasks/status`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('tasks:hasState', async (_event, taskId) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/tasks/${taskId}/has-state`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('tasks:listFiltered', async (_event, { status, limit, offset } = {}) => {
    const params = new URLSearchParams()
    if (status) params.set('status', status)
    if (limit !== undefined && limit !== null) params.set('limit', String(limit))
    if (offset) params.set('offset', String(offset))
    // 2.3 §3.1 形态分离：本通道唯一消费者是任务 Tab，PPT 任务（origin='ppt'）永不入列
    params.set('origin', 'chat')
    const qs = params.toString()
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/tasks${qs ? `?${qs}` : ''}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('tasks:archive', async (_event, taskId) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/tasks/${taskId}/archive`, {
      method: 'POST',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('tasks:unarchive', async (_event, taskId) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/tasks/${taskId}/unarchive`, {
      method: 'POST',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('tasks:export', async (_event, taskId) => {
    const { dialog, BrowserWindow } = require('electron')
    const fs = require('fs')
    const baseUrl = pythonManager.getBaseUrl()

    // Get task info for the filename
    const taskData = await handlePythonResponse(
      await fetchWithTimeout(`${baseUrl}/tasks/${taskId}`)
    )
    const task = taskData.task
    if (!task) return { error: 'Task not found' }

    const win = BrowserWindow.getFocusedWindow()
    const safeName = task.title.replace(/[\/\\]/g, '_').slice(0, 50)
    const { canceled, filePath } = await dialog.showSaveDialog(win || undefined, {
      title: 'Export Task',
      defaultPath: `${safeName}_${taskId.slice(0, 8)}.zip`,
      filters: [{ name: 'ZIP Archive', extensions: ['zip'] }],
    })
    if (canceled || !filePath) return { canceled: true }

    const res = await fetchWithTimeout(`${baseUrl}/tasks/${taskId}/export`, {}, 60000) // zip 导出，放宽超时
    const contentType = res.headers.get('content-type') || ''
    if (!res.ok || !contentType.includes('application/zip')) {
      const body = await res.json().catch(() => null)
      return { error: body?.error || 'Export failed' }
    }
    const buffer = Buffer.from(await res.arrayBuffer())
    fs.writeFileSync(filePath, buffer)
    return { success: true, path: filePath }
  })

  ipcMain.handle('tasks:selectDirectory', async () => {
    const { dialog } = require('electron')
    const result = await dialog.showOpenDialog({
      properties: ['openDirectory'],
    })
    if (result.canceled || result.filePaths.length === 0) {
      return { canceled: true }
    }
    return { path: result.filePaths[0] }
  })

  // ── Skills ──

  ipcMain.handle('skills:list', async (_event, { limit, offset } = {}) => {
    const params = new URLSearchParams()
    if (limit !== undefined && limit !== null) params.set('limit', String(limit))
    if (offset) params.set('offset', String(offset))
    const qs = params.toString()
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/skills${qs ? `?${qs}` : ''}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('skills:install', async (_event, { source, sourceType }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/skills/install`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source, source_type: sourceType }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('skills:delete', async (_event, skillId) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/skills/${skillId}`, {
      method: 'DELETE',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('skills:toggle', async (_event, { skillId, enabled }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/skills/${skillId}/toggle`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('skills:confirm', async (_event, skillId) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/skills/${skillId}/confirm`, {
      method: 'POST',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('skills:browseLocal', async () => {
    const { dialog } = require('electron')
    const result = await dialog.showOpenDialog({
      properties: ['openDirectory'],
    })
    if (result.canceled || result.filePaths.length === 0) {
      return {}
    }
    return { path: result.filePaths[0] }
  })

  ipcMain.handle('skills:builtinTools', async () => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/skills/builtin-tools`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('tasks:uploadFiles', async (_event, { taskId, filePaths }) => {
    return _uploadFilesToTask(pythonManager, taskId, filePaths)
  })

  ipcMain.handle('tasks:pickAndUpload', async (_event, { taskId }) => {
    const { dialog } = require('electron')
    const result = await dialog.showOpenDialog({
      properties: ['openFile', 'multiSelections'],
    })
    if (result.canceled || result.filePaths.length === 0) {
      return { uploaded: [] }
    }
    return _uploadFilesToTask(pythonManager, taskId, result.filePaths)
  })

  // 剪贴板截图直传：渲染进程 onPaste 捕获的 image/* Blob 无磁盘路径
  //（webUtils.getPathForFile 不适用）→ base64 过 IPC → main 解码后与磁盘
  // 文件共用同一 multipart 上传与 python 端点（落盘 + 注入上传通知）
  ipcMain.handle('tasks:uploadClipboardImage', async (_event, { taskId, dataBase64 }) => {
    if (typeof dataBase64 !== 'string' || !dataBase64) {
      return { error: 'empty clipboard image payload', code: 'INVALID_PAYLOAD' }
    }
    // base64 护栏（解码后约 3/4 ≈ 30MB）：截图一般 <5MB。该护栏是本路径唯一
    // 生效的尺寸限制（解码后 ≤30MB 触不到 python 端点的 50MB 校验）；拒绝经
    // 返回值 {error, code} 透传，前端判 error 转 toast——勿改成静默吞掉
    if (dataBase64.length > 40 * 1024 * 1024) {
      return { error: 'clipboard image too large (base64 cap 40MB)', code: 'FILE_TOO_LARGE' }
    }
    // 文件名经 clipboard-image-name.js 纯函数：JPEG 魔数嗅探定扩展名（恒 .png
    // 会把 JPEG 字节谎报为 PNG 被 vlm 严格端点拒收）+ 毫秒时间戳（端点对同名
    // 覆盖写，秒级时间戳在同秒多图/连续粘贴时静默丢图）
    const data = Buffer.from(dataBase64, 'base64')
    const name = clipboardImageName(data, new Date())
    return _postMultipartFiles(pythonManager, taskId, [{ name, data }])
  })

  // ── Pause ──
  ipcMain.handle('tasks:pause', async (_event, { taskId }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/tasks/${taskId}/pause`, {
      method: 'POST',
    })
    return handlePythonResponse(res)
  })

  // ── Execution Logs ──
  ipcMain.handle('tasks:getLogs', async (_event, { taskId, lines }) => {
    const params = lines ? `?lines=${lines}` : ''
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/tasks/${taskId}/logs${params}`)
    return handlePythonResponse(res)
  })

  // ── Skills list (for slash command autocomplete) ──
  ipcMain.handle('tasks:getSkills', async () => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/skills`)
    return handlePythonResponse(res)
  })

  // ── Schedules (2.11 §4.2, E3)；rebuildArchive 通道随档案实现（E3 T10）同变更落地 ──

  ipcMain.handle('schedules:list', async (_event, { category, enabled, q, limit, offset } = {}) => {
    const params = new URLSearchParams()
    if (category) params.set('category', category)
    if (enabled !== undefined && enabled !== null) params.set('enabled', String(enabled))
    if (q) params.set('q', q)
    if (limit !== undefined && limit !== null) params.set('limit', String(limit))
    if (offset) params.set('offset', String(offset))
    const qs = params.toString()
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/schedules${qs ? `?${qs}` : ''}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('schedules:create', async (_event, body) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/schedules`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('schedules:update', async (_event, { id, ...fields }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/schedules/${encodeURIComponent(String(id))}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(fields),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('schedules:delete', async (_event, id) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/schedules/${encodeURIComponent(String(id))}`, {
      method: 'DELETE',
    })
    return handlePythonResponse(res)
  })

  // 启停开关专用（2.11 §4.2「=PATCH enabled」）；其余编辑走 schedules:update
  ipcMain.handle('schedules:toggle', async (_event, { id, enabled }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/schedules/${encodeURIComponent(String(id))}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('schedules:runs', async (_event, { id, limit, offset } = {}) => {
    const params = new URLSearchParams()
    if (limit !== undefined && limit !== null) params.set('limit', String(limit))
    if (offset) params.set('offset', String(offset))
    const qs = params.toString()
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/schedules/${encodeURIComponent(String(id))}/runs${qs ? `?${qs}` : ''}`)
    return handlePythonResponse(res)
  })

  // run-now 异步受理（review P2-6）：后端受理即返回（执行在后台完成，终态
  // 走通知中心）；超时参数保留为防御（正常毫秒级返回）
  ipcMain.handle('schedules:runNow', async (_event, id) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/schedules/${encodeURIComponent(String(id))}/run-now`, {
      method: 'POST',
    }, 600000)
    return handlePythonResponse(res)
  })

  // §6 重建档案：同步等待全部分批 LLM 合并完成，超时对齐 runNow（600s）
  ipcMain.handle('schedules:rebuildArchive', async (_event, id) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/schedules/${encodeURIComponent(String(id))}/rebuild-archive`, {
      method: 'POST',
    }, 600000)
    return handlePythonResponse(res)
  })

  ipcMain.handle('schedules:summary', async () => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/schedules/summary`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('schedules:digest', async () => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/schedules/digest`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('schedules:inbox', async (_event, { unread, limit, offset } = {}) => {
    const params = new URLSearchParams()
    if (unread) params.set('unread', '1')
    if (limit !== undefined && limit !== null) params.set('limit', String(limit))
    if (offset) params.set('offset', String(offset))
    const qs = params.toString()
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/schedules/runs/inbox${qs ? `?${qs}` : ''}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('schedules:markRead', async (_event, runId) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/schedules/runs/${encodeURIComponent(String(runId))}/read`, {
      method: 'POST',
    })
    return handlePythonResponse(res)
  })

  // 单条删除期次（2.11 §4.1，2026-10-20 审计补齐）：终态 run 可删，running 409
  ipcMain.handle('schedules:deleteRun', async (_event, runId) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/schedules/runs/${encodeURIComponent(String(runId))}`, {
      method: 'DELETE',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('schedules:readReport', async (_event, runId) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/schedules/runs/${encodeURIComponent(String(runId))}/report`)
    return handlePythonResponse(res)
  })

  // LLM 提炼（单次调用，同步等待）：对齐 LLM 测试连接 60s
  ipcMain.handle('schedules:refinePrompt', async (_event, { taskId }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/schedules/refine-prompt`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ taskId }),
    }, 60000)
    return handlePythonResponse(res)
  })

  // ── External Dependencies Check ──
  ipcMain.handle('system:checkDeps', async () => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/system/deps/check`)
    return handlePythonResponse(res)
  })

  // ── Reports & Templates ──

  ipcMain.handle('dialog:openFile', async (_event, { filters, properties }) => {
    const { dialog } = require('electron')
    return dialog.showOpenDialog({
      filters: filters || [{ name: 'All Files', extensions: ['*'] }],
      properties: properties || ['openFile'],
    })
  })

  ipcMain.handle('reports:listTemplates', async (_event, { projectType, stage }) => {
    const params = new URLSearchParams()
    if (projectType) params.set('project_type', projectType)
    if (stage) params.set('stage', stage)
    const qs = params.toString() ? `?${params.toString()}` : ''
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/reports/templates${qs}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('reports:getTemplate', async (_event, templateId) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/reports/templates/${templateId}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('reports:saveTemplate', async (_event, { templateJson }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/reports/templates`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ template_json: templateJson }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('reports:updateTemplate', async (_event, { templateId, templateJson }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/reports/templates/${templateId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ template_json: templateJson }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('reports:copyTemplate', async (_event, { templateId, newName }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/reports/templates/${templateId}/copy`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ new_name: newName }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('reports:deleteTemplate', async (_event, templateId) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/reports/templates/${templateId}`, {
      method: 'DELETE',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('reports:upgradeTemplate', async (_event, templateId) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/reports/templates/${templateId}/upgrade`, {
      method: 'POST',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('reports:extractSample', async (_event, { filePath }) => {
    const fs = require('fs')
    const path = require('path')
    const fileData = fs.readFileSync(filePath)
    const fileName = path.basename(filePath)
    const boundary = '----InkCubeBoundary' + Date.now()

    const parts = []
    parts.push(
      Buffer.from(
        `--${boundary}\r\n` +
        `Content-Disposition: form-data; name="file"; filename="${fileName}"\r\n` +
        `Content-Type: application/octet-stream\r\n\r\n`
      )
    )
    parts.push(fileData)
    parts.push(Buffer.from(`\r\n--${boundary}--\r\n`))

    const body = Buffer.concat(parts)
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/reports/templates/extract-sample`, {
      method: 'POST',
      headers: { 'Content-Type': `multipart/form-data; boundary=${boundary}` },
      body: body,
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('reports:refreshTemplates', async () => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/reports/refresh-templates`, {
      method: 'POST',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('reports:listReports', async (_event, { projectId, limit, offset } = {}) => {
    const params = new URLSearchParams({ project_id: encodeURIComponent(projectId) })
    if (limit !== undefined && limit !== null) params.set('limit', String(limit))
    if (offset) params.set('offset', String(offset))
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/reports?${params}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('reports:deleteReport', async (_event, reportId) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/reports/${reportId}`, {
      method: 'DELETE',
    })
    return handlePythonResponse(res)
  })

  // Phase 3: Report generation (background task + progress polling)
  ipcMain.handle('reports:generateReport', async (_event, { projectId, templateId, title, variables, baseProjectIds, outputFormat, supplementaryNotes, sectionInternet, internetEnabled }) => {
    // Note: generation runs as a backend background task; the frontend polls report
    // status/logs for progress. This handler returns the report_id for tracking.
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/reports/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        project_id: projectId,
        template_id: templateId,
        title,
        variables: variables || {},
        base_project_ids: baseProjectIds || [],
        output_format: outputFormat || 'docx',
        supplementary_notes: supplementaryNotes || null,
        section_internet: sectionInternet || {},
        internet_enabled: !!internetEnabled,
      }),
    })
    return handlePythonResponse(res)
  })

  // Phase 4: Report content retrieval
  ipcMain.handle('reports:getReportContent', async (_event, reportId) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/reports/${reportId}/content`)
    return handlePythonResponse(res)
  })

  // Phase 4: Report export (Word/docx) — 导出面板两形式（2.5 §1.3）：
  // saveAs=false 直接打开：渲染到产物目录默认位置后 shell.openPath 系统默认程序打开；
  // saveAs=true 另存为：showSaveDialog 选定路径 → 请求体 target_path 渲染到指定路径。
  // 两形式共用 orientation（portrait/landscape → pandoc reference-doc 样式模板）
  ipcMain.handle('reports:exportReport', async (_event, { reportId, title, orientation, saveAs }) => {
    const { dialog, BrowserWindow, shell } = require('electron')
    let targetPath
    if (saveAs) {
      const win = BrowserWindow.getFocusedWindow()
      // 默认文件名 {标题}.docx（sanitize 与后端 _sanitize_filename 同规则）
      const safeName = sanitizeFilename(title, '报告')
      const { canceled, filePath } = await dialog.showSaveDialog(win || undefined, {
        title: 'Export Report',
        defaultPath: `${safeName}.docx`,
        filters: [{ name: 'Word Document', extensions: ['docx'] }],
      })
      if (canceled || !filePath) return { canceled: true }
      targetPath = filePath
    }
    const res = await fetchWithTimeout(
      `${pythonManager.getBaseUrl()}/reports/${reportId}/export`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          orientation: orientation === 'landscape' ? 'landscape' : 'portrait',
          ...(targetPath ? { target_path: targetPath } : {}),
        }),
      },
      90000 // pandoc 渲染（含 mermaid 桥）可能慢于默认 15s，放宽至渲染超时之上
    )
    const result = await handlePythonResponse(res)
    if (saveAs) {
      logElectron('INFO', `Report exported to ${result.path}`)
      return result // 另存为：path 由前端 toast 展示完整路径
    }
    // 直接打开：shell.openPath 返回非空字符串即失败信息
    const openErr = await shell.openPath(result.path)
    if (openErr) {
      logElectron('WARN', `Failed to open exported report ${result.path}: ${openErr}`)
      return { ...result, openError: openErr }
    }
    return { ...result, opened: true }
  })

  // ── Projects: 导出/导入交换包（2.9project-transfer-design §3.3）──
  // 对话框只选路径，请求体只传路径（不 base64）；导出同步返回统计（大项目分钟级，
  // 超时放宽 600s）；导入仅快速校验即返回，后台导入进度走统一活动心跳。
  ipcMain.handle('projects:exportProject', async (_event, { projectId, projectName }) => {
    const { dialog, BrowserWindow } = require('electron')
    const win = BrowserWindow.getFocusedWindow()
    const safeName = sanitizeFilename(projectName || '项目', '项目')
    const now = new Date()
    const stamp = `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, '0')}${String(now.getDate()).padStart(2, '0')}`
    const { canceled, filePath } = await dialog.showSaveDialog(win || undefined, {
      title: 'Export Project',
      defaultPath: `${safeName}-${stamp}.inkproj`,
      filters: [{ name: 'InkCube Project', extensions: ['inkproj'] }],
    })
    if (canceled || !filePath) return { canceled: true }
    const res = await fetchWithTimeout(
      `${pythonManager.getBaseUrl()}/knowledge/projects/${encodeURIComponent(projectId)}/export`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_path: filePath }),
      },
      600000, // 大项目导出分钟级：对齐报告导出的放宽策略，取更宽松值
    )
    const result = await handlePythonResponse(res)
    logElectron('INFO', `Project exported to ${result.path}`)
    return { canceled: false, ...result }
  })

  ipcMain.handle('projects:importProject', async () => {
    const { dialog, BrowserWindow } = require('electron')
    const win = BrowserWindow.getFocusedWindow()
    const { canceled, filePaths } = await dialog.showOpenDialog(win || undefined, {
      title: 'Import Project',
      filters: [{ name: 'InkCube Project', extensions: ['inkproj', 'zip'] }],
      properties: ['openFile'],
    })
    if (canceled || !filePaths[0]) return { canceled: true }
    const res = await fetchWithTimeout(
      `${pythonManager.getBaseUrl()}/knowledge/projects/import`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source_path: filePaths[0] }),
      },
      120000, // 仅快速校验（zip-slip/manifest/embedder），正常秒级；大包解压校验放宽
    )
    const result = await handlePythonResponse(res)
    logElectron('INFO', `Project import started: ${result.project_id}`)
    return { canceled: false, ...result }
  })

  // Retry failed report
  ipcMain.handle('reports:retryReport', async (_event, { reportId, mode }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/reports/${reportId}/retry`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: mode || 'resume' }),
    })
    return handlePythonResponse(res)
  })

  // Request report-level pause (takes effect at section boundary, 2.5 §3.1)
  ipcMain.handle('reports:pauseReport', async (_event, { reportId }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/reports/${reportId}/pause`, {
      method: 'POST',
    })
    return handlePythonResponse(res)
  })

  // Get report generation logs
  ipcMain.handle('reports:getReportLogs', async (_event, { reportId, limit }) => {
    const url = new URL(`${pythonManager.getBaseUrl()}/reports/${reportId}/logs`)
    if (limit) url.searchParams.set('limit', String(limit))
    const res = await fetchWithTimeout(url.toString())
    return handlePythonResponse(res)
  })

  // ── PPT 交付物（2.3ppt-export-design §6.3）──
  // 除另存为/打开文件夹走本机文件 IPC 外全为 HTTP 转发；
  // SVG 预览复用 tasks:readFile（task_id 在 ppt 详情内返回）。
  ipcMain.handle('ppt:list', async (_event, { projectId, limit, offset } = {}) => {
    const params = new URLSearchParams()
    if (limit !== undefined && limit !== null) params.set('limit', String(limit))
    if (offset) params.set('offset', String(offset))
    const qs = params.toString()
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/projects/${projectId}/ppts${qs ? `?${qs}` : ''}`)
    return handlePythonResponse(res)
  })

  // imageSelection：{categories, minWidth, pinnedIds}（2.3 §4.1 E2 复合选择，null = 不圈池）
  ipcMain.handle('ppt:create', async (_event, { projectId, title, sourceReportId, imageSelection, styleRef, notes }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/projects/${projectId}/ppts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title,
        source_report_id: sourceReportId || null,
        image_selection: imageSelection
          ? {
              categories: imageSelection.categories || [],
              min_width: imageSelection.minWidth ?? null,
              pinned_ids: imageSelection.pinnedIds || [],
            }
          : null,
        style_ref: styleRef || null,
        notes: notes || null,
      }),
    }, 120000) // 素材就位含 mermaid 同步重绘（秒级×块数、单块上限 30s），放宽对齐 form-template:upload
    return handlePythonResponse(res)
  })

  ipcMain.handle('ppt:get', async (_event, pptId) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/ppts/${pptId}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('ppt:delete', async (_event, pptId) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/ppts/${pptId}`, {
      method: 'DELETE',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('ppt:answer', async (_event, { pptId, text }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/ppts/${pptId}/answer`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: text || null }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('ppt:pause', async (_event, pptId) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/ppts/${pptId}/pause`, {
      method: 'POST',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('ppt:resume', async (_event, pptId) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/ppts/${pptId}/resume`, {
      method: 'POST',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('ppt:cancel', async (_event, pptId) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/ppts/${pptId}/cancel`, {
      method: 'POST',
    })
    return handlePythonResponse(res)
  })

  // 成品增强（2.3 §4.3 E3）：从项目内交付物 / 本机导入 pptx 两条入口，全 HTTP 转发；
  // structured 透传错误码（NOT_ENHANCEABLE/ENHANCE_IN_PROGRESS 等），渲染层判 res.ok
  ipcMain.handle('ppt:enhance', async (_event, { pptId, notes }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/ppts/${pptId}/enhance`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ notes: notes || null }),
    }, 120000) // prepare 同步拷源 pptx 到任务 staging，放宽对齐 ppt:create
    return structuredPythonResponse(res)
  })

  ipcMain.handle('ppt:enhanceImport', async (_event, { projectId, title, sourcePath, notes }) => {
    const res = await fetchWithTimeout(
      `${pythonManager.getBaseUrl()}/projects/${projectId}/ppts/enhance-import`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title,
        source_path: sourcePath,
        notes: notes || null,
      }),
    }, 120000)
    return structuredPythonResponse(res)
  })

  // 另存为导出：workspacePath+exportPath（详情返回的本机路径）拷到用户选定位置
  ipcMain.handle('ppt:downloadExport', async (_event, { workspacePath, exportPath, title }) => {
    const { dialog, BrowserWindow } = require('electron')
    const fs = require('fs')
    const path = require('path')
    const full = path.join(workspacePath, exportPath || '')
    if (!fs.existsSync(full)) return { error: 'PPTX file not found' }
    const win = BrowserWindow.getFocusedWindow()
    const { canceled, filePath } = await dialog.showSaveDialog(win || undefined, {
      title: 'Save PPTX',
      defaultPath: `${sanitizeFilename(title, 'PPT')}.pptx`,
      filters: [{ name: 'PowerPoint', extensions: ['pptx'] }],
    })
    if (canceled || !filePath) return { canceled: true }
    fs.copyFileSync(full, filePath)
    logElectron('INFO', `PPT exported to ${filePath}`)
    return { success: true, path: filePath }
  })

  // 打开所在文件夹（资源管理器中定位导出文件）
  ipcMain.handle('ppt:revealExport', async (_event, { workspacePath, exportPath }) => {
    const { shell } = require('electron')
    const path = require('path')
    const full = path.join(workspacePath, exportPath || '')
    shell.showItemInFolder(full)
    return { ok: true }
  })

  // ── PPT 模板工坊（2.3 §4.2）：索引聚合/创建任务/删除/重扫全 HTTP 转发，另存为本机操作 ──
  ipcMain.handle('ppt-template:list', async () => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/ppt-templates`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('ppt-template:jobs', async (_event, { limit, offset } = {}) => {
    const params = new URLSearchParams()
    if (limit !== undefined && limit !== null) params.set('limit', String(limit))
    if (offset) params.set('offset', String(offset))
    const qs = params.toString()
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/ppt-templates/jobs${qs ? `?${qs}` : ''}`)
    return handlePythonResponse(res)
  })

  // 模板规范文档（design_spec.md 正文）：详情抽屉「模板规范」渲染源
  ipcMain.handle('ppt-template:spec', async (_event, { kind, templateId }) => {
    const res = await fetchWithTimeout(
      `${pythonManager.getBaseUrl()}/ppt-templates/${encodeURIComponent(kind)}/${encodeURIComponent(templateId)}/spec`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('ppt-template:create', async (_event, { kind, templateId, templateName, refPaths, notes }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/ppt-templates/create`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        kind,
        template_id: templateId,
        template_name: templateName,
        ref_paths: refPaths || [],
        notes: notes || null,
      }),
    }, 120000) // prepare 同步拷贝参考素材到任务 staging，放宽对齐 ppt:create
    return handlePythonResponse(res)
  })

  ipcMain.handle('ppt-template:delete', async (_event, { kind, templateId }) => {
    const res = await fetchWithTimeout(
      `${pythonManager.getBaseUrl()}/ppt-templates/${encodeURIComponent(kind)}/${encodeURIComponent(templateId)}`,
      { method: 'DELETE' },
      120000) // 删除链含 N×任务工作区 rmtree + 模板包内大量文件，大工作区可能超 30s；超时后服务端仍继续执行且重试幂等收敛
    return handlePythonResponse(res)
  })

  ipcMain.handle('ppt-template:rescan', async () => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/ppt-templates/rescan`, {
      method: 'POST',
    }, 600000) // 自扫用户库索引（秒级），超时上限仅防 UI 挂死兜底
    return handlePythonResponse(res)
  })

  // 另存 Review PPTX：previewPath 是详情返回的绝对路径（不同于 ppt:downloadExport 的相对路径拼接）
  ipcMain.handle('ppt-template:downloadPreview', async (_event, { previewPath, title }) => {
    const { dialog, BrowserWindow } = require('electron')
    const fs = require('fs')
    if (!previewPath || !fs.existsSync(previewPath)) return { error: 'PPTX file not found' }
    const win = BrowserWindow.getFocusedWindow()
    const { canceled, filePath } = await dialog.showSaveDialog(win || undefined, {
      title: 'Save PPTX',
      defaultPath: `${sanitizeFilename(title, 'template')}.pptx`,
      filters: [{ name: 'PowerPoint', extensions: ['pptx'] }],
    })
    if (canceled || !filePath) return { canceled: true }
    fs.copyFileSync(previewPath, filePath)
    logElectron('INFO', `PPT template preview saved to ${filePath}`)
    return { success: true, path: filePath }
  })

  // ── Forms: 填表（2.4forms-design §4）──
  // 模板通道服务全局资产（模板管理页），实例通道服务项目作用域（FormsTab）。
  // upload 走手工 multipart（同 _uploadFilesToTask：FormData/Blob 在 Electron
  // main 不可靠）；download 走 showSaveDialog + 文件流写出（同 tasks:export）。

  ipcMain.handle('form-template:upload', async (_event, { filePath }) => {
    const fs = require('fs')
    const path = require('path')
    const fileData = fs.readFileSync(filePath)
    const fileName = path.basename(filePath).replace(/"/g, '\\"')
    const boundary = '----InkCubeBoundary' + Date.now()
    const parts = [
      Buffer.from(
        `--${boundary}\r\n` +
        `Content-Disposition: form-data; name="file"; filename="${fileName}"\r\n` +
        `Content-Type: application/octet-stream\r\n\r\n`
      ),
      fileData,
      Buffer.from(`\r\n--${boundary}--\r\n`),
    ]
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/forms/templates/upload`, {
      method: 'POST',
      headers: { 'Content-Type': `multipart/form-data; boundary=${boundary}` },
      body: Buffer.concat(parts),
    }, 120000) // 模板文件上传，放宽超时（对齐 _uploadFilesToTask）
    return handlePythonResponse(res)
  })

  ipcMain.handle('form-template:list', async (_event, { q, status, limit, offset } = {}) => {
    const params = new URLSearchParams()
    if (q) params.set('q', q)
    if (status && status !== 'all') params.set('status', status)
    if (limit !== undefined && limit !== null) params.set('limit', String(limit))
    if (offset) params.set('offset', String(offset))
    const qs = params.toString()
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/forms/templates${qs ? `?${qs}` : ''}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('form-template:detail', async (_event, templateId) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/forms/templates/${encodeURIComponent(templateId)}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('form-template:reanalyze', async (_event, { templateId, userHint }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/forms/templates/${encodeURIComponent(templateId)}/reanalyze`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_hint: userHint }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('form-template:delete', async (_event, templateId) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/forms/templates/${encodeURIComponent(templateId)}`, {
      method: 'DELETE',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('form-fill:create', async (_event, { projectId, templateId, title }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/forms/fills`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        project_id: projectId,
        template_id: templateId,
        title: title || null,
      }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('form-fill:list', async (_event, { projectId, limit, offset } = {}) => {
    const params = new URLSearchParams({ project_id: encodeURIComponent(projectId) })
    if (limit !== undefined && limit !== null) params.set('limit', String(limit))
    if (offset) params.set('offset', String(offset))
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/forms/fills?${params}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('form-fill:detail', async (_event, fillId) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/forms/fills/${encodeURIComponent(fillId)}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('form-fill:update', async (_event, { fillId, values }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/forms/fills/${encodeURIComponent(fillId)}/update`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ values: values || {} }),
    }, 60000) // 同步重放：读模板逐字段重写 docx + 落盘，可能超出默认 15s
    return handlePythonResponse(res)
  })

  ipcMain.handle('form-fill:download', async (_event, { fillId, title }) => {
    const { dialog, BrowserWindow } = require('electron')
    const fs = require('fs')
    const win = BrowserWindow.getFocusedWindow()
    // 先弹保存框（对齐 reports:exportReport saveAs 先例），取消不发起请求
    const safeName = sanitizeFilename(title, '填表')
    const { canceled, filePath } = await dialog.showSaveDialog(win || undefined, {
      title: 'Save Form',
      defaultPath: `${safeName}.docx`,
      filters: [{ name: 'Word Document', extensions: ['docx'] }],
    })
    if (canceled || !filePath) return { canceled: true }
    const res = await fetchWithTimeout(
      `${pythonManager.getBaseUrl()}/forms/fills/${encodeURIComponent(fillId)}/download`, {}, 60000)
    const contentType = res.headers.get('content-type') || ''
    if (!res.ok || !contentType.includes('wordprocessingml')) {
      const body = await res.json().catch(() => null)
      return { error: body?.error || body?.detail || 'Download failed' }
    }
    fs.writeFileSync(filePath, Buffer.from(await res.arrayBuffer()))
    logElectron('INFO', `Form fill exported to ${filePath}`)
    return { success: true, path: filePath }
  })

  ipcMain.handle('form-fill:delete', async (_event, fillId) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/forms/fills/${encodeURIComponent(fillId)}`, {
      method: 'DELETE',
    })
    return handlePythonResponse(res)
  })

  // ── Wiki ──

  ipcMain.handle('wiki:listPages', async (_event, { projectId, limit, offset } = {}) => {
    const params = new URLSearchParams()
    if (limit !== undefined && limit !== null) params.set('limit', String(limit))
    if (offset) params.set('offset', String(offset))
    const qs = params.toString()
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/pages${qs ? `?${qs}` : ''}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('wiki:getPage', async (_event, { projectId, pageKey }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/pages/${encodeURIComponent(pageKey)}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('wiki:compile', async (_event, { projectId, mode, force }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/compile`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: mode || 'incremental', trigger_type: 'manual', force: !!force }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('wiki:compileStatus', async (_event, { projectId }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/compile-status`)
    return handlePythonResponse(res)
  })

  // ── Activity Hub (0.4lifecycle-design.md §8.1 #0) ──
  ipcMain.handle('activities:getStatus', async () => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/activities/status`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('notifications:markRead', async (_event, { ids }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/activities/notifications/read`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids }),
    })
    return handlePythonResponse(res)
  })

  // 通知中心历史面（2.11 §3.4）：分页全量（含已读）+ 一键全部已读
  ipcMain.handle('notifications:list', async (_event, { offset, limit } = {}) => {
    const params = new URLSearchParams({
      offset: String(offset ?? 0),
      limit: String(limit ?? 50),
    })
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/activities/notifications?${params}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('notifications:readAll', async () => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/activities/notifications/read-all`, {
      method: 'POST',
    })
    return handlePythonResponse(res)
  })

  // 单条删除（2.11 §3.4）：通知中心行内删除，幂等 { ok, deleted }
  ipcMain.handle('notifications:delete', async (_event, { id }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/activities/notifications/${id}`, {
      method: 'DELETE',
    })
    return handlePythonResponse(res)
  })

  // 全局终态事件流订阅（0.4 §6.4 E3）：fire-and-forget——SSE 生命周期由 sender
  // destroyed / 重挂载去重管理，invoke 立即返回；事件经 'activity:stream' 通道推送
  ipcMain.handle('activities:subscribeStream', (event) => {
    streamActivities(pythonManager, event.sender)
    return { ok: true }
  })

  ipcMain.handle('wiki:stopCompile', async (_event, { projectId }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/compile/stop`, {
      method: 'POST',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('wiki:listReviews', async (_event, { projectId, resolved, limit, offset } = {}) => {
    const params = new URLSearchParams()
    if (resolved !== undefined) params.set('resolved', String(resolved))
    if (limit !== undefined && limit !== null) params.set('limit', String(limit))
    if (offset) params.set('offset', String(offset))
    const qs = params.toString()
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/reviews${qs ? `?${qs}` : ''}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('wiki:resolveReview', async (_event, { projectId, reviewId, action }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/reviews/${reviewId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action }),
    })
    return handlePythonResponse(res)
  })

  // 矛盾裁决（§5.5）：以 chosen 侧为准，后端对其余各侧页面做确定性替换+快照+归档
  ipcMain.handle('wiki:adjudicateReview', async (_event, { projectId, reviewId, chosen }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/reviews/${reviewId}/adjudicate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chosen }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('wiki:batchResolveReviews', async (_event, { projectId, reviewIds, action }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/reviews/batch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ review_ids: reviewIds, action }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('wiki:deletePage', async (_event, { projectId, pageKey }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/pages/${encodeURIComponent(pageKey)}`, {
      method: 'DELETE',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('wiki:editPage', async (_event, { projectId, pageKey, content, title }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/pages/${encodeURIComponent(pageKey)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content, title: title || null }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('wiki:lint', async (_event, { projectId, mode }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/lint`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: mode || 'structural' }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('wiki:getSchemas', async (_event, { projectId }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/schemas`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('wiki:updatePurpose', async (_event, { projectId, purpose }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/purpose`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ purpose }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('wiki:updateOverview', async (_event, { projectId, overview }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/overview`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ overview }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('wiki:getBacklinks', async (_event, { projectId, pageKey }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/pages/${encodeURIComponent(pageKey)}/backlinks`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('wiki:getChangeLog', async (_event, { projectId, limit, offset } = {}) => {
    const params = new URLSearchParams()
    if (limit !== undefined && limit !== null) params.set('limit', String(limit))
    if (offset) params.set('offset', String(offset))
    const qs = params.toString()
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/changelog${qs ? `?${qs}` : ''}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('wiki:archive', async (_event, { projectId, title, content, pageKey, sourceQuestion }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/archive`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title,
        content,
        page_key: pageKey || null,
        source_question: sourceQuestion || '',
      }),
    })
    return handlePythonResponse(res)
  })

  // Wiki search (hybrid retrieval: keyword + graph expansion)
  ipcMain.handle('wiki:search', async (_event, { projectId, query, topK, enableGraph }) => {
    const params = new URLSearchParams({ q: query || '', top_k: String(topK || 20) })
    if (enableGraph === false) params.set('enable_graph', 'false')
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/search?${params}`)
    return handlePythonResponse(res)
  })

  // Wiki review sweep (two-stage auto-resolution)
  ipcMain.handle('wiki:sweepReviews', async (_event, { projectId }) => {
    // LLM 型端点（Stage2 最多 5 批语义判断），放宽默认 15s 超时
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/sweep-reviews`, {
      method: 'POST',
    }, 180000)
    return handlePythonResponse(res)
  })

  // Wiki graph data
  ipcMain.handle('wiki:getGraph', async (_event, { projectId, insights }) => {
    const params = new URLSearchParams()
    if (insights) params.set('insights', 'true')
    const qs = params.toString()
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/graph${qs ? '?' + qs : ''}`)
    return handlePythonResponse(res)
  })

  // Wiki schema CRUD
  ipcMain.handle('wiki:createSchema', async (_event, { projectId, pageKey, title, compileHint, baseLibSearchHints, position, isLedger }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/schemas`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        page_key: pageKey,
        title,
        compile_hint: compileHint || '',
        base_lib_search_hints: baseLibSearchHints || [],
        position: position ?? undefined,
        is_ledger: Boolean(isLedger),   // 3.1 §3 台账页开关
      }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('wiki:updateSchema', async (_event, { projectId, schemaId, title, compileHint, baseLibSearchHints, position, isLedger }) => {
    const body = {}
    if (title !== undefined) body.title = title
    if (compileHint !== undefined) body.compile_hint = compileHint
    if (baseLibSearchHints !== undefined) body.base_lib_search_hints = baseLibSearchHints
    if (position !== undefined) body.position = position
    if (isLedger !== undefined) body.is_ledger = Boolean(isLedger)  // undefined=不改
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/schemas/${schemaId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('wiki:deleteSchema', async (_event, { projectId, schemaId }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/schemas/${schemaId}`, {
      method: 'DELETE',
    })
    return handlePythonResponse(res)
  })

  // Wiki page versions
  ipcMain.handle('wiki:listVersions', async (_event, { projectId, pageKey, limit, offset }) => {
    const params = new URLSearchParams({ limit: String(limit ?? 20) })
    if (offset) params.set('offset', String(offset))
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/pages/${encodeURIComponent(pageKey)}/versions?${params}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('wiki:getVersion', async (_event, { projectId, versionId }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/versions/${versionId}`)
    return handlePythonResponse(res)
  })

  // Wiki duplicate detection (§12.2.10)
  ipcMain.handle('wiki:dedupDetect', async (_event, { projectId }) => {
    // LLM 分组调用（≤100 页摘要一次长上下文请求），放宽默认 15s 超时
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/dedup/detect`, {}, 180000)
    return handlePythonResponse(res)
  })

  ipcMain.handle('wiki:dedupMerge', async (_event, { projectId, canonicalKey, duplicateKeys }) => {
    // 每 dup 页串行一次 LLM 内容合并 + 全库链接重写，放宽默认 15s 超时
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/dedup/merge`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ canonical_key: canonicalKey, duplicate_keys: duplicateKeys }),
    }, 180000)
    return handlePythonResponse(res)
  })

  // Wiki-link enrichment (§12.2.4)
  ipcMain.handle('wiki:enrichWikilinks', async (_event, { projectId, pageKey }) => {
    // LLM 建议调用（单次长上下文请求），放宽默认 15s 超时
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/enrich-wikilinks`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ page_key: pageKey }),
    }, 180000)
    return handlePythonResponse(res)
  })

  // ── Ledger: 台账条目(3.0 四期;知识首页时间轴卡 + 首页卡片墙摘要行)──
  ipcMain.handle('wiki:listLedgerEntries', async (_event, { projectId, limit, offset } = {}) => {
    const params = new URLSearchParams({ limit: String(limit ?? 20) })
    if (offset) params.set('offset', String(offset))
    const res = await fetchWithTimeout(
      `${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/ledger/entries?${params}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('ledger:listLatest', async () => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/ledger/latest`)
    return handlePythonResponse(res)
  })

  // ── Affairs: 时期任务(3.0 二期;前缀裁决 tasks:* 已被 TaskAgent 占用)──
  ipcMain.handle('affairs:listByProject', async (_event, { projectId, limit, offset } = {}) => {
    const params = new URLSearchParams()
    if (limit !== undefined && limit !== null) params.set('limit', String(limit))
    if (offset) params.set('offset', String(offset))
    const qs = params.toString()
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/projects/${projectId}/tasks${qs ? `?${qs}` : ''}`)
    return handlePythonResponse(res)
  })

  // 首页今日工作台 + 侧栏色点数据源(3.0 三期 §7.2)
  ipcMain.handle('affairs:listDue', async (_event, { horizonDays } = {}) => {
    const res = await fetchWithTimeout(
      `${pythonManager.getBaseUrl()}/projects/due?horizon_days=${Number(horizonDays) || 7}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('affairs:createTask', async (_event, { projectId, ...fields }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/projects/${projectId}/tasks`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title: fields.title,
        task_type: fields.taskType,
        description: fields.description || '',
        due_date: fields.dueDate || null,
        recurrence_rule: fields.recurrenceRule || null,
        auto_complete: fields.autoComplete || null,
      }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('affairs:updateTask', async (_event, { projectId, taskId, ...fields }) => {
    const body = {}
    if (fields.title !== undefined) body.title = fields.title
    if (fields.description !== undefined) body.description = fields.description
    if (fields.dueDate !== undefined) body.due_date = fields.dueDate
    if (fields.status !== undefined) body.status = fields.status
    if (fields.recurrenceRule !== undefined) body.recurrence_rule = fields.recurrenceRule
    if (fields.autoComplete !== undefined) body.auto_complete = fields.autoComplete
    if (fields.phase !== undefined) body.phase = fields.phase
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/projects/${projectId}/tasks/${taskId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('affairs:deleteTask', async (_event, { projectId, taskId }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/projects/${projectId}/tasks/${taskId}`, {
      method: 'DELETE',
    })
    return handlePythonResponse(res)
  })

  // ── Affairs: 事件驱动任务建议(3.0 四期;采纳/忽略是人类裁决口)──
  ipcMain.handle('affairs:listSuggestions', async (_event, { projectId, status, limit, offset } = {}) => {
    const params = new URLSearchParams({ status: encodeURIComponent(status || 'pending') })
    if (limit !== undefined && limit !== null) params.set('limit', String(limit))
    if (offset) params.set('offset', String(offset))
    const res = await fetchWithTimeout(
      `${pythonManager.getBaseUrl()}/projects/${projectId}/suggestions?${params}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('affairs:acceptSuggestion', async (_event, { projectId, suggestionId }) => {
    const res = await fetchWithTimeout(
      `${pythonManager.getBaseUrl()}/projects/${projectId}/suggestions/${encodeURIComponent(suggestionId)}/accept`, {
      method: 'POST',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('affairs:dismissSuggestion', async (_event, { projectId, suggestionId }) => {
    const res = await fetchWithTimeout(
      `${pythonManager.getBaseUrl()}/projects/${projectId}/suggestions/${encodeURIComponent(suggestionId)}/dismiss`, {
      method: 'POST',
    })
    return handlePythonResponse(res)
  })

  // ── Web logins (2.8 §9.2)：登录在 BrowserPanel 承载（V2.1 主进程
  // WebContentsView，persist:webfetch partition）内完成，本组仅存
  // cookie 清理与登记列表转发 ──

  ipcMain.handle('weblogin:remove-cookies', (_event, payload) => {
    return fetchPool.removeCookiesForDomain(payload?.domain)
  })

  // 登记列表与增删转发 Python REST（2.8 §9.1）。无效域名等业务错误由后端以
  // HTTP 200 + {error} 返回（非异常路径），由前端自行展示；网络/5xx 走 throw。
  ipcMain.handle('weblogin:list', async (_event, { limit, offset } = {}) => {
    const params = new URLSearchParams()
    if (limit !== undefined && limit !== null) params.set('limit', String(limit))
    if (offset) params.set('offset', String(offset))
    const qs = params.toString()
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/web-logins${qs ? `?${qs}` : ''}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('weblogin:add', async (_event, payload) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/web-logins`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ domain: payload?.domain ?? '', note: payload?.note ?? null }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('weblogin:remove', async (_event, payload) => {
    const res = await fetchWithTimeout(
      `${pythonManager.getBaseUrl()}/web-logins/${encodeURIComponent(payload?.domain ?? '')}`,
      { method: 'DELETE' }
    )
    return handlePythonResponse(res)
  })

  // ── Usage: token 用量统计（2.10 §4.5/§5.2）。StatusBar / 设置-用量统计 /
  // 项目详情徽章共用 summary 通道；taskDetail 供 TaskItem 轮次下钻（UsageRounds）──

  ipcMain.handle('usage:summary', async (_event, { rangeKey, projectId } = {}) => {
    const params = new URLSearchParams()
    if (rangeKey) params.set('range_key', rangeKey)
    if (projectId) params.set('project_id', projectId)
    const qs = params.toString()
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/usage/summary${qs ? `?${qs}` : ''}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('usage:taskDetail', async (_event, { taskId, limit, offset } = {}) => {
    const params = new URLSearchParams()
    if (limit !== undefined && limit !== null) params.set('limit', String(limit))
    if (offset) params.set('offset', String(offset))
    const qs = params.toString()
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/usage/task/${encodeURIComponent(taskId)}${qs ? `?${qs}` : ''}`)
    return handlePythonResponse(res)
  })
}

module.exports = { registerIpcHandlers }
