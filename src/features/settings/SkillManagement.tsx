import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import {
  BookOpen, Brain, Check, FileSearch, FileText, FolderOpen, GitBranch, Globe,
  Hammer, Map as MapIcon, Package, Play, Puzzle, Search, Sparkles, Terminal,
  Trash2, TriangleAlert, Wrench, type LucideIcon,
} from 'lucide-react'
import styles from './SkillManagement.module.css'
import { useConfirm } from '../../components/ConfirmDialog'
import { useErrorHandler } from '../../hooks/useErrorHandler'
import { usePagedList } from '../../hooks/usePagedList'
import { useToast } from '../knowledge/Toast'
import { apiErrorText } from '../../i18n/errorCodes'

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
}

// 内置工具图标映射（与 python/routers/skills.py BUILTIN_TOOLS 同序同键；图标属 UI 语言，后端不再下发 emoji）
const TOOL_ICONS: Record<string, LucideIcon> = {
  code_executor: Play,
  shell: Terminal,
  file_ops: FolderOpen,
  code_search: Search,
  web_search: Globe,
  code_lint: FileSearch,
  code_format: Sparkles,
  package_manager: Package,
  project_scaffold: Hammer,
  knowledge_search: BookOpen,
  doc_generator: FileText,
  repo_map: MapIcon,
  skill_runner: Puzzle,
  git_ops: GitBranch,
  lsp_client: Brain,
}

export function SkillManagement() {
  const { t } = useTranslation()
  const confirmDialog = useConfirm()
  const handleError = useErrorHandler()
  const showToast = useToast()
  // 技能列表分页（0.1dev-guide 统一约定）：50/页「加载更多」，挂载即拉
  const {
    items: skills, hasMore, loadingMore, loading, total: skillsTotal,
    error: listError,
    reload: reloadSkills, loadMore,
  } = usePagedList<Skill>({ channel: 'skills:list', itemsKey: 'skills' })
  // 列表加载失败提示（对齐 FormsTab m-3：静默吞掉会让列表空白且无任何提示）
  useEffect(() => {
    if (listError) showToast(`${t('common.listLoadFailed')}: ${listError}`, 'error')
  }, [listError, showToast, t])
  const [builtinTools, setBuiltinTools] = useState<BuiltinTool[]>([])
  const [installSource, setInstallSource] = useState('')
  const [installType, setInstallType] = useState<'local' | 'git'>('local')
  const [installing, setInstalling] = useState(false)
  const [installError, setInstallError] = useState('')
  const [showInstallConfirm, setShowInstallConfirm] = useState(false)
  // 安装确认：Esc = 取消（强制交互确认语义 0.3 §5.2，同 ConfirmDialog）
  useEffect(() => {
    if (!showInstallConfirm) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setShowInstallConfirm(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [showInstallConfirm])

  const loadBuiltinTools = useCallback(async () => {
    try {
      const res = await window.electronAPI.invoke('skills:builtinTools') as { tools: BuiltinTool[] }
      setBuiltinTools(res.tools || [])
    } catch (err) {
      handleError(err)
    }
  }, [handleError])

  useEffect(() => {
    loadBuiltinTools()
  }, [loadBuiltinTools])

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
      }) as { skill?: Skill; error?: string; code?: string; type?: string; sub_skills?: string[]; example?: string }
      if (result.error) {
        if (result.type === 'collection_detected' && result.sub_skills?.length) {
          // D1 集合目录：引导性提示，插值子 Skill 列表与示例路径
          setInstallError(t('errors.codes.collectionDetected', {
            skills: result.sub_skills.join(', '),
            example: result.example || '',
          }))
        } else {
          setInstallError(apiErrorText(result.code, result.error, t))
        }
      } else {
        setInstallSource('')
        await reloadSkills()
      }
    } catch (err) {
      setInstallError(String(err))
    } finally {
      setInstalling(false)
    }
  }

  const handleDelete = async (skillId: string) => {
    if (!(await confirmDialog({ title: t('skills.delete'), message: t('skills.confirmDelete') }))) return
    try {
      await window.electronAPI.invoke('skills:delete', skillId)
      await reloadSkills()
    } catch (err) {
      handleError(err)
    }
  }

  const handleToggle = async (skill: Skill) => {
    try {
      await window.electronAPI.invoke('skills:toggle', {
        skillId: skill.id,
        enabled: !skill.enabled,
      })
      await reloadSkills()
    } catch (err) {
      handleError(err)
    }
  }

  const handleConfirm = async (skillId: string) => {
    try {
      await window.electronAPI.invoke('skills:confirm', skillId)
      await reloadSkills()
    } catch (err) {
      handleError(err)
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

      {/* Install confirmation dialog */}
      {showInstallConfirm && (
        <div className={styles.installConfirmOverlay} onClick={() => setShowInstallConfirm(false)}>
          <div className={styles.installConfirmDialog} onClick={(e) => e.stopPropagation()}>
            <h4 className={styles.installConfirmTitle}><TriangleAlert size={16} /> {t('skills.installConfirmTitle')}</h4>
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
            {builtinTools.map((tool) => {
              const Icon = TOOL_ICONS[tool.name] || Wrench
              return (
                <div key={tool.name} className={styles.builtinItem}>
                  <span className={styles.builtinIcon}><Icon size={14} /></span>
                  <span className={styles.builtinName}>{tool.name.replace(/_/g, '-')}</span>
                  <span className={styles.builtinDesc}>{tool.description}</span>
                </div>
              )
            })}
          </div>
        </div>

        {/* Skills（含 3 个内置；安装表单归本栏，安装是 Skill 的动作） */}
        <div className={styles.column}>
          <h4 className={styles.sectionTitle}>
            {t('skills.listTitle')} ({skillsTotal ?? skills.length})
          </h4>

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
                  <FolderOpen size={16} />
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
                    {skill.source_type === 'builtin' ? (
                      <span className={styles.builtinBadge}>{t('skills.builtinBadge')}</span>
                    ) : (
                      <span className={styles.sourceBadge}>{skill.source_type}</span>
                    )}
                    {skill.needs_confirm && (
                      <span className={styles.unconfirmedBadge} title={t('skills.confirmHint')}><TriangleAlert size={12} /></span>
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
                        <Check size={12} /> {t('skills.confirmAction')}
                      </button>
                    )}
                    <label
                      className={styles.toggle}
                      title={skill.source_type === 'builtin' ? t('skills.builtinProtected') : undefined}
                    >
                      <input
                        type="checkbox"
                        checked={skill.enabled}
                        disabled={skill.source_type === 'builtin'}
                        onChange={() => handleToggle(skill)}
                      />
                      <span className={styles.toggleSlider}></span>
                    </label>
                    {/* 内置 skill 不可卸载（后端同样拒绝，2.8 §9.1）；仅可启停 */}
                    {skill.source_type !== 'builtin' && (
                      <button className={styles.deleteBtn} onClick={() => handleDelete(skill.id)} title={t('skills.delete')}>
                        <Trash2 size={14} />
                      </button>
                    )}
                  </div>
                </div>
              ))}
              {hasMore && (
                <button
                  className={styles.loadMoreBtn}
                  onClick={() => { void loadMore() }}
                  disabled={loadingMore}
                >
                  {loadingMore ? t('common.loading') : t('common.loadMore')}
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
