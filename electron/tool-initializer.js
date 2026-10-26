/**
 * tool-initializer.js — 首次启动时初始化工具链
 *
 * 从安装包 resources/tools/ 复制到 %APPDATA%/InkCube/tools/
 * 从安装包 resources/tools/python-build-standalone/ 复制到 %APPDATA%/InkCube/python/
 *
 * 通过 tool-versions.json 判断是否需要重新解压（版本变化时触发）。
 *
 * onProgress 载荷为结构化对象，不携带文案（文案由 renderer 经 i18n 渲染）：
 *   { phase: 'copy-tools' }                              — 顶层工具二进制复制开始
 *   { phase: 'copy-python', copied, total }              — Python 运行时复制进度
 */
const fs = require('fs')
const path = require('path')
const { app } = require('electron')
const { logElectron } = require('./log-utils')

/**
 * 获取 %APPDATA%/InkCube/ 目录
 */
function getAppDataDir() {
  return app.getPath('userData')
}

/**
 * 获取安装包 resources 目录
 */
function getResourcesDir() {
  return process.resourcesPath || path.join(__dirname, '..', 'resources')
}

/**
 * 读取 JSON 文件
 */
function readJson(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf-8'))
  } catch {
    return null
  }
}

/**
 * 写入 JSON 文件（异步 — L4）
 */
async function writeJson(filePath, data) {
  await fs.promises.mkdir(path.dirname(filePath), { recursive: true })
  await fs.promises.writeFile(filePath, JSON.stringify(data, null, 2), 'utf-8')
}

/**
 * 递归统计目录下文件总数（进度分母；与 copyDir 同量级 IO，libuv 线程池）
 */
async function countFiles(dir) {
  let count = 0
  const entries = await fs.promises.readdir(dir, { withFileTypes: true })
  for (const entry of entries) {
    if (entry.isDirectory()) {
      count += await countFiles(path.join(dir, entry.name))
    } else {
      count += 1
    }
  }
  return count
}

/**
 * 递归复制目录（异步 — L4）
 *
 * All IO goes through fs.promises so first-run copying of hundreds of MB
 * runs on the libuv thread pool instead of blocking the Electron main
 * loop — the window stays responsive during initialization.
 *
 * @param {function} [onFile] - 每复制完一个文件回调一次（进度计数用）
 */
async function copyDir(src, dest, onFile) {
  await fs.promises.mkdir(dest, { recursive: true })
  const entries = await fs.promises.readdir(src, { withFileTypes: true })
  for (const entry of entries) {
    const srcPath = path.join(src, entry.name)
    const destPath = path.join(dest, entry.name)
    if (entry.isDirectory()) {
      await copyDir(srcPath, destPath, onFile)
    } else {
      await fs.promises.copyFile(srcPath, destPath)
      if (onFile) onFile()
    }
  }
}

/**
 * 检查工具链是否需要初始化/更新。
 *
 * 比较逻辑：
 * - 若 %APPDATA%/InkCube/tool-versions.json 不存在 → 需要初始化
 * - 若安装包 resources/tools/tool-versions.json 版本更高 → 需要更新
 * - 否则跳过
 *
 * @returns {{ needed: boolean, reason: string }}
 */
function checkInitNeeded() {
  const appDataDir = getAppDataDir()
  const resourcesDir = getResourcesDir()

  const sourceVersionsPath = path.join(resourcesDir, 'tools', 'tool-versions.json')
  const targetVersionsPath = path.join(appDataDir, 'tool-versions.json')

  // 源版本文件不存在（开发环境 resources/tools/ 未人工放置 tool-versions.json）
  if (!fs.existsSync(sourceVersionsPath)) {
    return { needed: false, reason: 'source versions not found (dev mode)' }
  }

  const sourceVersions = readJson(sourceVersionsPath)
  if (!sourceVersions) {
    return { needed: false, reason: 'source versions invalid' }
  }

  // 目标版本文件不存在（首次启动）
  if (!fs.existsSync(targetVersionsPath)) {
    return { needed: true, reason: 'first run' }
  }

  const targetVersions = readJson(targetVersionsPath)
  if (!targetVersions) {
    return { needed: true, reason: 'target versions invalid' }
  }

  // 比较版本号（任意工具版本不同则更新）
  for (const key of ['uv', 'rg', 'ruff', 'pandoc', 'python']) {
    if (sourceVersions[key] && sourceVersions[key] !== targetVersions[key]) {
      return { needed: true, reason: `${key} version changed: ${targetVersions[key]} → ${sourceVersions[key]}` }
    }
  }

  return { needed: false, reason: 'versions match' }
}

