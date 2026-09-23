const { app, BrowserWindow, ipcMain, protocol, net, shell } = require('electron')
const path = require('path')
const { PythonManager } = require('./python-manager')
const { registerIpcHandlers } = require('./ipc-handlers')
const { logElectron } = require('./log-utils')
const { ensureToolsInitialized } = require('./tool-initializer')

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

  mainWindow.on('closed', () => {
    mainWindow = null
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
  // Handle inkcube:// protocol → serve local files
  protocol.handle('inkcube', (request) => {
    const url = new URL(request.url)
    const filePath = url.searchParams.get('file')
    if (!filePath) {
      return new Response('Missing file parameter', { status: 400 })
    }
    return net.fetch('file://' + filePath)
  })

  try {
    // Ensure bundled tools are initialized (first run / version update)
    await ensureToolsInitialized((msg) => logElectron('INFO', `[tool-init] ${msg}`))

    await pythonManager.start()
    registerIpcHandlers(ipcMain, pythonManager)
    await createWindow()
    logElectron('INFO', 'Application ready')
  } catch (err) {
    logElectron('ERROR', `Failed to start application: ${err}`)
    app.quit()
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
  if (BrowserWindow.getAllWindows().length === 0) {
    if (!pythonManager.isRunning()) {
      await pythonManager.start()
    }
    await createWindow()
  }
})
