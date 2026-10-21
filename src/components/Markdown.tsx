import React, { useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import type { Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useTranslation } from 'react-i18next'
import { Image } from 'lucide-react'
import { Mermaid } from './Mermaid'

/** Convert [[page_key]] and [[page_key|display text]] to markdown links with wiki: protocol */
function preprocessWikiLinks(content: string): string {
  return content.replace(/\[\[([^\]|]+?)(?:\|([^\]]+?))?\]\]/g, (_match, key: string, label?: string) => {
    const displayText = label || key
    // Use https://wiki/ prefix instead of wiki: protocol (react-markdown v9 strips non-standard protocols)
    return `[${displayText}](https://wiki/${encodeURIComponent(key.trim())})`
  })
}

/** Custom link renderer: open external links in browser, handle wiki: links via callback */
const SafeLink: Components['a'] = ({ href, children, ...rest }) => {
  const handleClick = (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (!href) return

    // Wiki-link: https://wiki/PAGE_KEY format (preprocessed from [[page_key]])
    if (href.startsWith('https://wiki/')) {
      const pageKey = decodeURIComponent(href.slice(13)) // Remove 'https://wiki/'
      window.dispatchEvent(new CustomEvent('wiki-link-click', { detail: { pageKey } }))
      return
    }
    window.open(href, '_blank', 'noopener,noreferrer')
  }

  // Style wiki-links differently from external links
  const isWikiLink = href?.startsWith('https://wiki/')
  return (
    <a
      href={href}
      onClick={handleClick}
      className={isWikiLink ? 'wiki-link' : undefined}
      target={isWikiLink ? undefined : '_blank'}
      rel={isWikiLink ? undefined : 'noopener noreferrer'}
      {...rest}
    >
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
  const { t } = useTranslation()
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
        background: 'var(--bg-tertiary)',
        borderRadius: 4,
        fontSize: '0.85em',
        color: 'var(--text-muted)',
      }}
    >
      <Image size={12} /> <a href={src} target="_blank" rel="noopener noreferrer" onClick={(e) => { e.preventDefault(); window.open(src, '_blank', 'noopener,noreferrer') }}>{alt || t('common.mdExternalImage')}</a>
      <span style={{ color: 'var(--text-muted)' }}>{t('common.mdCspBlocked')}</span>
    </span>
  )
}

interface MarkdownProps {
  content: string
  className?: string
}

interface Segment {
  type: 'text' | 'mermaid' | 'svg'
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

  return splitSvgSegments(segments)
}

// ── Sanitized inline HTML + block SVG (A+) ──
// The system prompt (_PROMPT_RICH_TEXT) invites the LLM to draw <svg> color
// cards and colorize words with <span style>. ReactMarkdown drops raw HTML by
// default (XSS-safe), so we whitelist: block SVGs pass a DOMParser-based
// sanitizer; inline tags on plain lines render as controlled React elements
// (text always escapes through React — HTML strings are never concatenated).

const SVG_BLOCK_RE = /<svg[\s\S]*?(?:<\/svg>|$)/gi

function splitSvgSegments(segments: Segment[]): Segment[] {
  if (!segments.some((s) => s.type === 'text' && s.content.includes('<svg'))) return segments
  const out: Segment[] = []
  for (const seg of segments) {
    if (seg.type !== 'text') {
      out.push(seg)
      continue
    }
    let last = 0
    SVG_BLOCK_RE.lastIndex = 0
    let m: RegExpExecArray | null
    while ((m = SVG_BLOCK_RE.exec(seg.content)) !== null) {
      if (m.index > last) out.push({ type: 'text', content: seg.content.slice(last, m.index) })
      out.push({ type: 'svg', content: m[0] })
      last = m.index + m[0].length
    }
    if (last < seg.content.length) out.push({ type: 'text', content: seg.content.slice(last) })
  }
  return out
}

// ── Block SVG sanitizer ──

const SVG_ELEMENTS = new Set([
  'svg', 'g', 'defs', 'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon',
  'path', 'text', 'tspan', 'title', 'lineargradient', 'radialgradient', 'stop',
])

