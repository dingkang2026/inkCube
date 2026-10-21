/** src/features/task/usePlaceholderFill.ts — 模板占位符表单联动 hook（0.5 §4.4）。
 *  解决同一占位符在 prompt 中出现多次、用户须逐处手改的痛点：变量按名称去重，
 *  每变量填一次，输入框内容实时全量替换（applyValues）。
 *  接管守卫：用户直接编辑文本框且内容 ≠ 自动生成内容 → manual=true，此后表单
 *  停用（联动重算会覆盖手动编辑，故直接编辑即接管）。任务聊天与项目对话面板共用。 */
import { useState, useCallback, useMemo, useEffect } from 'react'
import { uniquePlaceholderNames, applyValues } from './promptTemplate'

export function usePlaceholderFill() {
  /** 模板基底 prompt（含 {占位符}）；null = 未用模板（纯手动输入，无表单） */
  const [basePrompt, setBasePrompt] = useState<string | null>(null)
  const [values, setValues] = useState<Record<string, string>>({})
  const [manual, setManual] = useState(false)
  /** 输入框内容（hook 统一管理，调用方以 text/setText 受控） */
  const [text, setText] = useState('')

  /** 去重后的变量名（按首次出现顺序）；未用模板时空数组 */
  const names = useMemo(
    () => (basePrompt ? uniquePlaceholderNames(basePrompt) : []),
    [basePrompt],
  )
  /** 当前 values 下的自动生成内容（联动基准） */
  const applied = useMemo(
    () => (basePrompt ? applyValues(basePrompt, values) : ''),
    [basePrompt, values],
  )

  /** 联动：未接管时输入框内容实时跟随 applyValues 重算（全量替换即所见） */
  useEffect(() => {
    if (basePrompt && !manual) setText(applied)
  }, [basePrompt, manual, applied])

  /** 点模板：登记基底 prompt、重置表单，输入框预填模板原文 */
  const start = useCallback((prompt: string) => {
    setBasePrompt(prompt)
    setValues({})
    setManual(false)
    setText(prompt)
  }, [])

  /** 清空一切（发送成功后调用；与 start 区分：不预填） */
  const reset = useCallback(() => {
    setBasePrompt(null)
    setValues({})
    setManual(false)
    setText('')
  }, [])

  /** 摆脱模板联动但保留当前文本（slash 命令等程序化覆写输入的场景） */
  const detach = useCallback(() => {
    setBasePrompt(null)
    setValues({})
    setManual(false)
  }, [])

  /** 填写变量（表单入口；接管态下表单禁用，正常不会触发） */
  const setValue = useCallback((name: string, value: string) => {
    setValues((prev) => ({ ...prev, [name]: value }))
  }, [])

  /** 文本框用户输入：内容偏离自动生成内容即视为接管 */
  const onInputEdit = useCallback((next: string) => {
    setText(next)
    if (basePrompt && !manual && next !== applied) setManual(true)
  }, [basePrompt, manual, applied])

  return { text, setText, names, values, manual, applied, start, reset, detach, setValue, onInputEdit }
}
