/**
 * Standalone Electron script to render Mermaid diagrams / inline SVG to PNG.
 *
 * Usage:  electron electron/mermaid-renderer.js <input-file>
 *         <input-file> → UTF-8 file: .mmd = Mermaid code, .svg = SVG source
 *         (the suffix is the mode switch — both inputs come from
 *         python/tools/mermaid_bridge.py temp files)
 *         stdout → base64-encoded PNG (2× raster)
 *         stderr → debug / error info
 *
 * Called by python/tools/mermaid_bridge.py to pre-process Mermaid code
 * blocks and inline <svg> graphics before pandoc conversion (md → docx/pdf).
 *
 * The SVG is rasterized to PNG in the same offscreen window: embedding
 * SVG directly breaks in Word (its SVG engine drops <style> CSS so fills
 * fall back to black, and re-metrics CJK text into overlapping glyphs) —
 * a raster PNG renders identically in every viewer.
 *
 * Input is passed via a temp file (argv[2]) instead of stdin: a piped
 * stdin may yield 0 bytes on Windows Electron (GUI subsystem) child
 * processes, so file input is the only reliable channel.
 *
 * Uses Electron's bundled Chromium + the project's mermaid.min.js so that
 * no extra dependency (puppeteer, mmdc, …) is needed.
 */

// ── Pure helpers (no electron dependency — vitest-testable) ──

/**
 * Blacklist sanitization for LLM-generated SVG before offscreen rasterizing.
 * UI 内嵌走前端白名单（src/components/Markdown.tsx sanitizeSvg）；此处是无
 * DOMParser 的独立截图子进程，按攻击面剥离：脚本/HTML 逃逸元素
 * （script/foreignObject/iframe/object/embed）、事件属性、外链引用
 * （javascript:/http(s):/file:）——静态矢量截图不受影响；data: 内嵌
 * 图片无脚本面，保留。
 */
const SVG_DROP_PAIR_RE =
  /<(script|foreignObject|iframe|object|embed)\b[^>]*>[\s\S]*?<\/\1\s*>/gi
// 未闭合/自闭合的残留标签（配对正则兜不住的畸形输出）一并拆除
const SVG_DROP_TAG_RE = /<\/?(script|foreignObject|iframe|object|embed)\b[^>]*\/?>/gi
const SVG_EVENT_ATTR_RE = /\son[a-z]+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)/gi
const SVG_EXTERNAL_HREF_RE =
  /\s(?:xlink:href|href)\s*=\s*(?:"(?:javascript:|https?:|file:)[^"]*"|'(?:javascript:|https?:|file:)[^']*')/gi

function sanitizeSvgForRaster(source) {
  if (typeof source !== 'string') return source
  return source
    .replace(SVG_DROP_PAIR_RE, '')
    .replace(SVG_DROP_TAG_RE, '')
    .replace(SVG_EVENT_ATTR_RE, '')
    .replace(SVG_EXTERNAL_HREF_RE, '')
}

/**
 * Resolve the intrinsic pixel size of an SVG source: explicit numeric
 * width/height attributes first, viewBox w/h as fallback（viewBox-only
 * 根是 LLM 输出的常见形态）. Returns null when neither is usable
 * （仅百分比尺寸或无根标签）— caller fails the render and the bridge
 * degrades gracefully.
 */
function resolveSvgSize(source) {
  const rootTag = /<svg\b[^>]*>/i.exec(source)
  if (!rootTag) return null
  const w = /\bwidth\s*=\s*"(\d+(?:\.\d+)?)(?:px)?"/i.exec(rootTag[0])
  const h = /\bheight\s*=\s*"(\d+(?:\.\d+)?)(?:px)?"/i.exec(rootTag[0])
  if (w && h) {
    return { width: Math.ceil(parseFloat(w[1])), height: Math.ceil(parseFloat(h[1])) }
  }
  const vb = /\bviewBox\s*=\s*"[\d.eE+-]+\s+[\d.eE+-]+\s+([\d.eE+-]+)\s+([\d.eE+-]+)"/i
    .exec(rootTag[0])
  if (vb) {
    return { width: Math.ceil(parseFloat(vb[1])), height: Math.ceil(parseFloat(vb[2])) }
  }
  return null
}

