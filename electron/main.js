// ── Mermaid/SVG renderer mode (python/tools/mermaid_bridge.py spawns the
// renderer script directly; this env-guard only guards against a leaked
// INKCUBE_MERMAID_RENDERER=1 reaching a real main-app launch) ──
// When INKCUBE_MERMAID_RENDERER=1, this process is a headless one-shot
// renderer.  Delegate immediately and skip all main-app initialisation.
if (process.env.INKCUBE_MERMAID_RENDERER === '1') {
  require('./mermaid-renderer')
} else {

const { app, BrowserWindow, ipcMain, protocol, net, shell, Notification } = require('electron')
const path = require('path')
const fs = require('fs')
const { PythonManager } = require('./python-manager')
const { registerIpcHandlers } = require('./ipc-handlers')
const { logElectron } = require('./log-utils')
const { ensureToolsInitialized } = require('./tool-initializer')
const fetchPool = require('./fetch-pool')

// ── CDP port for browser-fetch (2.8 §6.1) ──
// port=0 → Chromium picks a random port (localhost-only) and writes it to
// <userData>/DevToolsActivePort once the DevTools HTTP server is up; we read
// that file after ready (with a /json/version liveness probe) and inject it
// into the Python backend env. Zero TOCTOU vs. pre-probing a port.
app.commandLine.appendSwitch('remote-debugging-port', '0')

// Windows 通知需要 AppUserModelID（2.11 §4.1）：缺省时 Electron Notification 在
// Windows 上静默不弹；值与 electron-builder.yml appId 一致，必须在 ready 前设置
app.setAppUserModelId('com.inkcube.app')

/** Poll DevToolsActivePort until Chromium's CDP server is actually listening.
 *  Returns the port, or null on timeout (browser-fetch will surface a
 *  clear error instead of failing opaquely at connect time). */
async function readCdpPort(maxWaitMs = 10000) {
  const file = path.join(app.getPath('userData'), 'DevToolsActivePort')
  const start = Date.now()
  while (Date.now() - start < maxWaitMs) {
    try {
      const port = parseInt(fs.readFileSync(file, 'utf-8').split('\n')[0].trim(), 10)
      if (port > 0) {
        const res = await fetch(`http://127.0.0.1:${port}/json/version`,
          { signal: AbortSignal.timeout(1000) })
        if (res.ok) return port
      }
    } catch { /* file not written yet / server not up */ }
    await new Promise((r) => setTimeout(r, 150))
  }
  logElectron('WARN', '[fetch-pool] DevToolsActivePort not available within timeout, CDP disabled')
  return null
}

const isDev = process.env.NODE_ENV === 'development'
let mainWindow = null
const pythonManager = new PythonManager()

// 单实例锁（打包/卸载复盘 2026-10-25）：多开会产生两个 Python 后端抢写
// SQLite 与端口，卸载/升级时文件锁面翻倍；secondary 实例直接退出，
// primary 聚焦已有窗口。Windows 下 dev 与打包版 userData 同为
// %APPDATA%/InkCube（大小写不敏感），两者同样互斥。
const gotTheLock = app.requestSingleInstanceLock()
if (!gotTheLock) {
  app.quit()
} else {
  app.on('second-instance', () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      if (mainWindow.isMinimized()) mainWindow.restore()
      mainWindow.focus()
    }
  })
}

logElectron('INFO', 'InkCube starting...')

// ── Boot stage machine（窗口先行，2026-10-24）──
// 旧行为：createWindow 排在 pythonManager.start() 之后，python 冷启动（import 链
// 6~20s + skill 全量重拷可达 22s）期间用户无窗口可看（实测 28.8s 无窗口）。
// 现行为：窗口先建，启动链（tools → cdp → python）在其后并行推进，阶段经
// app:boot-stage 事件推给 renderer 的 BootGate 呈现「阶段 + 计时」；
// app:boot-status 供 renderer 挂载时拉取初始态与就绪连通确认。
// 通道名全小写连字符：orphan_channel_audit 的 pushToRenderer 采集正则（CH）
// 只认小写，大写通道会被漏采（app:osNotifyClick 形态即审计盲区）。
const BOOT_TOTAL_START = Date.now()
const boot = { stage: 'tools', message: null, stageStartedAt: Date.now(), progress: null }

