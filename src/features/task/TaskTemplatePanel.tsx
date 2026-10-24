/** src/features/task/TaskTemplatePanel.tsx — 0.5 §4.4:任务模板面板（E1 T7 建，E2 T4 接持久化）。
 *  分类 tab + 模板卡网格；数据 = useTaskTemplates 合并视图（内置 i18n + 自定义 DB）。
 *  V2-E1a T12：TASK_TEMPLATES 收窄后情报模板自动消失；空分类 tab 隐藏；
 *  persona 非置顶区置顶排序（0.6 §3.2）。
 *  点卡仅回调 onPick(prompt 原文) 预填输入框，绝不发送；底部「管理模板」开管理弹层。
 *  scope 默认 global（任务欢迎页），项目对话空态传 scope='project'+projectId（0.5 §7.2）。 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Building2, SearchCheck, ClipboardList, Puzzle, FolderTree, Table,
  Sparkles, Settings2,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { BUILTIN_PREFIX, TEMPLATE_CATEGORIES, sortTemplatesByPersona } from './promptTemplate'
import { useTaskTemplates } from './useTaskTemplates'
import type { TemplateScope } from './useTaskTemplates'
import { useSettings } from '../settings/useSettings'
import { TemplateManageModal } from './TemplateManageModal'
import s from './TaskTemplatePanel.module.css'

/** 内置模板 icon（TASK_TEMPLATES.icon 字符串）→ lucide 组件；自定义模板统一 Sparkles */
const ICONS: Record<string, LucideIcon> = {
  Building2, SearchCheck, ClipboardList, Puzzle, FolderTree, Table,
}

interface TaskTemplatePanelProps {
  /** 只预填,绝不发送:回调回传模板 prompt 原文(含 {占位符}) */
  onPick: (prompt: string) => void
  /** 模板可见范围:global=任务欢迎页;project=项目对话空态(自定义模板按项目隔离) */
  scope?: TemplateScope
  /** scope='project' 时的项目 id */
  projectId?: string
}

export function TaskTemplatePanel({ onPick, scope = 'global', projectId }: TaskTemplatePanelProps) {
  const { t } = useTranslation()
  const { settings } = useSettings()
  const { templates, refresh, save, remove, togglePin } = useTaskTemplates(scope, projectId)
  const [category, setCategory] = useState<(typeof TEMPLATE_CATEGORIES)[number]>('all')
  const [managing, setManaging] = useState(false)

  // persona 排序（非置顶区置顶，0.6 §3.2 T12）+ 空分类 tab 隐藏（T12 收窄后
  // mon/ind/reg 无内置；自定义可归，有空模板才显示该 tab）
  const sorted = sortTemplatesByPersona(templates, settings?.persona ?? '', 'task')
  const cats = TEMPLATE_CATEGORIES.filter(
    (cat) => cat === 'all' || sorted.some((tpl) => tpl.category === cat),
  )
  const visible = category === 'all'
    ? sorted
    : sorted.filter((tpl) => tpl.category === category)

  return (
    <div className={s.panel}>
      <div className={s.tabs} role="tablist">
        {cats.map((cat) => (
          <button
            key={cat}
            role="tab"
            aria-selected={category === cat}
            className={`${s.tab} ${category === cat ? s.tabActive : ''}`}
            onClick={() => setCategory(cat)}
          >
            {t(`task.cat.${cat}`)}
          </button>
        ))}
      </div>
      <div className={s.grid}>
        {visible.map((tpl) => {
          const Icon = tpl.builtin
            ? ICONS[tpl.id.slice(BUILTIN_PREFIX.length)]
            : Sparkles
          return (
            <button key={tpl.id} className={s.card} onClick={() => onPick(tpl.prompt)}>
              <span className={s.cardIcon}>{Icon ? <Icon size={20} /> : null}</span>
              <span className={s.cardTitle}>{tpl.title}</span>
              <span className={s.cardDesc}>{tpl.desc}</span>
            </button>
          )
        })}
      </div>
      <button className={s.manageBtn} onClick={() => setManaging(true)}>
        <Settings2 size={14} />{t('task.tplMgmt.manage')}
      </button>
      {managing && (
        <TemplateManageModal
          templates={templates}
          refresh={refresh}
          save={save}
          remove={remove}
          togglePin={togglePin}
          onClose={() => setManaging(false)}
        />
      )}
    </div>
  )
}
