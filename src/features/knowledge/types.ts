/** src/features/knowledge/types.ts */

export interface Project {
  id: string
  name: string
  description?: string
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
