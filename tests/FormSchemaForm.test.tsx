/** tests/FormSchemaForm.test.tsx — 六型动态表单渲染器回归（2.4 §5.2）
 *  渲染回填 / 值收集形状 / 字段级状态徽标（§5.4 不变量 3：值集形状与后端
 *  _normalize_value 同构——choice/checkbox 上抛 {selected, other}，label_group
 *  上抛 Record<sub_key, string>，table_rows 上抛 string[][]）。
 *  纯组件测试，electron 链路由 FormsTab.test 覆盖。
 */
import { describe, it, expect, vi, afterEach } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('../src/features/project/tabs/FormSchemaForm.module.css', () => ({
  default: new Proxy({}, { get: (_t, k) => String(k) }),
}))

import { render, screen, fireEvent, cleanup, within } from '@testing-library/react'
import { FormSchemaForm, type FormSchema, type FormReportEntry } from '../src/features/project/tabs/FormSchemaForm'

const schema: FormSchema = {
  fields: [
    { field_key: 'proj_name', label: '项目名称', fill_type: 'text', target: 'A1', unit: '万元' },
    { field_key: 'industry', label: '所属行业', fill_type: 'choice', target: 'B2', options: ['AI', '装备'], multi_select: false },
    { field_key: 'tags', label: '标签', fill_type: 'checkbox', target: 'C3', options: ['高精尖'], multi_select: true },
    { field_key: 'shareholders', label: '股东信息', fill_type: 'label_group', target: 'D4', sub_fields: [{ sub_key: 'name', anchor: '股东名称' }, { sub_key: 'ratio', anchor: '持股比例' }] },
    { field_key: 'financials', label: '财务数据', fill_type: 'table_rows', target: 'T1', columns: ['年度', '营收'] },
    { field_key: 'business', label: '主营业务', fill_type: 'doc_section', target: 'SEC1', instruction: '写清楚', length_hint: 600 },
  ],
  batches: [
    { seq: 1, name: '头部信息', field_keys: ['proj_name'] },
    { seq: 2, name: '企业情况', field_keys: ['industry', 'tags', 'shareholders', 'financials', 'business'] },
  ],
}

afterEach(() => cleanup())

