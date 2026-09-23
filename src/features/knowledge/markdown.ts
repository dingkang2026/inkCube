/** src/features/knowledge/markdown.ts */

/**
 * Render markdown text to safe HTML.
 * Supports: tables, bold, italic, inline code, headings, lists, paragraphs.
 * Used by SearchResultList and ChatPanel.
 */
export function renderMarkdown(md: string): string {
  const lines = md.split('\n')
  const htmlParts: string[] = []
  let tableRows: string[] = []
  let inTable = false
  let inList = false

  const flushTable = () => {
    if (tableRows.length === 0) return
    htmlParts.push(buildTableHtml(tableRows))
    tableRows = []
    inTable = false
  }

  const flushList = () => {
    if (inList) {
      htmlParts.push('</ul>')
      inList = false
    }
  }

  for (const line of lines) {
    const trimmed = line.trim()

    // Markdown table rows
    if (trimmed.startsWith('|') && trimmed.endsWith('|')) {
      flushList()
      if (/^\|[\s\-:|]+\|$/.test(trimmed)) {
        inTable = true
        continue
      }
      inTable = true
      tableRows.push(trimmed)
      continue
    }

    // Flush pending table
    if (inTable) flushTable()

    // Unordered list items
    if (/^[-*]\s+/.test(trimmed)) {
      if (!inList) {
        htmlParts.push('<ul>')
        inList = true
      }
      htmlParts.push(`<li>${inlineFormat(trimmed.replace(/^[-*]\s+/, ''))}</li>`)
      continue
    }

    // Flush list if not a list item
    flushList()

    // Headings
    if (/^#{1,6}\s+/.test(trimmed)) {
      const level = trimmed.match(/^(#{1,6})/)?.[1].length || 1
      const text = trimmed.replace(/^#{1,6}\s+/, '')
      htmlParts.push(`<h${level}>${inlineFormat(text)}</h${level}>`)
      continue
    }

    // Non-empty text → paragraph
    if (trimmed) {
      htmlParts.push(`<p>${inlineFormat(trimmed)}</p>`)
    }
  }

  if (inTable) flushTable()
  flushList()

  return htmlParts.join('\n')
}

function buildTableHtml(rows: string[]): string {
  if (rows.length === 0) return ''
  const parseRow = (row: string) =>
    row.slice(1, -1).split('|').map(c => c.trim())

  const headerCells = parseRow(rows[0])
  let html = '<table><thead><tr>'
  for (const cell of headerCells) {
    html += `<th>${inlineFormat(cell)}</th>`
  }
  html += '</tr></thead><tbody>'

  for (let i = 1; i < rows.length; i++) {
    const cells = parseRow(rows[i])
    html += '<tr>'
    for (const cell of cells) {
      html += `<td>${inlineFormat(cell)}</td>`
    }
    html += '</tr>'
  }
  html += '</tbody></table>'
  return html
}

/** Apply inline formatting: bold, italic, inline code */
function inlineFormat(text: string): string {
  let result = escapeHtml(text)
  // Inline code
  result = result.replace(/`([^`]+)`/g, '<code>$1</code>')
  // Bold
  result = result.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
  // Italic
  result = result.replace(/\*(.+?)\*/g, '<em>$1</em>')
  return result
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}
