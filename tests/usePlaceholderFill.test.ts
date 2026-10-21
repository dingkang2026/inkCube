/** tests/usePlaceholderFill.test.ts — 占位符表单联动 hook（0.5 §4.4）。
 *  覆盖：start 预填 + 变量去重、setValue 一次填写全量替换、手动编辑接管守卫
 *  （接管后表单不再联动）、reset 清空、detach 保留文本摆脱联动。 */
import { describe, it, expect } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { usePlaceholderFill } from '../src/features/task/usePlaceholderFill'

const TPL = '为「{产业}」编制周报，检索「{产业} 融资」。\n标题：{产业}周报'

describe('usePlaceholderFill', () => {
  it('start：输入框预填模板原文，变量按名去重', () => {
    const { result } = renderHook(() => usePlaceholderFill())
    act(() => result.current.start(TPL))
    expect(result.current.text).toBe(TPL)
    expect(result.current.names).toEqual(['产业'])
    expect(result.current.manual).toBe(false)
  })

  it('setValue：填一次，同名占位符全文替换', () => {
    const { result } = renderHook(() => usePlaceholderFill())
    act(() => result.current.start(TPL))
    act(() => result.current.setValue('产业', '半导体'))
    expect(result.current.text).toBe(
      '为「半导体」编制周报，检索「半导体 融资」。\n标题：半导体周报',
    )
    expect(result.current.values['产业']).toBe('半导体')
  })

  it('未填变量保留占位符原样（发送守卫可拦截）', () => {
    const tpl = '基于「{企业名}」与「{关注点}」'
    const { result } = renderHook(() => usePlaceholderFill())
    act(() => result.current.start(tpl))
    act(() => result.current.setValue('企业名', '华润'))
    expect(result.current.text).toBe('基于「华润」与「{关注点}」')
    expect(result.current.names).toEqual(['企业名', '关注点'])
  })

  it('手动编辑偏离自动内容 → 接管，表单停用不再覆盖', () => {
    const { result } = renderHook(() => usePlaceholderFill())
    act(() => result.current.start(TPL))
    // 用户在文本框手动改了一个词
    act(() => result.current.onInputEdit(TPL.replace('周报', '月报')))
    expect(result.current.manual).toBe(true)
    // 接管后填表单：values 记录但不覆盖文本（UI 层表单已禁用）
    act(() => result.current.setValue('产业', '半导体'))
    expect(result.current.text).toBe(TPL.replace('周报', '月报'))
  })

  it('手动编辑与自动内容一致时不误判接管', () => {
    const { result } = renderHook(() => usePlaceholderFill())
    act(() => result.current.start(TPL))
    act(() => result.current.onInputEdit(TPL))
    expect(result.current.manual).toBe(false)
  })

  it('reset：全部清空（发送成功后）', () => {
    const { result } = renderHook(() => usePlaceholderFill())
    act(() => result.current.start(TPL))
    act(() => result.current.setValue('产业', '半导体'))
    act(() => result.current.reset())
    expect(result.current.text).toBe('')
    expect(result.current.names).toEqual([])
    expect(result.current.values).toEqual({})
    expect(result.current.manual).toBe(false)
  })

  it('detach：保留当前文本，摆脱模板联动', () => {
    const { result } = renderHook(() => usePlaceholderFill())
    act(() => result.current.start(TPL))
    act(() => result.current.setValue('产业', '半导体'))
    act(() => result.current.detach())
    expect(result.current.text).toContain('半导体')
    expect(result.current.names).toEqual([])
    // detach 后手动编辑不再涉及接管判定
    act(() => result.current.onInputEdit('任意文本'))
    expect(result.current.manual).toBe(false)
  })

  it('未用模板（纯手动输入）：names 为空，编辑不触发接管', () => {
    const { result } = renderHook(() => usePlaceholderFill())
    act(() => result.current.onInputEdit('自己写的{占位符}也无所谓'))
    expect(result.current.names).toEqual([])
    expect(result.current.manual).toBe(false)
  })
})