describe('FormSchemaForm 六型渲染与值收集', () => {
  it('text 字段回填值并带单位后缀；编辑上抛 (field_key, value)', () => {
    const onChange = vi.fn()
    const { container } = render(
      <FormSchemaForm schema={schema} values={{ proj_name: '墨方科技' }} onChange={onChange} />,
    )
    expect(screen.getByText('(万元)')).toBeTruthy()
    const input = screen.getByDisplayValue('墨方科技') as HTMLInputElement
    fireEvent.change(input, { target: { value: '新名字' } })
    expect(onChange).toHaveBeenCalledWith('proj_name', '新名字')
    // 报告徽条滚动定位锚点（§5.1）
    expect(container.querySelector('[data-field-key="proj_name"]')).toBeTruthy()
  })

  it('choice 单选渲染 radio，选中上抛 {selected, other} 形状', () => {
    const onChange = vi.fn()
    render(<FormSchemaForm schema={schema} values={{}} onChange={onChange} />)
    const radio = screen.getByLabelText('AI') as HTMLInputElement
    expect(radio.type).toBe('radio')
    fireEvent.click(radio)
    expect(onChange).toHaveBeenCalledWith('industry', { selected: ['AI'], other: null })
  })

  it('checkbox（multi_select）渲染 checkbox，勾选累积 selected', () => {
    const onChange = vi.fn()
    render(<FormSchemaForm schema={schema} values={{}} onChange={onChange} />)
    const box = screen.getByLabelText('高精尖') as HTMLInputElement
    expect(box.type).toBe('checkbox')
    fireEvent.click(box)
    expect(onChange).toHaveBeenCalledWith('tags', { selected: ['高精尖'], other: null })
  })

  it('其他选项：勾选出现输入框，输入落在 other 槽位', () => {
    const onChange = vi.fn()
    const { container } = render(
      <FormSchemaForm schema={schema} values={{ industry: { selected: [], other: null } }} onChange={onChange} />,
    )
    expect(container.querySelector('.otherInput')).toBeNull()
    // choice 与 checkbox 均有「其他」选项，限定到 industry 行查询
    const industryRow = container.querySelector('[data-field-key="industry"]') as HTMLElement
    fireEvent.click(within(industryRow).getByLabelText('project.forms.otherOption'))
    expect(onChange).toHaveBeenCalledWith('industry', { selected: [], other: '' })

    // 受控组件：父组件回写 other 后输入框出现，输入原样上抛
    const { container: c2, unmount } = render(
      <FormSchemaForm schema={schema} values={{ industry: { selected: [], other: '自研' } }} onChange={onChange} />,
    )
    const otherInput = c2.querySelector('.otherInput') as HTMLInputElement
    expect(otherInput).toBeTruthy()
    fireEvent.change(otherInput, { target: { value: '机器人' } })
    expect(onChange).toHaveBeenCalledWith('industry', { selected: [], other: '机器人' })
    unmount()
  })

  it('label_group 子字段回填并上抛 Record<sub_key, string>', () => {
    const onChange = vi.fn()
    render(
      <FormSchemaForm
        schema={schema}
        values={{ shareholders: { name: '张三', ratio: '51%' } }}
        onChange={onChange}
      />,
    )
    expect(screen.getByText('股东名称')).toBeTruthy()
    const input = screen.getByDisplayValue('张三') as HTMLInputElement
    fireEvent.change(input, { target: { value: '李四' } })
    expect(onChange).toHaveBeenCalledWith('shareholders', { name: '李四', ratio: '51%' })
  })

  it('table_rows 渲染 columns 表头，单元格编辑/增行/删行上抛 string[][]', () => {
    const onChange = vi.fn()
    render(
      <FormSchemaForm
        schema={schema}
        values={{ financials: [['2025', '1000万']] }}
        onChange={onChange}
      />,
    )
    expect(screen.getByText('年度')).toBeTruthy()
    fireEvent.change(screen.getByDisplayValue('1000万'), { target: { value: '2000万' } })
    expect(onChange).toHaveBeenCalledWith('financials', [['2025', '2000万']])

    // 增行：空行补齐列数
    fireEvent.click(screen.getByText('project.forms.addRow'))
    expect(onChange).toHaveBeenLastCalledWith('financials', [['2025', '1000万'], ['', '']])

    // 删行：唯一行的删除按钮（title 复用 project.forms.delete）
    fireEvent.click(screen.getByTitle('project.forms.delete'))
    expect(onChange).toHaveBeenLastCalledWith('financials', [])
  })

  it('doc_section 渲染多行编辑 + instruction/length_hint 提示', () => {
    const onChange = vi.fn()
    render(
      <FormSchemaForm schema={schema} values={{ business: '主营是芯片' }} onChange={onChange} />,
    )
    expect(screen.getByText('写清楚')).toBeTruthy()
    expect(screen.getByText('project.forms.suggestLength')).toBeTruthy()
    const area = screen.getByDisplayValue('主营是芯片') as HTMLTextAreaElement
    fireEvent.change(area, { target: { value: '新正文' } })
    expect(onChange).toHaveBeenCalledWith('business', '新正文')
  })

  it('按 batches 分组渲染（组标题 + 执行顺序）', () => {
    render(<FormSchemaForm schema={schema} values={{}} onChange={vi.fn()} />)
    expect(screen.getByText('头部信息')).toBeTruthy()
    expect(screen.getByText('企业情况')).toBeTruthy()
  })

  it('no_data 字段淡橙底 + 提示文案 + 橙色状态点', () => {
    const report: FormReportEntry[] = [{ field_key: 'proj_name', status: 'no_data' }]
    const { container } = render(
      <FormSchemaForm schema={schema} values={{}} report={report} onChange={vi.fn()} />,
    )
    const input = container.querySelector('[data-field-key="proj_name"] input') as HTMLInputElement
    expect(input.className).toContain('fieldInputNoData')
    expect(screen.getByText('project.forms.noDataHint')).toBeTruthy()
    const dot = container.querySelector('[data-field-key="proj_name"] span') as HTMLElement
    expect(dot.className).toContain('dotNoData')
  })

  it('filled/failed 报告映射绿/红状态点；无条目为灰点', () => {
    const report: FormReportEntry[] = [
      { field_key: 'proj_name', status: 'filled' },
      { field_key: 'industry', status: 'failed' },
    ]
    const { container } = render(
      <FormSchemaForm schema={schema} values={{}} report={report} onChange={vi.fn()} />,
    )
    expect(
      (container.querySelector('[data-field-key="proj_name"] span') as HTMLElement).className,
    ).toContain('dotFilled')
    expect(
      (container.querySelector('[data-field-key="industry"] span') as HTMLElement).className,
    ).toContain('dotFailed')
    expect(
      (container.querySelector('[data-field-key="tags"] span') as HTMLElement).className,
    ).toContain('dotNone')
  })
})
