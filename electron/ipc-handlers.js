const { logElectron, readLogFile, getLogPath, getLogInfo } = require('./log-utils')

/**
 * Unified Python response handler — bridges InkCubeError to frontend.
 * On success (res.ok): returns parsed JSON.
 * On error (!res.ok): throws an InkCubeIPCError with structured fields
 *   so that useErrorHandler can classify and display appropriate UI.
 */
async function handlePythonResponse(res) {
  if (res.ok) return res.json()
  const body = await res.json().catch(() => null)
  const err = new Error(body?.message || `Python backend error (${res.status})`)
  err.errorCode = body?.code || 'UNKNOWN_ERROR'
  err.errorCategory = body?.category || 'INTERNAL'
  err.errorSuggestion = body?.suggestion || ''
  err.errorDetail = body?.detail || ''
  throw err
}

/**
 * Upload files to a task workspace using manual multipart construction.
 * Avoids FormData/Blob which are unreliable in Electron main process.
 */
async function _uploadFilesToTask(pythonManager, taskId, filePaths) {
  const fs = require('fs')
  const path = require('path')
  const baseUrl = pythonManager.getBaseUrl()
  const boundary = '----InkCubeBoundary' + Date.now()

  const parts = []
  for (const fp of filePaths) {
    const fileName = path.basename(fp).replace(/"/g, '\\"')
    const fileData = fs.readFileSync(fp)
    parts.push(
      Buffer.from(
        `--${boundary}\r\n` +
        `Content-Disposition: form-data; name="files"; filename="${fileName}"\r\n` +
        `Content-Type: application/octet-stream\r\n\r\n`
      )
    )
    parts.push(fileData)
    parts.push(Buffer.from('\r\n'))
  }
  parts.push(Buffer.from(`--${boundary}--\r\n`))

  const body = Buffer.concat(parts)

  const res = await fetch(`${baseUrl}/tasks/${taskId}/files/upload`, {
    method: 'POST',
    headers: { 'Content-Type': `multipart/form-data; boundary=${boundary}` },
    body: body,
  })
  return handlePythonResponse(res)
}

function registerIpcHandlers(ipcMain, pythonManager) {
  // Expose Python backend base URL for direct HTTP calls (e.g. file upload)
  ipcMain.handle('python:getBaseUrl', () => pythonManager.getBaseUrl())

  // ── System ──

  ipcMain.handle('system:getHealth', async () => {
    try {
      const res = await fetch(`${pythonManager.getBaseUrl()}/health`)
      if (!res.ok) {
        return { status: 'degraded', services: {}, error: `Health check failed (${res.status})` }
      }
      return res.json()
    } catch (e) {
      logElectron('WARN', `Health check failed: ${e.message}`)
      // Return error code, let frontend handle i18n
      return { status: 'down', services: {}, errorCode: 'PYTHON_SERVICE_DOWN' }
    }
  })

  ipcMain.handle('system:getLogs', async () => {
    return readLogFile()
  })

  ipcMain.handle('system:getLogInfo', async () => {
    return getLogInfo()
  })

  ipcMain.handle('system:exportLogs', async () => {
    const { dialog, BrowserWindow } = require('electron')
    const fs = require('fs')
    const logContent = readLogFile()
    const logPath = getLogPath()

    // Build export content with system context
    let healthInfo = 'N/A'
    try {
      const res = await fetch(`${pythonManager.getBaseUrl()}/health`)
      if (res.ok) {
        const health = await res.json()
        healthInfo = Object.entries(health.services || {})
          .map(([k, v]) => `${k}: ${v.status}${v.code ? ` (${v.code})` : ''}`)
          .join('\n')
      }
    } catch { /* ignore */ }

    const exportContent = [
      '=== InkCube Log Export ===',
      `Time: ${new Date().toISOString()}`,
      `Log file: ${logPath}`,
      '',
      '--- Health Status ---',
      healthInfo,
      '',
      '--- Logs ---',
      logContent,
    ].join('\n')

    const win = BrowserWindow.getFocusedWindow()
    const { canceled, filePath } = await dialog.showSaveDialog(win || undefined, {
      title: 'Export Logs',
      defaultPath: `inkcube-logs-${new Date().toISOString().slice(0, 10)}.txt`,
      filters: [{ name: 'Text', extensions: ['txt', 'log'] }],
    })

    if (!canceled && filePath) {
      fs.writeFileSync(filePath, exportContent, 'utf-8')
      logElectron('INFO', `Logs exported to ${filePath}`)
    }
  })

  // ── Data Management ──

  ipcMain.handle('system:getDataUsage', async () => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/system/data/usage`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('system:cleanData', async (_event, category) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/system/data/${category}`, {
      method: 'DELETE',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('system:openDataDir', async () => {
    const { shell } = require('electron')
    const fs = require('fs')
    const isDev = process.env.NODE_ENV === 'development'
    let dataDir
    if (process.env.INKCUBE_DATA_DIR) {
      dataDir = process.env.INKCUBE_DATA_DIR
    } else if (isDev) {
      dataDir = require('path').join(__dirname, '..', 'python', 'data')
    } else {
      const { app } = require('electron')
      dataDir = require('path').join(app.getPath('userData'), 'data')
    }
    if (!fs.existsSync(dataDir)) {
      fs.mkdirSync(dataDir, { recursive: true })
    }
    await shell.openPath(dataDir)
    return { success: true }
  })

  ipcMain.handle('settings:get', async () => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/settings`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('settings:save', async (_event, settings) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/settings`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('settings:personal_test', async (_event, settings) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/settings/personal_test`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('models:status', async () => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/models/status`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('models:gpu-status', async () => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/models/gpu-status`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('models:download', async (_event, name) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/models/${name}/download`, {
      method: 'POST',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('models:download-all', async () => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/models/download-all`, {
      method: 'POST',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('models:gpu-download', async () => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/models/gpu/download`, {
      method: 'POST',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('models:libreoffice-status', async () => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/models/libreoffice/status`)
    return handlePythonResponse(res)
  })

  // ── Knowledge Base ──

  ipcMain.handle('knowledge:listProjects', async () => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/knowledge/projects`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('knowledge:createProject', async (_event, { name, description, type, stage, companyName, creditCode }) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/knowledge/projects`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, description, type: type || 'general', stage: stage || 'none', company_name: companyName || '', credit_code: creditCode || '' }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('knowledge:getFiles', async (_event, { projectId, limit, beforeCreatedAt, beforeId, fileType, filenameSearch }) => {
    const params = new URLSearchParams({ limit: String(limit || 20) })
    if (beforeCreatedAt) params.set('before_created_at', beforeCreatedAt)
    if (beforeId) params.set('before_id', beforeId)
    if (fileType) params.set('file_type', fileType)
    if (filenameSearch) params.set('filename_search', filenameSearch)
    const res = await fetch(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/files?${params}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('knowledge:getActiveFiles', async (_event, projectId) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/files/active`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('knowledge:uploadFile', async (_event, { projectId, filename, fileType, data }) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/upload`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ projectId, filename, fileType, data }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('knowledge:deleteFile', async (_event, { projectId, fileId }) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/files/${fileId}`, {
      method: 'DELETE',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('knowledge:retryFile', async (_event, { projectId, fileId }) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/files/${fileId}/retry`, {
      method: 'POST',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('knowledge:retryFailedFiles', async (_event, projectId) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/files/retry-failed`, {
      method: 'POST',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('knowledge:recentChunks', async (_event, { projectId, limit, fileId }) => {
    const params = new URLSearchParams({ limit: String(limit || 20) })
    if (fileId) params.set('file_id', fileId)
    const res = await fetch(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/recent-chunks?${params}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('knowledge:search', async (_event, { projectId, query, topK, contentTypeFilter, fileId }) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/knowledge/search`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ projectId, query, topK, contentTypeFilter, fileId }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('knowledge:deleteProject', async (_event, projectId) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}`, {
      method: 'DELETE',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('knowledge:renameProject', async (_event, { projectId, name }) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    })
    return handlePythonResponse(res)
  })

  // Image management
  ipcMain.handle('knowledge:getProjectImages', async (_event, { projectId, limit, offset, fileId }) => {
    const params = new URLSearchParams({ limit: String(limit || 40), offset: String(offset || 0) })
    if (fileId) params.set('file_id', fileId)
    const res = await fetch(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/images?${params}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('knowledge:getImages', async (_event, { projectId, fileId }) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/files/${fileId}/images`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('knowledge:deleteImage', async (_event, { projectId, imageId }) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/images/${imageId}`, {
      method: 'DELETE',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('knowledge:retryImage', async (_event, { projectId, imageId, force }) => {
    const url = `${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/images/${imageId}/retry`
    const params = force ? '?force=true' : ''
    const res = await fetch(url + params, { method: 'POST' })
    return handlePythonResponse(res)
  })

  ipcMain.handle('knowledge:retryFailedImages', async (_event, { projectId, fileId }) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/files/${fileId}/images/retry-failed`, {
      method: 'POST',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('knowledge:updateImageCategory', async (_event, { projectId, imageId, category }) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/images/${imageId}/category`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ category }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('knowledge:archiveImage', async (_event, { projectId, imageId }) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/images/${imageId}/archive`, {
      method: 'POST',
    })
    return handlePythonResponse(res)
  })

  // Batch operations
  ipcMain.handle('knowledge:batchDeleteFiles', async (_event, { projectId, fileIds }) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/files/batch-delete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_ids: fileIds }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('knowledge:batchReprocessFiles', async (_event, { projectId, fileIds }) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/files/batch-reprocess`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_ids: fileIds }),
    })
    return handlePythonResponse(res)
  })

  // Knowledge base chat (RAG Q&A) — independent from agent chat
  ipcMain.handle('knowledge:chat', async (_event, { projectId, message, fileId }) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, fileId }),
    })
    return handlePythonResponse(res)
  })

  // ── Base Library ──

  ipcMain.handle('baseLib:list', async () => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/knowledge/base-libs`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('baseLib:create', async (_event, { name, description }) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/knowledge/base-libs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, description: description || '' }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('baseLib:update', async (_event, { libId, projectId, name, description }) => {
    const id = libId || projectId
    const res = await fetch(`${pythonManager.getBaseUrl()}/knowledge/base-libs/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, description }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('baseLib:delete', async (_event, libId) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/knowledge/base-libs/${libId}`, {
      method: 'DELETE',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('baseLib:getFiles', async (_event, { libId, projectId, limit, beforeCreatedAt, beforeId, fileType, filenameSearch }) => {
    const id = libId || projectId
    const params = new URLSearchParams({ limit: String(limit || 20) })
    if (beforeCreatedAt) params.set('before_created_at', beforeCreatedAt)
    if (beforeId) params.set('before_id', beforeId)
    if (fileType) params.set('file_type', fileType)
    if (filenameSearch) params.set('filename_search', filenameSearch)
    const res = await fetch(`${pythonManager.getBaseUrl()}/knowledge/base-libs/${id}/files?${params}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('baseLib:getActiveFiles', async (_event, libId) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/knowledge/base-libs/${libId}/files/active`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('baseLib:upload', async (_event, { libId, projectId, filename, fileType, data }) => {
    const id = libId || projectId
    const res = await fetch(`${pythonManager.getBaseUrl()}/knowledge/base-libs/${id}/upload`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ projectId: id, filename, fileType, data }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('baseLib:deleteFile', async (_event, { libId, projectId, fileId }) => {
    const id = libId || projectId
    const res = await fetch(`${pythonManager.getBaseUrl()}/knowledge/base-libs/${id}/files/${fileId}`, {
      method: 'DELETE',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('baseLib:retryFile', async (_event, { libId, projectId, fileId }) => {
    const id = libId || projectId
    const res = await fetch(`${pythonManager.getBaseUrl()}/knowledge/base-libs/${id}/files/${fileId}/retry`, {
      method: 'POST',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('baseLib:search', async (_event, { libId, projectId, query, topK, contentTypeFilter, fileId }) => {
    const id = libId || projectId
    const res = await fetch(`${pythonManager.getBaseUrl()}/knowledge/base-libs/${id}/search`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ projectId: id, query, topK, contentTypeFilter, fileId }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('baseLib:recentChunks', async (_event, { libId, projectId, limit, fileId }) => {
    const id = libId || projectId
    const params = new URLSearchParams({ limit: String(limit || 20) })
    if (fileId) params.set('file_id', fileId)
    const res = await fetch(`${pythonManager.getBaseUrl()}/knowledge/base-libs/${id}/recent-chunks?${params}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('baseLib:retryFailedFiles', async (_event, libId) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/knowledge/base-libs/${libId}/files/retry-failed`, {
      method: 'POST',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('baseLib:batchDeleteFiles', async (_event, { libId, projectId, fileIds }) => {
    const id = libId || projectId
    const res = await fetch(`${pythonManager.getBaseUrl()}/knowledge/base-libs/${id}/files/batch-delete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_ids: fileIds }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('baseLib:batchReprocessFiles', async (_event, { libId, projectId, fileIds }) => {
    const id = libId || projectId
    const res = await fetch(`${pythonManager.getBaseUrl()}/knowledge/base-libs/${id}/files/batch-reprocess`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_ids: fileIds }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('baseLib:getImages', async (_event, { libId, projectId, limit, offset, fileId }) => {
    const id = libId || projectId
    const params = new URLSearchParams({ limit: String(limit || 40), offset: String(offset || 0) })
    if (fileId) params.set('file_id', fileId)
    const res = await fetch(`${pythonManager.getBaseUrl()}/knowledge/base-libs/${id}/images?${params}`)
    return handlePythonResponse(res)
  })

  // ── Project-BaseLib Association ──

  ipcMain.handle('project:getBaseLibs', async (_event, projectId) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/base-libs`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('project:linkBaseLib', async (_event, { projectId, baseLibId }) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/base-libs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ base_lib_id: baseLibId }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('project:unlinkBaseLib', async (_event, { projectId, baseLibId }) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/knowledge/projects/${projectId}/base-libs/${baseLibId}`, {
      method: 'DELETE',
    })
    return handlePythonResponse(res)
  })

  // ── Tasks ──

  ipcMain.handle('tasks:create', async (_event, { title, workspacePath, mode }) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/tasks`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title: title || '新任务',
        workspace_path: workspacePath || '',
        mode: mode || 'coding',
      }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('tasks:get', async (_event, taskId) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/tasks/${taskId}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('tasks:delete', async (_event, taskId) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/tasks/${taskId}`, {
      method: 'DELETE',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('tasks:rename', async (_event, { taskId, title }) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/tasks/${taskId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('tasks:setMode', async (_event, { taskId, mode }) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/tasks/${taskId}/mode`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('tasks:getMessages', async (_event, taskId) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/tasks/${taskId}/messages`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('tasks:chat', async (_event, { taskId, message, resume }) => {
    const baseUrl = pythonManager.getBaseUrl()
    const response = await fetch(`${baseUrl}/tasks/${taskId}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_id: taskId, message, resume: resume || false }),
    })

    if (!response.ok) {
      const body = await response.json().catch(() => null)
      const err = new Error(body?.message || `Python backend error (${response.status})`)
      err.errorCode = body?.code || 'UNKNOWN_ERROR'
      err.errorCategory = body?.category || 'INTERNAL'
      err.errorSuggestion = body?.suggestion || ''
      err.errorDetail = body?.detail || ''
      throw err
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let currentEvent = 'message'

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7).trim()
        } else if (line.startsWith('data: ')) {
          const data = line.slice(6)
          _event.sender.send('task:stream', { taskId, type: currentEvent, data })
          currentEvent = 'message'
        } else if (line === '') {
          currentEvent = 'message'
        }
      }
    }
  })

  ipcMain.handle('tasks:cancelChat', async (_event, taskId) => {
    const baseUrl = pythonManager.getBaseUrl()
    await fetch(`${baseUrl}/tasks/${taskId}/chat/cancel`, { method: 'POST' })
  })

  ipcMain.handle('tasks:confirmShell', async (_event, { taskId, confirmed, allow_session }) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/tasks/${taskId}/confirm-shell`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirmed, allow_session: allow_session || false }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('tasks:confirmSkill', async (_event, { taskId, confirmed }) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/tasks/${taskId}/confirm-skill`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirmed }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('tasks:getFiles', async (_event, taskId) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/tasks/${taskId}/files`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('tasks:readFile', async (_event, { taskId, path }) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/tasks/${taskId}/files/content?path=${encodeURIComponent(path)}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('tasks:getStatus', async () => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/tasks/status`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('tasks:hasState', async (_event, taskId) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/tasks/${taskId}/has-state`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('tasks:listFiltered', async (_event, { status }) => {
    const params = status ? `?status=${encodeURIComponent(status)}` : ''
    const res = await fetch(`${pythonManager.getBaseUrl()}/tasks${params}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('tasks:archive', async (_event, taskId) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/tasks/${taskId}/archive`, {
      method: 'POST',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('tasks:unarchive', async (_event, taskId) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/tasks/${taskId}/unarchive`, {
      method: 'POST',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('tasks:export', async (_event, taskId) => {
    const { dialog, BrowserWindow } = require('electron')
    const fs = require('fs')
    const baseUrl = pythonManager.getBaseUrl()

    // Get task info for the filename
    const taskData = await handlePythonResponse(await fetch(`${baseUrl}/tasks/${taskId}`))
    const task = taskData.task
    if (!task) return { error: 'Task not found' }

    const win = BrowserWindow.getFocusedWindow()
    const safeName = task.title.replace(/[\/\\]/g, '_').slice(0, 50)
    const { canceled, filePath } = await dialog.showSaveDialog(win || undefined, {
      title: 'Export Task',
      defaultPath: `${safeName}_${taskId.slice(0, 8)}.zip`,
      filters: [{ name: 'ZIP Archive', extensions: ['zip'] }],
    })
    if (canceled || !filePath) return { canceled: true }

    const res = await fetch(`${baseUrl}/tasks/${taskId}/export`)
    const contentType = res.headers.get('content-type') || ''
    if (!res.ok || !contentType.includes('application/zip')) {
      const body = await res.json().catch(() => null)
      return { error: body?.error || 'Export failed' }
    }
    const buffer = Buffer.from(await res.arrayBuffer())
    fs.writeFileSync(filePath, buffer)
    return { success: true, path: filePath }
  })

  ipcMain.handle('tasks:selectDirectory', async () => {
    const { dialog } = require('electron')
    const result = await dialog.showOpenDialog({
      properties: ['openDirectory'],
    })
    if (result.canceled || result.filePaths.length === 0) {
      return { canceled: true }
    }
    return { path: result.filePaths[0] }
  })

  // ── Skills ──

  ipcMain.handle('skills:list', async () => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/skills`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('skills:install', async (_event, { source, sourceType }) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/skills/install`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source, source_type: sourceType }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('skills:delete', async (_event, skillId) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/skills/${skillId}`, {
      method: 'DELETE',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('skills:toggle', async (_event, { skillId, enabled }) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/skills/${skillId}/toggle`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('skills:confirm', async (_event, skillId) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/skills/${skillId}/confirm`, {
      method: 'POST',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('skills:browseLocal', async () => {
    const { dialog } = require('electron')
    const result = await dialog.showOpenDialog({
      properties: ['openDirectory'],
    })
    if (result.canceled || result.filePaths.length === 0) {
      return {}
    }
    return { path: result.filePaths[0] }
  })

  ipcMain.handle('skills:builtinTools', async () => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/skills/builtin-tools`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('tasks:uploadFiles', async (_event, { taskId, filePaths }) => {
    return _uploadFilesToTask(pythonManager, taskId, filePaths)
  })

  ipcMain.handle('tasks:pickAndUpload', async (_event, { taskId }) => {
    const { dialog } = require('electron')
    const result = await dialog.showOpenDialog({
      properties: ['openFile', 'multiSelections'],
    })
    if (result.canceled || result.filePaths.length === 0) {
      return { uploaded: [] }
    }
    return _uploadFilesToTask(pythonManager, taskId, result.filePaths)
  })

  // ── Pause ──
  ipcMain.handle('tasks:pause', async (_event, { taskId }) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/tasks/${taskId}/pause`, {
      method: 'POST',
    })
    return handlePythonResponse(res)
  })

  // ── Execution Logs ──
  ipcMain.handle('tasks:getLogs', async (_event, { taskId, lines }) => {
    const params = lines ? `?lines=${lines}` : ''
    const res = await fetch(`${pythonManager.getBaseUrl()}/tasks/${taskId}/logs${params}`)
    return handlePythonResponse(res)
  })

  // ── Skills list (for slash command autocomplete) ──
  ipcMain.handle('tasks:getSkills', async () => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/skills`)
    return handlePythonResponse(res)
  })

  // ── External Dependencies Check ──
  ipcMain.handle('system:checkDeps', async () => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/system/deps/check`)
    return handlePythonResponse(res)
  })

  // ── Reports & Templates ──

  ipcMain.handle('dialog:openFile', async (_event, { filters, properties }) => {
    const { dialog } = require('electron')
    return dialog.showOpenDialog({
      filters: filters || [{ name: 'All Files', extensions: ['*'] }],
      properties: properties || ['openFile'],
    })
  })

  ipcMain.handle('reports:listTemplates', async (_event, { projectType, stage }) => {
    const params = new URLSearchParams()
    if (projectType) params.set('project_type', projectType)
    if (stage) params.set('stage', stage)
    const qs = params.toString() ? `?${params.toString()}` : ''
    const res = await fetch(`${pythonManager.getBaseUrl()}/reports/templates${qs}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('reports:getTemplate', async (_event, templateId) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/reports/templates/${templateId}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('reports:saveTemplate', async (_event, { templateJson }) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/reports/templates`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ template_json: templateJson }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('reports:updateTemplate', async (_event, { templateId, templateJson }) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/reports/templates/${templateId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ template_json: templateJson }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('reports:copyTemplate', async (_event, { templateId, newName }) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/reports/templates/${templateId}/copy`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ new_name: newName }),
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('reports:deleteTemplate', async (_event, templateId) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/reports/templates/${templateId}`, {
      method: 'DELETE',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('reports:extractSample', async (_event, { filePath }) => {
    const fs = require('fs')
    const path = require('path')
    const fileData = fs.readFileSync(filePath)
    const fileName = path.basename(filePath)
    const boundary = '----InkCubeBoundary' + Date.now()

    const parts = []
    parts.push(
      Buffer.from(
        `--${boundary}\r\n` +
        `Content-Disposition: form-data; name="file"; filename="${fileName}"\r\n` +
        `Content-Type: application/octet-stream\r\n\r\n`
      )
    )
    parts.push(fileData)
    parts.push(Buffer.from(`\r\n--${boundary}--\r\n`))

    const body = Buffer.concat(parts)
    const res = await fetch(`${pythonManager.getBaseUrl()}/reports/templates/extract-sample`, {
      method: 'POST',
      headers: { 'Content-Type': `multipart/form-data; boundary=${boundary}` },
      body: body,
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('reports:refreshTemplates', async () => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/reports/refresh-templates`, {
      method: 'POST',
    })
    return handlePythonResponse(res)
  })

  ipcMain.handle('reports:listReports', async (_event, { projectId }) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/reports?project_id=${encodeURIComponent(projectId)}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('reports:getReport', async (_event, reportId) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/reports/${reportId}`)
    return handlePythonResponse(res)
  })

  ipcMain.handle('reports:deleteReport', async (_event, reportId) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/reports/${reportId}`, {
      method: 'DELETE',
    })
    return handlePythonResponse(res)
  })

  // Phase 3: Report generation (SSE streaming)
  ipcMain.handle('reports:generateReport', async (_event, { projectId, templateId, title, variables, baseProjectIds, outputFormat, supplementaryNotes }) => {
    // Note: SSE streaming is handled via direct HTTP connection in the frontend
    // This handler returns the report_id for tracking
    const res = await fetch(`${pythonManager.getBaseUrl()}/reports/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        project_id: projectId,
        template_id: templateId,
        title,
        variables: variables || {},
        base_project_ids: baseProjectIds || [],
        output_format: outputFormat || 'Word',
        supplementary_notes: supplementaryNotes || null,
      }),
    })
    return handlePythonResponse(res)
  })

  // Phase 4: Report content retrieval
  ipcMain.handle('reports:getReportContent', async (_event, reportId) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/reports/${reportId}/content`)
    return handlePythonResponse(res)
  })

  // Phase 4: Report export
  ipcMain.handle('reports:exportReport', async (_event, { reportId, format }) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/reports/${reportId}/export`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ format: format || 'Word' }),
    })
    return handlePythonResponse(res)
  })

  // Phase 4: Section regeneration
  ipcMain.handle('reports:regenerateSection', async (_event, { reportId, sectionId, promptHint }) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/reports/${reportId}/sections/${sectionId}/regenerate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        section_id: sectionId,
        prompt_hint: promptHint || null,
      }),
    })
    return handlePythonResponse(res)
  })

  // Retry failed report
  ipcMain.handle('reports:retryReport', async (_event, { reportId, mode }) => {
    const res = await fetch(`${pythonManager.getBaseUrl()}/reports/${reportId}/retry`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: mode || 'resume' }),
    })
    return handlePythonResponse(res)
  })

  // Get report generation logs
  ipcMain.handle('reports:getReportLogs', async (_event, { reportId, limit }) => {
    const url = new URL(`${pythonManager.getBaseUrl()}/reports/${reportId}/logs`)
    if (limit) url.searchParams.set('limit', String(limit))
    const res = await fetch(url.toString())
    return handlePythonResponse(res)
  })
}

module.exports = { registerIpcHandlers }
