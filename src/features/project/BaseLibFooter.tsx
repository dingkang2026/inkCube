/** src/features/project/BaseLibFooter.tsx — Sidebar base library list (API-driven) */
import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Factory, DollarSign, Library, FileText } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useActivity } from '../activity/ActivityContext'
import type { ProjectActivity } from '../activity/types'
import { usePagedList } from '../../hooks/usePagedList'
import { useToast } from '../knowledge/Toast'
import s from './BaseLibFooter.module.css'

interface BaseLibRecord {
  id: string
  name: string
  description: string
  lib_type: string | null
  file_count: number
}

/** Icon mapping for known preset base lib IDs */
const PRESET_ICONS: Record<string, LucideIcon> = {
  'bl-recruitment': Factory,
  'bl-investment': DollarSign,
}

function isPreset(libType: string | null): boolean {
  return !!libType && libType.startsWith('preset_')
}

function getIcon(id: string): LucideIcon {
  return PRESET_ICONS[id] || Library
}

interface BaseLibFooterProps {
  selectedBaseLib: string | null
  onSelectBaseLib: (baseLibId: string) => void
  onOpenTemplateManagement?: () => void
  /** Whether the template management page is currently shown (peer highlight) */
  templateManagementActive?: boolean
}

export function BaseLibFooter({ selectedBaseLib, onSelectBaseLib, onOpenTemplateManagement, templateManagementActive }: BaseLibFooterProps) {
  const { t } = useTranslation()
  const { byProject } = useActivity()
  const { isActive: formTplAnalyzing, items: formTplItems } = useActivity('form_template')
  // PPT 模板任务活跃态（2.3 §4.2）：container='templates' 的 ppt 活动（模板工坊），
  // awaiting_input 门禁待确认用醒目色点，running 用中性活跃点（同基础库行视觉）
  const { items: pptItems } = useActivity('ppt')
  const tplAwaiting = pptItems.filter((a) => a.container === 'templates' && a.status === 'awaiting_input')
  const tplRunning = pptItems.filter((a) => a.container === 'templates' && a.status !== 'awaiting_input')
  const showToast = useToast()
  // 列表分页（0.1dev-guide 统一约定）：50/页「加载更多」，挂载即拉
  const {
    items: baseLibs, hasMore, loadingMore, loading, error: libsError, loadMore,
  } = usePagedList<BaseLibRecord>({ channel: 'baseLib:list', itemsKey: 'base_libs' })
  // 列表加载失败提示（对齐 FormsTab m-3：静默吞掉会让列表空白且无任何提示）
  useEffect(() => {
    if (libsError) showToast(`${t('common.listLoadFailed')}: ${libsError}`, 'error')
  }, [libsError, showToast, t])

  /** 活跃点 tooltip（0.4 §5.7）：与项目行对齐，复用 ProjectList 同一组 i18n */
  const activityTooltip = (pa: ProjectActivity): string => {
    const parts: string[] = []
    if (pa.files > 0) parts.push(t('activity.tipFiles', { count: pa.files }))
    if (pa.compiles > 0) parts.push(t('activity.tipCompiles'))
    if (pa.reports > 0) parts.push(t('activity.tipReports', { count: pa.reports }))
    if (pa.forms > 0) parts.push(t('activity.tipForms', { count: pa.forms }))
    if (pa.ppts > 0) parts.push(t('activity.tipPpts', { count: pa.ppts }))
    return parts.join(' / ')
  }

  return (
    <div className={s.footer}>
      <div className={s.header}>
        <span className={s.title}>{t('baseLib.title')}</span>
      </div>
      <div className={s.list}>
        {!loading && baseLibs.map((lib) => {
          const Icon = getIcon(lib.id)
          const preset = isPreset(lib.lib_type)
          // 活跃点与项目行对齐（0.4 §5.7）：全局心跳 byProject 派生，仅基础库容器行先小动作
          const pa = byProject.get(lib.id)
          const hasOngoing = !!pa && pa.container === 'baselib' && pa.files + pa.compiles + pa.reports + pa.forms + pa.ppts > 0
          return (
            <div
              key={lib.id}
              className={`${s.item} ${selectedBaseLib === lib.id ? s.itemSelected : ''}`}
              onClick={() => onSelectBaseLib(lib.id)}
            >
              <Icon size={15} className={s.icon} />
              <span className={s.name}>{lib.name}</span>
              {hasOngoing && pa && (
                <span
                  className={s.activityDot}
                  title={activityTooltip(pa)}
                />
              )}
              {preset && <span className={s.badge}>{t('baseLib.preset')}</span>}
            </div>
          )
        })}
        {hasMore && (
          <button
            className={s.loadMoreBtn}
            onClick={() => { void loadMore() }}
            disabled={loadingMore}
          >
            {loadingMore ? t('common.loading') : t('common.loadMore')}
          </button>
        )}
      </div>
      {onOpenTemplateManagement && (
        <>
          <div className={s.header} style={{ marginTop: '8px' }}>
            <span className={s.title}>{t('baseLib.templateManagement')}</span>
          </div>
          <div
            className={`${s.item} ${templateManagementActive ? s.itemSelected : ''}`}
            onClick={onOpenTemplateManagement}
            style={{ cursor: 'pointer', paddingLeft: '18px' }}
          >
            <FileText size={15} className={s.icon} />
            <span className={s.name}>{t('project.report.templateManagement')}</span>
            {formTplAnalyzing && (
              <span
                className={s.activityDot}
                title={t('activity.tipFormTemplates', { count: formTplItems.length })}
              />
            )}
            {tplAwaiting.length > 0 ? (
              <span
                className={`${s.activityDot} ${s.dotAlert}`}
                title={t('activity.tipPptTemplatesAwaiting', { count: tplAwaiting.length })}
              />
            ) : tplRunning.length > 0 ? (
              <span
                className={s.activityDot}
                title={t('activity.tipPptTemplates', { count: tplRunning.length })}
              />
            ) : null}
          </div>
        </>
      )}
    </div>
  )
}
