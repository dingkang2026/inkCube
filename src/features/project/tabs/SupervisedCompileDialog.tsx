/** src/features/project/tabs/SupervisedCompileDialog.tsx — Supervised ingest progress dialog */
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

interface SupervisedCompileDialogProps {
  task: {
    id: string
    status: string
    pages_total: number
    pages_completed: number
    pages_failed: number
    error_message: string | null
  } | null
  ingestSummaries: FileSummary[]
  onContinue: () => void
  onStop: () => void
  onClose?: () => void
}

export function SupervisedCompileDialog({
  task,
  ingestSummaries,
  onContinue,
  onStop,
  onClose,
}: SupervisedCompileDialogProps) {
  const { t } = useTranslation()
  if (!task) return null

  const isWaiting = task.status === 'waiting_review'
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
    <div className={s.supervisedOverlay}>
      <div className={s.supervisedDialog}>
        <div className={s.supervisedHeader}>
          {isProcessing && <Loader2 size={16} className={s.spin} />}
          {isWaiting && <Loader2 size={16} className={s.spin} />}
          {isCompleted && <CheckCircle size={16} className={s.supervisedCompleteIcon} />}
          {isStopped && <XCircle size={16} className={s.supervisedStoppedIcon} />}
          <span>{t('project.wiki.supervisedMode')}</span>
          {onClose && (
            <button
              className={s.supervisedCloseBtn}
              onClick={onClose}
              title={isProcessing ? '最小化（编译继续在后台运行）' : '关闭'}
            >
              <X size={14} />
            </button>
          )}
        </div>

        {/* Progress bar */}
        <div className={s.supervisedProgress}>
          <div
            className={`${s.supervisedProgressBar} ${task.pages_total === 0 && isProcessing ? s.supervisedProgressBarIndeterminate : ''}`}
            style={{
              width: task.pages_total > 0
                ? `${Math.round((task.pages_completed / task.pages_total) * 100)}%`
                : undefined,
            }}
          />
        </div>
        <div className={s.supervisedStats}>
          {task.pages_total > 0
            ? t('project.wiki.supervisedProgress', { done: task.pages_completed, total: task.pages_total })
            : isProcessing
              ? t('project.wiki.supervisedProgressCalculating', { defaultValue: '正在计算任务规模...' })
              : t('project.wiki.supervisedProgress', { done: 0, total: 0 })}
          {errorCount > 0 && (
            <span className={s.supervisedErrorCount}>
              {' · '}
              <AlertCircle size={12} /> {errorCount} {t('project.wiki.supervisedErrors', { defaultValue: '个失败' })}
            </span>
          )}
          {skippedCount > 0 && (
            <span className={s.supervisedSkippedCount}>
              {' · '}
              <SkipForward size={12} /> {skippedCount} {t('project.wiki.supervisedSkippedCount', { defaultValue: '个跳过' })}
            </span>
          )}
        </div>

        {/* All file results list */}
        {dedupedSummaries.length > 0 && (
          <div className={s.supervisedFileList}>
            {dedupedSummaries.map((summary, idx) => (
              <div key={summary.file_id || idx} className={s.supervisedFileItem}>
                <div className={s.supervisedFileHeader}>
                  <FileText size={13} />
                  <span className={s.supervisedFileName}>{summary.filename}</span>
                  {summary.skipped ? (
                    <span className={s.supervisedSkipped}>
                      <SkipForward size={11} /> {t('project.wiki.supervisedSkipped')}
                    </span>
                  ) : summary.error ? (
                    <span className={s.supervisedError}>
                      <AlertCircle size={11} /> {t('project.wiki.supervisedError')}
                    </span>
                  ) : (
                    <span className={s.supervisedDone}>
                      <CheckCircle size={11} /> {t('project.wiki.supervisedDone')}
                    </span>
                  )}
                </div>
                {/* Error detail */}
                {summary.error && (
                  <div className={s.supervisedErrorDetail}>{summary.error}</div>
                )}
                {/* Stats for successful files */}
                {!summary.skipped && !summary.error && (
                  <div className={s.supervisedFileStats}>
                    {(summary.pages_created ?? 0) > 0 && (
                      <span className={s.statCreated}>
                        +{summary.pages_created} {t('project.wiki.supervisedCreated')}
                      </span>
                    )}
                    {(summary.pages_updated ?? 0) > 0 && (
                      <span className={s.statUpdated}>
                        ~{summary.pages_updated} {t('project.wiki.supervisedUpdated')}
                      </span>
                    )}
                    {(summary.reviews_count ?? 0) > 0 && (
                      <span className={s.statReviews}>
                        {summary.reviews_count} {t('project.wiki.supervisedReviews')}
                      </span>
                    )}
                    {summary.pages_touched && summary.pages_touched.length > 0 && (
                      <div className={s.supervisedPages}>
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
          <div className={s.supervisedProcessingHint}>
            <Loader2 size={14} className={s.spin} />
            <span>{t('project.wiki.supervisedProcessing', { defaultValue: '正在处理第一个文件...' })}</span>
          </div>
        )}

        {/* Completed state */}
        {isCompleted && (
          <div className={s.supervisedComplete}>
            {errorCount > 0 ? (
              <>
                <AlertCircle size={16} className={s.supervisedStoppedIcon} />
                <span>
                  {t('project.wiki.supervisedCompletedWithErrors', {
                    defaultValue: `编译完成: ${successCount} 成功, ${errorCount} 失败`,
                    success: successCount,
                    errors: errorCount,
                  })}
                </span>
              </>
            ) : (
              <>
                <CheckCircle size={16} className={s.supervisedCompleteIcon} />
                <span>{t('project.wiki.supervisedAllDone')}</span>
              </>
            )}
          </div>
        )}

        {/* Stopped state */}
        {isStopped && (
          <div className={s.supervisedComplete}>
            <XCircle size={16} className={s.supervisedStoppedIcon} />
            <span>{t('project.wiki.supervisedStopped')}</span>
          </div>
        )}

        {/* Task-level error */}
        {task.error_message && (
          <div className={s.supervisedTaskError}>
            <AlertCircle size={13} />
            <span>{task.error_message}</span>
          </div>
        )}

        {/* Action buttons */}
        <div className={s.supervisedActions}>
          {isWaiting && (
            <>
              <button className={s.supervisedContinueBtn} onClick={onContinue}>
                {t('project.wiki.supervisedContinue')}
              </button>
              <button className={s.supervisedStopBtn} onClick={onStop}>
                {t('project.wiki.supervisedStop')}
              </button>
            </>
          )}
          {isProcessing && (
            <>
              <button className={s.supervisedStopBtn} onClick={onStop}>
                {t('project.wiki.supervisedStop')}
              </button>
              {onClose && (
                <button className={s.supervisedMinimizeBtn} onClick={onClose}>
                  最小化
                </button>
              )}
            </>
          )}
          {(isCompleted || isStopped) && (
            <button className={s.supervisedContinueBtn} onClick={onClose || onContinue}>
              {t('project.wiki.supervisedClose')}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
