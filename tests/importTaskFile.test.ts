/** tests/importTaskFile.test.ts — 0.5 E2 T5:导入纯逻辑 project/baselib 两分支。 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { importTaskFile } from '../src/features/task/importTaskFile'

const invokeMock = vi.fn()

beforeEach(() => {
  invokeMock.mockReset()
  ;(window as any).electronAPI = { invoke: invokeMock }
})

describe('importTaskFile (0.5 E2 T5)', () => {
  const B64 = 'aGVsbG8=' // 'hello'

  it('project 分支：tasks:readFile(b64) → knowledge:uploadFile（projectId + octet-stream 前缀）', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'tasks:readFile') return { type: 'file', data: B64, size: 5, filename: 'report.md' }
      return { success: true, file_id: 'file_abc', queued: true }
    })
    const out = await importTaskFile({
      taskId: 't1', path: 'out/report.md', filename: 'report.md',
      targetType: 'project', targetId: 'p1',
    })
    expect(invokeMock).toHaveBeenNthCalledWith(1, 'tasks:readFile', { taskId: 't1', path: 'out/report.md', b64: true })
    expect(invokeMock).toHaveBeenNthCalledWith(2, 'knowledge:uploadFile', {
      projectId: 'p1',
      filename: 'report.md',
      fileType: 'md',
      data: `data:application/octet-stream;base64,${B64}`,
    })
    expect(out).toEqual({ fileId: 'file_abc', duplicate: false })
  })

  it('baselib 分支：调 baseLib:upload（libId）', async () => {
    invokeMock.mockImplementation(async (channel: string) => {
      if (channel === 'tasks:readFile') return { type: 'file', data: B64, size: 5, filename: 'doc.pdf' }
      return { success: true, file_id: 'file_lib', duplicate: true }
    })
    const out = await importTaskFile({
      taskId: 't1', path: 'doc.pdf', filename: 'doc.pdf',
      targetType: 'baselib', targetId: 'b1',
    })
    expect(invokeMock).toHaveBeenNthCalledWith(2, 'baseLib:upload', {
      libId: 'b1',
      filename: 'doc.pdf',
      fileType: 'pdf',
      data: `data:application/octet-stream;base64,${B64}`,
    })
    expect(out).toEqual({ fileId: 'file_lib', duplicate: true })
  })

  it('readFile 无 data 时抛错且不调 upload', async () => {
    invokeMock.mockResolvedValue({ type: 'file' })
    await expect(importTaskFile({
      taskId: 't1', path: 'x', filename: 'x.md',
      targetType: 'project', targetId: 'p1',
    })).rejects.toThrow()
    expect(invokeMock).toHaveBeenCalledTimes(1)
  })
})