function setBootStage(stage, extra = {}) {
  const elapsedMs = Date.now() - boot.stageStartedAt
  const totalMs = Date.now() - BOOT_TOTAL_START
  if (stage !== 'error') {
    logElectron('INFO', `[boot] stage ${boot.stage} -> ${stage} (stage ${elapsedMs}ms, total ${totalMs}ms)`)
  }
  boot.stage = stage
  boot.message = extra.message ?? null
  // 阶段切换即清进度：进度只属于当前阶段（python 阶段不应残留 tools 进度）
  boot.progress = extra.progress ?? null
  boot.stageStartedAt = Date.now()
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('app:boot-stage', { stage, message: boot.message, elapsedMs, progress: boot.progress })
  }
}

// 阶段内进度更新（首装工具链复制，2026-10-25）：不重置阶段计时，只把
// progress 附在当前阶段事件上推给 renderer（BootGate 进度行）。
function setBootProgress(progress) {
  boot.progress = progress
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('app:boot-stage', { stage: boot.stage, message: boot.message, elapsedMs: 0, progress })
  }
}

// ── bge-m3 preload 双条件触发 ──
// CUDA 模型加载与 renderer 模块图装载争抢 CPU/磁盘，会把 dev 冷启动首屏拖到
// 85s（2026-10-13 实测 DCL 84s）——因此必须同时满足「renderer 首次完整加载
// 已结束」与「python 后端已就绪」才触发。did-finish-load 在每次完整 reload 重
// 触发 → rendererLoaded 语义不变；/models/preload 幂等（加载中/已载即跳过）。
let pythonReady = false
let rendererLoaded = false

function triggerBgeM3Preload() {
  if (!pythonReady || !rendererLoaded) return
  if (!mainWindow || mainWindow.isDestroyed()) return
  fetch(`${pythonManager.getBaseUrl()}/models/preload`, {
    method: 'POST',
    signal: AbortSignal.timeout(5000),
  })
    .then(async (res) => {
      const body = await res.json().catch(() => ({}))
      logElectron('INFO', `[preload] bge-m3 trigger: HTTP ${res.status} ${body.code || ''}`)
    })
    .catch((err) => {
      // External/dev backends older than the endpoint return 404 — harmless
      logElectron('WARN', `[preload] bge-m3 trigger failed: ${err.message}`)
    })
}

// ── dev server 就绪轮询（boot-splash 先行，2026-10-24）──
// vite 冷启动期启动任务把单线程事件循环吃满（实测 ~3-4 分钟 99% CPU），
// HTTP 管道全局饿死（curl 与浏览器请求同挂，loadURL 直接白等）→ 先加载本地
// 静态 splash（毫秒级），此处轮询到 dev server 可响应再切主页面。每次探测
// 独立短超时连接，阻塞期探测超时废弃，空闲瞬间即成功。
function waitForDevServer(maxAttempts = 360) {
  const attempt = (n) => {
    if (n >= maxAttempts) {
      logElectron('WARN', '[boot] dev server not reachable within timeout, staying on boot splash')
      return Promise.resolve()
    }
    return fetch('http://[::1]:5173/', { signal: AbortSignal.timeout(1500) })
      .then((res) => {
        res.body?.cancel()
        logElectron('INFO', `[boot] dev server ready (${n} failed probes)`)
      })
      .catch(() => new Promise((r) => setTimeout(r, 500)).then(() => attempt(n + 1)))
  }
  return attempt(0)
}

