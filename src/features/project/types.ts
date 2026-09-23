/** src/features/project/types.ts — Project feature type definitions (UI-First) */
import { Factory, DollarSign, FolderOpen } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

/** Project type — determines default templates, wiki structure, stages */
export type ProjectType = 'recruitment' | 'investment' | 'general'

/** Project stage — lifecycle phase */
export type ProjectStage =
  | 'recruitment_before' | 'recruitment_during' | 'recruitment_after'
  | 'investment_before' | 'investment_during' | 'investment_after'
  | 'none'

/** Extended project info (UI-First: type/stage stored locally, not yet in backend) */
export interface ProjectExtended {
  projectId: string
  type: ProjectType
  stage: ProjectStage
}

/** Mock report template */
export interface ReportTemplate {
  id: string
  name: string
  category: string
  outputFormat: 'Word' | 'PPT'
  chapterCount: number
  projectType: ProjectType
  stage: ProjectStage
}

/** Mock report instance */
export interface MockReport {
  id: string
  title: string
  templateName: string
  outputFormat: 'Word' | 'PPT'
  createdAt: string
  status: 'completed' | 'generating' | 'failed'
  progress?: number
}

/** Mock form template (填表) */
export interface FormTemplate {
  id: string
  name: string
  outputFormat: 'DOCX' | 'XLSX'
  projectType: ProjectType
  stage: ProjectStage
}

/** Mock form instance */
export interface MockForm {
  id: string
  title: string
  templateName: string
  outputFormat: 'DOCX' | 'XLSX'
  createdAt: string
  status: 'completed' | 'filling' | 'failed'
}

/** Stage definitions per project type */
export const STAGE_MAP: Record<ProjectType, { key: ProjectStage; labelKey: string }[]> = {
  investment: [
    { key: 'investment_before', labelKey: 'project.stage.investmentBefore' },
    { key: 'investment_during', labelKey: 'project.stage.investmentDuring' },
    { key: 'investment_after', labelKey: 'project.stage.investmentAfter' },
  ],
  recruitment: [
    { key: 'recruitment_before', labelKey: 'project.stage.recruitmentBefore' },
    { key: 'recruitment_during', labelKey: 'project.stage.recruitmentDuring' },
    { key: 'recruitment_after', labelKey: 'project.stage.recruitmentAfter' },
  ],
  general: [],
}

/** Project type config — uses Lucide components for consistent icon style */
export const PROJECT_TYPE_CONFIG: Record<ProjectType, { labelKey: string; Icon: LucideIcon }> = {
  recruitment: { labelKey: 'project.type.recruitment', Icon: Factory },
  investment: { labelKey: 'project.type.investment', Icon: DollarSign },
  general: { labelKey: 'project.type.general', Icon: FolderOpen },
}

/** UI-First: project type/stage stored locally, keyed by project id.
 *  Shared between ProjectDetailPage and ProjectList (create modal). */
export const projectExtState = new Map<string, { type: ProjectType; stage: ProjectStage }>()

/** Helper: set project type/stage for a newly created project */
export function setProjectExtState(projectId: string, type: ProjectType): void {
  const stages = STAGE_MAP[type]
  const stage: ProjectStage = stages.length > 0 ? stages[0].key : 'none'
  projectExtState.set(projectId, { type, stage })
}

/** Helper: get project type/stage, with defaults */
export function getProjectExtState(projectId: string): { type: ProjectType; stage: ProjectStage } {
  if (!projectExtState.has(projectId)) {
    projectExtState.set(projectId, { type: 'general', stage: 'none' })
  }
  return projectExtState.get(projectId)!
}
