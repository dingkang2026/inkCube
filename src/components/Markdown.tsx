import ReactMarkdown from 'react-markdown'
import type { Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Mermaid } from './Mermaid'

/** Custom link renderer: open all links in external browser */
const SafeLink: Components['a'] = ({ href, children, ...rest }) => {
  const handleClick = (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (href) {
      // Use Electron's shell.openExternal via a data attribute
      // The Electron main process will intercept this via will-navigate
      window.open(href, '_blank', 'noopener,noreferrer')
    }
  }
  return (
    <a href={href} onClick={handleClick} target="_blank" rel="noopener noreferrer" {...rest}>
      {children}
    </a>
  )
}

/** Check if an image URL is allowed by CSP (self / data: / inkcube:) */
function isSafeImageSrc(src: string): boolean {
  if (!src) return false
  if (src.startsWith('data:') || src.startsWith('inkcube:')) return true
  // Relative URLs are same-origin → OK
  if (src.startsWith('/') || src.startsWith('./') || src.startsWith('../')) return true
  return false
}

/** Custom img renderer: block external URLs, show placeholder + link */
const SafeImage: Components['img'] = ({ src, alt, ...rest }) => {
  if (!src) return null
  if (isSafeImageSrc(src)) {
    return <img src={src} alt={alt} {...rest} />
  }
  // External image blocked by CSP — show a clickable link instead
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        padding: '4px 8px',
        background: '#f0f0f0',
        borderRadius: 4,
        fontSize: '0.85em',
        color: '#666',
      }}
    >
      🖼️ <a href={src} target="_blank" rel="noopener noreferrer" onClick={(e) => { e.preventDefault(); window.open(src, '_blank', 'noopener,noreferrer') }}>{alt || '外部图片'}</a>
      <span style={{ color: '#999' }}>(CSP 阻止直接加载)</span>
    </span>
  )
}

interface MarkdownProps {
  content: string
  className?: string
}

interface Segment {
  type: 'text' | 'mermaid'
  content: string
}

/** Extract fenced mermaid blocks so they bypass ReactMarkdown's <pre><code> pipeline */
function splitSegments(src: string): Segment[] {
  const re = /^```mermaid\n([\s\S]*?)\n```/gm
  const segments: Segment[] = []
  let last = 0
  let m: RegExpExecArray | null

  while ((m = re.exec(src)) !== null) {
    if (m.index > last) {
      segments.push({ type: 'text', content: src.slice(last, m.index) })
    }
    segments.push({ type: 'mermaid', content: m[1] })
    last = m.index + m[0].length
  }

  if (last < src.length) {
    segments.push({ type: 'text', content: src.slice(last) })
  }

  return segments
}

export function Markdown({ content, className }: MarkdownProps) {
  const segments = splitSegments(content)

  return (
    <div className={`markdown ${className || ''}`}>
      {segments.map((seg, i) =>
        seg.type === 'mermaid' ? (
          <Mermaid key={i} chart={seg.content} />
        ) : (
          <ReactMarkdown
            key={i}
            remarkPlugins={[remarkGfm]}
            components={{ img: SafeImage, a: SafeLink }}
          >
            {seg.content}
          </ReactMarkdown>
        )
      )}
    </div>
  )
}
