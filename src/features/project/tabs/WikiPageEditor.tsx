/** src/features/project/tabs/WikiPageEditor.tsx — Wiki 页面编辑器（textarea 源码编辑）。
 *  Ctrl/Cmd+F 编辑器内查找（2026-10-23）：textarea 选中式——compute 读闭包 content 返回
 *  匹配字符偏移（渲染期一致，不读滞后一拍的 DOM）；locate 定位选区 + 还焦点查找框，
 *  焦点在 textarea（用户编辑中）时跳过定位避免劫持光标；文案 i18n 同批补齐（原硬编码中文）。 */
import { useState, useEffect, useCallback, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Save, X, Loader2, AlertTriangle,
} from 'lucide-react'
import { FindBar } from '../../../components/FindBar'
import { useInFileFind } from '../../../hooks/useInFileFind'
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
  const { t } = useTranslation()
  const [title, setTitle] = useState(page.title)
  const [content, setContent] = useState(page.content || '')
  const [saving, setSaving] = useState(false)
  const [hasChanges, setHasChanges] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const changed = title !== page.title || content !== (page.content || '')
    setHasChanges(changed)
  }, [title, content, page.title, page.content])

  // ── 编辑器内查找（textarea 选中式）：findQueryRef 镜像查询串供 locate 计算选区终点；
  // compute 读闭包 content（与 memo 依赖同源），编辑文本变化即重算命中
  const taRef = useRef<HTMLTextAreaElement | null>(null)
  const findQueryRef = useRef('')
  const editorFind = useInFileFind({
    compute: useCallback((q: string) => {
      // 读闭包 content 而非 taRef.current.value：useMemo 在渲染期执行，commit 前 DOM
      // 仍是旧文本，读 DOM 会使命中滞后一拍且偏移错位
      if (!q || !content) return []
      const out: number[] = []
      let i = content.indexOf(q)
      while (i !== -1) { out.push(i); i = content.indexOf(q, i + q.length) }
      return out
    }, [content]),
    locate: useCallback((pos: number) => {
      const ta = taRef.current
      // 焦点在 textarea = 用户正就地编辑：跳过定位。否则 matches 数组身份变化触发的
      // 重定位会劫持光标/焦点，「定位后就地修改」工作流失效（Enter 导航时焦点在
      // 查找框，不受影响）
      if (!ta || document.activeElement === ta) return
      ta.focus()
      ta.setSelectionRange(pos, pos + findQueryRef.current.length)
      // 焦点还给查找框：textarea 独占焦点会吃掉后续 Enter 导航（闭包引用 editorFind，
      // 执行时已初始化）
      requestAnimationFrame(() => editorFind.inputRef.current?.focus())
    }, []),
  })
  const setEditorQuery = useCallback((v: string) => {
    findQueryRef.current = v
    editorFind.setFindQuery(v)
  }, [editorFind.setFindQuery])

  // Ctrl/Cmd+F 接管：editorPage 为预览面板主内容（与 Reader 互斥挂载），window 级监听
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === 'f') {
        e.preventDefault()
        e.stopPropagation()
        editorFind.openFind()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [editorFind.openFind])

  const handleSave = async () => {
    if (!content.trim()) {
      setError(t('project.wiki.editorEmptyContent'))
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
      setError(err?.message || t('project.wiki.editorSaveFailed'))
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
          placeholder={t('project.wiki.editorTitlePlaceholder')}
        />
        <div className={s.editorActions}>
          {hasChanges && <span className={s.unsavedDot} />}
          <button className={s.editorCancelBtn} onClick={handleCancel}>
            <X size={12} /> {t('project.wiki.cancel')}
          </button>
          <button
            className={s.editorSaveBtn}
            onClick={handleSave}
            disabled={saving || !hasChanges}
          >
            {saving ? <Loader2 size={12} className={s.spin} /> : <Save size={12} />}
            {t('project.wiki.save')}
          </button>
        </div>
      </div>

      {editorFind.findOpen && (
        <FindBar
          inputRef={editorFind.inputRef}
          query={editorFind.findQuery}
          count={editorFind.matches.length}
          current={editorFind.safeIdx}
          onQueryChange={setEditorQuery}
          onPrev={() => editorFind.gotoMatch(editorFind.safeIdx - 1)}
          onNext={() => editorFind.gotoMatch(editorFind.safeIdx + 1)}
          onClose={editorFind.closeFind}
        />
      )}

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
          ref={taRef}
          className={s.editorTextarea}
          value={content}
          onChange={(e) => setContent(e.target.value)}
          placeholder={t('project.wiki.editorContentPlaceholder')}
          spellCheck={false}
        />
      </div>

      {/* Editor footer with hints */}
      <div className={s.editorFooter}>
        <span className={s.editorHint}>
          {t('project.wiki.editorHint')}
        </span>
        <span className={s.editorCharCount}>
          {t('project.wiki.editorCharCount', { count: content.length })}
        </span>
      </div>
    </div>
  )
}
