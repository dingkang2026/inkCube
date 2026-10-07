/** src/features/knowledge/types.ts */
import type { ProjectType, ProjectStage } from '../project/types'

export interface Project {
  id: string
  name: string
  description?: string
  type?: ProjectType
  stage?: ProjectStage
  company_name?: string
  credit_code?: string
  file_count: number
  chunk_count: number
  created_at: string
}

export interface FileRecord {
  id: string
  filename: string
  file_type: string
  status: 'pending' | 'parsing' | 'done' | 'error'
  progress?: number
  chunks_count?: number
  images_count?: number
  image_status?: 'none' | 'pending' | 'describing' | 'done' | 'error'
  image_described_count?: number
  error_message?: string | null
  created_at: string
  updated_at?: string
  metadata?: string
}

export interface ImageRecord {
  id: string
  page_no: number | null
  caption: string | null
  local_path: string
  vlm_description: string | null
  context_text: string | null
  status: 'pending' | 'processing' | 'done' | 'error' | 'archived'
  error_message: string | null
  category: string
  width_px?: number | null
  height_px?: number | null
  file_size?: number | null
  created_at: string
}

/** Available image categories for manual classification */
export const IMAGE_CATEGORIES = [
  'uncategorized',
  'chart',
  'photo',
  'diagram',
  'table',
  'formula',
  'logo',
  'qr_code',
  'signature',
  'seal',
  'other',
] as const

export type ImageCategory = typeof IMAGE_CATEGORIES[number]

export interface SearchResult {
  id: string
  content: string
  _score: number
  _dense_score?: number
  _fts_score?: number
  _rank_dense?: number
  _rank_fts?: number
  metadata: {
    file_id: string
    filename: string
    content_type: string
    chunk_index: number
    markdown_content?: string
    section_path?: string
    page?: number
  }
}

export interface UploadProgress {
  stage: string
  progress: number // 0.0 - 1.0
  message?: string
}

/** IPC channel name mapping — allows KnowledgePage to be reused with different backends (e.g. base libs) */
export interface KnowledgeIpcChannels {
  getFiles: string
  getActiveFiles: string
  uploadFile: string
  deleteFile: string
  retryFile: string
  retryFailedFiles: string
  batchDeleteFiles: string
  batchReprocessFiles: string
  search: string
  recentChunks: string
  getProjectImages: string
  getImages: string
  retryImage: string
  updateImageCategory: string
  archiveImage: string
  retryFailedImages: string
  queuePause: string
}

export const DEFAULT_KNOWLEDGE_IPC_CHANNELS: KnowledgeIpcChannels = {
  getFiles: 'knowledge:getFiles',
  getActiveFiles: 'knowledge:getActiveFiles',
  uploadFile: 'knowledge:uploadFile',
  deleteFile: 'knowledge:deleteFile',
  retryFile: 'knowledge:retryFile',
  retryFailedFiles: 'knowledge:retryFailedFiles',
  batchDeleteFiles: 'knowledge:batchDeleteFiles',
  batchReprocessFiles: 'knowledge:batchReprocessFiles',
  search: 'knowledge:search',
  recentChunks: 'knowledge:recentChunks',
  getProjectImages: 'knowledge:getProjectImages',
  getImages: 'knowledge:getImages',
  retryImage: 'knowledge:retryImage',
  updateImageCategory: 'knowledge:updateImageCategory',
  archiveImage: 'knowledge:archiveImage',
  retryFailedImages: 'knowledge:retryFailedImages',
  queuePause: 'knowledge:queuePause',
}
