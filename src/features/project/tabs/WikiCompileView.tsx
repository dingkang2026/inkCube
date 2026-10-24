/** src/features/project/tabs/WikiCompileView.tsx — 汇编成册视图（V2 E3 整理层主入口,0.6 §3.5）
 *
 * 薄容器：待汇编 CTA + CompilePanel 原样复用（逐文件明细逻辑不重写）。
 * 明细开合复用父级 showDetailPanel 语义（顶栏「查看详情」/触发汇编同一开关，
 * 折叠后经「查看详情」重开）；顶栏状态条仍在 WikiTab 全页可见，本视图不重复承载。 */
import { useTranslation } from 'react-i18next'
import { BookMarked, ListOrdered, Loader2, Play, RefreshCw } from 'lucide-react'
import { CompilePanel } from './CompilePanel'
import type { CompileTask } from './WikiTab'
import s from './WikiTab.module.css'

interface WikiCompileViewProps {
  /** 待汇编文件数（listPages.pending_compile_count） */
  pendingCount: number
  /** 最近一次汇编任务快照（父级 #4 轮询下发） */
  compileTask: CompileTask | null
  ingestSummaries: any[]
  /** 乐观触发窗口（已点开始、首个任务快照未回） */
  compiling: boolean
  /** 明细面板展开态（复用父级 showDetailPanel） */
  detailOpen: boolean
  onOpenDetail: () => void
  onCollapseDetail: () => void
  onCompile: () => void
  /** 清库重汇编（force，父级二次确认） */
  onForceRecompile: () => void
}

export function WikiCompileView({
  pendingCount, compileTask, ingestSummaries, compiling,
  detailOpen, onOpenDetail, onCollapseDetail, onCompile, onForceRecompile,
}: WikiCompileViewProps) {
  const { t } = useTranslation()
  const isProcessing = compiling
    || (compileTask != null && ['queued', 'ingesting', 'storing'].includes(compileTask.status))

  // 空闲且无任务快照：纯入口态（CTA / 空态）
  if (!compileTask && !isProcessing) {
    if (pendingCount > 0) {
      return (
        <div className={s.compileView}>
          <div className={s.compileViewHero}>
            <p className={s.compileViewHint}>
              {t('project.wiki.compileViewPendingHint', { count: pendingCount, defaultValue: `${pendingCount} 个文件待汇编` })}
            </p>
            <button className={s.compileViewStart} onClick={onCompile}>
              <Play size={15} />
              {t('project.wiki.compileViewStart', { count: pendingCount, defaultValue: `开始汇编（${pendingCount}）` })}
            </button>
          </div>
        </div>
      )
    }
    return (
      <div className={s.compileView}>
        <div className={s.compileViewEmpty}>
          <BookMarked size={28} className={s.compileViewEmptyIcon} />
          <p>{t('project.wiki.compileViewEmpty', { defaultValue: '暂无待汇编文件。上传资料后，这里会提示可汇编的内容。' })}</p>
          <button className={s.compileViewGhost} onClick={onForceRecompile} title={t('project.wiki.resetTip')}>
            <RefreshCw size={13} />
            {t('project.wiki.recompile', { defaultValue: '重新汇编' })}
          </button>
        </div>
      </div>
    )
  }

  // 有任务（运行中/终态）：CompilePanel 原样复用 + 终态增量再汇编入口
  return (
    <div className={s.compileView}>
      {detailOpen ? (
        compileTask ? (
          <CompilePanel
            task={compileTask}
            ingestSummaries={ingestSummaries}
            onCollapse={onCollapseDetail}
          />
        ) : (
          /* 乐观窗口（已点开始、首个轮询快照未回）：CompilePanel task=null 不渲染，
             此处占位防正文空白一个轮询周期（CodeReview Minor #2） */
          <div className={s.compileViewPending}>
            <Loader2 size={16} className={s.spin} />
            <span>{t('project.wiki.compileProcessing', { defaultValue: '正在处理第一个文件...' })}</span>
          </div>
        )
      ) : (
        <button className={s.compileViewGhost} onClick={onOpenDetail}>
          <ListOrdered size={13} />
          {t('project.wiki.viewDetails', { defaultValue: '查看详情' })}
        </button>
      )}
      {!isProcessing && pendingCount > 0 && (
        <div className={s.compileViewHero}>
          <p className={s.compileViewHint}>
            {t('project.wiki.compileViewPendingHint', { count: pendingCount, defaultValue: `${pendingCount} 个文件待汇编` })}
          </p>
          <button className={s.compileViewStart} onClick={onCompile}>
            <Play size={15} />
            {t('project.wiki.compileViewStart', { count: pendingCount, defaultValue: `开始汇编（${pendingCount}）` })}
          </button>
        </div>
      )}
    </div>
  )
}
