/** src/hooks/useErrorHandler.ts — Unified error handling hook.
 *
 * Inspects an error for structured InkCubeError fields (errorCategory, etc.)
 * and shows an appropriate toast with action buttons.
 */
import { useCallback } from 'react'
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

  return useCallback(
    (error: unknown) => {
      const err = error as StructuredError

      // Check for structured InkCubeError from IPC layer
      if (err.errorCategory) {
        switch (err.errorCategory) {
          case 'CONFIG':
          case 'CREDENTIAL': {
            const section = CODE_SETTINGS_SECTION[err.errorCode ?? '']
            showToast(err.message || '配置缺失', 'error', {
              suggestion: err.errorSuggestion || '请前往设置页配置',
              action: {
                label: '前往设置',
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
            showToast(err.message || '网络错误', 'error', {
              suggestion: '请检查网络连接',
              detail: err.errorDetail,
            })
            break

          case 'RESOURCE': {
            // 模型缺失类（ensure_ready 第三层拦截）→ 跳设置对应锚点（0.4 §7.3）
            const section = CODE_SETTINGS_SECTION[err.errorCode ?? '']
            showToast(err.message || '资源缺失', 'error', {
              suggestion: err.errorSuggestion || '请检查相关资源',
              ...(section
                ? {
                    action: {
                      label: '前往设置',
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
            showToast(err.message || '程序内部错误', 'error', {
              suggestion: '请查看日志或提交 Issue',
              action: {
                label: '查看详情',
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
            showToast(err.message || '服务异常', 'error', {
              suggestion: err.errorSuggestion || '请重启应用',
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
            suggestion: '请查看日志获取更多信息',
          })
        } else {
          showToast(msg, 'error')
        }
      }
    },
    [showToast],
  )
}