// Register custom protocol for serving local files (images, etc.)
// Must be called before app.whenReady()
protocol.registerSchemesAsPrivileged([
  {
    scheme: 'inkcube',
    privileges: {
      standard: true,
      secure: true,
      supportFetchAPI: true,
      stream: true,
    },
  },
])

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 800,
    minHeight: 600,
    frame: false,
    title: '墨方 InkCube',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      // 2.8 §6.2 V2.1：浏览器承载为主进程 WebContentsView（fetch-pool.js），
      // 主窗口不再开启 webviewTag（承载迁移后遗留开关已删，攻击面收敛）
    },
  })

  if (isDev) {
    // dev server 走 [::1]（2026-10-24 白板期修复之一）：vite 默认仅绑 IPv6 回环，
    // 而 127.0.0.1:5173 被本机代理/TUN 类软件黑洞化（SYN ~2s 后 refused）；
    // Chromium 对 localhost IPv4 优先，要轮询 SYN 超时才回退。实测 [::1] 0.006s 200。
    // vite server.host、package.json wait-on、下方 will-navigate 白名单同值。
    // 但 [::1] 只解决了连接层：vite 冷启动 CPU 阻塞期（见 waitForDevServer）
    // HTML 响应仍要等 ~3-4 分钟 → 先本地静态 splash（毫秒级），就绪后自动切换。
    mainWindow.loadFile(path.join(__dirname, 'boot-splash.html'))
    waitForDevServer().then(() => {
      if (!mainWindow || mainWindow.isDestroyed()) return
      mainWindow.loadURL('http://[::1]:5173')
      mainWindow.webContents.openDevTools()
    })
  } else {
    mainWindow.loadFile(path.join(__dirname, '..', 'dist-renderer', 'index.html'))
  }

  // 2.8 §6.2 V2.1：浏览器承载为主进程 WebContentsView 常驻池（playwright 仅
  // 识别 type=page 的 CDP target，webview guest 不可见——2026-10-14 实证）；
  // 面板控制通道同处注册（幂等，activate 重建窗口时重入安全）
  fetchPool.attachPoolView(mainWindow)
  fetchPool.registerFetchPoolIpc()

  // bge-m3 preload 双条件触发（见 triggerBgeM3Preload 注释）：
  // 「renderer 首次完整加载结束」这一半在此登记。
  mainWindow.webContents.on('dom-ready', () => {
    // 启动观测（2026-10-24）：HTML 解析完成≈首绘；boot-splash 与主页面各触发
    // 一次，host 字段区分（file 主机名为空显示 file）。
    let host = 'file'
    try { host = new URL(mainWindow.webContents.getURL()).host || 'file' } catch { /* about:blank */ }
    logElectron('INFO', `[boot] renderer dom-ready (${host})`)
  })
  mainWindow.webContents.on('did-finish-load', () => {
    const url = mainWindow.webContents.getURL()
    logElectron('INFO', `[boot] renderer did-finish-load (${url.slice(0, 60)})`)
    // boot-splash（file://）完成加载也会走到这里：仅 dev server 主页面 /
    // prod dist-renderer 才置 rendererLoaded（bge-m3 双条件语义不变）
    if (!isDev || url.startsWith('http://[::1]:5173')) {
      rendererLoaded = true
      triggerBgeM3Preload()
    }
  })

  // Prevent all in-app navigation — open external URLs in system browser
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url)
    return { action: 'deny' }
  })

  mainWindow.webContents.on('will-navigate', (event, url) => {
    // Allow navigation to the app's own pages (dev server / local file)
    const isDevUrl = isDev && url.startsWith('http://[::1]:5173')
    // dev 下放行 file://：boot-splash.html 初始页在本机（程序性 loadFile 不触发
    // will-navigate，此处防页面侧意外跳转被误拦外链）
    const isLocalFile = url.startsWith('file://')
    if (isDevUrl || isLocalFile) return
    // Block and open external URLs in system browser
    event.preventDefault()
    shell.openExternal(url)
  })

  // Explicit quit on main-window close（pool 窗口已退役，window-all-closed
  // 本可触发；保留显式 quit 语义不变）
  mainWindow.on('closed', () => {
    mainWindow = null
    if (process.platform !== 'darwin') app.quit()
  })

  mainWindow.on('maximize', () => {
    mainWindow?.webContents.send('window:maximized')
  })

  mainWindow.on('unmaximize', () => {
    mainWindow?.webContents.send('window:unmaximized')
  })
}

