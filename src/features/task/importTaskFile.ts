/** src/features/task/importTaskFile.ts — 0.5 E2 T5:任务产出文件导入项目/基础库（纯逻辑）。
 *  1) tasks:readFile(b64) 读工作区文件；2) 调目标侧 upload 通道（knowledge:uploadFile /
 *  baseLib:upload）——file 活动走现有解析队列，零新增 kind（2.11 §6 自动导入复用此链）。
 *  duplicate 语义透传（FILE_DUPLICATE：目标侧已有同 hash 文件，仍返回其 file_id）。 */

export interface ImportTaskFileOpts {
  taskId: string
  /** 任务工作区内相对路径 */
  path: string
  /** 展示/落库文件名（ext 从这里取） */
  filename: string
  targetType: 'project' | 'baselib'
  targetId: string
}

export interface ImportTaskFileResult {
  fileId: string
  duplicate: boolean
}

export async function importTaskFile(opts: ImportTaskFileOpts): Promise<ImportTaskFileResult> {
  const res = await window.electronAPI.invoke('tasks:readFile', {
    taskId: opts.taskId,
    path: opts.path,
    b64: true,
  }) as { type?: string; data?: string }
  if (!res?.data) {
    throw new Error(`Import: empty file data for ${opts.path}`)
  }
  const dot = opts.filename.lastIndexOf('.')
  const ext = dot > 0 ? opts.filename.slice(dot + 1).toLowerCase() : ''
  const data = `data:application/octet-stream;base64,${res.data}`
  const uploadRes = opts.targetType === 'project'
    ? await window.electronAPI.invoke('knowledge:uploadFile', {
        projectId: opts.targetId,
        filename: opts.filename,
        fileType: ext,
        data,
      })
    : await window.electronAPI.invoke('baseLib:upload', {
        libId: opts.targetId,
        filename: opts.filename,
        fileType: ext,
        data,
      })
  const r = uploadRes as { file_id?: string; duplicate?: boolean }
  return { fileId: r?.file_id ?? '', duplicate: !!r?.duplicate }
}
