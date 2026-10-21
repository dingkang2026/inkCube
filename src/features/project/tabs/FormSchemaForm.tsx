/** src/features/project/tabs/FormSchemaForm.tsx — 六型动态表单渲染器（2.4 §5.2）
 *
 * 按 schema.batches 分组渲染字段（与执行顺序一致）；值集形状与后端
 * _normalize_value 归一约定同构（AI 初填与人工修订共用通道，2.4 §5.4 不变量 3）：
 * - text / doc_section: string
 * - choice / checkbox: { selected: string[]; other: string | null }
 * - label_group: Record<sub_key, string>
 * - table_rows: string[][]
 * 字段级状态徽标取自填表报告（filled 绿 / no_data 橙 / failed 红，0.3 §4.3）；
 * no_data 字段输入框淡橙底 + 提示，与漏填/失败区分。
 * 严格归一与写盘在后端（POST /forms/fills/{id}/update），此处仅产生合法编辑形状。
 */
import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, X } from 'lucide-react'
import s from './FormSchemaForm.module.css'

export interface FormFieldSub {
  sub_key: string
  anchor: string
  kb_query?: string[]
}

export interface FormField {
  field_key: string
  label: string
  fill_type: 'text' | 'label_group' | 'table_rows' | 'doc_section' | 'choice' | 'checkbox'
  fill_mode?: string
  target: string
  placeholder?: string | null
  options?: string[]
  multi_select?: boolean | null
  unit?: string
  sub_fields?: FormFieldSub[]
  columns?: string[]
  header_rows?: number
  instruction?: string | null
  sections?: string[]
  length_hint?: number | null
  confidence?: string | null
  kb_query?: string[]
}

export interface FormBatch {
  seq: number
  name: string
  field_keys: string[]
  merged_kb_queries?: string[]
  depends_on?: number[]
}

export interface FormSchema {
  schema_version?: string
  template_name?: string
  fields: FormField[]
  batches?: FormBatch[]
  unmapped_cells?: unknown[]
}

export type FieldStatus = 'filled' | 'no_data' | 'failed'

export interface FormReportEntry {
  field_key: string
  label?: string | null
  fill_type?: string
  status: FieldStatus
  value_preview?: string | null
  missed?: string[]
}

/** 值集（形状与后端 filled_values 同构） */
export type FormValues = Record<string, unknown>

export interface ChoiceValue {
  selected: string[]
  other: string | null
}

interface FormSchemaFormProps {
  schema: FormSchema
  values: FormValues
  report?: FormReportEntry[] | null
  onChange: (fieldKey: string, value: unknown) => void
}

/** 报告条目 → 状态圆点样式类（无报告条目 = 灰点，如刚修订后模板新增字段） */
function statusDotClass(status: FieldStatus | undefined): string {
  if (status === 'filled') return `${s.statusDot} ${s.dotFilled}`
  if (status === 'no_data') return `${s.statusDot} ${s.dotNoData}`
  if (status === 'failed') return `${s.statusDot} ${s.dotFailed}`
  return `${s.statusDot} ${s.dotNone}`
}

export function FormSchemaForm({ schema, values, report, onChange }: FormSchemaFormProps) {
  const fieldsByKey = useMemo(
    () => new Map(schema.fields.map((f) => [f.field_key, f])),
    [schema],
  )
  const reportByKey = useMemo(
    () => new Map((report ?? []).map((r) => [r.field_key, r.status])),
    [report],
  )
  // 无 batches（异常 schema 兜底）→ 单组全字段；正常路径按执行顺序分组
  const groups = useMemo(() => {
    const valid = (schema.batches ?? []).filter((b) => (b.field_keys ?? []).length > 0)
    if (valid.length > 0) return valid
    return [{ seq: 1, name: '', field_keys: schema.fields.map((f) => f.field_key) }]
  }, [schema])

  return (
    <div>
      {groups.map((batch) => {
        const fields = (batch.field_keys ?? [])
          .map((k) => fieldsByKey.get(k))
          .filter((f): f is FormField => !!f)
        if (fields.length === 0) return null
        return (
          <div key={batch.seq} className={s.group}>
            {batch.name && <p className={s.groupTitle}>{batch.name}</p>}
            {fields.map((field) => (
              <FieldRow
                key={field.field_key}
                field={field}
                value={values[field.field_key]}
                status={reportByKey.get(field.field_key)}
                onChange={onChange}
              />
            ))}
          </div>
        )
      })}
    </div>
  )
}

