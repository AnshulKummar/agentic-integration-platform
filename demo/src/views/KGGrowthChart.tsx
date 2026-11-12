import { useState, type PointerEvent as RPointerEvent } from 'react'
import type { KGSnapshot } from '../scenario/types'
import { STEPS } from '../scenario/data'
import { useElementSize } from '../hooks/useElementSize'

interface Marker {
  stepIndex: number
  label: string
  token: string
}

interface Props {
  history: KGSnapshot[]
  markers: Marker[]
}

const SERIES: { key: 'nodes' | 'patterns'; label: string }[] = [
  { key: 'nodes', label: 'Nodes' },
  { key: 'patterns', label: 'Patterns + rules' },
]

const H = 118
const PAD = { top: 22, right: 12, bottom: 20, left: 34 }

function niceTicks(min: number, max: number): number[] {
  if (min === max) return [min]
  const span = max - min
  const step = span <= 6 ? 1 : span <= 12 ? 2 : span <= 30 ? 5 : 10
  const out: number[] = []
  for (let v = Math.ceil(min / step) * step; v <= max; v += step) out.push(v)
  return out.length > 4 ? out.filter((_, i) => i % 2 === 0) : out
}

/** One small multiple per measure (never a dual axis), sharing the step x-axis. */
function Panel({
  history,
  series,
  width,
  markers,
  hover,
  setHover,
}: {
  history: KGSnapshot[]
  series: (typeof SERIES)[number]
  width: number
  markers: Marker[]
  hover: number | null
  setHover: (i: number | null) => void
}) {
  const values = history.map((h) => h[series.key])
  const lo = Math.max(0, Math.min(...values) - 1)
  const hi = Math.max(...values) + 1
  const total = Math.max(1, STEPS.length - 1)
  const iw = Math.max(10, width - PAD.left - PAD.right)
  const ih = H - PAD.top - PAD.bottom
  const x = (i: number) => PAD.left + (i / total) * iw
  const y = (v: number) => PAD.top + ih - ((v - lo) / (hi - lo)) * ih
  const path = history.map((h, i) => `${i === 0 ? 'M' : 'L'}${x(h.stepIndex).toFixed(1)},${y(h[series.key]).toFixed(1)}`).join(' ')
  const last = history[history.length - 1]
  const hovered = hover !== null ? history.find((h) => h.stepIndex === hover) : undefined

  const onMove = (e: RPointerEvent<SVGRectElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const px = ((e.clientX - rect.left) / rect.width) * iw
    const i = Math.round((px / iw) * total)
    setHover(Math.max(0, Math.min(last.stepIndex, i)))
  }

  return (
    <figure className="m-0">
      <figcaption className="flex items-baseline gap-2 text-[12px]">
        <span className="font-semibold text-fg">{series.label}</span>
        <span className="tabular-nums text-muted">
          {values[0]} → <strong className="font-semibold text-fg">{last[series.key]}</strong>
        </span>
      </figcaption>
      <svg width={width} height={H} role="img" aria-label={`${series.label} over demo steps: from ${values[0]} to ${last[series.key]}`} className="block overflow-visible">
        {niceTicks(lo, hi).map((t) => (
          <g key={t}>
            <line x1={PAD.left} x2={PAD.left + iw} y1={y(t)} y2={y(t)} stroke="var(--border)" strokeWidth={1} />
            <text x={PAD.left - 6} y={y(t)} dy="0.32em" textAnchor="end" fontSize={10} fill="var(--muted)" className="tabular-nums">
              {t}
            </text>
          </g>
        ))}
        {markers.map((m) => (
          <g key={m.label}>
            <line x1={x(m.stepIndex)} x2={x(m.stepIndex)} y1={PAD.top - 6} y2={PAD.top + ih} stroke={`var(--${m.token})`} strokeWidth={1.5} strokeDasharray="3 3" />
            <text
              x={x(m.stepIndex)}
              y={PAD.top - 10}
              textAnchor={x(m.stepIndex) > PAD.left + iw - 50 ? 'end' : 'middle'}
              fontSize={10}
              fontWeight={600}
              fill="var(--muted)"
            >
              {m.label}
            </text>
          </g>
        ))}
        <text x={PAD.left} y={H - 4} fontSize={10} fill="var(--muted)">
          Step 1
        </text>
        <text x={PAD.left + iw} y={H - 4} fontSize={10} fill="var(--muted)" textAnchor="end">
          Step {STEPS.length}
        </text>
        <path d={path} fill="none" stroke="var(--accent)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        <circle cx={x(last.stepIndex)} cy={y(last[series.key])} r={4} fill="var(--accent)" stroke="var(--surface)" strokeWidth={2} />
        {hovered && (
          <g pointerEvents="none">
            <line x1={x(hovered.stepIndex)} x2={x(hovered.stepIndex)} y1={PAD.top} y2={PAD.top + ih} stroke="var(--muted)" strokeWidth={1} />
            <circle cx={x(hovered.stepIndex)} cy={y(hovered[series.key])} r={4.5} fill="var(--accent)" stroke="var(--surface)" strokeWidth={2} />
          </g>
        )}
        <rect
          x={PAD.left}
          y={PAD.top}
          width={iw}
          height={ih}
          fill="transparent"
          onPointerMove={onMove}
          onPointerLeave={() => setHover(null)}
        />
      </svg>
    </figure>
  )
}

export function KGGrowthChart({ history, markers }: Props) {
  const { ref, width } = useElementSize<HTMLDivElement>({ width: 520, height: 260 })
  const [hover, setHover] = useState<number | null>(null)
  const hovered = hover !== null ? history.find((h) => h.stepIndex === hover) : undefined
  return (
    <div ref={ref} className="relative flex w-full min-w-0 flex-col gap-3 overflow-hidden">
      {SERIES.map((s) => (
        <Panel key={s.key} history={history} series={s} width={width} markers={markers} hover={hover} setHover={setHover} />
      ))}
      {hovered && (
        <div className="pointer-events-none absolute right-0 top-0 rounded-lg border border-border bg-surface px-2.5 py-1.5 text-[11.5px] shadow-md">
          <p className="font-semibold text-fg">
            Step {hovered.stepIndex + 1} · {STEPS[hovered.stepIndex]?.title}
          </p>
          <p className="tabular-nums text-muted">
            {hovered.nodes} nodes · {hovered.edges} edges · {hovered.patterns} patterns + rules
          </p>
        </div>
      )}
    </div>
  )
}
