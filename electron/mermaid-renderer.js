/**
 * Standalone Electron script to render Mermaid diagrams to PNG.
 *
 * Usage:  electron electron/mermaid-renderer.js <input-file>
 *         <input-file> → UTF-8 file containing the Mermaid code
 *         stdout → base64-encoded PNG (2× raster)
 *         stderr → debug / error info
 *
 * Called by python/tools/mermaid_bridge.py to pre-process Mermaid
 * code blocks before pandoc conversion (md → docx / pdf).
 *
 * The SVG is rasterized to PNG in the same offscreen window: embedding
 * mermaid SVG directly breaks in Word (its SVG engine drops <style> CSS
 * so fills fall back to black, and re-metrics CJK text into overlapping
 * glyphs) — a raster PNG renders identically in every viewer.
 *
 * Mermaid code is passed via a temp file (argv[2]) instead of stdin:
 * a piped stdin may yield 0 bytes on Windows Electron (GUI subsystem)
 * child processes, so file input is the only reliable channel.
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

// Sweep userData dirs orphaned by earlier crashed/killed renderer runs.
// Their handles were released when those processes died, so any dir older
// than 60s (no live renderer lasts that long) is safe to remove. This keeps
// leaked dirs bounded even though exit-time removal is unreliable (Chromium
// child processes still hold files open while we are shutting down).
const ORPHAN_MIN_AGE_MS = 60_000
try {
  for (const name of fs.readdirSync(os.tmpdir())) {
    if (!name.startsWith('inkcube-mermaid-') || name === path.basename(tmpUserData)) continue
    const orphan = path.join(os.tmpdir(), name)
    try {
      if (Date.now() - fs.statSync(orphan).mtimeMs > ORPHAN_MIN_AGE_MS) {
        fs.rmSync(orphan, { recursive: true, force: true })
      }
    } catch { /* in use or raced — retried by the next renderer run */ }
  }
} catch { /* tmpdir unreadable — nothing to sweep */ }

// Prevent any window from flashing on screen
app.disableHardwareAcceleration()

let win = null

/**
 * Final exit path. NOTE: app.exit() terminates the process immediately —
 * pending `finally` blocks never run — so all cleanup must happen here,
 * before exiting.
 */
function cleanupAndExit(code) {
  if (win) {
    try { win.destroy() } catch { /* ignore */ }
    win = null
  }
  // Best-effort cleanup of temp userData
  try { fs.rmSync(tmpUserData, { recursive: true, force: true }) } catch { /* ignore */ }
  app.exit(code)
}

function exitWithError(err) {
  // Wait for the write to flush before exiting, or the message may be cut off
  process.stderr.write(`[mermaid-renderer] ${err}\n`, () => cleanupAndExit(1))
}

// ── Input: mermaid code passed as a temp file path in argv[2] ──
const inputFile = process.argv[2]
if (!inputFile || !fs.existsSync(inputFile)) {
  process.stderr.write(
    '[mermaid-renderer] usage: electron mermaid-renderer.js <mermaid-input-file>\n')
  cleanupAndExit(1)
} else {
  app.whenReady()
    .then(() => renderAndExit(fs.readFileSync(inputFile, 'utf-8')))
    .catch(exitWithError)
}

async function renderAndExit(mermaidCode) {
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
    // "\n;void 0" makes the evaluated expression value `undefined` — the
    // UMD bundle's own value (an object holding functions) cannot be
    // structured-cloned back to the main process, which would throw
    // "An object could not be cloned" before any rendering happens.
    await win.webContents.executeJavaScript(mermaidJs + '\n;void 0')

    // ── Render the diagram ──
    const svg = await win.webContents.executeJavaScript(`
      (async () => {
        window.mermaid.initialize({
          startOnLoad: false,
          theme: 'default',
          // strict (mermaid's current default): diagrams are LLM-generated
          // untrusted input — no HTML labels / click callbacks; <br/> still works
          securityLevel: 'strict',
        })
        const id = 'mmd-' + Date.now()
        const { svg } = await window.mermaid.render(id, ${JSON.stringify(mermaidCode)})
        return svg
      })()
    `)

    // ── Rasterize: display the SVG on a white canvas, then screenshot ──
    const vb = svg.match(/viewBox="([\d.eE+-]+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)"/)
    if (!vb) {
      exitWithError('rendered SVG has no viewBox')
      return
    }
    const W = Math.ceil(parseFloat(vb[3]))
    const H = Math.ceil(parseFloat(vb[4]))
    // 2× raster for crisp text: scale the SVG itself and enlarge the window
    // (setZoomFactor does NOT affect capturePage output size in offscreen
    // mode; the SVG is vector so the browser rasterizes it at full size).
    // Bound total pixels for huge diagrams.
    const zoom = W * 2 <= 16000 && H * 2 <= 16000 ? 2 : 1
    const displaySvg = svg
      .replace(/width="100%"/, `width="${W * zoom}" height="${H * zoom}"`)
      .replace(/ style="[^"]*"/, '') // root max-width would shrink it back
    await win.webContents.executeJavaScript(`(() => {
      document.body.style.margin = '0'
      document.body.style.background = '#fff'
      document.body.innerHTML = ${JSON.stringify(
        `<div style="width:${W * zoom}px;height:${H * zoom}px;overflow:hidden">${displaySvg}</div>`)}
    })()`)
    win.setContentSize(W * zoom, H * zoom)
    // Wait for two animation frames so the new layout is fully rasterized
    await win.webContents.executeJavaScript(
      'new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))')
    const image = await win.webContents.capturePage()
    const png = image.toPNG()
    if (png.length < 8 || png[0] !== 0x89 || png[1] !== 0x50 ||
        png[2] !== 0x4e || png[3] !== 0x47) {
      exitWithError('capturePage produced non-PNG output')
      return
    }

    // stdout is a pipe on Windows → async writes; wait for the flush or
    // large PNGs get truncated by the immediate app.exit()
    process.stdout.write(png.toString('base64'), () => cleanupAndExit(0))
  } catch (err) {
    exitWithError(err.message || String(err))
  }
}
