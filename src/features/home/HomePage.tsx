/** src/features/home/HomePage.tsx — Dashboard homepage with status, quick actions, recent activity. */
import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { Check, X, BookOpen, Folder } from 'lucide-react'
import type { HealthStatus } from '../../types/error'
import type { Project } from '../knowledge/types'
import s from './HomePage.module.css'

const SERVICE_LABELS: Record<string, string> = {
  llm: 'LLM',
  vlm: 'VLM',
  embedding: 'Embedding',
  parsing: 'parsing',
}

interface HomePageProps {
  projects: Project[]
  onNavigate: (nav: string) => void
  onOpenSettings: () => void
  onSelectProject: (id: string) => void
}

export function HomePage({
  projects,
  onNavigate,
  onOpenSettings,
  onSelectProject,
}: HomePageProps) {
  const { t } = useTranslation()
  const [health, setHealth] = useState<HealthStatus | null>(null)
  const [healthLoading, setHealthLoading] = useState(true)

  const fetchHealth = useCallback(async () => {
    setHealthLoading(true)
    try {
      const result = await window.electronAPI.invoke('system:getHealth') as HealthStatus
      setHealth(result)
    } catch {
      // Health check failed — treat as down
      setHealth({ status: 'down', services: {}, errorCode: 'PYTHON_SERVICE_DOWN' })
    } finally {
      setHealthLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchHealth()
  }, [fetchHealth])

  // Sort projects by created_at desc, take top 5
  const recentProjects = [...projects]
    .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
    .slice(0, 5)

  const handleStatusClick = (serviceKey: string, status: string) => {
    if (status === 'error') {
      if (serviceKey === 'llm' || serviceKey === 'vlm') {
        onOpenSettings()
      } else {
        onOpenSettings()
      }
    }
  }

  const services = health?.services ? Object.entries(health.services) : []
  const allServiceKeys = ['llm', 'vlm', 'embedding', 'parsing']

  return (
    <div className={s.page}>
      {/* ── Top: Service status ── */}
      <div className={s.sectionTitle}>{t('home.statusTitle')}</div>
      <div className={s.statusGrid}>
        {healthLoading ? (
          allServiceKeys.map((key) => (
            <div key={key} className={s.statusCard}>
              <div className={`${s.statusIcon} ${s.checking}`}>…</div>
              <div className={s.statusInfo}>
                <div className={s.statusName}>{SERVICE_LABELS[key] || key}</div>
                <div className={s.statusMsg}>{t('health.checking')}</div>
              </div>
            </div>
          ))
        ) : health?.status === 'down' && health.errorCode === 'PYTHON_SERVICE_DOWN' ? (
          allServiceKeys.map((key) => (
            <div key={key} className={s.statusCard}>
              <div className={`${s.statusIcon} ${s.error}`}><X size={18} /></div>
              <div className={s.statusInfo}>
                <div className={s.statusName}>{SERVICE_LABELS[key] || key}</div>
                <div className={`${s.statusMsg} ${s.errorMsg}`}>{t('health.pythonDown')}</div>
              </div>
            </div>
          ))
        ) : (
          allServiceKeys.map((key) => {
            const entry = services.find(([k]) => k === key)
            const svc = entry?.[1]
            const isOk = svc?.status === 'ok'
            return (
              <div
                key={key}
                className={`${s.statusCard} ${!isOk ? s.clickable : ''}`}
                onClick={() => !isOk && handleStatusClick(key, svc?.status || 'error')}
              >
                <div className={`${s.statusIcon} ${isOk ? s.ok : s.error}`}>
                  {isOk ? <Check size={18} /> : <X size={18} />}
                </div>
                <div className={s.statusInfo}>
                  <div className={s.statusName}>{SERVICE_LABELS[key] || key}</div>
                  <div className={`${s.statusMsg} ${!isOk ? s.errorMsg : ''}`}>
                    {svc?.message || (isOk ? t('health.ok') : t('health.error'))}
                  </div>
                </div>
              </div>
            )
          })
        )}
      </div>

      {/* ── Middle: Quick start ── */}
      <div className={s.sectionTitle}>{t('home.quickTitle')}</div>
      <div className={s.quickGrid}>
        <div className={s.quickCard} onClick={() => onNavigate('tasks')}>
          <span className={s.quickIcon}><BookOpen size={24} /></span>
          <div className={s.quickText}>
            <span className={s.quickLabel}>{t('home.quickTasks')}</span>
            <span className={s.quickDesc}>{t('home.quickTasksDesc')}</span>
          </div>
        </div>
        <div className={s.quickCard} onClick={() => onNavigate('projects')}>
          <span className={s.quickIcon}><Folder size={24} /></span>
          <div className={s.quickText}>
            <span className={s.quickLabel}>{t('home.quickProject')}</span>
            <span className={s.quickDesc}>{t('home.quickProjectDesc')}</span>
          </div>
        </div>
      </div>

      {/* ── Bottom: Recent activity ── */}
      <div className={s.sectionTitle}>{t('home.recentTitle')}</div>
      <div className={s.recentGrid}>
        {/* Recent projects */}
        <div className={s.recentSection}>
          <div className={s.sectionTitle} style={{ fontSize: 12, marginBottom: 8 }}>
            {t('home.recentProjects')}
          </div>
          <div className={s.recentList}>
            {recentProjects.length === 0 ? (
              <div className={s.recentEmpty}>{t('home.noProjects')}</div>
            ) : (
              <>
                {recentProjects.map((project) => (
                  <div
                    key={project.id}
                    className={s.recentItem}
                    onClick={() => {
                      onSelectProject(project.id)
                      onNavigate('projects')
                    }}
                  >
                    <span className={s.recentItemIcon}><Folder size={14} /></span>
                    <span className={s.recentItemText}>{project.name}</span>
                    <span className={s.recentItemMeta}>
                      {project.file_count} {t('knowledge.files')}
                    </span>
                  </div>
                ))}
                {projects.length > 5 && (
                  <button className={s.recentMore} onClick={() => onNavigate('projects')}>
                    {t('home.viewAll')}
                  </button>
                )}
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