// ── 单字段行：状态点 + 标签 + 分型控件 ──

function FieldRow({ field, value, status, onChange }: {
  field: FormField
  value: unknown
  status?: FieldStatus
  onChange: (fieldKey: string, value: unknown) => void
}) {
  const set = (v: unknown) => onChange(field.field_key, v)
  return (
    // data-field-key：FormsTab 报告徽条点击滚动定位（§5.1）
    <div className={s.fieldRow} data-field-key={field.field_key}>
      <span className={statusDotClass(status)} />
      <label className={s.fieldLabel} title={field.label}>{field.label}</label>
      {field.fill_type === 'doc_section' ? (
        <DocSectionField field={field} value={value} status={status} onChange={set} />
      ) : field.fill_type === 'choice' || field.fill_type === 'checkbox' ? (
        <ChoiceField field={field} value={value} status={status} onChange={set} />
      ) : field.fill_type === 'label_group' ? (
        <LabelGroupField field={field} value={value} status={status} onChange={set} />
      ) : field.fill_type === 'table_rows' ? (
        <TableField field={field} value={value} onChange={set} />
      ) : (
        <TextField field={field} value={value} status={status} onChange={set} />
      )}
    </div>
  )
}

// ── text：单行输入（#blank 内嵌填空只填数值，单位已印在模板）──

function TextField({ field, value, status, onChange }: {
  field: FormField
  value: unknown
  status?: FieldStatus
  onChange: (v: unknown) => void
}) {
  const { t } = useTranslation()
  const noData = status === 'no_data'
  return (
    <div className={s.fieldControl} style={{ flexWrap: 'wrap' }}>
      <input
        className={`${s.fieldInput} ${noData ? s.fieldInputNoData : ''}`}
        value={value == null ? '' : String(value)}
        placeholder={field.placeholder || ''}
        onChange={(e) => onChange(e.target.value)}
      />
      {field.unit && <span className={s.unitSuffix}>({field.unit})</span>}
      {noData && <p className={`${s.fieldHint} ${s.noDataHint}`}>{t('project.forms.noDataHint')}</p>}
    </div>
  )
}

// ── doc_section：多行编辑 + 建议字数 ──

function DocSectionField({ field, value, status, onChange }: {
  field: FormField
  value: unknown
  status?: FieldStatus
  onChange: (v: unknown) => void
}) {
  const { t } = useTranslation()
  const noData = status === 'no_data'
  return (
    <div className={s.fieldControl} style={{ flexWrap: 'wrap' }}>
      <textarea
        className={`${s.fieldTextarea} ${noData ? s.fieldTextareaNoData : ''}`}
        value={value == null ? '' : String(value)}
        onChange={(e) => onChange(e.target.value)}
      />
      <p className={s.fieldHint}>
        {noData && <span className={s.noDataHint}>{t('project.forms.noDataHint')}　</span>}
        {field.instruction && <span>{field.instruction}　</span>}
        {field.length_hint ? t('project.forms.suggestLength', { count: field.length_hint }) : null}
      </p>
    </div>
  )
}

// ── choice / checkbox：选项组 + 其他输入 ──

