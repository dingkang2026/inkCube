/** src/features/schedule/ArchiveView.tsx — 档案阅读页（2.11 §8.4，2026-10-23）。
 *  订阅情报档案（{订阅名}·档案.md）的应用内查看页：file:readText 读目标容器内
 *  档案文件 → Markdown 渲染 + Ctrl/Cmd+F 文档内查找（useMarkdownBlockFind + FindBar，
 *  同 ReadView）；「在文件夹中显示」为唯一定位逃生门——产品主张校准：系统打开
 *  （file:openOriginal）仅限用户主动上传的文件，应用生成物一律应用内查看。
 *  返回：管理台恢复选中订阅（scheduleView 'archive' 形态，App 分发）。 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeft, FolderOpen } from 'lucide-react'
import { Markdown } from '../../components/Markdown'
import { FindBar } from '../../components/FindBar'
import { useMarkdownBlockFind } from '../../hooks/useInFileFind'
import s from './ArchiveView.module.css'

interface ArchiveViewProps {
  /** 档案所在目标容器 id（project/baselib 通用文件区，file:readText 的 projectId） */
  targetId: string
  /** 档案文件 id（task_schedules.archive_file_id） */
  fileId: string
  /** 订阅名（档案标题与文件名 = {scheduleName}·档案；文件名后缀为数据格式，
   *  与 scheduler.py 生成名一致，不随语言变化） */
  scheduleName: string
  onBack: () => void
}

export function ArchiveView({ targetId, fileId, scheduleName, onBack }: ArchiveViewProps) {
  const { t } = useTranslation()
  const [content, setContent] = useState('')
  const [absPath, setAbsPath] = useState('')
  const [failReason, setFailReason] = useState<string | null>(null)
  const [failMessage, setFailMessage] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setContent('')
    setAbsPath('')
    setFailReason(null)
    setFailMessage('')
    const load = async () => {
      try {
        const res = await window.electronAPI.invoke('file:readText', {
          projectId: targetId,
          fileId,
          filename: `${scheduleName}·档案.md`,
        }) as {
          success?: boolean; content?: string; abs_path?: string
          reason?: string; message?: string
        }
        if (cancelled) return
        if (res?.success && typeof res.content === 'string') {
          setContent(res.content)
          setAbsPath(res.abs_path || '')
        } else {
          setFailReason(res?.reason || 'read_failed')
          setFailMessage(res?.message || '')
        }
      } catch {
        if (!cancelled) setFailReason('read_failed')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [targetId, fileId, scheduleName])

  // 在文件夹中显示（定位逃生门）：abs_path 由 file:readText 响应携带，加载完成前置灰
  const showInFolder = useCallback(() => {
    if (absPath) void window.electronAPI.invoke('shell:showItemInFolder', absPath)
  }, [absPath])

  // ── 文档内查找（公共基座 useMarkdownBlockFind：叶子块匹配 + 居中滚动 + 闪烁）──
  const reportRef = useRef<HTMLDivElement | null>(null)
  const find = useMarkdownBlockFind(reportRef, content, true)

  // Ctrl/Cmd+F：全内容区页 window 级监听（同 ReadView/ReportPreview 先例）
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === 'f') {
        e.preventDefault()
        e.stopPropagation()
        find.openFind()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [find.openFind])

  if (loading) {
    return <div className={s.page}><p className={s.hint}>{t('common.loading')}</p></div>
  }
  if (failReason) {
    return (
      <div className={s.page}>
        <p className={s.hint}>
          {failReason === 'not_found'
            ? t('schedule.archiveNotFound')
            : failReason === 'too_large'
              ? t('schedule.archiveTooLarge')
              : t('schedule.archiveLoadFailed', { reason: failMessage || failReason })}
        </p>
      </div>
    )
  }

  return (
    <div className={s.page}>
      <header className={s.head}>
        <button
          className={s.backBtn}
          data-testid="archive-back-btn"
          title={t('schedule.back')}
          aria-label={t('schedule.back')}
          onClick={onBack}
        >
          <ArrowLeft size={15} />
        </button>
        <h2 className={s.title}>{scheduleName} · {t('schedule.archiveTitle')}</h2>
        <span className={s.spacer} />
        <button
          className={s.actionBtn}
          data-testid="archive-show-in-folder"
          disabled={!absPath}
          title={t('schedule.showInFolder')}
          onClick={showInFolder}
        >
          <FolderOpen size={13} />{t('schedule.showInFolder')}
        </button>
      </header>
      {find.findOpen && (
        <FindBar
          inputRef={find.inputRef}
          query={find.findQuery}
          count={find.matches.length}
          current={find.safeIdx}
          onQueryChange={find.setFindQuery}
          onPrev={() => find.gotoMatch(find.safeIdx - 1)}
          onNext={() => find.gotoMatch(find.safeIdx + 1)}
          onClose={find.closeFind}
        />
      )}
      <div className={s.report} ref={reportRef} data-testid="archive-report">
        <Markdown content={content} />
      </div>
    </div>
  )
}
