import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import styles from './SkillManagement.module.css'

interface Skill {
  id: string
  name: string
  description: string
  source_type: string
  source_path: string
  install_path: string
  enabled: boolean
  needs_confirm: boolean
  permissions: string[]
  created_at: string
}

interface BuiltinTool {
  name: string
  description: string
  icon: string
}

export function SkillManagement() {
  const { t } = useTranslation()
  const [skills, setSkills] = useState<Skill[]>([])
  const [builtinTools, setBuiltinTools] = useState<BuiltinTool[]>([])
  const [loading, setLoading] = useState(true)
  const [installSource, setInstallSource] = useState('')
  const [installType, setInstallType] = useState<'local' | 'git'>('local')
  const [installing, setInstalling] = useState(false)
  const [installError, setInstallError] = useState('')
  const [showInstallConfirm, setShowInstallConfirm] = useState(false)

  const loadSkills = useCallback(async () => {
    try {
      const res = await window.electronAPI.invoke('skills:list') as { skills: Skill[] }
      setSkills(res.skills || [])
    } catch {
      // ignore
    } finally {
      setLoading(false)
    }
  }, [])

  const loadBuiltinTools = useCallback(async () => {
    try {
      const res = await window.electronAPI.invoke('skills:builtinTools') as { tools: BuiltinTool[] }
      setBuiltinTools(res.tools || [])
    } catch {
      // ignore
    }
  }, [])

  useEffect(() => {
    loadSkills()
    loadBuiltinTools()
  }, [loadSkills, loadBuiltinTools])

  const handleInstallClick = () => {
    if (!installSource.trim()) return
    setShowInstallConfirm(true)
  }

  const handleInstallConfirm = async () => {
    setShowInstallConfirm(false)
    setInstalling(true)
    setInstallError('')
    try {
      const result = await window.electronAPI.invoke('skills:install', {
        source: installSource.trim(),
        sourceType: installType,
      }) as { skill?: Skill; error?: string }
      if (result.error) {
        setInstallError(result.error)
      } else {
        setInstallSource('')
        await loadSkills()
      }
    } catch (err) {
      setInstallError(String(err))
    } finally {
      setInstalling(false)
    }
  }

  const handleDelete = async (skillId: string) => {
    if (!confirm(t('skills.confirmDelete'))) return
    try {
      await window.electronAPI.invoke('skills:delete', skillId)
      await loadSkills()
    } catch {
      // ignore
    }
  }

  const handleToggle = async (skill: Skill) => {
    try {
      await window.electronAPI.invoke('skills:toggle', {
        skillId: skill.id,
        enabled: !skill.enabled,
      })
      await loadSkills()
    } catch {
      // ignore
    }
  }

  const handleConfirm = async (skillId: string) => {
    try {
      await window.electronAPI.invoke('skills:confirm', skillId)
      await loadSkills()
    } catch {
      // ignore
    }
  }

  const handleBrowseLocal = async () => {
    // Use Electron dialog to pick a folder
    try {
      const result = await window.electronAPI.invoke('skills:browseLocal') as { path?: string }
      if (result?.path) {
        setInstallSource(result.path)
      }
    } catch {
      // Fallback: user types path manually
    }
  }

  return (
    <div className={styles.container}>
      <h3 className={styles.heading}>{t('skills.title')}</h3>
      <p className={styles.description}>{t('skills.description')}</p>

      {/* Install form */}
      <div className={styles.installForm}>
        <div className={styles.installRow}>
          <select
            className={styles.typeSelect}
            value={installType}
            onChange={(e) => setInstallType(e.target.value as 'local' | 'git')}
          >
            <option value="local">{t('skills.typeLocal')}</option>
            <option value="git">{t('skills.typeGit')}</option>
          </select>
          <input
            type="text"
            className={styles.sourceInput}
            placeholder={installType === 'local' ? t('skills.localPlaceholder') : t('skills.gitPlaceholder')}
            value={installSource}
            onChange={(e) => setInstallSource(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleInstallClick()}
          />
          {installType === 'local' && (
            <button className={styles.browseBtn} onClick={handleBrowseLocal} title={t('skills.browse')}>
              📁
            </button>
          )}
          <button
            className={styles.installBtn}
            onClick={handleInstallClick}
            disabled={installing || !installSource.trim()}
          >
            {installing ? t('skills.installing') : t('skills.install')}
          </button>
        </div>
        {installError && <div className={styles.error}>{installError}</div>}
      </div>

      {/* Install confirmation dialog */}
      {showInstallConfirm && (
        <div className={styles.installConfirmOverlay} onClick={() => setShowInstallConfirm(false)}>
          <div className={styles.installConfirmDialog} onClick={(e) => e.stopPropagation()}>
            <h4 className={styles.installConfirmTitle}>⚠️ {t('skills.installConfirmTitle')}</h4>
            <p className={styles.installConfirmDesc}>{t('skills.installConfirmDesc')}</p>
            <div className={styles.installConfirmDetails}>
              <div className={styles.installConfirmRow}>
                <span className={styles.installConfirmLabel}>{t('skills.installSourceType')}:</span>
                <span>{installType === 'git' ? t('skills.typeGit') : t('skills.typeLocal')}</span>
              </div>
              <div className={styles.installConfirmRow}>
                <span className={styles.installConfirmLabel}>{t('skills.installConfirmSource')}:</span>
                <span className={styles.installConfirmPath}>{installSource}</span>
              </div>
            </div>
            <p className={styles.installConfirmWarning}>{t('skills.installConfirmWarning')}</p>
            <div className={styles.installConfirmActions}>
              <button className={styles.installConfirmCancel} onClick={() => setShowInstallConfirm(false)}>
                {t('skills.installConfirmCancel')}
              </button>
              <button className={styles.installConfirmOk} onClick={handleInstallConfirm}>
                {t('skills.installConfirmOk')}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Skills list */}
      <div className={styles.twoColumn}>
        {/* Builtin tools */}
        <div className={styles.column}>
          <h4 className={styles.sectionTitle}>
            {t('skills.builtinTitle')} ({builtinTools.length})
          </h4>
          <div className={styles.builtinList}>
            {builtinTools.map((tool) => (
              <div key={tool.name} className={styles.builtinItem}>
                <span className={styles.builtinIcon}>{tool.icon}</span>
                <span className={styles.builtinName}>{tool.name.replace(/_/g, '-')}</span>
                <span className={styles.builtinDesc}>{tool.description}</span>
              </div>
            ))}
          </div>
        </div>

        {/* External skills */}
        <div className={styles.column}>
          <h4 className={styles.sectionTitle}>
            {t('skills.externalTitle')} ({skills.length})
          </h4>
          {loading ? (
            <div className={styles.loading}>{t('skills.loading')}</div>
          ) : skills.length === 0 ? (
            <div className={styles.empty}>{t('skills.empty')}</div>
          ) : (
            <div className={styles.list}>
              {skills.map((skill) => (
                <div key={skill.id} className={`${styles.card} ${!skill.enabled ? styles.disabled : ''}`}>
                  <div className={styles.cardHeader}>
                    <span className={styles.skillName}>{skill.name}</span>
                    <span className={styles.sourceBadge}>{skill.source_type}</span>
                    {skill.needs_confirm && (
                      <span className={styles.unconfirmedBadge} title={t('skills.confirmHint')}>⚠</span>
                    )}
                  </div>
                  {skill.description && (
                    <p className={styles.skillDesc}>{skill.description}</p>
                  )}
                  {skill.permissions && skill.permissions.length > 0 && (
                    <div className={styles.permissions}>
                      <span className={styles.permLabel}>{t('task.skillPermissions')}:</span>
                      {skill.permissions.map((perm) => (
                        <span key={perm} className={styles.permBadge}>{perm}</span>
                      ))}
                    </div>
                  )}
                  <div className={styles.cardActions}>
                    {skill.needs_confirm && (
                      <button
                        className={styles.confirmBtn}
                        onClick={() => handleConfirm(skill.id)}
                        title={t('skills.confirmHint')}
                      >
                        ✓ {t('skills.confirmAction')}
                      </button>
                    )}
                    <label className={styles.toggle}>
                      <input
                        type="checkbox"
                        checked={skill.enabled}
                        onChange={() => handleToggle(skill)}
                      />
                      <span className={styles.toggleSlider}></span>
                    </label>
                    <button className={styles.deleteBtn} onClick={() => handleDelete(skill.id)} title={t('skills.delete')}>
                      🗑
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
