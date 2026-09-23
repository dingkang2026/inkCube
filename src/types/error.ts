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

/** Overall health check response from /health */
export interface HealthStatus {
  status: 'ok' | 'degraded' | 'down'
  services: Record<string, ServiceHealth>
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
