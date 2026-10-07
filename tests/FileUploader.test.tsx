/** tests/FileUploader.test.tsx — 标题计数回归
 *  背景 bug：「已上传文件 (N)」曾用 files.length（当前页行数），满页时
 *  恰好等于每页上限（20），看起来像文件总数，实际只是页大小——误导。
 *  修复：改用 fileTotal（当前筛选/搜索下总数，与分页口径一致）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('../src/components/ConfirmDialog', () => ({
  useConfirm: () => async () => true,
}))

vi.mock('../src/features/knowledge/FileUploader.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, screen, cleanup, fireEvent } from '@testing-library/react'
import { FileUploader } from '../src/features/knowledge/FileUploader'
import type { FileRecord } from '../src/features/knowledge/types'

const makeFiles = (n: number): FileRecord[] =>
  Array.from({ length: n }, (_, i) => ({
    id: `f${i}`,
    filename: `doc-${i}.pdf`,
    file_type: '.pdf',
    file_size: 1024,
    file_hash: `h${i}`,
    status: 'done',
    chunks_count: 1,
    created_at: '2026-01-01T00:00:00',
  })) as unknown as FileRecord[]

const mount = (fileTotal: number) =>
  render(
    <FileUploader
      projectId="p1"
      files={makeFiles(20)}
      onRefresh={() => {}}
      filePage={1}
      fileTotal={fileTotal}
      fileLimit={20}
    />
  )

beforeEach(() => {
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: vi.fn() }
})

afterEach(() => {
  cleanup()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

describe('FileUploader parsing pause toggle', () => {
  it('renders the pause button only when wired via onToggleParsingPause', () => {
    mount(190)
    expect(screen.queryByTitle('knowledge.pauseParsing')).toBeNull()
    render(
      <FileUploader
        projectId="p1"
        files={makeFiles(1)}
        onRefresh={() => {}}
        fileTotal={1}
        parsingPaused={false}
        onToggleParsingPause={() => {}}
      />,
    )
    expect(screen.getByTitle('knowledge.pauseParsing')).toBeTruthy()
  })

  it('shows resume title while paused and invokes the callback on click', () => {
    const onToggle = vi.fn()
    render(
      <FileUploader
        projectId="p1"
        files={makeFiles(1)}
        onRefresh={() => {}}
        fileTotal={1}
        parsingPaused
        onToggleParsingPause={onToggle}
      />,
    )
    fireEvent.click(screen.getByTitle('knowledge.resumeParsing'))
    expect(onToggle).toHaveBeenCalledTimes(1)
  })
})

describe('FileUploader header count', () => {
  it('shows fileTotal, not the current-page row count (regression:满页 20 被误读为总数)', () => {
    // 满页场景：20 行 = 每页上限，曾显示误导性的 "(20)"（总数其实 190）
    mount(190)
    expect(screen.getByText('knowledge.uploadedFiles (190)')).toBeTruthy()
  })

  it('never renders the page row count as the header count', () => {
    mount(190)
    expect(screen.queryByText('knowledge.uploadedFiles (20)')).toBeNull()
  })
})
