/**
 * src/features/project/tabs/OutputTab.tsx — 项目「产出」Tab（0.6 §3.2 V2-E1a T11：
 * 报告 + 填表两分支合并为产出，项目五 Tab 收敛为四）。内部二级分区「报告/填表」
 * ＝渲染原 ReportsTab / FormsTab（迁移宿主，非重写，组件行为与既有测试不变）；
 * 二级分区按钮带图标（FileText/ClipboardList，2026-10-25 对齐 AffairsTab 视图切换按钮形态）。
 * 头部「模板管理」直达钮 → app:navigate-template-management（按当前分区落对应模板页签）。
 * 分区条仅列表态显示：子 Tab 经 onViewModeChange 上报视图态，任一侧进入详情/向导
 * 即隐藏整条分区条，详情占据整个 tab 内容区（2026-10-27 用户裁决）。
 * sectionSignal：跨 Tab 锚点（ProjectDetailPage 旧 tab 值 'forms'/'reports' 兼容映射，
 * 一个版本周期后 E3 清理）。
 */
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ClipboardList, FileText, LayoutTemplate } from 'lucide-react'
import { ReportsTab } from './ReportsTab'
import { FormsTab } from './FormsTab'
import type { ProjectType } from '../types'
import s from './OutputTab.module.css'

type OutputSection = 'reports' | 'forms'

interface OutputTabProps {
  projectId: string
  projectType: ProjectType
  /** 跨 Tab 锚点信号（'forms'/'reports' → 直落对应分区），消费后回调清信号 */
  sectionSignal?: { section: OutputSection; ts: number } | null
  onSectionConsumed?: () => void
}

export function OutputTab({ projectId, projectType, sectionSignal, onSectionConsumed }: OutputTabProps) {
  const { t } = useTranslation()
  const [section, setSection] = useState<OutputSection>('reports')
  // 子 Tab 视图态（仅 'list' 显示分区条；两侧进入任何详情/向导视图都上报非 list 值）
  const [childView, setChildView] = useState<string>('list')
  const handleChildViewChange = useCallback((view: string) => setChildView(view), [])

  // 跨 Tab 锚点消费（ProjectDetailPage 旧 tab 值兼容映射）
  useEffect(() => {
    if (!sectionSignal) return
    setSection(sectionSignal.section)
    onSectionConsumed?.()
  }, [sectionSignal, onSectionConsumed])

  return (
    <div className={s.wrap} data-testid="output-tab">
      {childView === 'list' && (
      <div className={s.bar}>
        <div className={s.segs} role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={section === 'reports'}
            className={`${s.seg} ${section === 'reports' ? s.segActive : ''}`}
            onClick={() => setSection('reports')}
          >
            <FileText size={14} />
            {t('project.tab.reports')}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={section === 'forms'}
            className={`${s.seg} ${section === 'forms' ? s.segActive : ''}`}
            onClick={() => setSection('forms')}
          >
            <ClipboardList size={14} />
            {t('project.tab.forms')}
          </button>
        </div>
        <button
          type="button"
          className={s.tplBtn}
          onClick={() => window.dispatchEvent(new CustomEvent('app:navigate-template-management', {
            detail: { section: section === 'forms' ? 'forms' : 'reports' },
          }))}
        >
          <LayoutTemplate size={14} />{t('project.report.templateManagement')}
        </button>
      </div>
      )}
      {section === 'reports' ? (
        <ReportsTab key={projectType} projectId={projectId} projectType={projectType} onViewModeChange={handleChildViewChange} />
      ) : (
        <FormsTab key={projectId} projectId={projectId} onViewModeChange={handleChildViewChange} />
      )}
    </div>
  )
}
