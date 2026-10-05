/** src/features/project/types.ts — Project feature type definitions */
import { Factory, DollarSign, FolderOpen } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

/** Project type — determines default templates, wiki structure, stages */
export type ProjectType = 'recruitment' | 'investment' | 'general'

/** Project stage — lifecycle phase (simple values matching template stage field) */
export type ProjectStage = 'before' | 'during' | 'after' | 'none'

/** Extended project info */
export interface ProjectExtended {
  projectId: string
  type: ProjectType
  stage: ProjectStage
}

/** Report instance */
export interface Report {
  id: string
  title: string
  template_id: string
  template_name: string
  output_format: string
  created_at: string
  status: 'done' | 'generating' | 'error'
  progress?: number
  stage?: ProjectStage
  error_message?: string
}

/** Report generation log entry */
export interface ReportLog {
  id: string
  report_id: string
  level: 'info' | 'error' | 'warning'
  message: string
  section_id?: string
  created_at: string
}

/** Stage definitions per project type */
export const STAGE_MAP: Record<ProjectType, { key: ProjectStage; labelKey: string }[]> = {
  investment: [
    { key: 'before', labelKey: 'project.stage.investmentBefore' },
    { key: 'during', labelKey: 'project.stage.investmentDuring' },
    { key: 'after', labelKey: 'project.stage.investmentAfter' },
  ],
  recruitment: [
    { key: 'before', labelKey: 'project.stage.recruitmentBefore' },
    { key: 'during', labelKey: 'project.stage.recruitmentDuring' },
    { key: 'after', labelKey: 'project.stage.recruitmentAfter' },
  ],
  general: [],
}

/** Stage labels for kanban columns */
export const STAGE_COLUMNS: { key: ProjectStage; labelKey: string }[] = [
  { key: 'before', labelKey: 'project.stage.before' },
  { key: 'during', labelKey: 'project.stage.during' },
  { key: 'after', labelKey: 'project.stage.after' },
  { key: 'none', labelKey: 'project.stage.other' },
]

/** Project type config — uses Lucide components for consistent icon style */
export const PROJECT_TYPE_CONFIG: Record<ProjectType, { labelKey: string; Icon: LucideIcon }> = {
  recruitment: { labelKey: 'project.type.recruitment', Icon: Factory },
  investment: { labelKey: 'project.type.investment', Icon: DollarSign },
  general: { labelKey: 'project.type.general', Icon: FolderOpen },
}

/** Helper: get initial stage for a project type */
export function getInitialStage(type: ProjectType): ProjectStage {
  const stages = STAGE_MAP[type]
  return stages.length > 0 ? stages[0].key : 'none'
}