const SVG_ATTRIBUTES = new Set([
  'xmlns', 'viewbox', 'preserveaspectratio', 'width', 'height',
  'x', 'y', 'x1', 'y1', 'x2', 'y2', 'cx', 'cy', 'r', 'rx', 'ry',
  'points', 'd', 'transform', 'offset', 'id',
  'fill', 'fill-opacity', 'fill-rule', 'stroke', 'stroke-width', 'stroke-opacity',
  'stroke-linecap', 'stroke-linejoin', 'stroke-dasharray', 'opacity',
  'stop-color', 'stop-opacity',
  'font-family', 'font-size', 'font-weight', 'font-style',
  'text-anchor', 'dominant-baseline', 'alignment-baseline',
  'gradientunits', 'gradienttransform', 'spreadmethod',
  'style', 'role', 'aria-hidden',
])

/** Values that must never appear in any kept attribute. */
const SVG_ATTR_VALUE_BLACKLIST = /javascript:|<\s*[a-z!]|&{\s|expression\(/i

function sanitizeSvgNode(el: Element): boolean {
  for (const attr of [...el.attributes]) {
    const name = attr.name.toLowerCase()
    if (!SVG_ATTRIBUTES.has(name) || SVG_ATTR_VALUE_BLACKLIST.test(attr.value)) {
      el.removeAttribute(attr.name)
    }
  }
  for (const child of [...el.children]) {
    if (!SVG_ELEMENTS.has(child.nodeName.toLowerCase()) || !sanitizeSvgNode(child)) {
      child.remove()
    }
  }
  return true
}

/** Parse and whitelist-sanitize an <svg> source. Returns null when unparseable
 * (e.g. mid-stream unclosed tag) so callers can fall back to plain text. */
function sanitizeSvg(source: string): string | null {
  try {
    const doc = new DOMParser().parseFromString(source, 'image/svg+xml')
    const root = doc.documentElement
    if (!root || root.nodeName.toLowerCase() === 'parsererror' || root.nodeName.toLowerCase() !== 'svg') {
      return null
    }
    if (!sanitizeSvgNode(root)) return null
    return new XMLSerializer().serializeToString(root)
  } catch {
    return null
  }
}

function SanitizedSvg({ source }: { source: string }) {
  const cleaned = useMemo(() => sanitizeSvg(source), [source])
  if (!cleaned) {
    // Unclosed mid-stream or malformed — show raw source instead of nothing
    return <pre style={{ fontSize: 'var(--text-sm)', opacity: 0.7, whiteSpace: 'pre-wrap' }}>{source}</pre>
  }
  return <div className="inline-svg" dangerouslySetInnerHTML={{ __html: cleaned }} />
}

// ── Inline tag renderer (span / b / i / code / mark / u / sub / sup / br) ──

const INLINE_HTML_TOKEN_RE = /<\s*(\/?)\s*(span|b|strong|i|em|code|mark|u|sub|sup|br)\b([^>]*)>/gi

interface InlineToken {
  kind: 'open' | 'close' | 'text'
  tag?: string
  attrs?: string
  text?: string
}

function tokenizeInline(line: string): InlineToken[] | null {
  const tokens: InlineToken[] = []
  let last = 0
  INLINE_HTML_TOKEN_RE.lastIndex = 0
  let m: RegExpExecArray | null
  while ((m = INLINE_HTML_TOKEN_RE.exec(line)) !== null) {
    if (m.index > last) tokens.push({ kind: 'text', text: line.slice(last, m.index) })
    if (m[1]) tokens.push({ kind: 'close', tag: m[2].toLowerCase() })
    else tokens.push({ kind: 'open', tag: m[2].toLowerCase(), attrs: m[3] || '' })
    last = m.index + m[0].length
  }
  if (tokens.length === 0) return null
  if (last < line.length) tokens.push({ kind: 'text', text: line.slice(last) })

  // Well-formedness gate: every open tag (except void br) must have a matching
  // close ON THE SAME LINE. Lines like "x<y" (math) lack </b> and fall back to
  // ReactMarkdown, preserving today's behaviour for non-HTML text.
  const open = new Map<string, number>()
  for (const t of tokens) {
    if (t.kind === 'open' && t.tag && t.tag !== 'br') open.set(t.tag, (open.get(t.tag) || 0) + 1)
    if (t.kind === 'close' && t.tag) {
      const n = (open.get(t.tag) || 0) - 1
      if (n < 0) return null // stray close — not authored HTML, fall back
      open.set(t.tag, n)
    }
  }
  if ([...open.values()].some((n) => n > 0)) return null
  return tokens
}

/** CSS declarations the LLM may set on <span style>. Visual-only: layout-
 * capable properties (position/z-index/float/display) are excluded so a
 * hostile style can't overlay real UI. */
const INLINE_STYLE_PROPS = new Set([
  'color', 'background', 'background-color', 'font-weight', 'font-style',
  'font-size', 'padding', 'padding-left', 'padding-right',
  'border-radius', 'text-decoration', 'opacity', 'letter-spacing',
])

const INLINE_STYLE_VALUE_BLACKLIST = /url\(|expression\(|var\(|<|&/i

function parseSafeStyle(attrs: string): React.CSSProperties {
  const style: Record<string, string> = {}
  const styleMatch = /style\s*=\s*["']([^"']*)["']/i.exec(attrs)
  if (!styleMatch) return style
  for (const decl of styleMatch[1].split(';')) {
    const idx = decl.indexOf(':')
    if (idx <= 0) continue
    const prop = decl.slice(0, idx).trim().toLowerCase()
    const value = decl.slice(idx + 1).trim()
    if (!prop || !value) continue
    if (!INLINE_STYLE_PROPS.has(prop) || INLINE_STYLE_VALUE_BLACKLIST.test(value)) continue
    const camel = prop.replace(/-([a-z])/g, (_, c: string) => c.toUpperCase())
    style[camel] = value
  }
  return style as React.CSSProperties
}

/** Minimal inline markdown (bold / italic / code / strikethrough / link) for
 * text inside HTML-tagged lines — full block markdown still goes to
 * ReactMarkdown, only these tagged lines need a lightweight pass. */
function renderInlineMarkdown(text: string, keyBase: string): React.ReactNode[] {
  const re = /(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|`[^`]+`|~~[^~]+~~|\[[^\]]+\]\([^)\s]+\))/
  const parts = text.split(re)
  return parts.map((part, i) => {
    const key = `${keyBase}-${i}`
    if (!part) return null
    if (part.startsWith('**') && part.endsWith('**')) return <strong key={key}>{part.slice(2, -2)}</strong>
    if (part.startsWith('~~') && part.endsWith('~~')) return <del key={key}>{part.slice(2, -2)}</del>
    if (part.startsWith('`') && part.endsWith('`')) return <code key={key}>{part.slice(1, -1)}</code>
    if (part.startsWith('*') && part.endsWith('*') && part.length > 2) return <em key={key}>{part.slice(1, -1)}</em>
    const link = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(part)
    if (link) {
      return (
        <a key={key} href={link[2]} target="_blank" rel="noopener noreferrer"
          onClick={(e) => { e.preventDefault(); window.open(link[2], '_blank', 'noopener,noreferrer') }}>
          {link[1]}
        </a>
      )
    }
    return part // plain text stays a bare string node (no wrapper span)
  })
}

function renderInlineTokens(tokens: InlineToken[], keyBase: string): React.ReactNode[] {
  const stack: { tag: string; attrs: string; children: React.ReactNode[] }[] = [
    { tag: '', attrs: '', children: [] },
  ]
  tokens.forEach((t, i) => {
    const key = `${keyBase}-t${i}`
    if (t.kind === 'text') {
      if (t.text) renderInlineMarkdown(t.text, key).forEach((n) => n && stack[stack.length - 1].children.push(n))
      return
    }
    if (t.kind === 'close') {
      if (stack.length > 1) {
        const done = stack.pop()!
        stack[stack.length - 1].children.push(renderInlineElement(done.tag, done.attrs, done.children, key))
      }
      return
    }
    // open
    if (t.tag === 'br') {
      stack[stack.length - 1].children.push(<br key={key} />)
      return
    }
    stack.push({ tag: t.tag!, attrs: t.attrs || '', children: [] })
  })
  // Unreachable for validated tokens (well-formedness gate), kept safe anyway
  while (stack.length > 1) {
    const done = stack.pop()!
    stack[stack.length - 1].children.push(renderInlineElement(done.tag, done.attrs, done.children, `${keyBase}-tail`))
  }
  return stack[0].children
}

function renderInlineElement(tag: string, attrs: string, children: React.ReactNode[], key: string): React.ReactNode {
  switch (tag) {
    case 'span':
      return <span key={key} style={parseSafeStyle(attrs)}>{children}</span>
    case 'b': case 'strong':
      return <strong key={key}>{children}</strong>
    case 'i': case 'em':
      return <em key={key}>{children}</em>
    case 'code':
      return <code key={key}>{children}</code>
    case 'mark':
      return <mark key={key}>{children}</mark>
    case 'u':
      return <u key={key}>{children}</u>
    case 'sub':
      return <sub key={key}>{children}</sub>
    case 'sup':
      return <sup key={key}>{children}</sup>
    default:
      return <span key={key}>{children}</span>
  }
}

/** Lines containing whitelisted inline HTML render via the controlled token
 * pipeline; table lines keep ReactMarkdown (HTML-in-table not supported —
 * the prompt tells the LLM not to mix them). */
function isTableLine(line: string): boolean {
  const t = line.trim()
  return t.startsWith('|') && t.endsWith('|')
}

function lineHasInlineHtml(line: string): boolean {
  return !isTableLine(line) && tokenizeInline(line) !== null
}

function InlineHtmlLine({ line, lineKey }: { line: string; lineKey: string }) {
  const tokens = tokenizeInline(line)
  if (!tokens) return null // caller checks first — defensive
  const prefixMatch = /^(#{1,6})\s+|^(?:[-*]\s+|^\d+[.)]\s+|^>\s?)/.exec(line)
  if (prefixMatch) {
    const rest = line.slice(prefixMatch[0].length)
    const restTokens = tokenizeInline(rest) || [{ kind: 'text', text: rest }]
    const inner = renderInlineTokens(restTokens, `${lineKey}-inner`)
    if (prefixMatch[1]) {
      const level = prefixMatch[1].length
      return React.createElement(`h${level}`, { key: lineKey }, inner)
    }
    if (/^\d+[.)]/.test(prefixMatch[0])) return <ol key={lineKey}><li>{inner}</li></ol>
    if (/^[-*]\s/.test(prefixMatch[0])) return <ul key={lineKey}><li>{inner}</li></ul>
    return <blockquote key={lineKey}>{inner}</blockquote>
  }
  return <>{renderInlineTokens(tokens, lineKey)}</>
}

interface LineChunk {
  html: boolean
  lines: string[]
}

function groupLines(content: string): LineChunk[] {
  const chunks: LineChunk[] = []
  for (const line of content.split('\n')) {
    const html = lineHasInlineHtml(line)
    const last = chunks[chunks.length - 1]
    if (last && last.html === html) last.lines.push(line)
    else chunks.push({ html, lines: [line] })
  }
  return chunks
}

function TextSegment({ content }: { content: string }) {
  if (!lineHasInlineHtml(content)) {
    return (
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{ img: SafeImage, a: SafeLink }}
      >
        {content}
      </ReactMarkdown>
    )
  }
  return (
    <>
      {groupLines(content).map((chunk, i) => {
        const key = `chunk-${i}`
        if (!chunk.html) {
          return (
            <ReactMarkdown key={key}
              remarkPlugins={[remarkGfm]}
              components={{ img: SafeImage, a: SafeLink }}
            >
              {chunk.lines.join('\n')}
            </ReactMarkdown>
          )
        }
        return chunk.lines.map((line, j) => (
          line.trim()
            ? <InlineHtmlLine key={`${key}-${j}`} line={line} lineKey={`${key}-${j}`} />
            : null
        ))
      })}
    </>
  )
}

export function Markdown({ content, className }: MarkdownProps) {
  const processed = preprocessWikiLinks(content)
  const segments = splitSegments(processed)

  return (
    <div className={`markdown ${className || ''}`}>
      {segments.map((seg, i) =>
        seg.type === 'mermaid' ? (
          <Mermaid key={i} chart={seg.content} />
        ) : seg.type === 'svg' ? (
          <SanitizedSvg key={i} source={seg.content} />
        ) : (
          <TextSegment key={i} content={seg.content} />
        )
      )}
    </div>
  )
}
