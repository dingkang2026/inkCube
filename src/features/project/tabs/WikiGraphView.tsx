/** src/features/project/tabs/WikiGraphView.tsx — Wiki knowledge graph visualization.
 *
 * Uses graphology + sigma (direct) + ForceAtlas2 for WebGL force-directed graph.
 * Per §7.1.1 / §12.3.1 of 2.6wiki-design.md.
 *
 * Features:
 *   - Force-directed layout (ForceAtlas2) with size-tier auto-adjustment
 *   - Nodes colored by page_type or community
 *   - Single click → open page preview (via onNavigateToPage)
 *   - Double click → focus zoom
 *   - Hover → highlight neighbors + tooltip
 *   - Curved edges
 *   - Onboarding hint (dismissible)
 *   - Insights panel (clickable → graph actions)
 *   - highlightNode prop (reader → graph navigation)
 *   - Orphan nodes highlighted (no inbound AND no outbound links)
 */
import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { Loader2, Info, X, AlertCircle } from 'lucide-react'
import Sigma from 'sigma'
import { MultiGraph } from 'graphology'
import forceAtlas2 from 'graphology-layout-forceatlas2'
// Explicitly import program classes to prevent Vite tree-shaking
// (sigma has "sideEffects: false" in package.json)
import { NodeCircleProgram } from 'sigma/rendering'
import EdgeCurveProgram, { DEFAULT_EDGE_CURVATURE } from '@sigma/edge-curve'
import React from 'react'
import s from './WikiGraphView.module.css'

// Page type → color mapping
const PAGE_TYPE_COLORS: Record<string, string> = {
  source: '#f59e0b',    // amber
  entity: '#3b82f6',    // blue
  concept: '#8b5cf6',   // violet
  topic: '#10b981',     // emerald
  structure: '#6b7280', // gray
}

const PAGE_TYPE_LABELS: Record<string, string> = {
  source: '来源', entity: '实体', concept: '概念', topic: '主题', structure: '结构',
}

// Community colors (up to 20 communities)
const COMMUNITY_COLORS = [
  '#3b82f6', '#ef4444', '#10b981', '#f59e0b', '#8b5cf6',
  '#ec4899', '#06b6d4', '#84cc16', '#f97316', '#6366f1',
  '#14b8a6', '#e11d48', '#a855f7', '#0ea5e9', '#d946ef',
  '#22c55e', '#eab308', '#64748b', '#fb923c', '#4f46e5',
]

// ── Graph size tier config (§12.3.1) ──────────────────

type GraphTier = 'small' | 'medium' | 'large'

function getGraphTier(nodeCount: number): GraphTier {
  if (nodeCount < 20) return 'small'
  if (nodeCount <= 100) return 'medium'
  return 'large'
}

const TIER_CONFIG: Record<GraphTier, {
  sizeMultiplier: number
  labelThreshold: number | 'top20'
  gravity: number
  labelSize: number
}> = {
  small:  { sizeMultiplier: 1.5, labelThreshold: 0,    gravity: 0.3, labelSize: 14 },
  medium: { sizeMultiplier: 1.0, labelThreshold: 6,    gravity: 0.5, labelSize: 12 },
  large:  { sizeMultiplier: 0.7, labelThreshold: 'top20', gravity: 0.8, labelSize: 10 },
}

// ── Interfaces ────────────────────────────────────────

interface GraphNode {
  id: string
  type: string
  label: string
  linkCount: number
  status: string
  community: number
  inDegree: number
  outDegree: number
}

interface GraphEdge {
  source: string
  target: string
  weight: number
}

interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
  stats: { nodes: number; edges: number; communities: number }
  insights?: {
    surprising_connections: Array<{ source: string; target: string; score: number; reason: string }>
    knowledge_gaps: Array<{ type: string; page_key: string; label: string; description: string }>
  }
}