ipcMain.handle('window:minimize', () => mainWindow?.minimize())
ipcMain.handle('window:maximize', () => {
  if (mainWindow?.isMaximized()) {
    mainWindow.unmaximize()
  } else {
    mainWindow?.maximize()
  }
})
ipcMain.handle('window:close', () => mainWindow?.close())
ipcMain.handle('app:version', () => app.getVersion())
ipcMain.handle('shell:showItemInFolder', (_event, filePath) => {
  shell.showItemInFolder(filePath)
})

// OS 系统通知（2.11 §4.2）：主进程无状态投递（D8）——标题/正文/合并决策全在
// renderer（ActivityMonitor dispatchToasts），main 不解析业务语义；nav 为通知
// 条目字段子集，点击聚焦窗口后原样回传 renderer 走 navigateToNotification。
ipcMain.handle('app:osNotify', (_event, { title, body, silent, nav }) => {
  if (!Notification.isSupported()) return { ok: false, reason: 'unsupported' }
  const n = new Notification({ title, body, silent: Boolean(silent) })
  n.on('click', () => {
    if (!mainWindow || mainWindow.isDestroyed()) return
    if (mainWindow.isMinimized()) mainWindow.restore()
    mainWindow.show()
    mainWindow.focus()
    mainWindow.webContents.send('app:osNotifyClick', nav)
  })
  n.show()
  return { ok: true }
})

ipcMain.on('menu:action', (_event, action) => {
  if (!mainWindow) return
  const wc = mainWindow.webContents
  switch (action) {
    case 'quit': app.quit(); break
    case 'open-settings': wc.send('menu:open-settings'); break
    case 'minimize': mainWindow.minimize(); break
    case 'maximize':
      if (mainWindow.isMaximized()) mainWindow.unmaximize()
      else mainWindow.maximize()
      break
    case 'zoomIn': wc.setZoomLevel(wc.getZoomLevel() + 0.5); break
    case 'zoomOut': wc.setZoomLevel(wc.getZoomLevel() - 0.5); break
    case 'zoomReset': wc.setZoomLevel(0); break
    case 'view-logs': wc.send('menu:view-logs'); break
    case 'export-logs': wc.send('menu:export-logs'); break
    default: break
  }
})

// Knowledge base IPC handlers are registered in ipc-handlers.js via registerIpcHandlers()
// using dynamic pythonManager.getBaseUrl() for correct port resolution.

