/** src/features/project/BaseLibDetailPage.tsx — Base library detail page (API-driven, embeds KnowledgePage) */
import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Factory, DollarSign, Library } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { KnowledgePage } from '../knowledge/KnowledgePage'
import type { Project, KnowledgeIpcChannels } from '../knowledge/types'
import { ProjectChatPanel } from './ProjectChatPanel'
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

interface BaseLibDetailPageProps {
  baseLibId: string
}

interface BaseLibInfo {
  id: string
  name: string
  description: string
  lib_type: string | null
}

export function BaseLibDetailPage({ baseLibId }: BaseLibDetailPageProps) {
  const { t } = useTranslation()
  const [libInfo, setLibInfo] = useState<BaseLibInfo | null>(null)
  const [loading, setLoading] = useState(true)
  const [chatFileId, setChatFileId] = useState<string | null>(null)
  const [chatFilename, setChatFilename] = useState<string | null>(null)

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
        <div className={s.headerInfo}>
          <span className={s.headerIcon}><Icon size={24} /></span>
          <div>
            <h2 className={s.libName}>{libInfo.name}</h2>
            {libInfo.description && <p className={s.libDesc}>{libInfo.description}</p>}
          </div>
        </div>
      </div>

      {/* Content area: materials + chat panel side by side */}
      <div className={s.contentArea}>
        <div className={s.tabContent}>
          <KnowledgePage
            selectedProject={fakeProject}
            compact
            ipcChannels={BASE_LIB_IPC_CHANNELS}
            onFileSelect={(fileId, filename) => { setChatFileId(fileId); setChatFilename(filename) }}
          />
        </div>
        <ProjectChatPanel
          projectId={libInfo.id}
          projectName={libInfo.name}
          selectedFileId={chatFileId || undefined}
          selectedFilename={chatFilename || undefined}
          onClearFileScope={() => { setChatFileId(null); setChatFilename(null) }}
          chatIpcChannel="baseLib:chat"
        />
      </div>
    </div>
  )
}
