// Visual encoding for the knowledge graph: every colour is a CSS variable so the
// light and dark themes both work. Shape + tint encode the node TYPE; the stroke
// and halo encode WHEN it was learned (seed / Loop 1 / Loop 2).
import type { EdgeType, KGEdge, KGNode, LearnedIn, NodeType } from '../../scenario/types'

export const TYPE_LABEL: Record<NodeType, string> = {
  system: 'System',
  business_object: 'Business object',
  data_field: 'Data field',
  api_endpoint: 'API endpoint',
  pattern: 'Pattern',
  rule: 'Rule',
  process: 'Process',
  roadmap: 'Roadmap item',
}

export const NODE_TYPE_ORDER: NodeType[] = [
  'system',
  'business_object',
  'data_field',
  'api_endpoint',
  'process',
  'pattern',
  'rule',
  'roadmap',
]

export const TYPE_COLOR: Record<NodeType, string> = {
  system: 'var(--text)',
  business_object: 'var(--agent-context)',
  data_field: 'var(--muted)',
  api_endpoint: 'var(--agent-mcp)',
  pattern: 'var(--agent-kg)',
  rule: 'var(--agent-kg)',
  process: 'var(--agent-external)',
  roadmap: 'var(--accent)',
}

/** Visual radius, used for edge clipping, collision and hit-testing. */
export const NODE_RADIUS: Record<NodeType, number> = {
  system: 24,
  business_object: 10,
  data_field: 4.5,
  api_endpoint: 7.5,
  pattern: 14,
  rule: 11,
  process: 12,
  roadmap: 12,
}

/** Node types whose label is always shown (outside compact mode). */
export const ALWAYS_LABELLED: ReadonlySet<NodeType> = new Set<NodeType>(['system', 'pattern', 'rule', 'roadmap'])

export const LABEL_FONT_SIZE: Record<NodeType, number> = {
  system: 13,
  business_object: 11,
  data_field: 10,
  api_endpoint: 10,
  pattern: 11.5,
  rule: 11,
  process: 11,
  roadmap: 11.5,
}

export const LEARNED_LABEL: Record<LearnedIn, string> = {
  seed: 'Seed',
  loop1: 'Learned in Loop 1',
  loop2: 'Learned in Loop 2',
}

export function learnedColor(learnedIn: LearnedIn): string | null {
  if (learnedIn === 'loop1') return 'var(--learned-loop1)'
  if (learnedIn === 'loop2') return 'var(--learned-loop2)'
  return null
}

export type MarkerKey = 'muted' | 'accent' | 'bad' | 'loop1' | 'loop2'

export const MARKER_COLOR: Record<MarkerKey, string> = {
  muted: 'var(--muted)',
  accent: 'var(--accent)',
  bad: 'var(--bad)',
  loop1: 'var(--learned-loop1)',
  loop2: 'var(--learned-loop2)',
}

export const MARKER_KEYS = Object.keys(MARKER_COLOR) as MarkerKey[]

export interface EdgeStyle {
  color: string
  markerKey: MarkerKey
  width: number
  dash?: string
  arrow: boolean
  /** Resting opacity before any dimming. */
  opacity: number
  /** Curvature factor (0 = straight). */
  bend: number
}

const STRUCTURAL: ReadonlySet<EdgeType> = new Set<EdgeType>(['BELONGS_TO', 'HAS_FIELD'])

export function isStructural(type: EdgeType): boolean {
  return STRUCTURAL.has(type)
}

export function edgeStyle(edge: KGEdge): EdgeStyle {
  const learned = learnedColor(edge.learnedIn)
  const learnedKey: MarkerKey | null = edge.learnedIn === 'seed' ? null : edge.learnedIn
  switch (edge.type) {
    case 'BELONGS_TO':
    case 'HAS_FIELD':
      return {
        color: learned ?? 'var(--muted)',
        markerKey: learnedKey ?? 'muted',
        width: 1,
        arrow: false,
        opacity: learned ? 0.6 : 0.32,
        bend: 0,
      }
    case 'DEPRECATES':
      return { color: 'var(--bad)', markerKey: 'bad', width: 2, dash: '5 4', arrow: true, opacity: 0.95, bend: 0.12 }
    case 'EQUIVALENT_TO':
      return {
        color: learned ?? 'var(--muted)',
        markerKey: learnedKey ?? 'muted',
        width: 2,
        dash: '7 5',
        arrow: false,
        opacity: 0.9,
        bend: 0.1,
      }
    case 'MAPS_TO':
      return {
        color: learned ?? 'var(--accent)',
        markerKey: learnedKey ?? 'accent',
        width: 2,
        arrow: true,
        opacity: 0.85,
        bend: 0.1,
      }
    case 'CONNECTS_TO':
      return {
        color: learned ?? 'var(--muted)',
        markerKey: learnedKey ?? 'muted',
        width: 2.5,
        arrow: false,
        opacity: learned ? 0.45 : 0.3,
        bend: 0.06,
      }
    default:
      return {
        color: learned ?? 'var(--muted)',
        markerKey: learnedKey ?? 'muted',
        width: 1.4,
        arrow: true,
        opacity: learned ? 0.8 : 0.55,
        bend: 0.12,
      }
  }
}

export function edgeTypeLabel(type: EdgeType): string {
  return type.toLowerCase().replace(/_/g, ' ')
}

/** Drop the "Pattern: " style prefix (the shape already says it) for on-canvas labels. */
export function shortLabel(node: KGNode): string {
  return node.label.replace(/^(Pattern|Rule|Roadmap):\s*/i, '')
}

/** Greedy word wrap into at most `maxLines` lines, with an ellipsis on overflow. */
export function wrapLabel(text: string, maxChars: number, maxLines: number): string[] {
  const words = text.split(/\s+/).filter(Boolean)
  const lines: string[] = []
  let current = ''
  for (const word of words) {
    const candidate = current ? `${current} ${word}` : word
    if (candidate.length <= maxChars || !current) {
      current = candidate
    } else {
      lines.push(current)
      current = word
    }
  }
  if (current) lines.push(current)
  if (lines.length <= maxLines) return lines
  const kept = lines.slice(0, maxLines)
  const last = kept[maxLines - 1]
  kept[maxLines - 1] = `${last.length > maxChars - 1 ? last.slice(0, maxChars - 1) : last}…`
  return kept
}
