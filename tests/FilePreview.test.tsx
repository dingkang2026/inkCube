import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'
import { FilePreview, computeMatches } from '../src/features/task/FilePreview'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}))

vi.mock('../src/features/task/FilePreview.module.css', () => ({
  default: new Proxy({}, { get: (_target, key) => String(key) }),
}))

// 重量级渲染依赖 mock（binary/纯函数用例不触发，防真实 import 拖慢/出错）
vi.mock('react-syntax-highlighter', () => ({
  Prism: ({ children }: { children?: unknown }) => <pre>{children as string}</pre>,
}))
vi.mock('react-syntax-highlighter/dist/esm/styles/prism', () => ({ oneLight: {} }))
// Markdown mock：对齐真实 SafeImage 的 ResolveImage 契约（string→接管 / undefined→加载占位 / null→原判定）
vi.mock('../src/components/Markdown', () => ({
  Markdown: ({ content, resolveImage }: {
    content: string
    resolveImage?: (src: string) => string | undefined | null
  }) => (
    <div data-testid="md">
      {content.split('\n').map((line: string, i: number) => {
        const m = /^!\[([^\]]*)\]\(([^)]+)\)\s*$/.exec(line.trim())
        if (!m) return <p key={i}>{line}</p>
        const resolved = resolveImage ? resolveImage(m[2]) : null
        if (typeof resolved === 'string') return <img key={i} data-testid="md-img" src={resolved} alt={m[1]} />
        if (resolved === undefined) return <span key={i} data-testid="md-img-pending" title={m[2]} />
        return <span key={i} data-testid="md-img-blocked" />
      })}
    </div>
  ),
}))

// tasks:readFile 的 binary 响应（abs_path 全链路：python routers/tasks.py → FilePreview.absPath）
const BINARY_RES = {
  type: 'binary',
  size: 12,
  path: 'report.pdf',
  ext: 'pdf',
  abs_path: 'C:\\ws\\report.pdf',
}

// text 响应（查找用例默认内容：两行命中、一行不命中）
const TEXT_RES = {
  type: 'text',
  content: 'alpha line\nmiddle\nsecond alpha',
  ext: 'txt',
  abs_path: 'C:\\ws\\a.txt',
}

// md 响应（工作区图片用例：裸相对路径引用，与用户截图场景同构）
const MD_IMG_RES = {
  type: 'text',
  content: '# T\n\n![fig](imgs/p6-000.png)\n\nplain text',
  ext: 'md',
  abs_path: 'D:\\ws\\doc.md',
}

function mount(invoke: ReturnType<typeof vi.fn>) {
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke }
  return render(
    <FilePreview
      taskId="t1"
      filePath="report.pdf"
      fileName="report.pdf"
      fileSize={12}
      onClose={() => {}}
    />,
  )
}

/** text 类文件挂载（查找用例） */
function mountFile(invoke: ReturnType<typeof vi.fn>, fileName = 'a.txt') {
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke }
  return render(
    <FilePreview
      taskId="t1"
      filePath={fileName}
      fileName={fileName}
      fileSize={100}
      onClose={() => {}}
    />,
  )
}

beforeEach(() => {
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: vi.fn() }
})

