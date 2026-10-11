/** src/hooks/useErrorHandler.ts — Unified error handling hook.
 *
 * Inspects an error for structured InkCubeError fields (errorCategory, etc.)
 * and shows an appropriate toast with action buttons.
 */
import { useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { useToast } from '../features/knowledge/Toast'
import type { ErrorCategory } from '../types/error'

interface StructuredError extends Error {
  errorCode?: string
  errorCategory?: ErrorCategory
  errorSuggestion?: string
  errorDetail?: string
}

/** errorCode → 设置页锚点 section（0.4 §7.3 第三层：跳设置带锚点直达） */
const CODE_SETTINGS_SECTION: Record<string, string> = {
  LLM_NOT_CONFIGURED: 'externalModel',
  VLM_NOT_CONFIGURED: 'externalModel',
  LLM_INVALID_KEY: 'externalModel',
  EMBEDDING_MODEL_MISSING: 'internalModel',
  PARSING_MODEL_MISSING: 'internalModel',
}

export function useErrorHandler() {
  const showToast = useToast()
  const { t } = useTranslation()

  return useCallback(
    (error: unknown) => {
      const err = error as StructuredError

      // Check for structured InkCubeError from IPC layer
      if (err.errorCategory) {
        switch (err.errorCategory) {
          case 'CONFIG':
          case 'CREDENTIAL': {
            const section = CODE_SETTINGS_SECTION[err.errorCode ?? '']
            showToast(err.message || t('errors.configFallback'), 'error', {
              suggestion: err.errorSuggestion || t('errors.configSuggestion'),
              action: {
                label: t('errors.goSettings'),
                onClick: () => {
                  // Dispatch custom event — App.tsx listens and navigates (detail.section = 锚点)
                  window.dispatchEvent(
                    new CustomEvent('app:navigate-settings', section ? { detail: { section } } : undefined),
                  )
                },
              },
              detail: err.errorDetail,
            })
            break
          }
          case 'NETWORK':
            showToast(err.message || t('errors.networkFallback'), 'error', {
              suggestion: t('errors.networkSuggestion'),
              detail: err.errorDetail,
            })
            break

          case 'RESOURCE': {
            // 模型缺失类（ensure_ready 第三层拦截）→ 跳设置对应锚点（0.4 §7.3）
            const section = CODE_SETTINGS_SECTION[err.errorCode ?? '']
            showToast(err.message || t('errors.resourceFallback'), 'error', {
              suggestion: err.errorSuggestion || t('errors.resourceSuggestion'),
              ...(section
                ? {
                    action: {
                      label: t('errors.goSettings'),
                      onClick: () => {
                        window.dispatchEvent(
                          new CustomEvent('app:navigate-settings', { detail: { section } }),
                        )
                      },
                    },
                  }
                : {}),
              detail: err.errorDetail,
            })
            break
          }

          case 'INTERNAL':
            showToast(err.message || t('errors.internalFallback'), 'error', {
              suggestion: t('errors.internalSuggestion'),
              action: {
                label: t('errors.viewDetails'),
                onClick: () => {
                  window.dispatchEvent(
                    new CustomEvent('app:show-logs', { detail: err.errorDetail })
                  )
                },
              },
              detail: err.errorDetail,
            })
            break

          case 'PROCESS':
            showToast(err.message || t('errors.processFallback'), 'error', {
              suggestion: err.errorSuggestion || t('errors.processSuggestion'),
            })
            break

          default:
            showToast(err.message || String(err), 'error')
        }
      } else {
        // Non-structured error — fallback display
        const msg = err.message || String(err)
        // Check if it's a Python backend error in the response body
        if (msg.includes('Python backend error')) {
          showToast(msg, 'error', {
            suggestion: t('errors.pythonErrorSuggestion'),
          })
        } else {
          showToast(msg, 'error')
        }
      }
    },
    [showToast, t],
  )
}
