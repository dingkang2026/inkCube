/**
 * quickCreateSub（0.6 §3.3 立项/新建项目勾选动线，2026-10-25）：
 * 两条项目创建动线（LeadPool 线索立项 / ProjectList 空白新建）勾选「同时创建
 * 企业动态跟踪订阅」的共享实现——payload 契约单一来源（2.11 §8.1「从立项/
 * 新建项目勾选」来源行）：复用线索升级同款模板（LEAD_UPGRADE_TPL_ID）固化
 * prompt（占位符名语言无关提取，i18n 键从常量派生同 App handleLeadUpgrade）、
 * daily 09:00 可改、去向绑项目 + autoImport（期次产出汇入项目知识，「情报是
 * 项目的输入」价值链闭环）。
 * 失败上抛由调用方 toast——部分失败语义：项目已建成不阻断（同 patchFailed）。
 */
import {
  applyValues, BUILTIN_PREFIX, LEAD_UPGRADE_TPL_ID, uniquePlaceholderNames,
} from '../task/promptTemplate'

/** 快捷创建盯梢订阅：company = 盯梢对象企业全称，projectId = 产出沉淀去向项目 */
export async function quickCreateTrackingSub(
  company: string,
  projectId: string,
  t: (key: string, opts?: Record<string, unknown>) => string,
): Promise<void> {
  const tplPrompt = t(`task.tpl.${LEAD_UPGRADE_TPL_ID.slice(BUILTIN_PREFIX.length)}.prompt`)
  const phName = uniquePlaceholderNames(tplPrompt)[0]
  if (!phName) throw new Error('no placeholder in lead-upgrade tpl prompt')
  await window.electronAPI.invoke('schedules:create', {
    name: t('schedule.createSub.name', { name: company }),
    prompt: applyValues(tplPrompt, { [phName]: company }),
    category: 'corporate',   // mon→corporate 静态映射（同 ScheduleFormModal）
    intervalType: 'daily',
    intervalValue: '09:00',
    targetType: 'project',
    targetId: projectId,
    autoImport: true,
    scope: null,
    digestScheduleIds: null,
  })
}
