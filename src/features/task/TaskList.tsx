import { useTranslation } from 'react-i18next'
import { FolderOpen } from 'lucide-react'
import type { Task } from '../../types'
import type { TaskTab } from './useTasks'
import { TaskItem } from './TaskItem'
import styles from './TaskList.module.css'

interface TaskListProps {
  tasks: Task[]
  activeTaskId: string | null
  activeTab: TaskTab
  loading?: boolean
  creating?: boolean
  onSelect: (id: string) => void
  onNew: () => void
  onNewWithDir?: () => void
  onDelete: (id: string) => void
  onRename: (id: string, title: string) => void
  onArchive?: (id: string) => void
  onUnarchive?: (id: string) => void
  onExport?: (id: string) => void
  onTabChange: (tab: TaskTab) => void
  archivedCount: number
  canCreateTask?: boolean
  runningTaskIds?: Set<string>
  hasMore?: boolean
  onLoadMore?: () => void
}

export function TaskList({
  tasks,
  activeTaskId,
  activeTab,
  loading = false,
  creating = false,
  onSelect,
  onNew,
  onNewWithDir,
  onDelete,
  onRename,
  onArchive,
  onUnarchive,
  onExport,
  onTabChange,
  archivedCount,
  canCreateTask = true,
  runningTaskIds,
  hasMore,
  onLoadMore,
}: TaskListProps) {
  const { t } = useTranslation()

  return (
    <div className={styles.list}>
      <div className={styles.newBtnRow}>
        <button
          className={styles.newBtn}
          onClick={onNew}
          disabled={!canCreateTask || creating}
          title={creating ? t('task.creating') : canCreateTask ? '' : t('errors.codes.taskLimitReached')}
        >
          <span className={styles.newIcon}>+</span>
          {creating ? t('task.creating') : t('task.newTask')}
        </button>
        {onNewWithDir && (
          <button
            className={styles.newDirBtn}
            onClick={onNewWithDir}
            disabled={!canCreateTask || creating}
            title={t('task.selectDirectory')}
          >
            <FolderOpen size={16} />
          </button>
        )}
      </div>

      {/* Tab bar */}
      <div className={styles.tabs}>
        <button
          className={`${styles.tab} ${activeTab === 'active' ? styles.tabActive : ''}`}
          onClick={() => onTabChange('active')}
        >
          {t('task.activeTab')}
        </button>
        <button
          className={`${styles.tab} ${activeTab === 'archived' ? styles.tabActive : ''}`}
          onClick={() => onTabChange('archived')}
        >
          {t('task.archivedTab')}
          {archivedCount > 0 && <span className={styles.badge}>{archivedCount}</span>}
        </button>
      </div>

      {loading && tasks.length === 0 ? (
        <div className={styles.empty}>{t('task.loading')}</div>
      ) : tasks.length === 0 ? (
        <div className={styles.empty}>
          {activeTab === 'archived' ? t('task.archivedEmpty') : t('task.empty')}
        </div>
      ) : (
        <>
          {tasks.map((task) => (
            <TaskItem
              key={task.id}
              task={task}
              isActive={task.id === activeTaskId}
              isRunning={runningTaskIds?.has(task.id) ?? false}
              onSelect={() => onSelect(task.id)}
              onDelete={() => onDelete(task.id)}
              onRename={(title) => onRename(task.id, title)}
              onArchive={onArchive ? () => onArchive(task.id) : undefined}
              onUnarchive={onUnarchive ? () => onUnarchive(task.id) : undefined}
              onExport={onExport ? () => onExport(task.id) : undefined}
            />
          ))}
          {hasMore && onLoadMore && (
            <button className={styles.loadMoreBtn} onClick={onLoadMore}>
              {t('task.loadMore')}
            </button>
          )}
        </>
      )}
    </div>
  )
}
