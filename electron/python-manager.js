const { spawn } = require('child_process')
const path = require('path')
const net = require('net')
const { app, BrowserWindow } = require('electron')
const { logElectron } = require('./log-utils')

class PythonManager {
  constructor() {
    this.process = null
    this.port = null
    this.isDev = process.env.NODE_ENV === 'development'
    this.externalPython = false  // true when auto-detected a running Python instance
  }

  async getFreePort() {
    return new Promise((resolve, reject) => {
      const server = net.createServer()
      server.listen(0, '127.0.0.1', () => {
        const port = server.address().port
        server.close(() => resolve(port))
      })
      server.on('error', reject)
    })
  }

  getPythonCommand() {
    if (this.isDev) {
      const projectRoot = path.join(__dirname, '..')
      const condaBase =
        process.platform === 'win32'
          ? path.join(process.env.USERPROFILE || 'C:\\Users\\Kelvin', '.conda')
          : '/opt/conda'
      const condaPython =
        process.platform === 'win32'
          ? path.join(condaBase, 'envs', 'inkcube', 'python.exe')
          : path.join(condaBase, 'envs', 'inkcube', 'bin', 'python')
      const pythonDir = path.join(projectRoot, 'python')

      return {
        command: condaPython,
        args: ['-m', 'uvicorn', 'main:app', '--host', '127.0.0.1'],
        cwd: pythonDir,
      }
    }

    const exeName = process.platform === 'win32' ? 'inkcube-agent.exe' : 'inkcube-agent'
    return {
      command: path.join(process.resourcesPath, 'python', exeName),
      args: [],
      cwd: app.getPath('userData'),
    }
  }

  async start() {
    // Dev mode: auto-detect already-running Python (e.g. started manually or from PyCharm)
    if (this.isDev) {
      const detected = await this._detectRunningPython()
      if (detected) {
        this.port = detected
        this.externalPython = true
        console.log(`[PythonManager] Dev mode — detected existing Python on port ${detected}`)
        return
      }
    }

    this.port = await this.getFreePort()
    const { command, args, cwd } = this.getPythonCommand()

    // Kill any stale Python processes from previous sessions
    await this._killStaleProcesses()

    const spawnEnv = {
      ...process.env,
      PYTHONUNBUFFERED: '1',
      PYTHONUTF8: '1',
      PYTHONIOENCODING: 'utf-8',
      INKCUBE_PARENT_PID: String(process.pid),
    }

    this.process = spawn(command, [...args, '--port', String(this.port)], {
      cwd,
      env: spawnEnv,
      stdio: ['pipe', 'pipe', 'pipe'],
    })

    this.process.stdout.on('data', (data) => {
      const text = data.toString().trim()
      if (text) logElectron('INFO', `[python] ${text}`)
    })

    this.process.stderr.on('data', (data) => {
      const text = data.toString().trim()
      if (text) logElectron('ERROR', `[python] ${text}`)
    })

    this.process.on('error', (err) => {
      logElectron('ERROR', `Python process error: ${err.message}`)
    })

    this.process.on('exit', (code, signal) => {
      logElectron('INFO', `Python process exited with code ${code}, signal ${signal}`)
      this.process = null
      // Notify frontend of process exit
      BrowserWindow.getAllWindows().forEach(win => {
        win.webContents.send('python:process-exit', { code, signal })
      })
    })

    await this.waitForHealth(15000)
  }

  async waitForHealth(timeout) {
    const start = Date.now()
    while (Date.now() - start < timeout) {
      try {
        const res = await fetch(`http://127.0.0.1:${this.port}/health`)
        if (res.ok) return
      } catch {
        // not ready yet
      }
      await new Promise((r) => setTimeout(r, 300))
    }
    throw new Error(`Python backend failed to start within ${timeout}ms`)
  }

  async stop() {
    if (!this.process) return
    try {
      await fetch(`http://127.0.0.1:${this.port}/shutdown`, { method: 'POST' })
      await new Promise((r) => setTimeout(r, 2000))
    } catch {
      // ignore
    }
    if (this.process) {
      this.process.kill('SIGTERM')
      setTimeout(() => {
        if (this.process) this.process.kill('SIGKILL')
      }, 3000)
    }
  }

  getBaseUrl() {
    return `http://127.0.0.1:${this.port}`
  }

  isRunning() {
    if (this.externalPython) return true  // external Python managed by user
    return this.process !== null && this.process.exitCode === null
  }

  /**
   * Check if a Python backend is already running on common dev ports.
   * Returns the port number if found, null otherwise.
   */
  async _detectRunningPython() {
    const ports = [8765, 8766, 8767]
    for (const port of ports) {
      try {
        const res = await fetch(`http://127.0.0.1:${port}/health`, { signal: AbortSignal.timeout(1000) })
        if (res.ok) return port
      } catch {
        // not running on this port
      }
    }
    return null
  }

  async _killStaleProcesses() {
    // Kill leftover uvicorn processes on ports 8765-8799
    // This handles the case where the app was force-closed
    try {
      if (process.platform === 'win32') {
        const { execSync } = require('child_process')
        const output = execSync('netstat -ano | findstr LISTENING', { encoding: 'utf8', timeout: 3000 })
        const lines = output.split('\n')
        const killedPids = new Set()
        for (const line of lines) {
          const match = line.match(/127\.0\.0\.1:(\d+)\s+/)
          if (match) {
            const port = parseInt(match[1])
            // uvicorn dynamic ports 8765-8799
            if (port >= 8765 && port <= 8799) {
              const pidMatch = line.match(/(\d+)\s*$/)
              if (pidMatch) {
                const pid = parseInt(pidMatch[1])
                if (!killedPids.has(pid)) {
                  killedPids.add(pid)
                  try {
                    process.kill(pid, 'SIGTERM')
                    console.log(`[PythonManager] Killed stale PID ${pid} on port ${port}`)
                  } catch { /* ignore */ }
                }
              }
            }
          }
        }
      }
    } catch { /* ignore - non-critical cleanup */ }
    // Brief pause to let ports free up
    await new Promise(r => setTimeout(r, 300))
  }
}

module.exports = { PythonManager }
