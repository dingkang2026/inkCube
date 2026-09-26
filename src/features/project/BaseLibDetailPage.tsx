/** src/features/project/BaseLibDetailPage.tsx — Base library detail page (API-driven, embeds KnowledgePage) */
import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeft, FolderOpen, BookOpen, Factory, DollarSign, Library } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { KnowledgePage } from '../knowledge/KnowledgePage'
import type { Project, KnowledgeIpcChannels } from '../knowledge/types'
import { WikiTab } from './tabs/WikiTab'
import type { ProjectType } from './types'
import s from './BaseLibDetailPage.module.css'

/** IPC channel mapping: reuse KnowledgePage with baseLib:* backend channels */
const BASE_LIB_IPC_CHANNELS: KnowledgeIpcChannels = {
  getFiles: 'baseLib:getFiles',
  getActiveFiles: 'baseLib:getActiveFiles',
  uploadFile: 'baseLib:upload',
  deleteFile: 'baseLib:deleteFile',
  retryFile: 'baseLib:retryFile',
  retryFailedFiles: 'baseLib:retryFailedFiles',
  batchDeleteFiles: 'baseLib:batchDeleteFiles',
  batchReprocessFiles: 'baseLib:batchReprocessFiles',
  search: 'baseLib:search',
  recentChunks: 'baseLib:recentChunks',
  getProjectImages: 'baseLib:getImages',
  getImages: 'baseLib:getImages',
  retryImage: 'knowledge:retryImage',
  updateImageCategory: 'knowledge:updateImageCategory',
  archiveImage: 'knowledge:archiveImage',
  retryFailedImages: 'knowledge:retryFailedImages',
}

/** Icon mapping for known preset base lib IDs */
const PRESET_ICONS: Record<string, LucideIcon> = {
  'bl-recruitment': Factory,
  'bl-investment': DollarSign,
}

function getIcon(id: string): LucideIcon {
  return PRESET_ICONS[id] || Library
}

/** Map lib_type to ProjectType for WikiTab hints */
function getWikiProjectType(libType: string | null): ProjectType {
  if (libType === 'preset_recruitment') return 'recruitment'
  if (libType === 'preset_investment') return 'investment'
  return 'general'
}

type TabKey = 'materials' | 'wiki'

interface BaseLibDetailPageProps {
  baseLibId: string
  onBack: () => void
}

interface BaseLibInfo {
  id: string
  name: string
  description: string
  lib_type: string | null
}

export function BaseLibDetailPage({ baseLibId, onBack }: BaseLibDetailPageProps) {
  const { t } = useTranslation()
  const [activeTab, setActiveTab] = useState<TabKey>('materials')
  const [libInfo, setLibInfo] = useState<BaseLibInfo | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const load = async () => {
      try {
        const result: any = await window.electronAPI.invoke('baseLib:list')
        const libs = result.base_libs || []
        const found = libs.find((l: any) => l.id === baseLibId)
        if (found) {
          setLibInfo({ id: found.id, name: found.name, description: found.description, lib_type: found.lib_type })
        }
      } catch (err) {
        console.error('Failed to load base lib info:', err)
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [baseLibId])

  if (!loading && !libInfo) {
    return (
      <div className={s.notFound}>
        <p>{t('baseLib.notFound')}</p>
        <button className={s.backBtn} onClick={onBack}>
          <ArrowLeft size={14} /> {t('baseLib.back')}
        </button>
      </div>
    )
  }

  if (loading || !libInfo) {
    return <div className={s.page} />
  }

  const Icon = getIcon(baseLibId)
  // Construct a Project-compatible object for KnowledgePage
  const fakeProject: Project = {
    id: libInfo.id,
    name: libInfo.name,
    description: libInfo.description,
    file_count: 0,
    chunk_count: 0,
    created_at: '',
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
          <span className={s.headerIcon}><Icon size={24} /></span>
          <div>
            <h2 className={s.libName}>{libInfo.name}</h2>
            {libInfo.description && <p className={s.libDesc}>{libInfo.description}</p>}
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
          <KnowledgePage
            selectedProject={fakeProject}
            compact
            ipcChannels={BASE_LIB_IPC_CHANNELS}
          />
        )}
        {activeTab === 'wiki' && (
          <WikiTab projectType={getWikiProjectType(libInfo.lib_type)} />
        )}
      </div>
    </div>
  )
}