/**
 * 执行工具链初始化/更新。
 *
 * 将 resources/tools/ 下的二进制文件复制到 %APPDATA%/InkCube/tools/
 * 将 resources/tools/python-build-standalone/ 复制到 %APPDATA%/InkCube/python/
 *
 * @param {function} [onProgress] - 进度回调 (message: string) => void
 */
async function initializeTools(onProgress) {
  const appDataDir = getAppDataDir()
  const resourcesDir = getResourcesDir()

  const sourceToolsDir = path.join(resourcesDir, 'tools')
  const targetToolsDir = path.join(appDataDir, 'tools')
  const targetPythonDir = path.join(appDataDir, 'python')

  if (!fs.existsSync(sourceToolsDir)) {
    logElectron('WARN', 'Tool initializer: source tools directory not found')
    return false
  }

  // All IO below is async (L4): first-run copying of hundreds of MB runs
  // on the libuv thread pool, so it never blocks the Electron main loop
  // during startup.

  // 1. 复制工具二进制
  if (onProgress) onProgress({ phase: 'copy-tools' })
  logElectron('INFO', 'Tool initializer: copying tools to ' + targetToolsDir)

  await fs.promises.mkdir(targetToolsDir, { recursive: true })

  // 复制顶层文件（uv.exe, rg.exe, ruff.exe, pandoc.exe, tool-versions.json）
  const toolFiles = await fs.promises.readdir(sourceToolsDir)
  for (const file of toolFiles) {
    const srcPath = path.join(sourceToolsDir, file)
    const stat = await fs.promises.stat(srcPath)
    if (stat.isFile()) {
      await fs.promises.copyFile(srcPath, path.join(targetToolsDir, file))
      logElectron('INFO', `Tool initializer: copied ${file}`)
    }
  }

  // 2. 复制 python-build-standalone
  const sourcePythonDir = path.join(sourceToolsDir, 'python-build-standalone')
  if (fs.existsSync(sourcePythonDir)) {
    logElectron('INFO', 'Tool initializer: copying python-build-standalone to ' + targetPythonDir)

    // 如果已存在先删除（避免残留）
    if (fs.existsSync(targetPythonDir)) {
      await fs.promises.rm(targetPythonDir, { recursive: true, force: true })
    }
    // 进度节流：数千文件逐个推送会洪泛 IPC，每 25 个上报一次 + 收尾一次
    const total = await countFiles(sourcePythonDir)
    if (onProgress) onProgress({ phase: 'copy-python', copied: 0, total })
    let copied = 0
    const reportPythonProgress = () => {
      copied += 1
      if (onProgress && (copied % 25 === 0)) {
        onProgress({ phase: 'copy-python', copied, total })
      }
    }
    await copyDir(sourcePythonDir, targetPythonDir, reportPythonProgress)
    if (onProgress) onProgress({ phase: 'copy-python', copied, total })
    logElectron('INFO', `Tool initializer: python-build-standalone copied (${copied} files)`)
  }

  // 3. 写入版本信息
  const sourceVersions = readJson(path.join(sourceToolsDir, 'tool-versions.json'))
  if (sourceVersions) {
    sourceVersions.app_version = app.getVersion()
    sourceVersions.initialized_at = new Date().toISOString()
    await writeJson(path.join(appDataDir, 'tool-versions.json'), sourceVersions)
    logElectron('INFO', 'Tool initializer: versions written')
  }

  return true
}

/**
 * 确保工具链已初始化。在 app.whenReady() 中调用。
 *
 * @param {function} [onProgress] - 进度回调
 * @returns {Promise<boolean>} 是否成功（或无需初始化）
 */
async function ensureToolsInitialized(onProgress) {
  const { needed, reason } = checkInitNeeded()

  if (!needed) {
    logElectron('INFO', `Tool initializer: no init needed (${reason})`)
    return true
  }

  logElectron('INFO', `Tool initializer: init needed (${reason})`)

  try {
    const success = await initializeTools(onProgress)
    if (success) {
      logElectron('INFO', 'Tool initializer: initialization complete')
    } else {
      logElectron('WARN', 'Tool initializer: initialization skipped (source not found)')
    }
    return success
  } catch (err) {
    logElectron('ERROR', `Tool initializer: initialization failed: ${err.message}`)
    return false
  }
}

module.exports = {
  ensureToolsInitialized,
  checkInitNeeded,
  getAppDataDir,
  getResourcesDir,
}