interface WikiGraphViewProps {
  projectId: string
  onNavigateToPage: (pageKey: string) => void
  highlightNode?: string
}

type ColorMode = 'type' | 'community'

// ── Helpers ───────────────────────────────────────────

/** Error Boundary — catches Sigma / WebGL crashes and shows fallback UI. */
class GraphErrorBoundary extends React.Component<
  { children: React.ReactNode; fallback: React.ReactNode },
  { hasError: boolean }
> {
  state = { hasError: false }
  static getDerivedStateFromError() {
    return { hasError: true }
  }
  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('WikiGraphView crashed:', error, info)
  }
  render() {
    if (this.state.hasError) return this.props.fallback
    return this.props.children
  }
}

// ── Main Component ────────────────────────────────────

export function WikiGraphView({ projectId, onNavigateToPage, highlightNode }: WikiGraphViewProps) {
  const [graphData, setGraphData] = useState<GraphData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [colorMode, setColorMode] = useState<ColorMode>('type')
  const [showInsights, setShowInsights] = useState(false)
  const [hoveredNode, setHoveredNode] = useState<string | null>(null)
  const [showOnboarding, setShowOnboarding] = useState(true)
  const [graphHeight, setGraphHeight] = useState<number>(0)
  const containerRef = useRef<HTMLDivElement>(null)
  const graphContainerRef = useRef<HTMLDivElement>(null)
  const sigmaRef = useRef<Sigma | null>(null)
  const graphObjRef = useRef<MultiGraph | null>(null)
  // Use refs for hover state to avoid recreating sigma on every hover change
  const hoveredNodeRef = useRef<string | null>(null)
  const neighborMapRef = useRef<Map<string, Set<string>>>(new Map())

  // ── Node drag state ──
  const draggedNodeRef = useRef<string | null>(null)
  const dragStartMouseRef = useRef<{ x: number; y: number } | null>(null)
  const dragStartCameraRef = useRef<{ x: number; y: number; ratio: number; angle: number } | null>(null)
  const hasDraggedRef = useRef(false)

  // Load graph data
  useEffect(() => {
    let cancelled = false
    async function load() {
      setLoading(true)
      setError(null)
      try {
        const result: any = await window.electronAPI.invoke('wiki:getGraph', { projectId, insights: true })
        if (!cancelled) {
          if (result.nodes?.length > 0) {
            setGraphData(result)
          } else {
            setError('暂无图谱数据')
          }
        }
      } catch (err: any) {
        if (!cancelled) setError(err.message || '加载图谱失败')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [projectId])

  // Measure available height for graph wrapper via ResizeObserver
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const update = () => {
      let ancestor: HTMLElement | null = el.parentElement
      while (ancestor) {
        if (ancestor.clientHeight > 50) {
          const headerH = el.querySelector<HTMLElement>('[data-graph-header]')?.offsetHeight ?? 0
          const legendH = el.querySelector<HTMLElement>('[data-graph-legend]')?.offsetHeight ?? 0
          setGraphHeight(Math.max(100, ancestor.clientHeight - headerH - legendH))
          return
        }
        ancestor = ancestor.parentElement
      }
    }
    const ro = new ResizeObserver(update)
    let p: HTMLElement | null = el.parentElement
    while (p) { ro.observe(p); p = p.parentElement }
    update()
    return () => ro.disconnect()
  }, [graphData])

  // Build adjacency set for neighbor lookup
  const neighborMap = useMemo(() => {
    const map = new Map<string, Set<string>>()
    if (!graphData) return map
    for (const edge of graphData.edges) {
      if (!map.has(edge.source)) map.set(edge.source, new Set())
      if (!map.has(edge.target)) map.set(edge.target, new Set())
      map.get(edge.source)!.add(edge.target)
      map.get(edge.target)!.add(edge.source)
    }
    return map
  }, [graphData])

  // Keep refs in sync
  useEffect(() => { neighborMapRef.current = neighborMap }, [neighborMap])
  useEffect(() => { hoveredNodeRef.current = hoveredNode }, [hoveredNode])

  // Create sigma instance and manage graph
  useEffect(() => {
    if (!graphData || !graphContainerRef.current || graphHeight <= 0) return

    const container = graphContainerRef.current
    const graph = new MultiGraph()
    const tier = getGraphTier(graphData.nodes.length)
    const tierCfg = TIER_CONFIG[tier]

    // Calculate degree threshold for large graphs
    let degreeThreshold = 0
    if (tierCfg.labelThreshold === 'top20') {
      const degrees = graphData.nodes.map(n => (n.inDegree || 0) + (n.outDegree || 0)).sort((a, b) => b - a)
      degreeThreshold = degrees[Math.floor(degrees.length * 0.2)] || 0
    } else {
      degreeThreshold = tierCfg.labelThreshold as number
    }

    // Build graph nodes
    for (const node of graphData.nodes) {
      const color = colorMode === 'community'
        ? COMMUNITY_COLORS[node.community % COMMUNITY_COLORS.length]
        : PAGE_TYPE_COLORS[node.type] || '#6b7280'

      const degree = (node.inDegree || 0) + (node.outDegree || 0)
      const baseSize = Math.max(4, Math.min(20, 4 + degree * 2))
      const size = baseSize * tierCfg.sizeMultiplier
      // Orphan = truly isolated (no inbound AND no outbound links)
      // A source document with high outDegree is NOT an orphan
      const isOrphan = (node.inDegree || 0) === 0 && (node.outDegree || 0) === 0
      const showLabel = degree >= degreeThreshold

      graph.addNode(node.id, {
        x: Math.random() * 1000 - 500,
        y: Math.random() * 1000 - 500,
        size,
        color: isOrphan ? '#ef4444' : color,
        label: node.label,
        // Store metadata for reducers
        _showLabel: showLabel,
        _baseColor: isOrphan ? '#ef4444' : color,
        _isOrphan: isOrphan,
      })
    }

    // Build graph edges
    for (const edge of graphData.edges) {
      if (graph.hasNode(edge.source) && graph.hasNode(edge.target)) {
        graph.addEdgeWithKey(`${edge.source}->${edge.target}`, edge.source, edge.target, {
          type: 'curved',
          size: 1.5,
          color: '#94a3b8',
          curvature: DEFAULT_EDGE_CURVATURE,
        })
      }
    }

    // Run ForceAtlas2 layout
    if (graph.order > 0) {
      try {
        forceAtlas2.assign(graph, {
          iterations: 150,
          settings: {
            adjustSizes: true,
            linLogMode: true,
            gravity: tierCfg.gravity,
            strongGravityMode: false,
          },
        })
      } catch (e) {
        console.warn('ForceAtlas2 layout failed:', e)
      }
    }

    // Create sigma instance
    try {
      const sigmaInstance = new Sigma(graph, container, {
        allowInvalidContainer: true,
        renderLabels: true,
        renderEdgeLabels: true,
        edgeLabelSize: 8,
        edgeLabelColor: { color: '#64748b' },
        labelSize: tierCfg.labelSize,
        labelDensity: 0.1,
        labelRenderedSizeThreshold: tier === 'small' ? 3 : 6,
        minCameraRatio: 0.1,
        maxCameraRatio: 10,
        nodeProgramClasses: {
          circle: NodeCircleProgram,
        },
        edgeProgramClasses: {
          curved: EdgeCurveProgram,
        },
        // Hover highlighting — uses refs to avoid recreating sigma
        nodeReducer: (_node: string, data: any) => {
          const res = { ...data }
          const currentHovered = hoveredNodeRef.current
          const currentNeighbors = neighborMapRef.current

          if (currentHovered === _node) {
            res.highlighted = true
            res.size = (data.size || 8) + 4
          } else if (currentHovered) {
            const neighbors = currentNeighbors.get(currentHovered)
            if (!neighbors || !neighbors.has(_node)) {
              res.color = (data._baseColor || data.color || '#6b7280') + '30'
              res.label = ''
            }
          }
          // Hide labels for low-degree nodes in medium/large graphs
          if (!data._showLabel && !currentHovered) {
            res.label = ''
          }
          return res
        },
        edgeReducer: (edge: string, data: any) => {
          const res = { ...data }
          const currentHovered = hoveredNodeRef.current

          if (currentHovered) {
            const parts = edge.split('->')
            if (parts.length === 2 && (parts[0] === currentHovered || parts[1] === currentHovered)) {
              res.color = '#3b82f6'
              res.size = 2.5
            } else {
              res.color = '#e5e7eb20'
            }
          }
          return res
        },
      })

      sigmaRef.current = sigmaInstance
      graphObjRef.current = graph

      // ── Node drag: disable camera panning when dragging a node ──
      const camera = sigmaInstance.getCamera()
      const renderer = sigmaInstance

      sigmaInstance.on('downNode', ({ node }: { node: string }) => {
        draggedNodeRef.current = node
        hasDraggedRef.current = false
        // Restore camera to pre-drag state (undo any camera move between mousedown and downNode)
        if (dragStartCameraRef.current) {
          camera.setState(dragStartCameraRef.current)
        }
        // Disable camera panning so only the node moves
        camera.enabledPanning = false
        graph.setNodeAttribute(node, 'highlighted', true)
      })

      sigmaInstance.getMouseCaptor().on('mousedown', (e: any) => {
        dragStartMouseRef.current = { x: e.x, y: e.y }
        dragStartCameraRef.current = camera.getState()
      })

      sigmaInstance.getMouseCaptor().on('mousemovebody', (e: any) => {
        const node = draggedNodeRef.current
        if (!node) return
        hasDraggedRef.current = true
        // Convert screen coords → normalized framed graph coords → raw graph coords
        const framedPos = renderer.viewportToFramedGraph({ x: e.x, y: e.y })
        const graphPos = (renderer as any).normalizationFunction.inverse(framedPos)
        graph.setNodeAttribute(node, 'x', graphPos.x)
        graph.setNodeAttribute(node, 'y', graphPos.y)
      })

      sigmaInstance.getMouseCaptor().on('mouseup', () => {
        const node = draggedNodeRef.current
        if (node) {
          graph.removeNodeAttribute(node, 'highlighted')
          draggedNodeRef.current = null
        }
        // Re-enable camera panning
        camera.enabledPanning = true
        dragStartMouseRef.current = null
        dragStartCameraRef.current = null
      })

      // Register events
      sigmaInstance.on('enterNode', ({ node }: { node: string }) => setHoveredNode(node))
      sigmaInstance.on('leaveNode', () => setHoveredNode(null))

      // Single click → open page preview panel (same as home page)
      // Only trigger if the user didn't drag the node
      sigmaInstance.on('clickNode', ({ node }: { node: string }) => {
        if (!hasDraggedRef.current) {
          onNavigateToPage(node)
        }
      })

      // Double click → focus mode (zoom to node)
      sigmaInstance.on('doubleClickNode', ({ node }: { node: string }) => {
        const nodeAttrs = graph.getNodeAttributes(node) as any
        const camera = sigmaInstance.getCamera()
        camera.animate(
          { x: nodeAttrs.x, y: nodeAttrs.y, ratio: 0.3 },
          { duration: 500 }
        )
      })

      // Fit graph to view after layout
      setTimeout(() => {
        try {
          sigmaInstance.getCamera().animatedZoom({ duration: 300 })
        } catch { /* ignore */ }
      }, 200)

      // Handle highlightNode prop (reader → graph navigation)
      if (highlightNode && graph.hasNode(highlightNode)) {
        setTimeout(() => {
          try {
            const nodeAttrs = graph.getNodeAttributes(highlightNode) as any
            const camera = sigmaInstance.getCamera()
            camera.animate(
              { x: nodeAttrs.x, y: nodeAttrs.y, ratio: 0.5 },
              { duration: 600 }
            )
          } catch { /* ignore */ }
        }, 500)
      }
    } catch (err) {
      console.error('Failed to create Sigma:', err)
    }

    return () => {
      sigmaRef.current?.kill()
      sigmaRef.current = null
      graphObjRef.current = null
    }
  }, [graphData, colorMode, graphHeight, highlightNode, projectId])

  // ── Insight click handlers ──────────────────────────

  const handleInsightClick = useCallback((insight: { type?: string; page_key?: string; source?: string; target?: string }) => {
    if (!sigmaRef.current || !graphObjRef.current) return
    const sigma = sigmaRef.current
    const graph = graphObjRef.current
    const camera = sigma.getCamera()

    if ('source' in insight && insight.source && insight.target) {
      // Surprising connection — highlight both nodes
      if (graph.hasNode(insight.source) && graph.hasNode(insight.target)) {
        const a = graph.getNodeAttributes(insight.source) as any
        const b = graph.getNodeAttributes(insight.target) as any
        const midX = (a.x + b.x) / 2
        const midY = (a.y + b.y) / 2
        camera.animate({ x: midX, y: midY, ratio: 0.6 }, { duration: 500 })
      }
    } else if (insight.page_key && graph.hasNode(insight.page_key)) {
      // Knowledge gap — center on the node
      const nodeAttrs = graph.getNodeAttributes(insight.page_key) as any
      camera.animate({ x: nodeAttrs.x, y: nodeAttrs.y, ratio: 0.5 }, { duration: 500 })
    }
  }, [])

  // ── Render ──────────────────────────────────────────

  if (loading) {
    return (
      <div className={s.container}>
        <div className={s.loading}>
          <Loader2 size={24} className={s.spin} />
          <span>加载图谱...</span>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className={s.container}>
        <div className={s.empty}>
          <Info size={24} />
          <p>{error}</p>
        </div>
      </div>
    )
  }

  if (!graphData) return null

  return (
    <div ref={containerRef} className={s.container}>
      {/* Header */}
      <div className={s.header} data-graph-header>
        <div className={s.headerStats}>
          <span>{graphData.stats.nodes} 节点</span>
          <span>·</span>
          <span>{graphData.stats.edges} 链接</span>
          <span>·</span>
          <span>{graphData.stats.communities} 社区</span>
        </div>
        <div className={s.headerActions}>
          <button
            className={`${s.colorToggle} ${colorMode === 'type' ? s.colorToggleActive : ''}`}
            onClick={() => setColorMode('type')}
            title="按页面类型着色：来源、实体、概念、主题等"
          >
            按类型
          </button>
          <button
            className={`${s.colorToggle} ${colorMode === 'community' ? s.colorToggleActive : ''}`}
            onClick={() => setColorMode('community')}
            title="按知识社区着色：互相链接紧密的页面归为一组"
          >
            按社区
          </button>
          {graphData.insights && (
            <button
              className={`${s.insightsBtn} ${showInsights ? s.insightsBtnActive : ''}`}
              onClick={() => setShowInsights(!showInsights)}
              title="发现意外关联和知识空白地带"
            >
              <Info size={13} />
              洞察
            </button>
          )}
        </div>
      </div>

      {/* Onboarding hint */}
      {showOnboarding && (
        <div className={s.onboarding}>
          <Info size={14} />
          <span>这是你的 Wiki 知识地图。每个圆点代表一个 Wiki 页面，连线代表页面间的引用关系。圆点越大 = 被引用越多 = 知识枢纽。</span>
          <button className={s.onboardingClose} onClick={() => setShowOnboarding(false)}>
            <X size={14} />
          </button>
        </div>
      )}

      {/* Legend */}
      <div className={s.legend} data-graph-legend>
        {colorMode === 'type' ? (
          <>
            {Object.entries(PAGE_TYPE_COLORS).map(([type, color]) => (
              <span key={type} className={s.legendItem}>
                <span className={s.legendDot} style={{ backgroundColor: color }} />
                {PAGE_TYPE_LABELS[type]}
              </span>
            ))}
            <span className={s.legendItem}>
              <span className={s.legendDot} style={{ backgroundColor: '#ef4444' }} />
              孤儿页
            </span>
          </>
        ) : (
          <span className={s.legendHint}>同色节点 = 互相链接紧密的知识群组（由算法自动发现）</span>
        )}
        <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--text-muted)' }}>
          单击打开 · 双击聚焦 · 拖拽移动节点 · 悬停高亮 · 滚轮缩放
        </span>
      </div>

      {/* Main area: graph canvas */}
      <div className={s.mainArea}>
        <GraphErrorBoundary fallback={
          <div className={s.graphCanvas}>
            <div className={s.empty}>
              <AlertCircle size={24} />
              <p>图谱渲染失败，请检查浏览器是否支持 WebGL2</p>
            </div>
          </div>
        }>
          <div className={s.graphCanvas}>
            <div
              ref={graphContainerRef}
              className={s.graphWrapper}
              style={graphHeight > 0 ? { height: graphHeight } : undefined}
            />

            {/* Tooltip */}
            {hoveredNode && (
              <NodeTooltip nodeId={hoveredNode} graphData={graphData} />
            )}

            {/* Insights panel */}
            {showInsights && graphData.insights && (
              <div className={s.insightsPanel}>
                <h4 className={s.insightsTitle}>图谱洞察</h4>
                {graphData.insights.surprising_connections.length > 0 && (
                  <div className={s.insightsSection}>
                    <h5>意外关联</h5>
                    {graphData.insights.surprising_connections.slice(0, 5).map((sc, i) => (
                      <div key={i} className={s.insightItem} onClick={() => handleInsightClick(sc)}>
                        <span className={s.insightNodes}>{sc.source} ↔ {sc.target}</span>
                        <span className={s.insightReason}>{sc.reason}</span>
                      </div>
                    ))}
                  </div>
                )}
                {graphData.insights.knowledge_gaps.length > 0 && (
                  <div className={s.insightsSection}>
                    <h5>知识空白</h5>
                    {graphData.insights.knowledge_gaps.slice(0, 5).map((gap, i) => (
                      <div key={i} className={s.insightItem} onClick={() => handleInsightClick(gap)}>
                        <span className={s.insightType}>
                          {gap.type === 'isolated_node' ? '孤立' : gap.type === 'sparse_community' ? '稀疏' : '桥接'}
                        </span>
                        <span className={s.insightDesc}>{gap.description}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </GraphErrorBoundary>
      </div>
    </div>
  )
}

// ── Tooltip sub-component ─────────────────────────────

function NodeTooltip({ nodeId, graphData }: { nodeId: string | null; graphData: GraphData }) {
  if (!nodeId) return null
  const nodeData = graphData.nodes.find(n => n.id === nodeId)
  if (!nodeData) return null

  return (
    <div className={s.tooltip}>
      <div className={s.tooltipTitle}>{nodeData.label}</div>
      <div className={s.tooltipMeta}>
        <span className={s.tooltipType} style={{ color: PAGE_TYPE_COLORS[nodeData.type] }}>
          {PAGE_TYPE_LABELS[nodeData.type] || nodeData.type}
        </span>
        <span>入链 {nodeData.inDegree} · 出链 {nodeData.outDegree}</span>
      </div>
      {nodeData.inDegree === 0 && nodeData.outDegree === 0 && (
        <div className={s.tooltipOrphan}>孤立页 — 无入链且无出链</div>
      )}
    </div>
  )
}
