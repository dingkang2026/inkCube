/** Shared utility functions for task feature */

import {
  File,
  FileCode,
  FileImage,
  FileJson,
  FileSpreadsheet,
  FileText,
  Presentation,
  Terminal,
  type LucideIcon,
} from 'lucide-react'

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`
}

/** 文件名/扩展名 → lucide 图标组件（任务域 FileTree 与知识域 FileUploader 共用；2026-10-19 图标语言统一，替代 emoji 映射） */
export function fileIcon(nameOrExt: string): LucideIcon {
  // 'file.pdf' / '.pdf' / 'pdf' 三种形态均取末段扩展名
  const ext = nameOrExt.split('.').pop()?.toLowerCase() || ''
  const icons: Record<string, LucideIcon> = {
    py: FileCode, js: FileCode, ts: FileCode, tsx: FileCode, jsx: FileCode, html: FileCode, css: FileCode,
    json: FileJson, yaml: FileJson, yml: FileJson, toml: FileJson,
    md: FileText, txt: FileText, docx: FileText, doc: FileText, pdf: FileText,
    csv: FileSpreadsheet, xlsx: FileSpreadsheet, xls: FileSpreadsheet,
    pptx: Presentation, ppt: Presentation,
    png: FileImage, jpg: FileImage, jpeg: FileImage, gif: FileImage, svg: FileImage,
    sh: Terminal, bat: Terminal,
  }
  return icons[ext] || File
}
