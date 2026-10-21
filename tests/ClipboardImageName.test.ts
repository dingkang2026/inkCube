// @vitest-environment node
/**
 * tasks:uploadClipboardImage 文件名构建回归（electron/clipboard-image-name.js，
 * 零依赖纯逻辑——fetch-pool-bounds.js 同模式）：
 * - 毫秒时间戳：python 端点对同名覆盖写，秒级时间戳在同秒多图/连续粘贴时
 *   第二张静默覆盖第一张（review M2）
 * - JPEG 魔数嗅探：vlm_client._MIME_BY_EXT 按扩展名映射真实 MIME，恒 .png
 *   把 JPEG 字节谎报为 PNG 会被严格校验端点拒收（review M3）
 */
import { describe, expect, it } from 'vitest'

import { clipboardImageName } from '../electron/clipboard-image-name.js'

const PNG_HEAD = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])
const JPEG_HEAD = Buffer.from([0xff, 0xd8, 0xff, 0xe0])

describe('clipboardImageName（剪贴板截图命名）', () => {
  it('PNG 魔数 → .png 扩展', () => {
    const name = clipboardImageName(PNG_HEAD, new Date(2026, 9, 21, 14, 30, 5, 123))
    expect(name).toBe('clipboard-20261021-143005-123.png')
  })

  it('JPEG 魔数（FF D8）→ .jpg 扩展（MIME 契约：扩展名即 vlm 真实 MIME 来源）', () => {
    const name = clipboardImageName(JPEG_HEAD, new Date(2026, 9, 21, 14, 30, 5, 123))
    expect(name).toBe('clipboard-20261021-143005-123.jpg')
  })

  it('同一秒内两次调用（毫秒不同）文件名不同——同秒多图不再互相覆盖', () => {
    const a = clipboardImageName(PNG_HEAD, new Date(2026, 9, 21, 14, 30, 5, 1))
    const b = clipboardImageName(PNG_HEAD, new Date(2026, 9, 21, 14, 30, 5, 2))
    expect(a).not.toBe(b)
  })

  it('毫秒不足三位补零，格式恒为 clipboard-YYYYMMDD-HHmmss-SSS.ext', () => {
    const name = clipboardImageName(PNG_HEAD, new Date(2026, 0, 2, 3, 4, 5, 7))
    expect(name).toMatch(/^clipboard-\d{8}-\d{6}-\d{3}\.(png|jpg)$/)
    expect(name).toBe('clipboard-20260102-030405-007.png')
  })

  it('非 Buffer/空数据不抛错，回退 .png', () => {
    expect(clipboardImageName(Buffer.alloc(0), new Date(2026, 9, 21))).toMatch(/\.png$/)
    expect(clipboardImageName('not-a-buffer' as unknown as Buffer, new Date(2026, 9, 21))).toMatch(/\.png$/)
  })
})
