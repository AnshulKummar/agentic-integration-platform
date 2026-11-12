// Force-layout model: a fixed virtual coordinate space (roughly 1000 x 700, centred on
// 0,0) in which clusters are anchored by system. The view zooms to fit this space, so
// node positions never depend on the container size and stay stable across resizes.
import {
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  type Simulation,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from 'd3'
import type { EdgeType, KGNode, NodeType } from '../../scenario/types'
import { ALWAYS_LABELLED, NODE_RADIUS } from './encoding'

export interface SimNode extends SimulationNodeDatum {
  id: string
  type: NodeType
  r: number
  /** Anchor the cluster force pulls towards. */
  ax: number
  ay: number
  /** Anchor strength (x and y separately, so e.g. roadmap tags stack vertically). */
  axs: number
  ays: number
  collide: number
}

export interface SimLink extends SimulationLinkDatum<SimNode> {
  id: string
  type: EdgeType
}

export type KGSimulation = Simulation<SimNode, SimLink>

interface Point {
  x: number
  y: number
}

/** System cluster anchors: CRM left, Billing right, Tax lower-right, e-Signature top-centre. */
const SYSTEM_ANCHORS: Record<string, Point> = {
  'sys-crm': { x: -360, y: 10 },
  'sys-billing': { x: 360, y: -40 },
  'sys-tax': { x: 330, y: 230 },
  'sys-esign': { x: 0, y: -260 },
}

const CENTRE: Point = { x: 0, y: -20 }
const ROADMAP_ANCHOR: Point = { x: -320, y: 270 }
const FREE_PROCESS_ANCHOR: Point = { x: -40, y: 200 }
const RULE_ANCHOR: Point = { x: 0, y: 110 }

function systemAnchor(id: string | undefined): Point | null {
  if (!id) return null
  return SYSTEM_ANCHORS[id] ?? null
}

/** Unknown systems (not one of the four) get a deterministic spot on a ring. */
function fallbackSystemAnchor(id: string): Point {
  const a = (hash(id) % 360) * (Math.PI / 180)
  return { x: Math.cos(a) * 340, y: Math.sin(a) * 240 }
}

export function anchorFor(node: KGNode): { point: Point; strength: number; yStrength?: number } {
  switch (node.type) {
    case 'system':
      return { point: systemAnchor(node.id) ?? fallbackSystemAnchor(node.id), strength: 1 }
    case 'pattern':
      return { point: CENTRE, strength: 0.09 }
    case 'rule':
      return { point: RULE_ANCHOR, strength: 0.06 }
    case 'roadmap':
      return { point: ROADMAP_ANCHOR, strength: 0.3, yStrength: 0.03 }
    case 'process': {
      const sys = systemAnchor(node.system)
      return sys ? { point: towardCentre(sys, 0.3), strength: 0.08 } : { point: FREE_PROCESS_ANCHOR, strength: 0.08 }
    }
    default: {
      const sys = systemAnchor(node.system) ?? (node.system ? fallbackSystemAnchor(node.system) : CENTRE)
      return { point: sys, strength: node.type === 'data_field' ? 0.045 : 0.07 }
    }
  }
}

function towardCentre(p: Point, t: number): Point {
  return { x: p.x + (CENTRE.x - p.x) * t, y: p.y + (CENTRE.y - p.y) * t }
}

/** Small stable string hash (FNV-1a) so the initial layout is deterministic. */
export function hash(s: string): number {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return h >>> 0
}

function jitter(id: string, radius: number): Point {
  const h = hash(id)
  const angle = ((h % 3600) / 3600) * Math.PI * 2
  const dist = radius * (0.4 + ((h >>> 12) % 1000) / 1666)
  return { x: Math.cos(angle) * dist, y: Math.sin(angle) * dist }
}

export function createSimNode(node: KGNode): SimNode {
  const { point, strength, yStrength } = anchorFor(node)
  const r = NODE_RADIUS[node.type]
  const labelAllowance =
    node.type === 'pattern' ? 46 : node.type === 'rule' ? 36 : node.type === 'roadmap' ? 26 : ALWAYS_LABELLED.has(node.type) ? 16 : 5
  const sim: SimNode = {
    id: node.id,
    type: node.type,
    r,
    ax: point.x,
    ay: point.y,
    axs: strength,
    ays: yStrength ?? strength,
    collide: r + labelAllowance,
  }
  if (node.type === 'system') {
    sim.fx = point.x
    sim.fy = point.y
  }
  return sim
}

/** Initial position for a node in a from-scratch layout: its anchor plus deterministic jitter. */
export function placeAtAnchor(sim: SimNode): void {
  if (sim.type === 'system') {
    sim.x = sim.ax
    sim.y = sim.ay
    return
  }
  const j = jitter(sim.id, 70)
  sim.x = sim.ax + j.x
  sim.y = sim.ay + j.y
}

/** Spawn a newly learned node right next to a neighbour that is already on screen. */
export function placeNearNeighbour(sim: SimNode, neighbour: SimNode | undefined): void {
  if (sim.type === 'system') {
    placeAtAnchor(sim)
    return
  }
  if (!neighbour || neighbour.x === undefined || neighbour.y === undefined) {
    const j = jitter(sim.id, 30)
    sim.x = sim.ax + j.x
    sim.y = sim.ay + j.y
    return
  }
  const dx = sim.ax - neighbour.x
  const dy = sim.ay - neighbour.y
  const len = Math.hypot(dx, dy) || 1
  const j = jitter(sim.id, 10)
  const step = neighbour.r + sim.r + 14
  sim.x = neighbour.x + (dx / len) * step + j.x
  sim.y = neighbour.y + (dy / len) * step + j.y
}

function linkDistance(l: SimLink): number {
  switch (l.type) {
    case 'HAS_FIELD':
      return 36
    case 'BELONGS_TO':
      return 72
    case 'APPLIES_TO':
      return 96
    case 'DERIVED_FROM':
      return 90
    case 'IMPLEMENTS':
    case 'REQUIRES':
    case 'TRIGGERS':
      return 130
    default:
      return 170
  }
}

function linkStrength(l: SimLink): number {
  switch (l.type) {
    case 'HAS_FIELD':
      return 0.9
    case 'BELONGS_TO':
      return 0.45
    case 'APPLIES_TO':
      return 0.35
    case 'DERIVED_FROM':
      return 0.25
    case 'IMPLEMENTS':
    case 'REQUIRES':
    case 'TRIGGERS':
      return 0.035
    case 'CONNECTS_TO':
      return 0
    default:
      // MAPS_TO / EQUIVALENT_TO / DEPRECATES cross clusters: keep them from collapsing the layout.
      return 0.015
  }
}

export function createSimulation(): KGSimulation {
  return forceSimulation<SimNode, SimLink>()
    .force(
      'link',
      forceLink<SimNode, SimLink>()
        .id((d) => d.id)
        .distance(linkDistance)
        .strength(linkStrength),
    )
    .force(
      'charge',
      forceManyBody<SimNode>()
        .strength((d) => (d.type === 'system' ? -320 : d.type === 'data_field' ? -45 : d.type === 'pattern' ? -420 : d.type === 'rule' ? -260 : -110))
        .distanceMax(300),
    )
    .force(
      'collide',
      forceCollide<SimNode>()
        .radius((d) => d.collide)
        .strength(0.85)
        .iterations(2),
    )
    .force('x', forceX<SimNode>((d) => d.ax).strength((d) => d.axs))
    .force('y', forceY<SimNode>((d) => d.ay).strength((d) => d.ays))
    .alphaDecay(0.03)
    .velocityDecay(0.42)
    .stop()
}

/** Path from source to target, clipped to both node radii, optionally bent. */
export function edgePath(s: SimNode, t: SimNode, bend: number, arrow: boolean): string {
  const sx = s.x ?? 0
  const sy = s.y ?? 0
  const tx = t.x ?? 0
  const ty = t.y ?? 0
  const dx = tx - sx
  const dy = ty - sy
  const len = Math.hypot(dx, dy)
  if (len < 1e-3) return `M${sx},${sy}`
  const ux = dx / len
  const uy = dy / len
  const startPad = s.r + 2
  const endPad = t.r + (arrow ? 5 : 2)
  if (len <= startPad + endPad) return `M${sx},${sy}L${tx},${ty}`
  const x1 = sx + ux * startPad
  const y1 = sy + uy * startPad
  const x2 = tx - ux * endPad
  const y2 = ty - uy * endPad
  if (bend === 0) return `M${x1.toFixed(1)},${y1.toFixed(1)}L${x2.toFixed(1)},${y2.toFixed(1)}`
  const mx = (x1 + x2) / 2 - uy * len * bend
  const my = (y1 + y2) / 2 + ux * len * bend
  return `M${x1.toFixed(1)},${y1.toFixed(1)}Q${mx.toFixed(1)},${my.toFixed(1)} ${x2.toFixed(1)},${y2.toFixed(1)}`
}

// ------------------------------------------------------------------ cross-mount memory
// The demo mounts/unmounts the graph as views change. Remembering positions and the last
// rendered ids at module level keeps the layout stable across mounts, and lets a graph that
// mounts on a "learning" step still animate what was added since it was last seen.

export interface GraphMemory {
  positions: Map<string, Point>
  lastNodeIds: Set<string> | null
  lastEdgeIds: Set<string> | null
}

export const graphMemory: GraphMemory = {
  positions: new Map(),
  lastNodeIds: null,
  lastEdgeIds: null,
}

/** Test helper: forget remembered layout and last-seen ids. */
export function resetGraphMemory(): void {
  graphMemory.positions.clear()
  graphMemory.lastNodeIds = null
  graphMemory.lastEdgeIds = null
}

export function rememberPositions(nodes: Iterable<SimNode>): void {
  for (const n of nodes) {
    if (n.x !== undefined && n.y !== undefined) graphMemory.positions.set(n.id, { x: n.x, y: n.y })
  }
}

export interface Bounds {
  x0: number
  y0: number
  x1: number
  y1: number
}

export function boundsOf(nodes: Iterable<SimNode>): Bounds | null {
  let x0 = Infinity
  let y0 = Infinity
  let x1 = -Infinity
  let y1 = -Infinity
  for (const n of nodes) {
    if (n.x === undefined || n.y === undefined) continue
    const labelled = ALWAYS_LABELLED.has(n.type)
    x0 = Math.min(x0, n.x - n.r - (labelled ? 55 : 20))
    x1 = Math.max(x1, n.x + n.r + (labelled ? 55 : 20))
    y0 = Math.min(y0, n.y - n.r - 12)
    y1 = Math.max(y1, n.y + n.r + (labelled ? 36 : 16))
  }
  if (!Number.isFinite(x0)) return null
  return { x0, y0, x1, y1 }
}
