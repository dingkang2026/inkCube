const { logElectron, readLogFile, getLogPath, getLogInfo } = require('./log-utils')
const fetchPool = require('./fetch-pool')

/**
 * Unified Python response handler — bridges InkCubeError to frontend.
 * On success (res.ok): returns parsed JSON.
 * On error (!res.ok): throws an InkCubeIPCError with structured fields
 *   so that useErrorHandler can classify and display appropriate UI.
 */
async function handlePythonResponse(res) {
  if (res.ok) return res.json()
  const body = await res.json().catch(() => null)
  const err = new Error(body?.message || `Python backend error (${res.status})`)
  err.errorCode = body?.code || 'UNKNOWN_ERROR'
  err.errorCategory = body?.category || 'INTERNAL'
  err.errorSuggestion = body?.suggestion || ''
  err.errorDetail = body?.detail || ''
  throw err
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

/**
 * Upload files to a task workspace using manual multipart construction.
 * Avoids FormData/Blob which are unreliable in Electron main process.
 */
async function _uploadFilesToTask(pythonManager, taskId, filePaths) {
  const fs = require('fs')
  const path = require('path')
  const baseUrl = pythonManager.getBaseUrl()
  const boundary = '----InkCubeBoundary' + Date.now()

  const parts = []
  for (const fp of filePaths) {
    const fileName = path.basename(fp).replace(/"/g, '\\"')
    const fileData = fs.readFileSync(fp)
    parts.push(
      Buffer.from(
        `--${boundary}\r\n` +
        `Content-Disposition: form-data; name="files"; filename="${fileName}"\r\n` +
        `Content-Type: application/octet-stream\r\n\r\n`
      )
    )
    parts.push(fileData)
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
  // Expose Python backend base URL for direct HTTP calls (e.g. file upload)
  ipcMain.handle('python:getBaseUrl', () => pythonManager.getBaseUrl())

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

  // Onboarding wizard flag (persisted in backend settings kv table, 0.4lifecycle §5.3)
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
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/system/data/${category}`, {
      method: 'DELETE',
    })
    return handlePythonResponse(res)
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

  // ── 一键加白名单（2.8 §12 F3）：杀软实时防护阻断 uv 缓存改名的根因处置 ──
  // 信号端点：uv-cache .tmp 残留（阻断/中断的持久物证，Python 侧零状态维护）。
  // 阻断是 Windows 杀软特性，非 win32 直接短路，前端零平台逻辑。
  ipcMain.handle('system:getUvCacheBlocked', async () => {
    if (process.platform !== 'win32') {
      return { blocked: false, tmp_count: 0 }
    }
    try {
      const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/system/uv-cache-blocked`)
      return handlePythonResponse(res)
    } catch (e) {
      logElectron('WARN', `uv-cache-blocked check failed: ${e.message}`)
      return { blocked: false, tmp_count: 0 }
    }
  })

  // 加白：提权 Add-MpPreference（UAC 由系统弹窗，用户一次确认即完成）。
  // 成功与否以 Get-MpPreference 回读验证为准（exit code 不可靠：防篡改
  // 拦截时提权子进程也会退出，但排除项列表不会变化）。验证失败 →
  // 深链直达 Defender 排除项页 + 剪贴板路径，降级为手动引导。
  // 两次 PowerShell 均异步 spawn：UAC 等待最长 2 分钟，同步 spawnSync
  // 会冻结主进程事件循环（全应用「未响应」，所有 IPC/轮询停摆）。
  ipcMain.handle('system:addUvCacheExclusion', async () => {
    if (process.platform !== 'win32') {
      return { added: false, notApplicable: true }
    }
    const path = require('path')
    const { spawn } = require('child_process')
    const { shell, clipboard } = require('electron')
    const cacheDir = path.join(getDataDir(), 'uv-cache')
    // 单引号形式（'' 转义），与回读命令对称：路径段含 $ 时双引号会被
    // PowerShell 插值，静默写错排除路径（回读 False → 误入 fallback）
    const psPath = cacheDir.replace(/'/g, "''")
    // EncodedCommand（Base64 UTF-16LE）完全避开引号嵌套地狱
    const inner = Buffer.from(
      `Add-MpPreference -ExclusionPath '${psPath}'`, 'utf16le').toString('base64')
    const elevate = (
      `$p = Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden -PassThru ` +
      `-EncodedCommand ${inner}; exit $p.ExitCode`
    )
    // 异步包 Promise：'error'（spawn 失败）与 'close'（含 timeout 强杀）
    // 双路 resolve，永不 reject——成败统一交由回读验证裁决
    const runPS = (args, timeout) => new Promise((resolve) => {
      const p = spawn('powershell.exe', args, { timeout })
      let out = ''
      p.stdout.on('data', (d) => { out += d })
      p.on('error', (e) => {
        logElectron('WARN', `uv-cache powershell spawn failed: ${e.message}`)
        resolve({ code: null, stdout: out })
      })
      p.on('close', (code) => resolve({ code, stdout: out }))
    })

    await runPS(['-NoProfile', '-NonInteractive', '-Command', elevate], 120000)

    // 回读验证（读不需提权；防篡改拦截下 Add 会静默失败，只有回读能发现）
    const check = await runPS(
      ['-NoProfile', '-NonInteractive', '-Command',
        `(Get-MpPreference).ExclusionPath -contains '${psPath}'`],
      30000)
    if (check.code === 0 && check.stdout.trim() === 'True') {
      logElectron('INFO', `uv-cache exclusion added: ${cacheDir}`)
      // 信号闭环：历史残骸会让信号持续为真，重启后横幅反复弹出；加白成功
      // 后残骸不再有产生原因，顺手清零。只删陈旧条目（与 Python 端
      // get_uv_cache_tmp_residue 的计数阈值同一 10 分钟值，2.8 §12 F3）：
      // 在途解压的 .tmp 可能正被 uv 占用，误删会白烧一级重试；逐条捕获，
      // 单条被占用不中断其余清理
      const staleMs = 10 * 60_000
      try {
        const fs = require('fs')
        for (const name of fs.readdirSync(cacheDir)) {
          if (!name.startsWith('.tmp')) continue
          try {
            const st = fs.statSync(path.join(cacheDir, name))
            if (Date.now() - st.mtimeMs <= staleMs) continue
            fs.rmSync(path.join(cacheDir, name), { recursive: true, force: true })
          } catch (e) {
            logElectron('WARN', `uv-cache tmp cleanup skip ${name}: ${e.message}`)
          }
        }
      } catch (e) {
        logElectron('WARN', `uv-cache tmp cleanup failed: ${e.message}`)
      }
      return { added: true, fallback: false }
    }
    logElectron('WARN', `uv-cache exclusion verify failed: code=${check.code}`)

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
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/models/${name}/download`, {
      method: 'POST',
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

  ipcMain.handle('knowledge:listProjects', async () => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/projects`)
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
    const params = new URLSearchParams({ limit: String(limit || 20) })
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
    const params = new URLSearchParams({ limit: String(limit || 20) })
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

  ipcMain.handle('knowledge:deleteProject', async (_event, projectId) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}`, {
      method: 'DELETE',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('knowledge:renameProject', async (_event, { projectId, name }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('knowledge:updateProject', async (_event, { projectId, name, description, type, stage }) => {
    const body = {}
    if (name !== undefined) body.name = name
    if (description !== undefined) body.description = description
    if (type !== undefined) body.type = type
    if (stage !== undefined) body.stage = stage
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    return handlePythonResponse(res)
  })

  // Image management
  ipcMain.handle('knowledge:getProjectImages', async (_event, { projectId, limit, offset, fileId, status, category, search, size }) => {
    const params = new URLSearchParams({ limit: String(limit || 40), offset: String(offset || 0) })
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

  ipcMain.handle('baseLib:list', async () => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/base-libs`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('baseLib:create', async (_event, { name, description }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/base-libs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, description: description || '' }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('baseLib:update', async (_event, { libId, projectId, name, description }) => {
    const id = libId || projectId
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/base-libs/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, description }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('baseLib:delete', async (_event, libId) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/knowledge/base-libs/${libId}`, {
      method: 'DELETE',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('baseLib:getFiles', async (_event, { libId, projectId, limit, offset, status, fileType, filenameSearch }) => {
    const id = libId || projectId
    const params = new URLSearchParams({ limit: String(limit || 20) })
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
    const params = new URLSearchParams({ limit: String(limit || 20) })
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
    const params = new URLSearchParams({ limit: String(limit || 40), offset: String(offset || 0) })
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

  ipcMain.handle('tasks:get', async (_event, taskId) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/tasks/${taskId}`)
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

  ipcMain.handle('tasks:readFile', async (_event, { taskId, path }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/tasks/${taskId}/files/content?path=${encodeURIComponent(path)}`)
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
    if (limit) params.set('limit', String(limit))
    if (offset) params.set('offset', String(offset))
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

  ipcMain.handle('skills:list', async () => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/skills`)
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

  ipcMain.handle('reports:listReports', async (_event, { projectId }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/reports?project_id=${encodeURIComponent(projectId)}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('reports:getReport', async (_event, reportId) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/reports/${reportId}`)
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
      // 默认文件名 {标题}.docx，标题经文件名 sanitize（非法字符/结尾点，与后端 _sanitize_filename 同规则）
      const safeName = String(title || '报告')
        .replace(/[\\/:*?"<>|\x00-\x1f]/g, '_')
        .replace(/[. ]+$/g, '')
        .slice(0, 80)
        .replace(/[. ]+$/g, '') || '报告'
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

  // ── Wiki ──

  ipcMain.handle('wiki:listPages', async (_event, { projectId }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/pages`)
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

  // ── Activity Hub (0.4lifecycle-design.md §3.1 #0) ──
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

  ipcMain.handle('wiki:listReviews', async (_event, { projectId, resolved }) => {
    const params = resolved !== undefined ? `?resolved=${resolved}` : ''
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/reviews${params}`)
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

  ipcMain.handle('wiki:getPurpose', async (_event, { projectId }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/purpose`)
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

  ipcMain.handle('wiki:getOverview', async (_event, { projectId }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/overview`)
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

  ipcMain.handle('wiki:getChangeLog', async (_event, { projectId, limit }) => {
    const params = limit ? `?limit=${limit}` : ''
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/changelog${params}`)
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
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/sweep-reviews`, {
      method: 'POST',
    })
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
  ipcMain.handle('wiki:listVersions', async (_event, { projectId, pageKey, limit }) => {
    const params = new URLSearchParams({ limit: String(limit || 20) })
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/pages/${encodeURIComponent(pageKey)}/versions?${params}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('wiki:getVersion', async (_event, { projectId, versionId }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/versions/${versionId}`)
    return handlePythonResponse(res)
  })

  // Wiki duplicate detection (§12.2.10)
  ipcMain.handle('wiki:dedupDetect', async (_event, { projectId }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/dedup/detect`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('wiki:dedupMerge', async (_event, { projectId, canonicalKey, duplicateKeys }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/dedup/merge`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ canonical_key: canonicalKey, duplicate_keys: duplicateKeys }),
    })
    return handlePythonResponse(res)
  })

  // Wiki-link enrichment (§12.2.4)
  ipcMain.handle('wiki:enrichWikilinks', async (_event, { projectId, pageKey }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/enrich-wikilinks`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ page_key: pageKey }),
    })
    return handlePythonResponse(res)
  })

  // ── Ledger: 台账条目(3.0 四期;知识首页时间轴卡 + 首页卡片墙摘要行)──
  ipcMain.handle('wiki:listLedgerEntries', async (_event, { projectId, limit } = {}) => {
    const res = await fetchWithTimeout(
      `${pythonManager.getBaseUrl()}/wiki/projects/${projectId}/ledger/entries?limit=${Number(limit) || 20}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('ledger:listLatest', async () => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/wiki/ledger/latest`)
    return handlePythonResponse(res)
  })

  // ── Affairs: 时期任务(3.0 二期;前缀裁决 tasks:* 已被 TaskAgent 占用)──
  ipcMain.handle('affairs:listByProject', async (_event, { projectId }) => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/projects/${projectId}/tasks`)
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
  ipcMain.handle('affairs:listSuggestions', async (_event, { projectId, status } = {}) => {
    const res = await fetchWithTimeout(
      `${pythonManager.getBaseUrl()}/projects/${projectId}/suggestions?status=${encodeURIComponent(status || 'pending')}`)
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

  // ── Web logins (2.8 §9.2)：登录窗口与 partition cookie 清理 ──

  // 创建/复用登录窗口并导航至 domain；用户关闭窗口即完成（无 done 通道）。
  // 前端调 DELETE /web-logins/{domain} 之前需先发本组 remove-cookies。
  ipcMain.handle('weblogin:open', (_event, payload) => {
    return fetchPool.openLoginWindow(payload?.domain)
  })

  ipcMain.handle('weblogin:remove-cookies', (_event, payload) => {
    return fetchPool.removeCookiesForDomain(payload?.domain)
  })

  // 登记列表与增删转发 Python REST（2.8 §9.1）。无效域名等业务错误由后端以
  // HTTP 200 + {error} 返回（非异常路径），由前端自行展示；网络/5xx 走 throw。
  ipcMain.handle('weblogin:list', async () => {
    const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/web-logins`)
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
}

module.exports = { registerIpcHandlers }