afterEach(() => {
  cleanup()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

describe('FilePreview header open-folder button', () => {
  it('invokes shell:showItemInFolder with abs_path on click', async () => {
    const invoke = vi.fn().mockResolvedValue(BINARY_RES)
    mount(invoke)
    const btn = await screen.findByTitle('task.openContainingFolder')
    await waitFor(() => expect((btn as HTMLButtonElement).disabled).toBe(false))
    fireEvent.click(btn)
    expect(invoke).toHaveBeenCalledWith('shell:showItemInFolder', 'C:\\ws\\report.pdf')
  })

  it('is disabled until abs_path arrives (加载中/无路径不误触)', async () => {
    const invoke = vi.fn().mockResolvedValue({ type: 'binary', size: 1, path: 'a.bin', ext: 'bin' })
    mount(invoke)
    const btn = await screen.findByTitle('task.openContainingFolder')
    // 响应缺 abs_path：终态仍禁用（仓内无 jest-dom，用 DOM 属性断言）
    expect((btn as HTMLButtonElement).disabled).toBe(true)
  })

  it('binary body no longer renders the legacy in-body button（顶部按钮为唯一入口）', async () => {
    const invoke = vi.fn().mockResolvedValue(BINARY_RES)
    const { container } = mount(invoke)
    await waitFor(() => expect(container.querySelector('.binaryInfo')).toBeTruthy())
    expect(screen.queryByText('task.openContainingFolder')).toBeNull()
  })
})

describe('computeMatches（纯函数：关键词 → 命中行号序列）', () => {
  it('empty query returns no matches', () => {
    expect(computeMatches('a\nb', '')).toEqual([])
  })

  it('counts multiple hits per line and across lines', () => {
    expect(computeMatches('aa\na', 'a')).toEqual([0, 0, 1])
  })

  it('is case-insensitive', () => {
    expect(computeMatches('Alpha\nbeta', 'AL')).toEqual([0])
  })

  it('matches are non-overlapping', () => {
    // 同一行多次命中逐个计入（返回行号序列，非字符位置）：'aaaa' 命中 2 次均在第 0 行
    expect(computeMatches('aaaa', 'aa')).toEqual([0, 0])
    expect(computeMatches('aaa', 'aa')).toEqual([0])
  })
})

describe('FilePreview md 工作区图片（resolveImage + tasks:readFile b64 通道）', () => {
  it('relative md image is fetched via b64 channel and rendered as data URI', async () => {
    const invoke = vi.fn().mockImplementation(async (channel: string, payload?: { path?: string; b64?: boolean }) => {
      if (channel === 'tasks:readFile' && payload?.b64) {
        // 相对 md 所在目录归一化后的工作区绝对路径；响应为端点真实形状（无 mime 字段）
        expect(payload.path).toBe('D:/ws/imgs/p6-000.png')
        return { type: 'file', data: 'QUJD', size: 3 }
      }
      return MD_IMG_RES
    })
    const { findByTestId } = mountFile(invoke, 'doc.md')
    const img = await findByTestId('md-img')
    // mime 由扩展名推导（端点不返回 mime）：p6-000.png → image/png
    expect((img as HTMLImageElement).getAttribute('src')).toBe('data:image/png;base64,QUJD')
  })

  it('svg extension maps to image/svg+xml (否则 SVGImage 解码路径不走，静默破图)', async () => {
    const invoke = vi.fn().mockImplementation(async (channel: string, payload?: { b64?: boolean }) => {
      if (channel === 'tasks:readFile' && payload?.b64) {
        return { type: 'file', data: 'PDNI', size: 3 }
      }
      return { type: 'text', content: '![d](assets/arch.svg)', ext: 'md', abs_path: 'D:\\ws\\doc.md' }
    })
    const { findByTestId } = mountFile(invoke, 'doc.md')
    const img = await findByTestId('md-img')
    expect((img as HTMLImageElement).getAttribute('src')).toBe('data:image/svg+xml;base64,PDNI')
  })

  it('parent-relative refs (../x.png from subdirectory md) resolve via b64 channel too', async () => {
    // review 修复回归：深度守卫曾误拒合法 ../ 引用致落回破图；现在统一交服务端裁决
    const invoke = vi.fn().mockImplementation(async (channel: string, payload?: { path?: string; b64?: boolean }) => {
      if (channel === 'tasks:readFile' && payload?.b64) {
        expect(payload.path).toBe('D:/ws/x.png') // md 在 D:/ws/report/ 下，../x.png → D:/ws/x.png
        return { type: 'file', data: 'QQ==', size: 1 }
      }
      return { type: 'text', content: '![up](../x.png)', ext: 'md', abs_path: 'D:\\ws\\report\\doc.md' }
    })
    const { findByTestId } = mountFile(invoke, 'doc.md')
    const img = await findByTestId('md-img')
    expect((img as HTMLImageElement).getAttribute('src')).toBe('data:image/png;base64,QQ==')
  })

  it('failed fetch keeps neutral placeholder instead of broken img (no retry storm)', async () => {
    const invoke = vi.fn().mockImplementation(async (channel: string, payload?: { b64?: boolean }) => {
      if (channel === 'tasks:readFile' && payload?.b64) {
        return { error: '文件不存在', code: 'FILE_NOT_FOUND' }
      }
      return MD_IMG_RES
    })
    const { findByTestId, container } = mountFile(invoke, 'doc.md')
    await findByTestId('md-img-pending')
    await waitFor(() => {
      // 失败后仍是占位（无 img），且同一图片只发过一次请求（防重试风暴）
      expect(container.querySelector('[data-testid="md-img"]')).toBeNull()
      expect(invoke.mock.calls.filter((c) => c[0] === 'tasks:readFile' && (c[1] as { b64?: boolean })?.b64)).toHaveLength(1)
    })
  })

  it('external image src is not hijacked (falls through to CSP placeholder)', async () => {
    const invoke = vi.fn().mockResolvedValue({
      type: 'text',
      content: '![a](https://a.b/c.png)',
      ext: 'md',
      abs_path: 'D:\\ws\\doc.md',
    })
    const { findByTestId, container } = mountFile(invoke, 'doc.md')
    await findByTestId('md-img-blocked')
    // 带 scheme 的外链不进 b64 通道
    expect(invoke.mock.calls.filter((c) => c[0] === 'tasks:readFile' && (c[1] as { b64?: boolean })?.b64)).toHaveLength(0)
    expect(container.querySelector('[data-testid="md-img"]')).toBeNull()
  })
})

describe('FilePreview find-in-file（useInFileFind + FindBar 复用）', () => {
  it('opens FindBar from the header button and shows n/m count', async () => {
    const invoke = vi.fn().mockResolvedValue(TEXT_RES)
    mountFile(invoke)
    fireEvent.click(await screen.findByTitle('task.findInFile'))
    fireEvent.change(screen.getByPlaceholderText('find.placeholder'), {
      target: { value: 'alpha' },
    })
    expect(screen.getByText('1/2')).toBeTruthy()
  })

  it('navigates to next with Enter and wraps around at the end', async () => {
    const invoke = vi.fn().mockResolvedValue(TEXT_RES)
    mountFile(invoke)
    fireEvent.click(await screen.findByTitle('task.findInFile'))
    const input = screen.getByPlaceholderText('find.placeholder')
    fireEvent.change(input, { target: { value: 'alpha' } })
    expect(screen.getByText('1/2')).toBeTruthy()
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(screen.getByText('2/2')).toBeTruthy()
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(screen.getByText('1/2')).toBeTruthy()
  })

  it('navigates to previous with Shift+Enter (wraps backwards)', async () => {
    const invoke = vi.fn().mockResolvedValue(TEXT_RES)
    mountFile(invoke)
    fireEvent.click(await screen.findByTitle('task.findInFile'))
    const input = screen.getByPlaceholderText('find.placeholder')
    fireEvent.change(input, { target: { value: 'alpha' } })
    expect(screen.getByText('1/2')).toBeTruthy()
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: true })
    expect(screen.getByText('2/2')).toBeTruthy()
  })

  it('closes FindBar with Escape', async () => {
    const invoke = vi.fn().mockResolvedValue(TEXT_RES)
    mountFile(invoke)
    fireEvent.click(await screen.findByTitle('task.findInFile'))
    const input = screen.getByPlaceholderText('find.placeholder')
    fireEvent.change(input, { target: { value: 'alpha' } })
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(screen.queryByPlaceholderText('find.placeholder')).toBeNull()
  })

  it('Ctrl+F opens find only when focus is inside the preview panel', async () => {
    const invoke = vi.fn().mockResolvedValue(TEXT_RES)
    const { container } = mountFile(invoke)
    await screen.findByTitle('task.findInFile')
    const panel = container.firstElementChild as HTMLElement
    // 焦点移出面板（如对话区）：预览不接管全局 Ctrl+F
    ;(document.activeElement as HTMLElement | null)?.blur()
    fireEvent.keyDown(panel, { key: 'f', ctrlKey: true })
    expect(screen.queryByPlaceholderText('find.placeholder')).toBeNull()
    // 面板聚焦（挂载时自动聚焦）：接管
    panel.focus()
    fireEvent.keyDown(panel, { key: 'f', ctrlKey: true })
    expect(screen.getByPlaceholderText('find.placeholder')).toBeTruthy()
  })

  it('md rendered mode switches to source on first query input', async () => {
    const invoke = vi.fn().mockResolvedValue({
      type: 'text',
      content: '# Title\nsome text',
      ext: 'md',
      abs_path: 'C:\\ws\\r.md',
    })
    const { container } = mountFile(invoke, 'r.md')
    fireEvent.click(await screen.findByTitle('task.findInFile'))
    // md 渲染模式：Markdown 组件呈现，无源码 pre
    expect(container.querySelector('pre')).toBeNull()
    // 查找对象是文件源文本：首个字符输入即切源码视图
    fireEvent.change(screen.getByPlaceholderText('find.placeholder'), {
      target: { value: 'x' },
    })
    expect(container.querySelector('pre')).toBeTruthy()
  })

  it('image files expose no find entry', async () => {
    const invoke = vi.fn().mockResolvedValue({
      type: 'image',
      data: 'aGk=',
      mime: 'image/png',
      ext: 'png',
      abs_path: 'C:\\ws\\p.png',
    })
    mountFile(invoke, 'p.png')
    await screen.findByTitle('task.openContainingFolder')
    expect(screen.queryByTitle('task.findInFile')).toBeNull()
    expect(screen.queryByPlaceholderText('find.placeholder')).toBeNull()
  })

  it('switching file resets the find state', async () => {
    const invoke = vi.fn().mockResolvedValue(TEXT_RES)
    const { rerender } = mountFile(invoke)
    fireEvent.click(await screen.findByTitle('task.findInFile'))
    fireEvent.change(screen.getByPlaceholderText('find.placeholder'), {
      target: { value: 'alpha' },
    })
    expect(screen.getByText('1/2')).toBeTruthy()
    rerender(
      <FilePreview taskId="t1" filePath="b.txt" fileName="b.txt" fileSize={100} onClose={() => {}} />,
    )
    await waitFor(() => expect(screen.queryByPlaceholderText('find.placeholder')).toBeNull())
  })
})
