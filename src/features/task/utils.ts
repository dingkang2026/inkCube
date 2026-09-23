/** Shared utility functions for task feature */

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`
}

export function fileIcon(name: string): string {
  const ext = name.split('.').pop()?.toLowerCase() || ''
  const icons: Record<string, string> = {
    py: '🐍', js: '📜', ts: '📜', tsx: '📜', jsx: '📜',
    json: '📋', yaml: '📋', yml: '📋', toml: '📋',
    md: '📝', txt: '📝', csv: '📊',
    pptx: '📊', ppt: '📊', xlsx: '📊', xls: '📊', docx: '📄', doc: '📄', pdf: '📕',
    html: '🌐', css: '🎨',
    png: '🖼', jpg: '🖼', jpeg: '🖼', gif: '🖼', svg: '🖼',
    sh: '⚙', bat: '⚙',
  }
  return icons[ext] || '📄'
}