function ChoiceField({ field, value, status, onChange }: {
  field: FormField
  value: unknown
  status?: FieldStatus
  onChange: (v: unknown) => void
}) {
  const { t } = useTranslation()
  const options = (field.options ?? []).map(String)
  const payload: ChoiceValue = typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as ChoiceValue
    : { selected: [], other: null }
  const selected = payload.selected ?? []
  const multi = field.fill_type === 'checkbox' || field.multi_select === true

  const toggle = (opt: string, checked: boolean) => {
    const next = multi
      ? (checked ? [...selected, opt] : selected.filter((v) => v !== opt))
      : (checked ? [opt] : [])
    onChange({ ...payload, selected: next })
  }

  return (
    <div className={s.fieldControl} style={{ flexDirection: 'column', alignItems: 'stretch' }}>
      <div className={s.optionsCol}>
        {options.map((opt) => (
          <label key={opt} className={s.optionRow}>
            <input
              type={multi ? 'checkbox' : 'radio'}
              name={field.field_key}
              checked={selected.includes(opt)}
              onChange={(e) => toggle(opt, e.target.checked)}
            />
            {opt}
          </label>
        ))}
        <label className={s.optionRow}>
          <input
            type={multi ? 'checkbox' : 'radio'}
            name={field.field_key}
            checked={!!payload.other}
            onChange={(e) => onChange({ ...payload, other: e.target.checked ? (payload.other || '') : null })}
          />
          {t('project.forms.otherOption')}
        </label>
        {payload.other != null && (
          <input
            className={s.otherInput}
            value={payload.other}
            autoFocus
            onChange={(e) => onChange({ ...payload, other: e.target.value })}
          />
        )}
      </div>
      {status === 'no_data' && <NoDataHintLine />}
    </div>
  )
}

// ── label_group：子字段两列栅格 ──

function LabelGroupField({ field, value, status, onChange }: {
  field: FormField
  value: unknown
  status?: FieldStatus
  onChange: (v: unknown) => void
}) {
  const subs = field.sub_fields ?? []
  const map = (typeof value === 'object' && value !== null && !Array.isArray(value))
    ? value as Record<string, string>
    : {}
  const setSub = (subKey: string, v: string) => onChange({ ...map, [subKey]: v })
  return (
    <div className={s.fieldControl} style={{ flexDirection: 'column', alignItems: 'stretch' }}>
      <div className={s.subGrid}>
        {subs.map((sub) => (
          <div key={sub.sub_key} className={s.subItem}>
            <span className={s.subLabel} title={sub.anchor}>{sub.anchor}</span>
            <input
              className={s.fieldInput}
              value={map[sub.sub_key] ?? ''}
              onChange={(e) => setSub(sub.sub_key, e.target.value)}
            />
          </div>
        ))}
      </div>
      {status === 'no_data' && <NoDataHintLine />}
    </div>
  )
}

// ── table_rows：可编辑表格（columns 表头 + 增删行）──

function TableField({ field, value, onChange }: {
  field: FormField
  value: unknown
  onChange: (v: unknown) => void
}) {
  const { t } = useTranslation()
  const columns = (field.columns ?? []).map(String)
  const colCount = columns.length || 1
  const rows: string[][] = Array.isArray(value)
    ? value.filter((r) => Array.isArray(r)).map((r) => r.map((c) => (c == null ? '' : String(c))))
    : []

  const setCell = (ri: number, ci: number, v: string) => {
    const next = rows.map((row) => [...row])
    next[ri][ci] = v
    onChange(next)
  }
  const addRow = () => onChange([...rows, Array.from({ length: colCount }, () => '')])
  const removeRow = (ri: number) => onChange(rows.filter((_, i) => i !== ri))

  return (
    <div className={s.fieldControl} style={{ flexDirection: 'column', alignItems: 'stretch' }}>
      <div className={s.tableWrap}>
        <table className={s.editorTable}>
          <thead>
            <tr>
              {columns.map((c, i) => <th key={i}>{c}</th>)}
              <th style={{ width: 64 }} />
            </tr>
          </thead>
          <tbody>
            {rows.map((row, ri) => (
              <tr key={ri}>
                {Array.from({ length: colCount }, (_, ci) => (
                  <td key={ci}>
                    <input
                      className={s.cellInput}
                      value={row[ci] ?? ''}
                      onChange={(e) => setCell(ri, ci, e.target.value)}
                    />
                  </td>
                ))}
                <td>
                  <div className={s.rowActions}>
                    <button type="button" className={s.rowBtn} title={t('project.forms.delete')}
                      onClick={() => removeRow(ri)}>
                      <X size={12} />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <button type="button" className={s.addRowBtn} onClick={addRow}>
        <Plus size={12} style={{ verticalAlign: -1 }} /> {t('project.forms.addRow')}
      </button>
    </div>
  )
}

// ── no_data 提示行（choice/label_group 等复合控件复用）──

function NoDataHintLine() {
  const { t } = useTranslation()
  return <p className={`${s.fieldHint} ${s.noDataHint}`}>{t('project.forms.noDataHint')}</p>
}
