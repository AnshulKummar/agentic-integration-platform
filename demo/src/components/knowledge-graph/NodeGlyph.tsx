// The shape for one node type, drawn centred on (0,0). Used on the canvas and in the legend.
import type { JSX } from 'react'
import { Server } from 'lucide-react'
import type { LearnedIn, NodeType } from '../../scenario/types'
import { NODE_RADIUS, TYPE_COLOR, learnedColor } from './encoding'

interface ShapeProps {
  fill: string
  fillOpacity?: number
  stroke: string
  strokeWidth: number
}

function hexagon(r: number): string {
  const pts: string[] = []
  for (let i = 0; i < 6; i++) {
    const a = (Math.PI / 3) * i
    pts.push(`${(Math.cos(a) * r).toFixed(2)},${(Math.sin(a) * r).toFixed(2)}`)
  }
  return pts.join(' ')
}

function shape(type: NodeType, r: number, p: ShapeProps): JSX.Element {
  const common = {
    fill: p.fill,
    fillOpacity: p.fillOpacity,
    stroke: p.stroke,
    strokeWidth: p.strokeWidth,
    strokeLinejoin: 'round' as const,
  }
  switch (type) {
    case 'pattern':
      return <polygon points={hexagon(r)} {...common} />
    case 'rule':
      return <polygon points={`0,${-r} ${r},0 0,${r} ${-r},0`} {...common} />
    case 'api_endpoint':
      return <rect x={-r} y={-r} width={r * 2} height={r * 2} rx={2.5} {...common} />
    case 'process':
      return <rect x={-r} y={-r * 0.6} width={r * 2} height={r * 1.2} rx={r * 0.6} {...common} />
    case 'roadmap': {
      // A tag / flag: flat left edge, pointed right edge.
      const w = r
      const h = r * 0.7
      return <path d={`M${-w},${-h}H${w * 0.55}L${w},0L${w * 0.55},${h}H${-w}Z`} {...common} />
    }
    default:
      return <circle r={r} {...common} />
  }
}

export interface NodeGlyphProps {
  type: NodeType
  learnedIn: LearnedIn
  /** Override radius (legend uses smaller glyphs). */
  r?: number
  /** Hide the permanent learned halo (legend). */
  plain?: boolean
}

export function NodeGlyph({ type, learnedIn, r, plain }: NodeGlyphProps): JSX.Element {
  const radius = r ?? NODE_RADIUS[type]
  const color = TYPE_COLOR[type]
  const learned = learnedColor(learnedIn)

  if (type === 'data_field') {
    return (
      <g>
        {learned && !plain && <circle r={radius + 3.5} fill="none" stroke={learned} strokeWidth={1.5} opacity={0.5} />}
        <circle r={radius} fill={learned ?? color} stroke="var(--surface)" strokeWidth={1.5} />
      </g>
    )
  }

  if (type === 'system') {
    const icon = radius * 0.9
    return (
      <g>
        <circle r={radius} fill="var(--surface)" stroke={learned ?? color} strokeWidth={2.5} />
        <circle r={radius - 4} fill={color} fillOpacity={0.07} />
        <Server x={-icon / 2} y={-icon / 2} size={icon} color={color} strokeWidth={1.8} aria-hidden />
      </g>
    )
  }

  return (
    <g>
      {learned && !plain && (
        <circle r={radius + 5} fill={learned} fillOpacity={0.12} stroke={learned} strokeWidth={1.2} strokeOpacity={0.55} />
      )}
      {shape(type, radius, { fill: 'var(--surface)', stroke: 'none', strokeWidth: 0 })}
      {shape(type, radius, {
        fill: color,
        fillOpacity: 0.22,
        stroke: learned ?? color,
        strokeWidth: learned ? 2.5 : 1.6,
      })}
    </g>
  )
}
