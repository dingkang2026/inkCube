/**
 * Derive the real log level from a Python console line.
 * Python's console format embeds the level inline:
 *   "2026-10-05 20:54:15 [WARNING] module: message"
 * Blanket-INFO(stdout)/blanket-ERROR(stderr) painted routine logs red in the
 * log viewer — stderr is logging's default stream, not an error signal.
 *
 * Pure functions, zero dependencies (kept separate from python-manager so
 * they are unit-testable without the electron runtime).
 */
const _PY_LEVEL_RE = /\[(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|FATAL)\]/

function _pyLogLevel(text, fallback) {
  const m = text.match(_PY_LEVEL_RE)
  if (!m) return fallback
  const lv = m[1]
  if (lv === 'WARNING' || lv === 'WARN') return 'WARN'
  if (lv === 'ERROR' || lv === 'CRITICAL' || lv === 'FATAL') return 'ERROR'
  return 'INFO' // DEBUG / INFO (DEBUG never reaches console: root level is INFO)
}

// Known routine stderr producers without an inline level — the blanket ERROR
// fallback painted them red on every boot (2026-10-13 startup noise):
//   - uvicorn banner: "INFO:     Uvicorn running on http://127.0.0.1:65170"
//   - tqdm progress:  "Loading weights:  27%|██▋| 105/391 [00:00<00:00, 1013.58it/s]"
const _UVICORN_BANNER_RE = /^INFO:\s/
const _TQDM_PROGRESS_RE = /\d+%\|/

function _stderrFallbackLevel(text) {
  if (_UVICORN_BANNER_RE.test(text) || _TQDM_PROGRESS_RE.test(text)) return 'INFO'
  // Remaining levelless stderr output (tracebacks, native crashes) genuinely
  // means trouble — keep it red.
  return 'ERROR'
}

module.exports = { _pyLogLevel, _stderrFallbackLevel }
