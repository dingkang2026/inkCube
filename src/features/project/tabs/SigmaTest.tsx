/** SigmaTest.tsx — Test Sigma rendering with explicit program class registration.
 *  sigma has "sideEffects: false" in package.json, so Vite may tree-shake
 *  the WebGL program classes. This test explicitly imports and registers them. */
import { useEffect, useRef } from 'react'
import Sigma from 'sigma'
import { MultiGraph } from 'graphology'
// Explicitly import program classes to prevent tree-shaking
import { NodeCircleProgram } from 'sigma/rendering'

export function SigmaTest() {
  const containerRef = useRef<HTMLDivElement>(null)
  const sigmaRef = useRef<Sigma | null>(null)

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    console.log('[SigmaTest] Container size:', container.offsetWidth, 'x', container.offsetHeight)
    console.log('[SigmaTest] NodeCircleProgram:', typeof NodeCircleProgram, NodeCircleProgram)

    // Build a simple test graph
    const graph = new MultiGraph()
    graph.addNode('A', { x: 0, y: 100, size: 10, color: '#3b82f6', label: 'Node A' })
    graph.addNode('B', { x: 100, y: -50, size: 10, color: '#ef4444', label: 'Node B' })
    graph.addNode('C', { x: -100, y: -50, size: 10, color: '#10b981', label: 'Node C' })
    graph.addNode('D', { x: 50, y: 50, size: 8, color: '#f59e0b', label: 'Node D' })
    graph.addNode('E', { x: -50, y: 50, size: 8, color: '#8b5cf6', label: 'Node E' })

    graph.addEdge('A', 'B', { size: 1, color: '#94a3b8' })
    graph.addEdge('B', 'C', { size: 1, color: '#94a3b8' })
    graph.addEdge('C', 'A', { size: 1, color: '#94a3b8' })
    graph.addEdge('A', 'D', { size: 0.5, color: '#64748b' })
    graph.addEdge('D', 'E', { size: 0.5, color: '#64748b' })
    graph.addEdge('E', 'B', { size: 0.5, color: '#64748b' })

    // Create sigma instance with EXPLICIT program classes
    try {
      const sigma = new Sigma(graph, container, {
        allowInvalidContainer: true,
        renderLabels: true,
        labelSize: 14,
        labelColor: { color: '#e2e8f0' },
        defaultEdgeColor: '#94a3b8',
        stagePadding: 20,
        // Explicitly register node program classes to bypass tree-shaking
        nodeProgramClasses: {
          circle: NodeCircleProgram,
        },
      })
      sigmaRef.current = sigma

      console.log('[SigmaTest] Sigma created successfully')
      const npc = (sigma as any).settings?.nodeProgramClasses
      console.log('[SigmaTest] nodeProgramClasses keys:', npc ? Object.keys(npc) : 'undefined')

      // Force a refresh after a short delay
      setTimeout(() => {
        try {
          sigma.refresh()
          console.log('[SigmaTest] Refresh successful - graph should be visible')
        } catch (e) {
          console.error('[SigmaTest] Refresh failed:', e)
        }
      }, 200)
    } catch (err) {
      console.error('[SigmaTest] Failed to create Sigma:', err)
    }

    return () => {
      sigmaRef.current?.kill()
      sigmaRef.current = null
    }
  }, [])

  return (
    <div style={{ padding: 40 }}>
      <h2 style={{ color: '#fff', marginBottom: 16 }}>
        Sigma 测试（显式注册 NodeCircleProgram）
      </h2>
      <div
        ref={containerRef}
        style={{
          width: 500,
          height: 500,
          background: '#0f172a',
          border: '2px solid #334155',
          borderRadius: 8,
        }}
      />
      <p style={{ color: '#94a3b8', marginTop: 12, fontSize: 13 }}>
        查看控制台 [SigmaTest] 日志。如果 NodeCircleProgram 是 undefined，说明被 tree-shake 了。
      </p>
    </div>
  )
}
