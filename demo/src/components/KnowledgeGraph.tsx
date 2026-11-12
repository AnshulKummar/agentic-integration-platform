// Knowledge-graph view: React owns the SVG elements (keyed by node/edge id), d3-force owns
// their positions (written straight to the DOM on each tick), d3-zoom owns pan/zoom.
// Positions persist by node id across prop changes and remounts, so the layout never jumps;
// newly learned nodes spawn beside a neighbour and the simulation reheats gently.
import './knowledge-graph/kg.css'
import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type JSX,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
} from 'react'
import {
  drag as d3drag,
  select,
  zoom as d3zoom,
  zoomIdentity,
  type D3DragEvent,
  type D3ZoomEvent,
  type DragBehavior,
  type ForceLink,
  type ZoomBehavior,
  type ZoomTransform,
} from 'd3'
import { Maximize2, ZoomIn, ZoomOut } from 'lucide-react'
import type { KGEdge, KGNode } from '../scenario/types'
import {
  ALWAYS_LABELLED,
  LABEL_FONT_SIZE,
  MARKER_COLOR,
  MARKER_KEYS,
  NODE_RADIUS,
  TYPE_LABEL,
  edgeStyle,
  learnedColor,
  shortLabel,
  wrapLabel,
  type EdgeStyle,
} from './knowledge-graph/encoding'
import {
  boundsOf,
  createSimNode,
  createSimulation,
  edgePath,
  graphMemory,
  placeAtAnchor,
  placeNearNeighbour,
  rememberPositions,
  type KGSimulation,
  type SimLink,
  type SimNode,
} from './knowledge-graph/layout'
import { NodeGlyph } from './knowledge-graph/NodeGlyph'
import { Legend } from './knowledge-graph/Legend'
import { EdgeTooltip, NodeDetail } from './knowledge-graph/DetailCard'
import { useElementSize, usePrefersReducedMotion, type Size } from './knowledge-graph/hooks'

export interface KnowledgeGraphProps {
  nodes: KGNode[]
  edges: KGEdge[]
  /** Node ids to spotlight (pulse + dim the rest). */
  highlight: string[]
  /** Pixel height; width fills the container. */
  height?: number
  /** Compact mode for small embeds (no legend, no labels except highlighted). */
  compact?: boolean
}

/** How long a newly learned node/edge stays "fresh" (glow, pulse, forced label). */
const FRESH_MS = 5200
const DEFAULT_HEIGHT = 520
const INITIAL_TICKS = 300

type Hover = { kind: 'node'; id: string } | { kind: 'edge'; id: string; x: number; y: number } | null

interface RenderEdge {
  edge: KGEdge
  style: EdgeStyle
}

const clamp = (v: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, v))

/** Keep labels a constant on-screen size across normal zoom levels. */
const labelScale = (k: number): string => (1 / clamp(k, 0.65, 1.3)).toFixed(3)

