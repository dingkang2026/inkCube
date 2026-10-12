/** tests/errorCodes.test.ts — D1 错误契约前端映射单测。
 *
 * 覆盖：
 *  - apiErrorText / apiErrorSuggestion：code 命中映射 → 当前语言 key；
 *    未知/缺失 code → fallback 原文（后端新增 code 未同步前端时降级可读）；
 *  - translateStoredError：`INKCUBE_CODE:<CODE>|<detail>` 协议串还原（含混合
 *    文本中的 ❌ 前缀、detail 拼接）；未知 code 降级 code 本身；普通文本
 *    （历史中文落库数据）原样返回零成本兼容。
 */
import { describe, it, expect } from 'vitest'
import {
  ERROR_CODE_I18N,
  apiErrorText,
  apiErrorSuggestion,
  translateStoredError,
} from '../src/i18n/errorCodes'

// 恒等 t：返回 `T:<key>` 便于断言映射命中与参数透传
const t = (key: string, opts?: Record<string, unknown>) =>
  `T:${key}${opts ? `(${JSON.stringify(opts)})` : ''}`

describe('ERROR_CODE_I18N 完整性', () => {
  it('每条 entry 的 msg 必填且非空', () => {
    for (const [code, entry] of Object.entries(ERROR_CODE_I18N)) {
      expect(entry.msg, `${code}.msg`).toBeTruthy()
      expect(entry.msg).not.toMatch(/\s/)
    }
  })

  it('code 命名符合大写蛇形（协议约定）', () => {
    for (const code of Object.keys(ERROR_CODE_I18N)) {
      expect(code).toMatch(/^[A-Z0-9_]+$/)
    }
  })
})

describe('apiErrorText', () => {
  it('code 命中映射 → errors.codes 段 key', () => {
    expect(apiErrorText('TASK_NOT_FOUND', 'fallback', t)).toBe('T:errors.codes.taskNotFound')
  })

  it('未知 code → fallback 原文（降级可读）', () => {
    expect(apiErrorText('NOT_A_CODE', '后端原文', t)).toBe('后端原文')
  })

  it('code 缺失（null/undefined）→ fallback 原文', () => {
    expect(apiErrorText(null, '后端原文', t)).toBe('后端原文')
    expect(apiErrorText(undefined, '后端原文', t)).toBe('后端原文')
  })
})

describe('apiErrorSuggestion', () => {
  it('code 命中且带 sug → sug key', () => {
    expect(apiErrorSuggestion('LLM_INVALID_KEY', 'fallback', t)).toBe(
      'T:errors.codes.llmInvalidKeySug',
    )
  })

  it('code 命中但无 sug → fallback', () => {
    expect(apiErrorSuggestion('TASK_NOT_FOUND', 'fallback', t)).toBe('fallback')
  })

  it('未知 code → fallback', () => {
    expect(apiErrorSuggestion('NOT_A_CODE', 'fallback', t)).toBe('fallback')
  })
})

describe('translateStoredError', () => {
  it('纯协议串（无 detail）→ 当前语言文案', () => {
    expect(translateStoredError('INKCUBE_CODE:TASK_NOT_FOUND', t)).toBe(
      'T:errors.codes.taskNotFound',
    )
  })

  it('协议串带 detail → 文案 + (detail)', () => {
    expect(translateStoredError('INKCUBE_CODE:FILE_NOT_FOUND|docs/a.pdf', t)).toBe(
      'T:errors.codes.fileNotFound (docs/a.pdf)',
    )
  })

  it('混合文本（❌ 前缀助手消息）只替换协议串部分', () => {
    expect(translateStoredError('❌ INKCUBE_CODE:LLM_INVALID_KEY', t)).toBe(
      '❌ T:errors.codes.llmInvalidKey',
    )
  })

  it('多处协议串全部替换（detail 贪婪匹配到行尾，协议约定 | 后整行都是 detail）', () => {
    expect(
      translateStoredError('A INKCUBE_CODE:TASK_NOT_FOUND B INKCUBE_CODE:FILE_NOT_FOUND|x C', t),
    ).toBe('A T:errors.codes.taskNotFound B T:errors.codes.fileNotFound (x C)')
  })

  it('未知 code → code 本身（带 detail 则 code: detail）', () => {
    expect(translateStoredError('INKCUBE_CODE:UNKNOWN_X', t)).toBe('UNKNOWN_X')
    expect(translateStoredError('INKCUBE_CODE:UNKNOWN_X|boom', t)).toBe('UNKNOWN_X: boom')
  })

  it('普通文本原样返回（历史中文数据 / 第三方异常串零成本兼容）', () => {
    expect(translateStoredError('手动归档', t)).toBe('手动归档')
    expect(translateStoredError('调用模型出错: boom', t)).toBe('调用模型出错: boom')
    expect(translateStoredError('', t)).toBe('')
  })

  it('INKCUBE_CODE: 后无合法 code 字符时不误替换', () => {
    // code 段要求 [A-Z0-9_]+，小写/空串不构成协议串
    expect(translateStoredError('INKCUBE_CODE: lowercase', t)).toBe('INKCUBE_CODE: lowercase')
  })
})
