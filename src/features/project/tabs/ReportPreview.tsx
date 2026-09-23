/** src/features/project/tabs/ReportPreview.tsx — Report edit/preview view (mock, UI-First) */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeft, FileText, Presentation, RefreshCw, Paperclip, ChevronDown, ChevronUp } from 'lucide-react'
import type { MockReport } from '../types'
import s from './ReportPreview.module.css'

/** Full mock report content in Markdown */
const MOCK_REPORT_CONTENT = `# XX公司尽职调查报告

## 1. 公司概况

XX科技有限公司成立于2018年，注册资本5000万元，是一家专注于新材料研发与生产的高新技术企业。公司总部位于上海张江高科技园区，拥有现代化生产基地20000平方米，员工总数300余人。

公司先后获得国家级高新技术企业认定、上海市专精特新企业等荣誉，拥有有效专利12项（其中发明专利8项）。

> 📎 来源: BP.pdf P3, 企业工商信息查询

## 2. 核心团队

**创始人兼CEO 张三**：清华大学材料科学博士，15年行业经验，曾主导多个亿元级项目的技术研发和产业化落地。

**CTO 李四**：中科院硕士，曾任某上市公司技术总监，在新型材料合成领域拥有丰富的工程化经验。

**CFO 王五**：CPA，10年财务管理经验，曾服务于四大会计师事务所。

核心团队共12人，其中博士3人、硕士6人，团队结构合理、互补性强。

> 📎 来源: BP.pdf P8, 团队介绍章节

## 3. 业务模式

公司主营业务分为三大板块：

1. **新材料研发**：为客户提供定制化材料解决方案，毛利率约45%
2. **技术授权**：将自有专利技术授权给合作伙伴，毛利率约90%
3. **产品代工**：利用自有产能为客户提供代工服务，毛利率约25%

收入结构：新材料研发占60%，技术授权占25%，产品代工占15%。

> 📎 来源: BP.pdf P12-15

## 4. 财务分析

| 指标 | 2024年 | 2025年 | 增长率 |
|------|--------|--------|--------|
| 营业收入 | 8500万 | 1.2亿 | 41% |
| 净利润 | 1200万 | 2100万 | 75% |
| 毛利率 | 42% | 45% | +3pp |
| 研发投入占比 | 15% | 18% | +3pp |

公司财务状况良好，营收和利润均保持高速增长。研发投入持续加大，体现了公司对技术创新的重视。

> 📎 来源: 财报.pdf P5-8, 财务数据摘要

## 5. 市场分析

目标市场规模约200亿元，年增长率15-20%。目前市场集中度较低，CR5约35%，行业仍处于快速成长期。

主要驱动因素：
- 下游需求持续增长（新能源、半导体等）
- 国产替代趋势加速
- 政策扶持力度加大

> 📎 来源: 行业研究报告.pdf P20-25

## 6. 风险提示

1. **技术风险**：核心技术依赖少数关键人员
2. **市场风险**：下游行业周期性波动可能影响需求
3. **竞争风险**：大型企业可能加大该领域投入
4. **政策风险**：相关补贴政策存在不确定性

> 📎 来源: 综合分析
`

const SOURCES = [
  { name: 'BP.pdf', count: 8 },
  { name: '财报.pdf', count: 5 },
  { name: '行业研究报告.pdf', count: 6 },
  { name: '专利查询结果', count: 3 },
  { name: '工商查询数据', count: 1 },
]

interface ReportPreviewProps {
  report: MockReport
  onBack: () => void
}

export function ReportPreview({ report, onBack }: ReportPreviewProps) {
  const { t } = useTranslation()
  const [showSources, setShowSources] = useState(true)
  // regeneratingChapter is set by mock interactions (e.g. clicking "regenerate" on a chapter)
  const [regeneratingChapter, setRegeneratingChapter] = useState<string | null>(null)
  void setRegeneratingChapter // suppress unused warning in mock

  const handleExport = (format: string) => {
    alert(t('project.report.preview.exportSuccess', { format, title: report.title }))
  }

  return (
    <div className={s.container}>
      {/* Top bar */}
      <div className={s.topBar}>
        <button className={s.backBtn} onClick={onBack}>
          <ArrowLeft size={16} />
          {t('project.report.preview.backToList')}
        </button>
        <h3 className={s.reportTitle}>{report.title}</h3>
        <div className={s.toolbar}>
          <button className={s.exportBtn} onClick={() => handleExport('Word')}>
            <FileText size={14} /> Word
          </button>
          <button className={s.exportBtn} onClick={() => handleExport('PPT')}>
            <Presentation size={14} /> PPT
          </button>
          <div className={s.divider} />
          <button className={s.toolBtn}>
            {t('project.report.preview.editOutline')}
          </button>
          <button className={s.toolBtn}>
            {t('project.report.preview.regenerateChapter')}
          </button>
        </div>
      </div>

      {/* Report content */}
      <div className={s.contentArea}>
        <pre className={s.markdownContent}>{MOCK_REPORT_CONTENT}</pre>
      </div>

      {/* Sources panel */}
      <div className={s.sourcesPanel}>
        <button className={s.sourcesToggle} onClick={() => setShowSources(!showSources)}>
          <Paperclip size={14} />
          <span>{t('project.report.preview.sources', { count: SOURCES.length, total: SOURCES.reduce((a, s) => a + s.count, 0) })}</span>
          {showSources ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
        </button>
        {showSources && (
          <div className={s.sourcesList}>
            {SOURCES.map((src) => (
              <div key={src.name} className={s.sourceItem}>
                <span className={s.sourceName}>{src.name}</span>
                <span className={s.sourceCount}>{src.count} {t('project.report.preview.fragments')}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Regenerating overlay */}
      {regeneratingChapter && (
        <div className={s.regenerating}>
          <RefreshCw size={16} className={s.spinIcon} />
          <span>{t('project.report.preview.regenerating', { chapter: regeneratingChapter })}</span>
        </div>
      )}
    </div>
  )
}