/**
 * Normalize the root <svg> tag for offscreen display: drop root
 * width/height（mermaid 产物是 width="100%"）and style（mermaid 根的
 * max-width 会把光栅缩回去）, then inject the explicit display size.
 */
function prepareSvgSource(source, displayWidth, displayHeight) {
  return source.replace(/<svg\b([^>]*)>/i, (_m, attrs) => {
    const cleaned = attrs
      .replace(/\swidth\s*=\s*("[^"]*"|'[^']*')/i, '')
      .replace(/\sheight\s*=\s*("[^"]*"|'[^']*')/i, '')
      .replace(/\sstyle\s*=\s*("[^"]*"|'[^']*')/i, '')
    return `<svg${cleaned} width="${displayWidth}" height="${displayHeight}">`
  })
}

module.exports = { sanitizeSvgForRaster, resolveSvgSize, prepareSvgSource }

// ── Entry (electron-dependent; skipped under vitest import) ──

function runRenderer() {
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

  // 无头渲染输出尺寸确定性：offscreen capturePage 输出 = 逻辑尺寸 ×
  // deviceScaleFactor，默认跟随宿主显示器 DPI（高缩放屏上「2× raster」
  // 实测出 4×，导出尺寸随宿主屏漂移，2026-10-24 根因定位）——PNG 尺寸
  // 契约（viewBox × zoom）必须与宿主屏无关，强制 scale 1
  app.commandLine.appendSwitch('force-device-scale-factor', '1')

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

  /**
   * Rasterize an SVG string on a white canvas and emit base64 PNG on stdout.
   * Shared by the mermaid path（render 产物）and the inline-SVG path.
   */
  async function rasterizeAndExit(svg, width, height) {
    // 2× raster for crisp text: scale the SVG itself and enlarge the window
    // (setZoomFactor does NOT affect capturePage output size in offscreen
    // mode; the SVG is vector so the browser rasterizes it at full size).
    // Bound total pixels for huge diagrams.
    const zoom = width * 2 <= 16000 && height * 2 <= 16000 ? 2 : 1
    const displaySvg = prepareSvgSource(svg, width * zoom, height * zoom)
    await win.webContents.executeJavaScript(`(() => {
      document.body.style.margin = '0'
      document.body.style.background = '#fff'
      document.body.innerHTML = ${JSON.stringify(
        `<div style="width:${width * zoom}px;height:${height * zoom}px;overflow:hidden">${displaySvg}</div>`)}
    })()`)
    win.setContentSize(width * zoom, height * zoom)
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
  }

  // ── Input: source passed as a temp file path in argv[2] ──
  const inputFile = process.argv[2]
  if (!inputFile || !fs.existsSync(inputFile)) {
    process.stderr.write(
      '[mermaid-renderer] usage: electron mermaid-renderer.js <input-file>\n')
    cleanupAndExit(1)
    return
  }
  const isSvgInput = inputFile.toLowerCase().endsWith('.svg')
  const source = fs.readFileSync(inputFile, 'utf-8')

  app.whenReady()
    .then(async () => {
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

      if (isSvgInput) {
        // ── Inline-SVG mode: sanitize → size → rasterize (no mermaid) ──
        const cleaned = sanitizeSvgForRaster(source)
        const size = resolveSvgSize(cleaned)
        if (!size) {
          exitWithError('svg has no usable width/height/viewBox')
          return
        }
        await rasterizeAndExit(cleaned, size.width, size.height)
        return
      }

      // ── Mermaid mode ──
      // Inject mermaid.min.js (UMD bundle → sets window.mermaid)
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
          const { svg } = await window.mermaid.render(id, ${JSON.stringify(source)})
          return svg
        })()
      `)

      const size = resolveSvgSize(svg)
      if (!size) {
        exitWithError('rendered SVG has no usable width/height/viewBox')
        return
      }
      await rasterizeAndExit(svg, size.width, size.height)
    })
    .catch(exitWithError)
}

// Spawned directly (electron mermaid-renderer.js …) or delegated from
// main.js renderer-mode guard; neither fires under vitest import.
if (require.main === module || process.env.INKCUBE_MERMAID_RENDERER === '1') {
  runRenderer()
}
