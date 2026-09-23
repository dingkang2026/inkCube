/** src/features/project/BaseLibDetailPage.tsx — Base library detail page (two-tab: materials + wiki) */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeft, FolderOpen, BookOpen, FileText, CheckCircle, Clock, Factory, DollarSign, Library } from 'lucide-react'
import { WikiTab } from './tabs/WikiTab'
import s from './BaseLibDetailPage.module.css'

/** Mock base library data */
const BASE_LIB_DATA: Record<string, {
  nameKey: string; descKey: string; Icon: typeof Factory; type: 'recruitment' | 'investment' | 'general';
  files: { name: string; size: string; status: 'done' | 'processing' | 'pending' }[]
}> = {
  'bl-recruitment': {
    nameKey: 'baseLib.recruitmentLib',
    descKey: 'baseLib.recruitmentLibDesc',
    Icon: Factory,
    type: 'recruitment',
    files: [
      { name: '全国产业园区名录.pdf', size: '2.3 MB', status: 'done' },
      { name: '各地招商政策汇编2026.pdf', size: '5.1 MB', status: 'done' },
      { name: '园区配套设施标准.docx', size: '890 KB', status: 'done' },
      { name: '产业扶持政策细则.pdf', size: '1.2 MB', status: 'processing' },
    ],
  },
  'bl-investment': {
    nameKey: 'baseLib.investmentLib',
    descKey: 'baseLib.investmentLibDesc',
    Icon: DollarSign,
    type: 'investment',
    files: [
      { name: '投资估值基准数据.xlsx', size: '1.5 MB', status: 'done' },
      { name: '尽调法规汇编.pdf', size: '3.2 MB', status: 'done' },
      { name: '行业数据报告合集/', size: '12.8 MB', status: 'done' },
      { name: '投后管理模板集.docx', size: '670 KB', status: 'pending' },
    ],
  },
  'bl-custom-1': {
    nameKey: 'baseLib.myLib',
    descKey: 'baseLib.myLibDesc',
    Icon: Library,
    type: 'general',
    files: [
      { name: '我的参考资料.pdf', size: '4.5 MB', status: 'done' },
      { name: '行业笔记.docx', size: '320 KB', status: 'done' },
    ],
  },
}

type TabKey = 'materials' | 'wiki'

interface BaseLibDetailPageProps {
  baseLibId: string
  onBack: () => void
}

export function BaseLibDetailPage({ baseLibId, onBack }: BaseLibDetailPageProps) {
  const { t } = useTranslation()
  const [activeTab, setActiveTab] = useState<TabKey>('materials')
  const libData = BASE_LIB_DATA[baseLibId]

  if (!libData) {
    return (
      <div className={s.notFound}>
        <p>{t('baseLib.notFound')}</p>
        <button className={s.backBtn} onClick={onBack}>
          <ArrowLeft size={14} /> {t('baseLib.back')}
        </button>
      </div>
    )
  }

  return (
    <div className={s.page}>
      {/* Header */}
      <div className={s.header}>
        <button className={s.backBtn} onClick={onBack}>
          <ArrowLeft size={14} />
          {t('baseLib.back')}
        </button>
        <div className={s.headerInfo}>
          <span className={s.headerIcon}><libData.Icon size={24} /></span>
          <div>
            <h2 className={s.libName}>{t(libData.nameKey)}</h2>
            <p className={s.libDesc}>{t(libData.descKey)}</p>
          </div>
        </div>
      </div>

      {/* Tab bar */}
      <div className={s.tabBar}>
        <button
          className={`${s.tab} ${activeTab === 'materials' ? s.tabActive : ''}`}
          onClick={() => setActiveTab('materials')}
        >
          <FolderOpen size={15} />
          <span>{t('baseLib.tabMaterials')}</span>
        </button>
        <button
          className={`${s.tab} ${activeTab === 'wiki' ? s.tabActive : ''}`}
          onClick={() => setActiveTab('wiki')}
        >
          <BookOpen size={15} />
          <span>{t('baseLib.tabWiki')}</span>
        </button>
      </div>

      {/* Tab content */}
      <div className={s.tabContent}>
        {activeTab === 'materials' && (
          <div className={s.fileList}>
            {libData.files.map((file, idx) => (
              <div key={idx} className={s.fileCard}>
                <FileText size={18} className={s.fileIcon} />
                <div className={s.fileInfo}>
                  <div className={s.fileName}>{file.name}</div>
                  <div className={s.fileMeta}>{file.size}</div>
                </div>
                <div className={s.fileStatus}>
                  {file.status === 'done' && (
                    <span className={s.statusDone}><CheckCircle size={13} /> {t('baseLib.statusDone')}</span>
                  )}
                  {file.status === 'processing' && (
                    <span className={s.statusProcessing}><Clock size={13} /> {t('baseLib.statusProcessing')}</span>
                  )}
                  {file.status === 'pending' && (
                    <span className={s.statusPending}>{t('baseLib.statusPending')}</span>
                  )}
                </div>
              </div>
            ))}
            <div className={s.uploadHint}>
              <p>{t('baseLib.uploadHint')}</p>
            </div>
          </div>
        )}
        {activeTab === 'wiki' && (
          <WikiTab projectType={libData.type === 'recruitment' ? 'recruitment' : libData.type === 'investment' ? 'investment' : 'general'} />
        )}
      </div>
    </div>
  )
}
