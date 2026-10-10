// ── Mermaid renderer mode (spawned by doc_generator.py for SVG export) ──
// When INKCUBE_MERMAID_RENDERER=1, this process is a headless one-shot
// renderer.  Delegate immediately and skip all main-app initialisation.
if (process.env.INKCUBE_MERMAID_RENDERER === '1') {
  require('./mermaid-renderer')
} else {

const { app, BrowserWindow, ipcMain, protocol, net, shell } = require('electron')
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

logElectron('INFO', 'InkCube starting...')

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
    },
  })

  if (isDev) {
    mainWindow.loadURL('http://localhost:5173')
    mainWindow.webContents.openDevTools()
  } else {
    mainWindow.loadFile(path.join(__dirname, '..', 'dist-renderer', 'index.html'))
  }

  // Prevent all in-app navigation — open external URLs in system browser
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url)
    return { action: 'deny' }
  })

  mainWindow.webContents.on('will-navigate', (event, url) => {
    // Allow navigation to the app's own pages (dev server / local file)
    const isDevUrl = isDev && url.startsWith('http://localhost:5173')
    const isLocalFile = !isDev && url.startsWith('file://')
    if (isDevUrl || isLocalFile) return
    // Block and open external URLs in system browser
    event.preventDefault()
    shell.openExternal(url)
  })

  // Explicit quit on main-window close: the hidden fetch-pool window (2.8)
  // stays alive by design, so window-all-closed never fires — quit here.
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
ipcMain.handle('window:isMaximized', () => mainWindow?.isMaximized() ?? false)
ipcMain.handle('app:version', () => app.getVersion())
ipcMain.handle('shell:showItemInFolder', (_event, filePath) => {
  shell.showItemInFolder(filePath)
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

app.whenReady().then(async () => {
  // Handle inkcube:// protocol → serve local files (whitelisted)
  protocol.handle('inkcube', (request) => {
    const url = new URL(request.url)
    const filePath = url.searchParams.get('file')
    if (!filePath) {
      return new Response('Missing file parameter', { status: 400 })
    }
    // Whitelist (H6): only user content under <dataDir>/files (uploads and
    // the images extracted next to them). The renderer displays
    // LLM/markdown-derived content, so an unrestricted handler would let
    // it read ANY local file via inkcube://app?file=...
    const dataDir = process.env.INKCUBE_DATA_DIR
      ? path.resolve(process.env.INKCUBE_DATA_DIR)
      : (isDev
          ? path.resolve(__dirname, '..', 'python', 'data')
          : path.resolve(app.getPath('userData'), 'data'))
    const allowedRoot = path.resolve(path.join(dataDir, 'files'))
    // The query param uses forward slashes — convert before resolving so
    // ../ traversal is normalized and caught by the prefix check.
    const resolved = path.resolve(filePath.replace(/\//g, path.sep))
    const isAllowed = process.platform === 'win32'
      ? resolved.toLowerCase().startsWith(allowedRoot.toLowerCase() + path.sep)
      : resolved.startsWith(allowedRoot + path.sep)
    if (!isAllowed) {
      return new Response('Forbidden', { status: 403 })
    }
    return net.fetch('file://' + filePath)
  })

  // Fetch-pool: partition-level SSRF interceptor + resident pool window (2.8 §6.2)
  fetchPool.installSsrfInterceptor()
  fetchPool.ensurePoolWindow()

  try {
    // Ensure bundled tools are initialized (first run / version update)
    await ensureToolsInitialized((msg) => logElectron('INFO', `[tool-init] ${msg}`))

    const cdpPort = await readCdpPort()
    await pythonManager.start({ cdpPort })
    registerIpcHandlers(ipcMain, pythonManager)
    await createWindow()
    logElectron('INFO', 'Application ready')
  } catch (err) {
    logElectron('ERROR', `Failed to start application: ${err}`)
    app.quit()
  }
})

app.on('before-quit', (event) => {
  // Fetch-pool windows must die before the close-chain settles: the pool
  // window's close is preventDefault-ed, it would block window-all-closed.
  fetchPool.shutdown()
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
  // Count the MAIN window only: the hidden fetch-pool window is always alive,
  // so getAllWindows().length would never reach 0 and macOS activate would
  // never recreate the main window after close.
  if (!mainWindow || mainWindow.isDestroyed()) {
    if (!pythonManager.isRunning()) {
      await pythonManager.start()
    }
    await createWindow()
  }
})

} // end of non-renderer branch
