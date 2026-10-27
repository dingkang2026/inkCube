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

/** Check if an image URL is allowed by CSP (self / data: / inkcube:)
 * 注意：react-markdown v9 defaultUrlTransform 会把 data:/inkcube: 等非 http(s)
 * 协议在到达本组件前剥空（SafeImage 收到空 src 返回 null），故这些分支在
 * md 源直写场景下不触达；保留作纵深防御。真正可加载的 data:/inkcube: URI
 * 须经 resolveImage 接管（组件层生成 img，不经 urlTransform）。 */
function isSafeImageSrc(src: string): boolean {
  if (!src) return false
  if (src.startsWith('data:') || src.startsWith('inkcube:')) return true
  // Relative URLs are same-origin → OK
  if (src.startsWith('/') || src.startsWith('./') || src.startsWith('../')) return true
  return false
}

/** resolveImage 契约：调用方（如 FilePreview）可将相对路径图片接管为可加载 URL。
 * 返回 string = 接管（img src 用返回值）；undefined = 异步加载中（渲染中性占位）；
 * null/未传 = 落回 CSP 白名单判定（isSafeImageSrc）。 */
export type ResolveImage = (src: string) => string | undefined | null

/** Custom img renderer: resolveImage 接管优先，外链仍阻断显示占位 + 链接 */
function makeSafeImage(resolveImage?: ResolveImage): Components['img'] {
  return function SafeImage({ src, alt, ...rest }) {
    const { t } = useTranslation()
    if (!src) return null
    const resolved = resolveImage ? resolveImage(src) : null
    if (typeof resolved === 'string') {
      return <img src={resolved} alt={alt} {...rest} />
    }
    if (resolved === undefined) {
      // 相对路径图片经异步通道（如 FilePreview 的 b64 通道）加载中：中性占位，title 保留原 src 供排查
      return (
        <span
          title={src}
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
          <Image size={12} /> {alt || t('common.mdImagePending')}
        </span>
      )
    }
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
}

interface MarkdownProps {
  content: string
  className?: string
  /** 相对路径图片接管（见 ResolveImage）；未传维持 CSP 白名单行为 */
  resolveImage?: ResolveImage
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

// 引用上标占位符协议（2026-10-27，视觉尾注 §2.5report-design §3.7）：
// ReportPreview 转换器把正文 [n] 注入为 <sup>[n]</sup>，但 sup 标签会把
// markdown 加粗/斜体区间切开——renderInlineMarkdown 的 ** 配对要求在
// 同一文本 token 内，跨标签即失败（字面星号）。行进入 token 管线前先把
// <sup>[n]</sup> 编码为私有区占位符（U+E000 + 引用号 + U+E001，正文不会
// 出现），** 配对不受影响；文本渲染处（renderInlineMarkdown / decodeCitationSup）
// 解码还原为 <sup>。仅本组件认识该协议，chat/wiki 无占位符输入不受影响。
const CITATION_SUP_TAG_RE = /<sup>\[(\d{1,3})\]<\/sup>/g
const CITATION_SUP_PLACEHOLDER_RE = /\uE000(\d{1,3})\uE001/g
const encodeCitationSup = (line: string): string =>
  line.replace(CITATION_SUP_TAG_RE, '\uE000$1\uE001')

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

/** 占位符 → <sup>[n]</sup>（引用上标协议，见 encodeCitationSup）。
 * split 单捕获组保证奇数索引恒为引用号段；无占位符时退化为 [text]。 */
function decodeCitationSup(text: string, keyBase: string): React.ReactNode[] {
  if (!text.includes('\uE000')) return [text]
  return text.split(CITATION_SUP_PLACEHOLDER_RE).map((seg, j) =>
    j % 2 === 1
      ? <sup key={`${keyBase}-s${j}`}>[{seg}]</sup>
      : (seg || null)
  )
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
    if (part.startsWith('**') && part.endsWith('**')) return <strong key={key}>{decodeCitationSup(part.slice(2, -2), `${key}-b`)}</strong>
    if (part.startsWith('~~') && part.endsWith('~~')) return <del key={key}>{decodeCitationSup(part.slice(2, -2), `${key}-d`)}</del>
    if (part.startsWith('`') && part.endsWith('`')) {
      // 代码内容不渲染上标，占位符还原为字面 [n]
      return <code key={key}>{part.slice(1, -1).replace(CITATION_SUP_PLACEHOLDER_RE, '[$1]')}</code>
    }
    if (part.startsWith('*') && part.endsWith('*') && part.length > 2) return <em key={key}>{decodeCitationSup(part.slice(1, -1), `${key}-i`)}</em>
    const link = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(part)
    if (link) {
      return (
        <a key={key} href={link[2]} target="_blank" rel="noopener noreferrer"
          onClick={(e) => { e.preventDefault(); window.open(link[2], '_blank', 'noopener,noreferrer') }}>
          {decodeCitationSup(link[1], `${key}-a`)}
        </a>
      )
    }
    return decodeCitationSup(part, key) // plain text（无占位符时仍是裸字符串节点）
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
  // 引用上标先编码为占位符（协议见 encodeCitationSup），避免 sup 标签切开加粗区间
  const encoded = encodeCitationSup(line)
  // 行内唯一 HTML 是引用上标 sup 时，编码后已无标签 → tokenize 判 null：
  // 降级为单 text token，仍走下方同一条 prefix/段落分支——不得返回 null
  // （整行丢失）、不得回退 ReactMarkdown（sup 被剥）、不得无条件包 p（丢标题语义）
  const tokens = tokenizeInline(encoded) || [{ kind: 'text', text: encoded }]
  const prefixMatch = /^(#{1,6})\s+|^(?:[-*]\s+|^\d+[.)]\s+|^>\s?)/.exec(encoded)
  if (prefixMatch) {
    const rest = encoded.slice(prefixMatch[0].length)
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
  // 纯段落行包 <p>：保证段落语义与间距（.markdown p）、useMarkdownBlockFind
  // 叶子块（FIND_BLOCK_SELECTOR 含 p）可命中——裸 fragment 会使引用段落
  // 粘连成段且对文档内查找不可见（2026-10-27 review 修复，chat/wiki 同惠）
  return <p key={lineKey}>{renderInlineTokens(tokens, lineKey)}</p>
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

function TextSegment({ content, resolveImage }: { content: string; resolveImage?: ResolveImage }) {
  const imageRenderer = useMemo(() => makeSafeImage(resolveImage), [resolveImage])
  if (!lineHasInlineHtml(content)) {
    return (
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{ img: imageRenderer, a: SafeLink }}
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
              components={{ img: imageRenderer, a: SafeLink }}
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

export function Markdown({ content, className, resolveImage }: MarkdownProps) {
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
          <TextSegment key={i} content={seg.content} resolveImage={resolveImage} />
        )
      )}
    </div>
  )
}
