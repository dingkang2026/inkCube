/** src/components/layout/SystemAlerts.tsx — App 级统一告警区（0.5 §4.2 打样修订 2026-10-18：
 *  服务异常红条 + 杀软授权橙条（AvExclusionAlerts）同区堆叠，TitleBar 之下全局可见，
 *  全部条目消失时整区不占位。服务条数据自含：健康拉取与边沿刷新经共享 hook
 *  useHealthServices（挂载一次 + app:health-changed / focus 重拉，零新增轮询，0.4 §8.1；
 *  设置页导航状态点为第二消费方）。红条判定 = overall 全必配聚合（0.4 §7.2，
 *  2026-10-19 必配统一：llm/网络搜索/内部模型任一缺失即 degraded）；缺失明细
 *  逐项列出，点击直达对应设置
 *  section（0.4 §7.5 suggestion 动作化）。 */
import { useTranslation } from 'react-i18next'
import { AlertTriangle } from 'lucide-react'
import { settingsSectionOfAction } from '../../features/readiness/useReadiness'
import { useHealthServices } from '../../features/readiness/useHealthServices'
import { AvExclusionAlerts } from './AvExclusionAlerts'
import s from './SystemAlerts.module.css'

interface SystemAlertsProps {
  /** 告警条「去设置」动作（App.handleOpenSettings，复用 app:navigate-settings 同链） */
  onOpenSettings: (section?: string) => void
}

export function SystemAlerts({ onOpenSettings }: SystemAlertsProps) {
  const { t } = useTranslation()
  // 健康数据经共享 hook（原本地状态机提取，行为不变）：down = overall 异常/拉取失败
  const { services, down } = useHealthServices()
  // 非 ok 服务明细（degraded 时逐项显示；down 时 services 为空无明细）
  const issues = Object.entries(services ?? {})
    .filter(([, v]) => v.status !== 'ok')
    .map(([key, v]) => ({ key, code: v.code ?? '', action: v.action ?? '' }))

  return (
    <div className={s.area}>
      {down && (
        <div className={`${s.alert} ${s.danger}`} role="alert">
          <span className={s.alertBar} />
          <span className={s.alertIcon}><AlertTriangle size={15} /></span>
          <div className={s.alertBody}>
            <span className={s.alertText}>{t('alerts.serviceMessage')}</span>
            {issues.length > 0 && (
              <div className={s.issueList}>
                {issues.map((iss) => (
                  <button
                    key={iss.key}
                    type="button"
                    className={s.issueItem}
                    onClick={() => onOpenSettings(settingsSectionOfAction(iss.action))}
                  >
                    <span className={s.issueDot} />
                    {t(`alerts.serviceItem.${iss.code}`)}
                  </button>
                ))}
              </div>
            )}
          </div>
          <button type="button" className={s.alertAction} onClick={() => onOpenSettings()}>
            {t('alerts.serviceAction')}
          </button>
        </div>
      )}
      <AvExclusionAlerts />
    </div>
  )
}
