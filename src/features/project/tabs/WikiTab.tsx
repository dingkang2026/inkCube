/** src/features/project/tabs/WikiTab.tsx — Wiki tab placeholder (UI-First) */
import { useTranslation } from 'react-i18next'
import { BookOpen, Sparkles } from 'lucide-react'
import type { ProjectType } from '../types'
import s from './WikiTab.module.css'

interface WikiTabProps {
  projectType: ProjectType
}

/** Wiki structure hints per project type (from design doc §5.3) */
const WIKI_HINTS: Record<string, string[]> = {
  investment: ['公司概况', '核心团队', '财务健康度', '法律风险', '估值参考', '风险清单'],
  recruitment: ['招商政策摘要', '目标企业画像', '园区/区域概况', '产业链分析', '竞品园区对比'],
  general: ['由 LLM 根据文档自动推断'],
}

export function WikiTab({ projectType }: WikiTabProps) {
  const { t } = useTranslation()
  const hints = WIKI_HINTS[projectType] || WIKI_HINTS.general

  return (
    <div className={s.container}>
      <div className={s.placeholder}>
        <div className={s.icon}>
          <BookOpen size={32} />
        </div>
        <h3 className={s.title}>{t('project.wiki.title')}</h3>
        <p className={s.desc}>{t('project.wiki.description')}</p>

        <div className={s.hintBox}>
          <div className={s.hintHeader}>
            <Sparkles size={14} />
            <span>{t('project.wiki.suggestedStructure')}</span>
          </div>
          <ul className={s.hintList}>
            {hints.map((h, i) => (
              <li key={i}>{h}</li>
            ))}
          </ul>
        </div>

        <div className={s.comingSoon}>
          {t('project.wiki.comingSoon')}
        </div>
      </div>
    </div>
  )
}
