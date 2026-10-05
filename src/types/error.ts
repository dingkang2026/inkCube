/** src/types/error.ts — Structured error types matching Python InkCubeError. */

export type ErrorCategory =
  | 'CONFIG'
  | 'CREDENTIAL'
  | 'NETWORK'
  | 'RESOURCE'
  | 'INTERNAL'
  | 'PROCESS'

/** Structured error from Python backend (InkCubeError.to_dict()) */
export interface InkCubeError {
  code: string
  category: ErrorCategory
  message: string
  suggestion: string
  detail?: string
}

/** Health status of a single service */
export interface ServiceHealth {
  status: 'ok' | 'error' | 'warning'
  code?: string
  message?: string
  suggestion?: string
}

/** 功能就绪度缺失项（0.4 §7.2）：action 为设置页锚点编码 */
export interface ReadinessMissing {
  kind: 'model' | 'config'
  id: string
  action: 'settings:external' | 'settings:internal'
  /** 可选依赖缺失时的影响面（如 image_description） */
  impact?: string
}

/** 单个功能的就绪度 */
export interface FeatureReadiness {
  ready: boolean
  missing: ReadinessMissing[]
  optional_missing: ReadinessMissing[]
}

export type FeatureKey = 'file_parsing' | 'wiki_compile' | 'report' | 'task' | 'knowledge_search'

/** Overall health check response from /health */
export interface HealthStatus {
  status: 'ok' | 'degraded' | 'down'
  services: Record<string, ServiceHealth>
  /** 功能就绪度矩阵（0.4 §7.2；Python 服务 down 时无此段） */
  features?: Record<string, FeatureReadiness>
  error?: string
  errorCode?: string
}

/** Extended Error with structured fields (from IPC layer) */
export interface InkCubeIPCError extends Error {
  errorCode?: string
  errorCategory?: ErrorCategory
  errorSuggestion?: string
  errorDetail?: string
}
