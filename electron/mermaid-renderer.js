/**
 * Standalone Electron script to render Mermaid diagrams to SVG.
 *
 * Usage:  electron electron/mermaid-renderer.js
 *         stdin  → Mermaid code
 *         stdout → SVG string
 *         stderr → debug / error info
 *
 * Called by python/tools/doc_generator.py to pre-process Mermaid
 * code blocks before pandoc conversion (md → docx / pdf).
 *
 * Uses Electron's bundled Chromium + the project's mermaid.min.js
 * so that no extra dependency (puppeteer, mmdc, …) is needed.
 */

const { app, BrowserWindow } = require('electron')
const fs = require('fs')
const path = require('path')
const os = require('os')

// Use a unique temp userData dir so this process never conflicts with
// the main application instance that may be running simultaneously.
const tmpUserData = path.join(os.tmpdir(), `inkcube-mermaid-${process.pid}`)
app.setPath('userData', tmpUserData)

// Prevent any window from flashing on screen
app.disableHardwareAcceleration()

let inputData = ''

process.stdin.setEncoding('utf8')
process.stdin.on('data', (chunk) => { inputData += chunk })
process.stdin.on('end', () => {
  app.whenReady().then(() => renderAndExit(inputData)).catch(exitWithError)
})

function exitWithError(err) {
  process.stderr.write(`[mermaid-renderer] ${err}\n`)
  app.exit(1)
}

async function renderAndExit(mermaidCode) {
  let win = null
  try {
    win = new BrowserWindow({
      show: false,
      width: 1200,
      height: 800,
      webPreferences: {
        offscreen: true,
        backgroundThrottling: false,
      },
    })

    // Load a blank page so we have a DOM to work with
    await win.loadURL('data:text/html,<html><body></body></html>')

    // ── Inject mermaid.min.js (UMD bundle → sets window.mermaid) ──
    // Production: copied to resources/mermaid/ via electron-builder extraResources
    // Development: read from node_modules
    const prodMermaid = path.join(process.resourcesPath, 'mermaid', 'mermaid.min.js')
    const devMermaid = path.join(__dirname, '..', 'node_modules', 'mermaid', 'dist', 'mermaid.min.js')
    const mermaidPath = fs.existsSync(prodMermaid) ? prodMermaid : devMermaid
    const mermaidJs = fs.readFileSync(mermaidPath, 'utf-8')
    await win.webContents.executeJavaScript(mermaidJs)

    // ── Render the diagram ──
    const svg = await win.webContents.executeJavaScript(`
      (async () => {
        window.mermaid.initialize({
          startOnLoad: false,
          theme: 'default',
          securityLevel: 'loose',
        })
        const id = 'mmd-' + Date.now()
        const { svg } = await window.mermaid.render(id, ${JSON.stringify(mermaidCode)})
        return svg
      })()
    `)

    process.stdout.write(svg)
    app.exit(0)
  } catch (err) {
    exitWithError(err.message || String(err))
  } finally {
    if (win) {
      win.destroy()
    }
    // Best-effort cleanup of temp userData
    try { fs.rmSync(tmpUserData, { recursive: true, force: true }) } catch { /* ignore */ }
  }
}