export function KnowledgeGraph({
  nodes,
  edges,
  highlight,
  height = DEFAULT_HEIGHT,
  compact = false,
}: KnowledgeGraphProps): JSX.Element {
  const uid = useId().replace(/[^a-zA-Z0-9_-]/g, '')
  const reducedMotion = usePrefersReducedMotion()

  const wrapRef = useRef<HTMLDivElement>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  const viewportRef = useRef<SVGGElement>(null)
  const size = useElementSize(wrapRef)

  const sizeRef = useRef<Size>(size)
  const reducedRef = useRef(reducedMotion)
  const highlightRef = useRef(highlight)
  const simRef = useRef<KGSimulation | null>(null)
  const simNodesRef = useRef(new Map<string, SimNode>())
  const nodeEls = useRef(new Map<string, SVGGElement>())
  const labelEls = useRef(new Map<string, SVGGElement>())
  const edgePathEls = useRef(new Map<string, Set<SVGPathElement>>())
  const renderEdgesRef = useRef<RenderEdge[]>([])
  const zoomRef = useRef<ZoomBehavior<SVGSVGElement, unknown> | null>(null)
  const dragRef = useRef<DragBehavior<SVGGElement, unknown, SimNode | undefined> | null>(null)
  const draggable = useRef(new WeakSet<SVGGElement>())
  const transformRef = useRef<ZoomTransform>(zoomIdentity)
  const userZoomedRef = useRef(false)
  const fittedRef = useRef(false)
  const pendingFitRef = useRef(false)
  const heatRef = useRef(0)
  const prevIdsRef = useRef<{ nodes: Set<string>; edges: Set<string> } | null>(null)
  const fitRef = useRef<(animate: boolean) => boolean>(() => false)

  const [fresh, setFresh] = useState<Map<string, number>>(() => new Map())
  const [hover, setHover] = useState<Hover>(null)
  const [pinned, setPinned] = useState<string | null>(null)

  // ------------------------------------------------------------ derived data
  const nodeById = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes])
  const renderEdges = useMemo<RenderEdge[]>(
    () =>
      edges
        .filter((e) => nodeById.has(e.source) && nodeById.has(e.target))
        .map((edge) => ({ edge, style: edgeStyle(edge) })),
    [edges, nodeById],
  )
  const hl = useMemo(() => new Set(highlight), [highlight])
  const counts = useMemo(
    () => ({
      nodes: nodes.length,
      edges: edges.length,
      learned: nodes.filter((n) => n.learnedIn !== 'seed').length + edges.filter((e) => e.learnedIn !== 'seed').length,
    }),
    [nodes, edges],
  )

  useLayoutEffect(() => {
    sizeRef.current = size
    reducedRef.current = reducedMotion
    highlightRef.current = highlight
    renderEdgesRef.current = renderEdges
  })

  // ------------------------------------------------------------ DOM painting (no React re-render)
  const paint = useCallback((): void => {
    const sims = simNodesRef.current
    for (const [id, el] of nodeEls.current) {
      const s = sims.get(id)
      if (s?.x !== undefined && s.y !== undefined) el.setAttribute('transform', `translate(${s.x.toFixed(1)},${s.y.toFixed(1)})`)
    }
    for (const [id, el] of labelEls.current) {
      const s = sims.get(id)
      if (s?.x !== undefined && s.y !== undefined) el.setAttribute('transform', `translate(${s.x.toFixed(1)},${s.y.toFixed(1)})`)
    }
    for (const { edge, style } of renderEdgesRef.current) {
      const els = edgePathEls.current.get(edge.id)
      const s = sims.get(edge.source)
      const t = sims.get(edge.target)
      if (!els || !s || !t) continue
      const d = edgePath(s, t, style.bend, style.arrow)
      for (const p of els) p.setAttribute('d', d)
    }
  }, [])

  // ------------------------------------------------------------ fit / zoom
  const fit = useCallback(
    (animate: boolean): boolean => {
      const svg = svgRef.current
      const z = zoomRef.current
      const { width, height: h } = sizeRef.current
      if (!svg || !z || width < 10 || h < 10) return false
      const b = boundsOf(simNodesRef.current.values())
      if (!b) return false
      const pad = compact ? 10 : 24
      const k = clamp(Math.min((width - pad * 2) / (b.x1 - b.x0), (h - pad * 2) / (b.y1 - b.y0)), 0.15, 1.8)
      const t = zoomIdentity
        .translate(width / 2, h / 2)
        .scale(k)
        .translate(-(b.x0 + b.x1) / 2, -(b.y0 + b.y1) / 2)
      const sel = select(svg)
      if (animate && !reducedRef.current) sel.transition().duration(650).call(z.transform, t)
      else sel.call(z.transform, t)
      return true
    },
    [compact],
  )

  useLayoutEffect(() => {
    fitRef.current = fit
  }, [fit])

  useLayoutEffect(() => {
    const svg = svgRef.current
    const viewport = viewportRef.current
    if (!svg || !viewport) return
    const z = d3zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.15, 4])
      // Plain wheel scrolls the page; Ctrl/Cmd + wheel (or trackpad pinch) zooms. Drag pans.
      .filter((event: MouseEvent) =>
        event.type === 'wheel' ? event.ctrlKey || event.metaKey : !event.ctrlKey && !event.button,
      )
      .wheelDelta((event: WheelEvent) => -event.deltaY * (event.deltaMode === 1 ? 0.05 : event.deltaMode ? 1 : 0.002))
      .extent(() => [
        [0, 0],
        [Math.max(1, sizeRef.current.width), Math.max(1, sizeRef.current.height)],
      ])
      .on('zoom', (event: D3ZoomEvent<SVGSVGElement, unknown>) => {
        transformRef.current = event.transform
        viewport.setAttribute('transform', event.transform.toString())
        viewport.style.setProperty('--kg-ls', labelScale(event.transform.k))
        if (event.sourceEvent) {
          userZoomedRef.current = true
          setHover(null)
        }
      })
    const sel = select(svg)
    sel.call(z).on('dblclick.zoom', null)
    zoomRef.current = z
    return () => {
      sel.on('.zoom', null)
      zoomRef.current = null
    }
  }, [])

  // Auto-fit on first measurement and whenever the container resizes.
  useLayoutEffect(() => {
    if (size.width < 10 || size.height < 10) return
    if (fit(false)) {
      fittedRef.current = true
      userZoomedRef.current = false
    }
  }, [size, fit])

  const zoomBy = (factor: number): void => {
    const svg = svgRef.current
    const z = zoomRef.current
    if (!svg || !z) return
    userZoomedRef.current = true
    const sel = select(svg)
    if (reducedRef.current) sel.call(z.scaleBy, factor)
    else sel.transition().duration(250).call(z.scaleBy, factor)
  }

  // ------------------------------------------------------------ simulation lifecycle
  useLayoutEffect(() => {
    const simNodes = simNodesRef.current
    const sim = createSimulation()
    sim.on('tick', paint)
    sim.on('end', () => {
      heatRef.current = 0
      rememberPositions(simNodes.values())
      if (pendingFitRef.current) {
        pendingFitRef.current = false
        if (!userZoomedRef.current) fitRef.current(true)
      }
    })
    simRef.current = sim
    return () => {
      sim.stop()
      sim.on('tick', null).on('end', null)
      rememberPositions(simNodes.values())
      simRef.current = null
    }
  }, [paint])

  // Sync props -> simulation. Existing nodes keep their objects (and positions).
  useLayoutEffect(() => {
    const sim = simRef.current
    if (!sim) return
    const map = simNodesRef.current
    const ids = new Set(nodes.map((n) => n.id))
    for (const id of [...map.keys()]) if (!ids.has(id)) map.delete(id)

    let anyPlaced = map.size > 0
    const created: SimNode[] = []
    for (const n of nodes) {
      if (map.has(n.id)) continue
      const s = createSimNode(n)
      const cached = graphMemory.positions.get(n.id)
      if (n.type === 'system') {
        placeAtAnchor(s)
      } else if (cached) {
        s.x = cached.x
        s.y = cached.y
        anyPlaced = true
      } else {
        created.push(s)
      }
      map.set(n.id, s)
    }

    const edgeIds = new Set(renderEdges.map((r) => r.edge.id))
    const prev =
      prevIdsRef.current ??
      (graphMemory.lastNodeIds && graphMemory.lastEdgeIds
        ? { nodes: graphMemory.lastNodeIds, edges: graphMemory.lastEdgeIds }
        : null)

    // What is new since this graph (or any graph) last rendered?
    let newNodeIds: string[]
    let newEdgeIds: string[]
    if (prev) {
      newNodeIds = nodes.filter((n) => !prev.nodes.has(n.id)).map((n) => n.id)
      newEdgeIds = [...edgeIds].filter((id) => !prev.edges.has(id))
    } else {
      // Very first mount: replay the current learning moment (learned + spotlighted).
      const spot = new Set(highlightRef.current)
      newNodeIds = nodes.filter((n) => n.learnedIn !== 'seed' && spot.has(n.id)).map((n) => n.id)
      const nn = new Set(newNodeIds)
      newEdgeIds = renderEdges
        .filter((r) => r.edge.learnedIn !== 'seed' && (nn.has(r.edge.source) || nn.has(r.edge.target)))
        .map((r) => r.edge.id)
    }
    const edgesRemoved = prev ? [...prev.edges].some((id) => !edgeIds.has(id)) : false

    // Place new nodes: a from-scratch layout settles synchronously; additions spawn by a neighbour.
    const initial = !anyPlaced
    if (initial) {
      for (const s of created) placeAtAnchor(s)
    } else if (created.length > 0) {
      const adjacency = new Map<string, string[]>()
      for (const { edge } of renderEdges) {
        adjacency.set(edge.source, [...(adjacency.get(edge.source) ?? []), edge.target])
        adjacency.set(edge.target, [...(adjacency.get(edge.target) ?? []), edge.source])
      }
      for (const s of created) {
        const neighbour = (adjacency.get(s.id) ?? [])
          .map((id) => map.get(id))
          .find((m) => m !== undefined && m.x !== undefined && m.y !== undefined)
        placeNearNeighbour(s, neighbour)
      }
    }

    const link = sim.force<ForceLink<SimNode, SimLink>>('link')
    link?.links([])
    sim.nodes(nodes.map((n) => map.get(n.id)).filter((s): s is SimNode => s !== undefined))
    link?.links(renderEdges.map(({ edge }) => ({ id: edge.id, type: edge.type, source: edge.source, target: edge.target })))

    if (initial && created.length > 0) {
      sim.alpha(1)
      sim.tick(INITIAL_TICKS)
      heatRef.current = 0
    } else if (created.length > 0) {
      heatRef.current = Math.max(heatRef.current, 0.3)
      pendingFitRef.current = true
    } else if (newEdgeIds.length > 0 || edgesRemoved) {
      heatRef.current = Math.max(heatRef.current, 0.06)
    }

    if (heatRef.current > 0) {
      if (reducedRef.current) {
        // No animated settling: jump straight to the settled layout.
        sim.alpha(heatRef.current)
        sim.tick(150)
        heatRef.current = 0
        if (pendingFitRef.current) {
          pendingFitRef.current = false
          if (!userZoomedRef.current) fitRef.current(false)
        }
      } else {
        sim.alpha(Math.max(sim.alpha(), heatRef.current)).restart()
      }
    }

    rememberPositions(map.values())
    paint()
    if (initial && !fittedRef.current) fittedRef.current = fitRef.current(false)

    prevIdsRef.current = { nodes: ids, edges: edgeIds }
    graphMemory.lastNodeIds = ids
    graphMemory.lastEdgeIds = edgeIds

    if (newNodeIds.length > 0 || newEdgeIds.length > 0) {
      const expires = Date.now() + FRESH_MS
      setFresh((old) => {
        const next = new Map(old)
        for (const id of newNodeIds) next.set(`n:${id}`, expires)
        for (const id of newEdgeIds) next.set(`e:${id}`, expires)
        return next
      })
    }
  }, [nodes, renderEdges, paint])

  // Elements mounted in this commit (e.g. a new draw-in overlay) need coordinates too.
  useLayoutEffect(() => {
    paint()
  })

  // Expire "fresh" markers.
  useEffect(() => {
    if (fresh.size === 0) return
    const soonest = Math.min(...fresh.values())
    const timer = window.setTimeout(
      () =>
        setFresh((old) => {
          const now = Date.now() + 16
          return new Map([...old].filter(([, exp]) => exp > now))
        }),
      Math.max(0, soonest - Date.now()),
    )
    return () => window.clearTimeout(timer)
  }, [fresh])

  useEffect(() => {
    if (pinned && !nodeById.has(pinned)) setPinned(null)
  }, [pinned, nodeById])

  // ------------------------------------------------------------ element registration + drag
  const getDrag = (): DragBehavior<SVGGElement, unknown, SimNode | undefined> => {
    if (dragRef.current) return dragRef.current
    type DragEvent = D3DragEvent<SVGGElement, unknown, SimNode | undefined>
    dragRef.current = d3drag<SVGGElement, unknown, SimNode | undefined>()
      .clickDistance(4)
      .subject(function () {
        const id = this.getAttribute('data-node-id')
        return id ? simNodesRef.current.get(id) : undefined
      })
      .on('start', (event: DragEvent) => {
        const s = event.subject
        if (!s) return
        if (!event.active) simRef.current?.alphaTarget(0.12).restart()
        s.fx = s.x
        s.fy = s.y
        setHover(null)
      })
      .on('drag', (event: DragEvent) => {
        const s = event.subject
        if (!s) return
        s.fx = event.x
        s.fy = event.y
      })
      .on('end', (event: DragEvent) => {
        const s = event.subject
        if (!s) return
        if (!event.active) simRef.current?.alphaTarget(0)
        // Systems stay pinned where they are dropped; everything else is released.
        if (s.type !== 'system') {
          s.fx = null
          s.fy = null
        }
      })
    return dragRef.current
  }

  const nodeRef =
    (id: string) =>
    (el: SVGGElement | null): (() => void) | undefined => {
      if (!el) return undefined
      nodeEls.current.set(id, el)
      if (!draggable.current.has(el)) {
        draggable.current.add(el)
        select(el).call(getDrag())
      }
      return () => {
        if (nodeEls.current.get(id) === el) nodeEls.current.delete(id)
      }
    }

  const labelRef =
    (id: string) =>
    (el: SVGGElement | null): (() => void) | undefined => {
      if (!el) return undefined
      labelEls.current.set(id, el)
      return () => {
        if (labelEls.current.get(id) === el) labelEls.current.delete(id)
      }
    }

  const edgeRef =
    (id: string) =>
    (el: SVGPathElement | null): (() => void) | undefined => {
      if (!el) return undefined
      const set = edgePathEls.current.get(id) ?? new Set<SVGPathElement>()
      set.add(el)
      edgePathEls.current.set(id, set)
      return () => {
        set.delete(el)
        if (set.size === 0 && edgePathEls.current.get(id) === set) edgePathEls.current.delete(id)
      }
    }

  // ------------------------------------------------------------ interaction
  const hoverNodeId = hover?.kind === 'node' ? hover.id : null
  const hoverEdgeId = hover?.kind === 'edge' ? hover.id : null

  const onNodeKey = (id: string) => (e: ReactKeyboardEvent<SVGGElement>) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      setPinned((p) => (p === id ? null : id))
    }
  }

  const onEdgeEnter = (id: string) => (e: ReactMouseEvent<SVGPathElement>) => {
    const rect = wrapRef.current?.getBoundingClientRect()
    setHover({ kind: 'edge', id, x: e.clientX - (rect?.left ?? 0), y: e.clientY - (rect?.top ?? 0) })
  }

  const clearHover = (): void => setHover(null)

  // ------------------------------------------------------------ render model
  const hasHighlight = hl.size > 0
  const isFreshNode = (id: string): boolean => fresh.has(`n:${id}`)
  const isFreshEdge = (id: string): boolean => fresh.has(`e:${id}`)
  const freshEdgeEnds = new Set<string>()
  for (const { edge } of renderEdges) {
    if (isFreshEdge(edge.id)) {
      freshEdgeEnds.add(edge.source)
      freshEdgeEnds.add(edge.target)
    }
  }
  const lit = (id: string): boolean => hl.has(id) || isFreshNode(id) || id === hoverNodeId || id === pinned

  const nodeOpacity = (id: string): number => {
    if (!hasHighlight || lit(id)) return 1
    return freshEdgeEnds.has(id) ? 0.6 : 0.3
  }

  const edgeOpacity = (edge: KGEdge): number => {
    if (isFreshEdge(edge.id) || edge.id === hoverEdgeId) return 1
    if (hoverNodeId && (edge.source === hoverNodeId || edge.target === hoverNodeId)) return 1
    if (!hasHighlight) return 1
    const a = lit(edge.source)
    const b = lit(edge.target)
    if (a && b) return 1
    return a || b ? 0.35 : 0.12
  }

  const pinnedNode = pinned ? nodeById.get(pinned) : undefined
  const hoveredNode = hoverNodeId && hoverNodeId !== pinned ? nodeById.get(hoverNodeId) : undefined
  const hoveredEdge = hoverEdgeId ? renderEdges.find((r) => r.edge.id === hoverEdgeId)?.edge : undefined

  const systemName = (n: KGNode): string | undefined =>
    n.system ? (nodeById.get(n.system)?.label ?? n.system) : undefined
  const connectionsOf = (id: string): number =>
    renderEdges.filter((r) => r.edge.source === id || r.edge.target === id).length

  let tooltipPos: { left: number; top: number } | null = null
  if (hoveredNode) {
    const s = simNodesRef.current.get(hoveredNode.id)
    if (s?.x !== undefined && s.y !== undefined) {
      const [sx, sy] = transformRef.current.apply([s.x, s.y])
      const offset = s.r * transformRef.current.k + 12
      const right = sx + offset + 260 < size.width
      tooltipPos = { left: right ? sx + offset : Math.max(8, sx - offset - 260), top: Math.max(8, sy - 18) }
    }
  } else if (hover?.kind === 'edge') {
    const right = hover.x + 16 + 280 < size.width
    tooltipPos = { left: right ? hover.x + 16 : Math.max(8, hover.x - 296), top: Math.max(8, hover.y + 12) }
  }

  const buttonClass =
    'flex items-center justify-center rounded-md border border-border bg-surface text-muted shadow-sm transition-colors hover:bg-surface-2 hover:text-fg focus:outline-none focus-visible:ring-2 focus-visible:ring-accent'
  const iconSize = compact ? 14 : 16

  return (
    <div
      data-testid="knowledge-graph"
      className="flex w-full flex-col overflow-hidden bg-surface text-fg"
      style={{ height }}
      onKeyDown={(e) => {
        if (e.key === 'Escape') setPinned(null)
      }}
    >
      <div ref={wrapRef} className="relative min-h-0 flex-1">
        <svg
          ref={svgRef}
          className="absolute inset-0 block h-full w-full touch-none select-none"
          role="group"
          aria-label={`Knowledge graph: ${nodes.length} nodes, ${edges.length} edges`}
          onClick={(e) => {
            if (e.target === svgRef.current || (e.target as Element).getAttribute('data-kg-bg') === 'true') setPinned(null)
          }}
        >
          <defs>
            <pattern id={`${uid}-dots`} width={22} height={22} patternUnits="userSpaceOnUse">
              <circle cx={1} cy={1} r={1} fill="var(--border)" />
            </pattern>
            {MARKER_KEYS.map((key) => (
              <marker
                key={key}
                id={`${uid}-arrow-${key}`}
                viewBox="0 -4 8 8"
                refX={4}
                refY={0}
                markerWidth={8}
                markerHeight={8}
                markerUnits="userSpaceOnUse"
                orient="auto"
              >
                <path d="M0,-3.5L8,0L0,3.5Z" fill={MARKER_COLOR[key]} />
              </marker>
            ))}
          </defs>
          <rect data-kg-bg="true" width="100%" height="100%" fill={`url(#${uid}-dots)`} />

          <g ref={viewportRef}>
            <g className="kg-edges">
              {renderEdges.map(({ edge, style }) => {
                const isFresh = isFreshEdge(edge.id)
                const mult = edgeOpacity(edge)
                const bright = hasHighlight && mult === 1
                return (
                  <g
                    key={edge.id}
                    className="kg-edge"
                    data-edge-id={edge.id}
                    data-edge-type={edge.type}
                    data-learned={edge.learnedIn}
                    opacity={mult}
                  >
                    <path
                      ref={edgeRef(edge.id)}
                      className={isFresh && !reducedMotion ? 'kg-edge-enter' : undefined}
                      fill="none"
                      stroke={style.color}
                      strokeWidth={bright || edge.id === hoverEdgeId ? style.width + 0.8 : style.width}
                      strokeOpacity={bright ? Math.min(1, style.opacity + 0.3) : style.opacity}
                      strokeDasharray={style.dash}
                      strokeLinecap="round"
                      markerEnd={style.arrow ? `url(#${uid}-arrow-${style.markerKey})` : undefined}
                      pointerEvents="none"
                    />
                    {isFresh && !reducedMotion && (
                      <path
                        ref={edgeRef(edge.id)}
                        className="kg-edge-draw"
                        pathLength={1}
                        fill="none"
                        stroke={learnedColor(edge.learnedIn) ?? style.color}
                        strokeWidth={style.width + 2.5}
                        strokeLinecap="round"
                        pointerEvents="none"
                      />
                    )}
                    <path
                      ref={edgeRef(edge.id)}
                      fill="none"
                      stroke="transparent"
                      strokeWidth={12}
                      style={{ cursor: 'help' }}
                      onMouseEnter={onEdgeEnter(edge.id)}
                      onMouseLeave={clearHover}
                    />
                  </g>
                )
              })}
            </g>

            <g className="kg-nodes">
              {nodes.map((n) => {
                const r = NODE_RADIUS[n.type]
                const isHl = hl.has(n.id)
                const isFresh = isFreshNode(n.id)
                const accent = learnedColor(n.learnedIn) ?? 'var(--accent)'
                return (
                  <g
                    key={n.id}
                    ref={nodeRef(n.id)}
                    className={`kg-node${isFresh && !reducedMotion ? ' kg-enter' : ''}`}
                    data-node-id={n.id}
                    data-node-type={n.type}
                    data-learned={n.learnedIn}
                    data-highlighted={isHl ? 'true' : 'false'}
                    opacity={nodeOpacity(n.id)}
                    tabIndex={0}
                    role="button"
                    aria-label={`${n.label} (${TYPE_LABEL[n.type]})`}
                    aria-pressed={pinned === n.id}
                    onMouseEnter={() => setHover({ kind: 'node', id: n.id })}
                    onMouseLeave={clearHover}
                    onClick={() => setPinned((p) => (p === n.id ? null : n.id))}
                    onKeyDown={onNodeKey(n.id)}
                  >
                    <circle className="kg-focus-ring" r={r + 8} fill="none" stroke="var(--accent)" strokeWidth={2} />
                    {isFresh && (
                      <circle
                        className="kg-ring kg-glow-new"
                        r={r + 9}
                        fill={accent}
                        fillOpacity={0.16}
                        stroke={accent}
                        strokeWidth={2}
                        strokeOpacity={0.8}
                      />
                    )}
                    {isFresh && !reducedMotion && (
                      <circle className="kg-ring kg-ring-new" r={r + 6} fill="none" stroke={accent} strokeWidth={3} />
                    )}
                    {isHl && (
                      <circle
                        className={`kg-ring${reducedMotion ? '' : ' kg-ring-highlight'}`}
                        r={r + 6}
                        fill="none"
                        stroke={accent}
                        strokeWidth={2.5}
                        opacity={0.75}
                      />
                    )}
                    {pinned === n.id && (
                      <circle r={r + 5} fill="none" stroke="var(--text)" strokeWidth={1.5} strokeDasharray="3 3" />
                    )}
                    <g className="kg-body">
                      <NodeGlyph type={n.type} learnedIn={n.learnedIn} />
                    </g>
                    <circle r={Math.max(r + 4, 10)} fill="transparent" />
                  </g>
                )
              })}
            </g>

            <g className="kg-labels" pointerEvents="none">
              {nodes.map((n) => {
                const r = NODE_RADIUS[n.type]
                const isHl = hl.has(n.id)
                const isFresh = isFreshNode(n.id)
                const focused = isHl || isFresh || n.id === hoverNodeId || n.id === pinned
                const showLabel = focused || (!compact && ALWAYS_LABELLED.has(n.type))
                const lines = showLabel
                  ? n.type === 'system'
                    ? wrapLabel(n.label, 22, 2)
                    : focused
                      ? wrapLabel(shortLabel(n), 26, 3)
                      : wrapLabel(shortLabel(n), 22, 2)
                  : []
                const fs = LABEL_FONT_SIZE[n.type]
                const showConfidence = !compact && n.confidence !== undefined && (n.type === 'pattern' || n.type === 'rule')
                return (
                  <g
                    key={n.id}
                    ref={labelRef(n.id)}
                    className="kg-label"
                    data-label-id={n.id}
                    opacity={nodeOpacity(n.id) === 1 ? 1 : Math.max(0.35, nodeOpacity(n.id))}
                  >
                    {lines.length > 0 && (
                      <text
                        className={isFresh && !reducedMotion ? 'kg-label-enter' : undefined}
                        y={r + 3}
                        textAnchor="middle"
                        fill={n.type === 'data_field' ? 'var(--muted)' : 'var(--text)'}
                        stroke="var(--surface)"
                        strokeLinejoin="round"
                        paintOrder="stroke"
                        style={{
                          fontSize: `calc(${fs}px * var(--kg-ls, 1))`,
                          strokeWidth: `calc(3.5px * var(--kg-ls, 1))`,
                          fontWeight: n.type === 'system' ? 650 : focused ? 600 : 500,
                        }}
                      >
                        {lines.map((line, i) => (
                          <tspan key={i} x={0} dy={i === 0 ? '1em' : '1.15em'}>
                            {line}
                          </tspan>
                        ))}
                      </text>
                    )}
                    {showConfidence && (
                      <text
                        key={n.confidence}
                        className={reducedMotion ? undefined : 'kg-conf-pop'}
                        x={r * 0.8 + 2}
                        y={-r * 0.8 - 2}
                        fill={learnedColor(n.learnedIn) ?? 'var(--muted)'}
                        stroke="var(--surface)"
                        strokeLinejoin="round"
                        paintOrder="stroke"
                        style={{
                          fontSize: `calc(9.5px * var(--kg-ls, 1))`,
                          strokeWidth: `calc(3px * var(--kg-ls, 1))`,
                          fontWeight: 700,
                        }}
                      >
                        {Math.round((n.confidence ?? 0) * 100)}%
                      </text>
                    )}
                  </g>
                )
              })}
            </g>
          </g>
        </svg>

        <div className={`absolute flex flex-col gap-1.5 ${compact ? 'right-2 top-2' : 'right-3 top-3'}`}>
          <button
            type="button"
            className={`${buttonClass} ${compact ? 'h-7 w-7' : 'h-8 w-8'}`}
            aria-label="Zoom in"
            title="Zoom in"
            onClick={() => zoomBy(1.3)}
          >
            <ZoomIn size={iconSize} />
          </button>
          <button
            type="button"
            className={`${buttonClass} ${compact ? 'h-7 w-7' : 'h-8 w-8'}`}
            aria-label="Zoom out"
            title="Zoom out"
            onClick={() => zoomBy(1 / 1.3)}
          >
            <ZoomOut size={iconSize} />
          </button>
          <button
            type="button"
            className={`${buttonClass} ${compact ? 'h-7 w-7' : 'h-8 w-8'}`}
            aria-label="Zoom to fit"
            title="Zoom to fit"
            onClick={() => {
              userZoomedRef.current = false
              fit(true)
            }}
          >
            <Maximize2 size={iconSize} />
          </button>
        </div>

        {tooltipPos && (hoveredNode || hoveredEdge) && (
          <div className="pointer-events-none absolute z-10" style={{ left: tooltipPos.left, top: tooltipPos.top }}>
            {hoveredNode ? (
              <NodeDetail
                node={hoveredNode}
                systemName={systemName(hoveredNode)}
                connections={connectionsOf(hoveredNode.id)}
                pinned={false}
              />
            ) : hoveredEdge ? (
              <EdgeTooltip
                edge={hoveredEdge}
                sourceLabel={nodeById.get(hoveredEdge.source)?.label ?? hoveredEdge.source}
                targetLabel={nodeById.get(hoveredEdge.target)?.label ?? hoveredEdge.target}
              />
            ) : null}
          </div>
        )}

        {pinnedNode && (
          <div className={`absolute z-20 ${compact ? 'left-2 top-2' : 'left-3 top-3'}`}>
            <NodeDetail
              node={pinnedNode}
              systemName={systemName(pinnedNode)}
              connections={connectionsOf(pinnedNode.id)}
              pinned
              onClose={() => setPinned(null)}
            />
          </div>
        )}
      </div>
      {!compact && <Legend counts={counts} />}
    </div>
  )
}
