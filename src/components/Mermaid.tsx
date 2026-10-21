import { useEffect, useRef, useState } from 'react'
import mermaid from 'mermaid'

// Initialize mermaid with light theme
mermaid.initialize({
  startOnLoad: false,
  theme: 'default',
  securityLevel: 'loose',
})

interface MermaidProps {
  chart: string
}

export function Mermaid({ chart }: MermaidProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!containerRef.current || !chart) return
    let cancelled = false

    const renderChart = async () => {
      try {
        setError(null)
        const renderId = `mermaid-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`
        const container = containerRef.current
        if (!container) return
        container.innerHTML = ''

        // Create a div for mermaid.run() to render into
        const chartDiv = document.createElement('div')
        chartDiv.className = 'mermaid'
        chartDiv.id = renderId
        chartDiv.textContent = chart
        container.appendChild(chartDiv)

        await mermaid.run({ nodes: [chartDiv] })

        if (cancelled) return
      } catch (err) {
        if (cancelled) return
        setError(err instanceof Error ? err.message : 'Mermaid render failed')
      }
    }

    renderChart()
    return () => { cancelled = true }
  }, [chart])

  if (error) {
    return (
      <pre style={{ color: 'var(--error)', fontSize: 'var(--text-sm)' }}>
        Mermaid Error: {error}
      </pre>
    )
  }

  return <div ref={containerRef} className="mermaid-diagram" />
}
