import type { JSX } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import type { LearnedIn } from '../../scenario/types'
import { LEARNED_LABEL, NODE_TYPE_ORDER, TYPE_LABEL } from './encoding'
import { NodeGlyph } from './NodeGlyph'

export interface LegendCounts {
  nodes: number
  edges: number
  learned: number
}

function Swatch({ children, size = 18 }: { children: JSX.Element; size?: number }): JSX.Element {
  return (
    <svg width={size} height={size} viewBox={`${-size / 2} ${-size / 2} ${size} ${size}`} aria-hidden className="shrink-0">
      {children}
    </svg>
  )
}

function LineSwatch({ color, dash }: { color: string; dash?: string }): JSX.Element {
  return (
    <svg width={22} height={10} aria-hidden className="shrink-0">
      <line x1={1} y1={5} x2={21} y2={5} stroke={color} strokeWidth={2} strokeDasharray={dash} />
    </svg>
  )
}

function Count({ label, value, tone }: { label: string; value: number; tone?: string }): JSX.Element {
  return (
    <span className="flex items-baseline gap-1">
      <span className="relative inline-flex min-w-[1.6em] justify-end font-semibold tabular-nums" style={{ color: tone ?? 'var(--text)' }}>
        <AnimatePresence mode="popLayout" initial={false}>
          <motion.span
            key={value}
            initial={{ y: -8, opacity: 0, scale: 1.3 }}
            animate={{ y: 0, opacity: 1, scale: 1 }}
            exit={{ y: 8, opacity: 0 }}
            transition={{ duration: 0.35 }}
          >
            {value}
          </motion.span>
        </AnimatePresence>
      </span>
      <span className="text-muted">{label}</span>
    </span>
  )
}

const LEARNED_SWATCH: { key: LearnedIn; color: string }[] = [
  { key: 'seed', color: 'var(--muted)' },
  { key: 'loop1', color: 'var(--learned-loop1)' },
  { key: 'loop2', color: 'var(--learned-loop2)' },
]

export function Legend({ counts }: { counts: LegendCounts }): JSX.Element {
  return (
    <div
      data-testid="kg-legend"
      className="flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-border bg-surface px-4 py-2.5 text-xs text-fg"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5" aria-label="Node types">
        {NODE_TYPE_ORDER.map((t) => (
          <span key={t} className="flex items-center gap-1.5">
            <Swatch>
              <NodeGlyph type={t} learnedIn="seed" r={t === 'system' ? 8 : t === 'data_field' ? 3.5 : 6.5} plain />
            </Swatch>
            {TYPE_LABEL[t]}
          </span>
        ))}
      </div>
      <div className="h-4 w-px bg-border" aria-hidden />
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5" aria-label="Provenance">
        {LEARNED_SWATCH.map(({ key, color }) => (
          <span key={key} className="flex items-center gap-1.5">
            <Swatch size={14}>
              <circle r={5} fill={color} fillOpacity={key === 'seed' ? 0.25 : 0.35} stroke={color} strokeWidth={2} />
            </Swatch>
            {LEARNED_LABEL[key]}
          </span>
        ))}
      </div>
      <div className="h-4 w-px bg-border" aria-hidden />
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5" aria-label="Edge types">
        <span className="flex items-center gap-1.5">
          <LineSwatch color="var(--accent)" />
          Maps to
        </span>
        <span className="flex items-center gap-1.5">
          <LineSwatch color="var(--muted)" dash="5 3" />
          Equivalent
        </span>
        <span className="flex items-center gap-1.5">
          <LineSwatch color="var(--bad)" dash="4 3" />
          Deprecates
        </span>
      </div>
      <div className="ml-auto flex items-center gap-4" aria-live="polite">
        <Count label="nodes" value={counts.nodes} />
        <Count label="edges" value={counts.edges} />
        <Count label="learned" value={counts.learned} tone="var(--learned-loop1)" />
      </div>
    </div>
  )
}
