/** src/features/project/tabs/CompileDialog.tsx — Wiki compile progress dialog
 *
 * 连续编译：展示逐文件进度与结果摘要，processing 态可停止；发现项随逐文件摄入累积。 */
import { useTranslation } from 'react-i18next'
import { FileText, CheckCircle, AlertCircle, Loader2, XCircle, SkipForward, X } from 'lucide-react'
import s from './WikiTab.module.css'

interface FileSummary {
  file_id: string
  filename: string
  pages_created?: number
  pages_updated?: number
  pages_failed?: number
  reviews_count?: number
  pages_touched?: string[]
  skipped?: boolean
  error?: string
}

interface CompileDialogProps {
  task: {
    id: string
    status: string
    pages_total: number
    pages_completed: number
    pages_failed: number
    error_message: string | null
  } | null
  ingestSummaries: FileSummary[]
  onStop: () => void
  onClose?: () => void
}

export function CompileDialog({
  task,
  ingestSummaries,
  onStop,
  onClose,
}: CompileDialogProps) {
  const { t } = useTranslation()
  if (!task) return null

  const isStopped = task.status === 'stopped'
  const isCompleted = task.status === 'completed'
  const isProcessing = ['queued', 'ingesting', 'storing'].includes(task.status)

  // Deduplicate summaries by file_id (keep last entry for re-compiles)
  const dedupedSummaries = (() => {
    const seen = new Map<string, number>()
    ingestSummaries.forEach((s, i) => {
      if (s.file_id) seen.set(s.file_id, i)
    })
    return ingestSummaries.filter((s, i) => !s.file_id || seen.get(s.file_id) === i)
  })()

  // Count results
  const successCount = dedupedSummaries.filter(s => !s.skipped && !s.error).length
  const errorCount = dedupedSummaries.filter(s => !!s.error).length
  const skippedCount = dedupedSummaries.filter(s => s.skipped).length

  return (
    <div className={s.compileDialogOverlay}>
      <div className={s.compileDialogBox}>
        <div className={s.compileDialogHeader}>
          {isProcessing && <Loader2 size={16} className={s.spin} />}
          {isCompleted && <CheckCircle size={16} className={s.compileDialogCompleteIcon} />}
          {isStopped && <XCircle size={16} className={s.compileDialogStoppedIcon} />}
          <span>{t('project.wiki.compileTitle')}</span>
          {onClose && (
            <button
              className={s.compileDialogCloseIcon}
              onClick={onClose}
              title={isProcessing ? t('project.wiki.compileMinimizeTip') : t('common.close')}
            >
              <X size={14} />
            </button>
          )}
        </div>

        {/* Progress bar */}
        <div className={s.compileDialogProgress}>
          <div
            className={`${s.compileDialogProgressBar} ${task.pages_total === 0 && isProcessing ? s.compileDialogProgressBarIndeterminate : ''}`}
            style={{
              width: task.pages_total > 0
                ? `${Math.round((task.pages_completed / task.pages_total) * 100)}%`
                : undefined,
            }}
          />
        </div>
        <div className={s.compileDialogStats}>
          {task.pages_total > 0
            ? t('project.wiki.compileProgress', { done: task.pages_completed, total: task.pages_total })
            : isProcessing
              ? t('project.wiki.compileProgressCalculating', { defaultValue: '正在计算任务规模...' })
              : t('project.wiki.compileProgress', { done: 0, total: 0 })}
          {errorCount > 0 && (
            <span className={s.compileDialogErrorCount}>
              {' · '}
              <AlertCircle size={12} /> {errorCount} {t('project.wiki.compileErrors', { defaultValue: '个失败' })}
            </span>
          )}
          {skippedCount > 0 && (
            <span className={s.compileDialogSkippedCount}>
              {' · '}
              <SkipForward size={12} /> {skippedCount} {t('project.wiki.compileSkippedCount', { defaultValue: '个跳过' })}
            </span>
          )}
        </div>

        {/* All file results list */}
        {dedupedSummaries.length > 0 && (
          <div className={s.compileDialogFileList}>
            {dedupedSummaries.map((summary, idx) => (
              <div key={summary.file_id || idx} className={s.compileDialogFileItem}>
                <div className={s.compileDialogFileHeader}>
                  <FileText size={13} />
                  <span className={s.compileDialogFileName}>{summary.filename}</span>
                  {summary.skipped ? (
                    <span className={s.compileDialogSkipped}>
                      <SkipForward size={11} /> {t('project.wiki.compileSkipped')}
                    </span>
                  ) : summary.error ? (
                    <span className={s.compileDialogError}>
                      <AlertCircle size={11} /> {t('project.wiki.compileError')}
                    </span>
                  ) : (
                    <span className={s.compileDialogDone}>
                      <CheckCircle size={11} /> {t('project.wiki.compileFileDone')}
                    </span>
                  )}
                </div>
                {/* Error detail */}
                {summary.error && (
                  <div className={s.compileDialogErrorDetail}>{summary.error}</div>
                )}
                {/* Stats for successful files */}
                {!summary.skipped && !summary.error && (
                  <div className={s.compileDialogFileStats}>
                    {(summary.pages_created ?? 0) > 0 && (
                      <span className={s.statCreated}>
                        +{summary.pages_created} {t('project.wiki.compileCreated')}
                      </span>
                    )}
                    {(summary.pages_updated ?? 0) > 0 && (
                      <span className={s.statUpdated}>
                        ~{summary.pages_updated} {t('project.wiki.compileUpdated')}
                      </span>
                    )}
                    {(summary.reviews_count ?? 0) > 0 && (
                      <span className={s.statReviews}>
                        {summary.reviews_count} {t('project.wiki.compileReviews')}
                      </span>
                    )}
                    {summary.pages_touched && summary.pages_touched.length > 0 && (
                      <div className={s.compileDialogPages}>
                        {summary.pages_touched.slice(0, 5).map((key: string) => (
                          <span key={key} className={s.pageTag}>{key}</span>
                        ))}
                        {summary.pages_touched.length > 5 && (
                          <span className={s.pageTagMore}>+{summary.pages_touched.length - 5}</span>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {/* Processing state — no file results yet */}
        {isProcessing && dedupedSummaries.length === 0 && (
          <div className={s.compileDialogProcessingHint}>
            <Loader2 size={14} className={s.spin} />
            <span>{t('project.wiki.compileProcessing', { defaultValue: '正在处理第一个文件...' })}</span>
          </div>
        )}

        {/* Completed state */}
        {isCompleted && (
          <div className={s.compileDialogComplete}>
            {errorCount > 0 ? (
              <>
                <AlertCircle size={16} className={s.compileDialogStoppedIcon} />
                <span>
                  {t('project.wiki.compileDoneWithErrors', {
                    defaultValue: `编译完成: ${successCount} 成功, ${errorCount} 失败`,
                    success: successCount,
                    errors: errorCount,
                  })}
                </span>
              </>
            ) : (
              <>
                <CheckCircle size={16} className={s.compileDialogCompleteIcon} />
                <span>{t('project.wiki.compileAllDone')}</span>
              </>
            )}
          </div>
        )}

        {/* Stopped state */}
        {isStopped && (
          <div className={s.compileDialogComplete}>
            <XCircle size={16} className={s.compileDialogStoppedIcon} />
            <span>{t('project.wiki.compileIngestStopped')}</span>
          </div>
        )}

        {/* Task-level error */}
        {task.error_message && (
          <div className={s.compileDialogTaskError}>
            <AlertCircle size={13} />
            <span>{task.error_message}</span>
          </div>
        )}

        {/* Action buttons */}
        <div className={s.compileDialogActions}>
          {isProcessing && (
            <>
              <button className={s.compileDialogStopBtn} onClick={onStop}>
                {t('project.wiki.compileStop')}
              </button>
              {onClose && (
                <button className={s.compileDialogMinimizeBtn} onClick={onClose}>
                  {t('project.wiki.compileMinimize')}
                </button>
              )}
            </>
          )}
          {(isCompleted || isStopped) && (
            <button className={s.compileDialogCloseBtn} onClick={onClose}>
              {t('project.wiki.compileClose')}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
