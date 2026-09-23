const fs = require('fs')
const path = require('path')
const { app } = require('electron')

const isDev = process.env.NODE_ENV === 'development'

// ── Log rotation config (aligned with Python RotatingFileHandler) ──
const LOG_MAX_BYTES = 5 * 1024 * 1024  // 5 MB
const LOG_BACKUP_COUNT = 3

/**
 * Resolve the shared log file path.
 * In dev mode: <projectRoot>/python/data/logs/app.log
 * In prod:     <userData>/data/logs/app.log
 */
function getLogFilePath() {
  let dataDir
  if (process.env.INKCUBE_DATA_DIR) {
    dataDir = process.env.INKCUBE_DATA_DIR
  } else if (isDev) {
    dataDir = path.join(__dirname, '..', 'python', 'data')
  } else {
    dataDir = path.join(app.getPath('userData'), 'data')
  }
  const logDir = path.join(dataDir, 'logs')
  fs.mkdirSync(logDir, { recursive: true })
  return path.join(logDir, 'app.log')
}

const logFile = getLogFilePath()

/**
 * Format timestamp to match Python's log format:
 *   [YYYY-MM-DD HH:MM:SS.mmm]
 */
function _formatTimestamp() {
  const now = new Date()
  const y = now.getFullYear()
  const mo = String(now.getMonth() + 1).padStart(2, '0')
  const d = String(now.getDate()).padStart(2, '0')
  const h = String(now.getHours()).padStart(2, '0')
  const mi = String(now.getMinutes()).padStart(2, '0')
  const s = String(now.getSeconds()).padStart(2, '0')
  const ms = String(now.getMilliseconds()).padStart(3, '0')
  return `${y}-${mo}-${d} ${h}:${mi}:${s}.${ms}`
}

/**
 * Rotate log file if it exceeds LOG_MAX_BYTES.
 * Shifts existing backups: .3 → deleted, .2 → .3, .1 → .2, current → .1
 */
function _rotateIfNeeded() {
  try {
    const stat = fs.statSync(logFile)
    if (stat.size < LOG_MAX_BYTES) return

    // Shift backups: .3 ← .2 ← .1 ← current
    for (let i = LOG_BACKUP_COUNT; i >= 1; i--) {
      const src = i === 1 ? logFile : `${logFile}.${i - 1}`
      const dst = `${logFile}.${i}`
      try {
        if (fs.existsSync(src)) {
          if (i === LOG_BACKUP_COUNT) {
            fs.unlinkSync(src)  // Delete oldest backup
          } else {
            fs.renameSync(src, dst)
          }
        }
      } catch { /* ignore rotation errors */ }
    }
  } catch { /* ignore stat errors */ }
}

/**
 * Write a structured log line to the shared app.log file.
 * Format: [YYYY-MM-DD HH:MM:SS.mmm] [electron] [LEVEL] message
 * Aligned with Python's log format.
 */
function logElectron(level, msg) {
  const line = `[${_formatTimestamp()}] [electron] [${level}] ${msg}\n`
  try {
    _rotateIfNeeded()
    fs.appendFileSync(logFile, line)
  } catch { /* ignore write errors */ }
  // Also print to console
  if (level === 'ERROR') {
    console.error(msg)
  } else {
    console.log(msg)
  }
}

/**
 * Read all log files (current + rotated backups) in chronological order.
 * Returns combined content with separator markers for backup files.
 */
function readLogFile() {
  try {
    const parts = []

    // Read oldest backups first (chronological order)
    for (let i = LOG_BACKUP_COUNT; i >= 1; i--) {
      const backupPath = `${logFile}.${i}`
      if (fs.existsSync(backupPath)) {
        const stat = fs.statSync(backupPath)
        const mtime = new Date(stat.mtimeMs).toISOString().replace('T', ' ').slice(0, 19)
        parts.push(`\n─── app.log.${i} (${_formatFileSize(stat.size)}, ${mtime}) ───\n`)
        parts.push(fs.readFileSync(backupPath, 'utf-8'))
      }
    }

    // Read current log file last
    if (fs.existsSync(logFile)) {
      const stat = fs.statSync(logFile)
      parts.push(`\n─── app.log (${_formatFileSize(stat.size)}, current) ───\n`)
      parts.push(fs.readFileSync(logFile, 'utf-8'))
    }

    return parts.join('')
  } catch {
    return ''
  }
}

/** Format file size to human-readable string. */
function _formatFileSize(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
}

/**
 * Get log file information (sizes, counts).
 * Returns info about current log and all backup files.
 */
function getLogInfo() {
  const files = []
  let totalSize = 0
  let totalLines = 0

  // Check backup files
  for (let i = LOG_BACKUP_COUNT; i >= 1; i--) {
    const backupPath = `${logFile}.${i}`
    try {
      if (fs.existsSync(backupPath)) {
        const stat = fs.statSync(backupPath)
        const content = fs.readFileSync(backupPath, 'utf-8')
        const lineCount = content.split('\n').filter(Boolean).length
        files.push({
          name: `app.log.${i}`,
          size: stat.size,
          sizeFormatted: _formatFileSize(stat.size),
          lines: lineCount,
          modified: stat.mtime.toISOString(),
        })
        totalSize += stat.size
        totalLines += lineCount
      }
    } catch { /* ignore */ }
  }

  // Check current log file
  try {
    if (fs.existsSync(logFile)) {
      const stat = fs.statSync(logFile)
      const content = fs.readFileSync(logFile, 'utf-8')
      const lineCount = content.split('\n').filter(Boolean).length
      files.push({
        name: 'app.log',
        size: stat.size,
        sizeFormatted: _formatFileSize(stat.size),
        lines: lineCount,
        modified: stat.mtime.toISOString(),
        current: true,
      })
      totalSize += stat.size
      totalLines += lineCount
    }
  } catch { /* ignore */ }

  return {
    files,
    totalSize,
    totalSizeFormatted: _formatFileSize(totalSize),
    totalLines,
    maxBytes: LOG_MAX_BYTES,
    backupCount: LOG_BACKUP_COUNT,
  }
}

/** Get the log file path (for export). */
function getLogPath() {
  return logFile
}

module.exports = { logElectron, readLogFile, getLogPath, getLogFilePath, getLogInfo }
