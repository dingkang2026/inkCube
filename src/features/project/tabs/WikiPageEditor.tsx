/** src/features/project/tabs/WikiPageEditor.tsx — Wiki page Markdown editor */
import { useState, useEffect } from 'react'
import {
  Save, X, Loader2, AlertTriangle,
} from 'lucide-react'
import s from './WikiTab.module.css'

interface WikiPageEditorProps {
  projectId: string
  page: {
    page_key: string
    title: string
    content: string | null
  }
  onBack: () => void
  onSaved: () => void
}

export function WikiPageEditor({ projectId, page, onBack, onSaved }: WikiPageEditorProps) {
  const [title, setTitle] = useState(page.title)
  const [content, setContent] = useState(page.content || '')
  const [saving, setSaving] = useState(false)
  const [hasChanges, setHasChanges] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const changed = title !== page.title || content !== (page.content || '')
    setHasChanges(changed)
  }, [title, content, page.title, page.content])

  const handleSave = async () => {
    if (!content.trim()) {
      setError('内容不能为空')
      return
    }
    setSaving(true)
    setError('')
    try {
      await window.electronAPI.invoke('wiki:editPage', {
        projectId,
        pageKey: page.page_key,
        content,
        title: title !== page.title ? title : null,
      })
      onSaved()
    } catch (err: any) {
      setError(err?.message || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const handleCancel = () => {
    if (hasChanges) {
      // In production, could show a confirm dialog. For now, just go back.
    }
    onBack()
  }

  return (
    <div className={s.editorPage}>
      {/* Editor header */}
      <div className={s.editorHeader}>
        <input
          className={s.editorTitleInput}
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="页面标题"
        />
        <div className={s.editorActions}>
          {hasChanges && <span className={s.unsavedDot} />}
          <button className={s.editorCancelBtn} onClick={handleCancel}>
            <X size={12} /> 取消
          </button>
          <button
            className={s.editorSaveBtn}
            onClick={handleSave}
            disabled={saving || !hasChanges}
          >
            {saving ? <Loader2 size={12} className={s.spin} /> : <Save size={12} />}
            保存
          </button>
        </div>
      </div>

      {/* Error message */}
      {error && (
        <div className={s.editorError}>
          <AlertTriangle size={13} />
          {error}
        </div>
      )}

      {/* Markdown editor */}
      <div className={s.editorBody}>
        <textarea
          className={s.editorTextarea}
          value={content}
          onChange={(e) => setContent(e.target.value)}
          placeholder="输入 Markdown 内容...&#10;&#10;支持 [[page_key]] 语法创建页面间链接"
          spellCheck={false}
        />
      </div>

      {/* Editor footer with hints */}
      <div className={s.editorFooter}>
        <span className={s.editorHint}>
          支持 Markdown 语法 · 使用 [[page_key]] 创建页面链接 · 保存后页面状态将标记为「用户已编辑」
        </span>
        <span className={s.editorCharCount}>
          {content.length} 字符
        </span>
      </div>
    </div>
  )
}