// 启动链仅 primary 实例执行：app.quit() 是异步的，secondary 实例上 whenReady
// 仍会 resolve（2026-10-25 双实例实测：quit 生效前已建窗口、跑 tool-initializer、
// spawn Python），单实例锁失败必须显式跳过全部启动链注册。
if (gotTheLock) {
app.whenReady().then(async () => {
  // Handle inkcube:// protocol → serve local files (whitelisted)
  protocol.handle('inkcube', (request) => {
    const url = new URL(request.url)
    const filePath = url.searchParams.get('file')
    if (!filePath) {
      return new Response('Missing file parameter', { status: 400 })
    }
    // Whitelist (H6): only user content under <dataDir>/files (uploads and
    // the images extracted next to them) + PPT 模板库资产（详情页页面原型 SVG，
    // 2.3 §4.2；E2 库外置后双源：用户库 ppt-library + 随包内置源）。The renderer
    // displays LLM/markdown-derived content, so an unrestricted handler would
    // let it read ANY local file via inkcube://app?file=...
    const dataDir = process.env.INKCUBE_DATA_DIR
      ? path.resolve(process.env.INKCUBE_DATA_DIR)
      : (isDev
          ? path.resolve(__dirname, '..', 'python', 'data')
          : path.resolve(app.getPath('userData'), 'data'))
    const resourcesRoot = process.resourcesPath
      || path.resolve(__dirname, '..', 'resources')
    const allowedRoots = [
      path.resolve(path.join(dataDir, 'files')),
      // 用户模板库（E2：收录目标，可管理条目的详情预览源）
      path.resolve(path.join(dataDir, 'ppt-library', 'templates')),
      // 随包内置模板源（builtin 条目只读展示的详情预览源）
      path.resolve(path.join(resourcesRoot, 'skills', 'ppt-master', 'templates')),
    ]
    // The query param uses forward slashes — convert before resolving so
    // ../ traversal is normalized and caught by the prefix check.
    const resolved = path.resolve(filePath.replace(/\//g, path.sep))
    const isAllowed = allowedRoots.some((root) =>
      process.platform === 'win32'
        ? resolved.toLowerCase().startsWith(root.toLowerCase() + path.sep)
        : resolved.startsWith(root + path.sep)
    )
    if (!isAllowed) {
      return new Response('Forbidden', { status: 403 })
    }
    return net.fetch('file://' + filePath)
  })

  // Fetch-pool: partition-level SSRF interceptor（2.8 §5.3；V2 池窗口退役，
  // 浏览器承载为主窗口内嵌面板，启动即挂拦截器）
  fetchPool.installSsrfInterceptor()

  // 业务 IPC 注册先于窗口创建：registerIpcHandlers 内部 baseUrl 动态解析，
  // 不依赖 python 在线（fetch 在请求时才发，boot 期业务 UI 未挂载无调用面）。
  registerIpcHandlers(ipcMain, pythonManager)

  // Boot gate IPC 先于窗口创建：BootGate 挂载即 invoke app:boot-status，
  // handler 必须已就位。
  ipcMain.handle('app:boot-status', () => ({
    stage: boot.stage,
    message: boot.message,
    elapsedMs: Date.now() - boot.stageStartedAt,
    progress: boot.progress,
  }))
  ipcMain.handle('app:relaunch', () => {
    logElectron('INFO', '[boot] relaunch requested from boot gate')
    app.relaunch()
    app.exit(0)
  })

  try {
    // 窗口先行：立即创建并显示（index.html 静态 splash 随 HTML 解析即见），
    // 后端启动链在其后推进，阶段经 app:boot-stage 呈现。
    await createWindow()
    logElectron('INFO', '[boot] window shown before backend start (window-first)')

    // Ensure bundled tools are initialized (first run / version update)
    setBootStage('tools')
    const cdpStart = Date.now()
    // 进度载荷为结构化对象（{ phase, copied?, total? }）：日志直接序列化，
    // UI 侧由 BootGate 经 i18n 渲染（tool-initializer 不硬编码文案）
    await ensureToolsInitialized((progress) => {
      logElectron('INFO', `[tool-init] ${JSON.stringify(progress)}`)
      setBootProgress(progress)
    })
    const cdpPort = await readCdpPort()
    logElectron('INFO', `[boot] tools ready, cdp probe took ${Date.now() - cdpStart}ms`)

    setBootStage('python')
    await pythonManager.start({ cdpPort })
    pythonReady = true
    setBootStage('ready')
    logElectron('INFO', 'Application ready')
    triggerBgeM3Preload()
  } catch (err) {
    logElectron('ERROR', `Failed to start application: ${err}`)
    // 窗口先行后不再静默 app.quit()：错误态经 boot gate 呈现，用户可重试
    // （app:relaunch）或退出；boot 期业务 UI 未挂载，无半初始化调用面。
    setBootStage('error', { message: err && err.message ? err.message : String(err) })
  }
})

app.on('before-quit', (event) => {
  if (pythonManager.isRunning()) {
    event.preventDefault()
    pythonManager.stop().finally(() => app.quit())
  }
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

app.on('activate', async () => {
  // Recreate the main window on macOS activate（按主窗口计数，多窗口时代仍正确）
  if (!mainWindow || mainWindow.isDestroyed()) {
    if (!pythonManager.isRunning()) {
      await pythonManager.start()
    }
    await createWindow()
  }
})
} // end of single-instance primary guard

} // end of non-renderer branch
