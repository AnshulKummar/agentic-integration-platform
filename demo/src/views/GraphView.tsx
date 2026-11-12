import { BrainCircuit, GitBranch, Network, Sparkles, TrendingUp, ZapOff } from 'lucide-react'
import type { ReactNode } from 'react'
import { KnowledgeGraph } from '../components/KnowledgeGraph'
import { Badge, Card, SectionTitle, ViewHeader, cx, tint } from '../components/ui'
import { useElementSize } from '../hooks/useElementSize'
import type { KGNode, NodeType } from '../scenario/types'
import { currentLoop, type ViewProps } from './shared'

const TYPE_LABEL: Record<NodeType, string> = {
  system: 'System',
  business_object: 'Object',
  data_field: 'Field',
  api_endpoint: 'Endpoint',
  pattern: 'Pattern',
  rule: 'Rule',
  process: 'Process',
  roadmap: 'Roadmap',
}

function Stat({ icon, label, value, delta }: { icon: ReactNode; label: string; value: number; delta: number }) {
  return (
    <div className="rounded-lg border border-border bg-surface-2 px-3 py-2">
      <p className="flex items-center gap-1.5 text-[11px] font-medium text-muted">
        {icon}
        {label}
      </p>
      <p className="mt-0.5 flex items-baseline gap-1.5">
        <span className="text-xl font-semibold tabular-nums text-fg">{value}</span>
        {delta !== 0 && (
          <span className={cx('text-xs font-semibold tabular-nums', delta > 0 ? 'text-ok' : 'text-bad')}>
            {delta > 0 ? '+' : ''}
            {delta}
          </span>
        )}
      </p>
    </div>
  )
}

interface Reinforced {
  node: KGNode
  from: number
  to: number
}

export function GraphView({ state, prev }: ViewProps) {
  const { ref, height } = useElementSize<HTMLDivElement>({ width: 800, height: 520 })
  const snap = state.kgHistory[state.kgHistory.length - 1]
  const before = state.kgHistory[state.kgHistory.length - 2]
  const loop = currentLoop(state)
  const prevIds = new Set(prev?.nodes.map((n) => n.id) ?? state.nodes.map((n) => n.id))
  const learned = prev ? state.nodes.filter((n) => !prevIds.has(n.id) && (loop === null || n.learnedIn === loop)) : []
  const prevEdgeIds = new Set(prev?.edges.map((e) => e.id) ?? [])
  const newEdges = prev ? state.edges.filter((e) => !prevEdgeIds.has(e.id)) : []
  const reinforced: Reinforced[] = []
  for (const n of state.nodes) {
    const p = prev?.nodes.find((x) => x.id === n.id)
    if (p && p.confidence !== undefined && n.confidence !== undefined && p.confidence !== n.confidence) {
      reinforced.push({ node: n, from: p.confidence, to: n.confidence })
    }
  }
  const learningOff = !state.options.learningEnabled && Boolean(state.step.learningOff)
  const loopTone = loop === 'loop2' ? 'loop2' : 'loop1'

  return (
    <div className="flex h-full min-h-0 flex-col gap-4">
      <ViewHeader eyebrow="Knowledge graph" title="Institutional memory of every system we connect" />
      <div className="grid grid-cols-[minmax(0,1fr)] min-h-0 flex-1 gap-4 xl:grid-cols-[minmax(0,1fr)_280px]">
        <Card className="relative min-h-[360px] overflow-hidden p-0 lg:min-h-[420px]">
          <div ref={ref} className="absolute inset-0">
            <KnowledgeGraph nodes={state.nodes} edges={state.edges} highlight={state.highlight} height={height} />
          </div>
        </Card>

        <div className="flex min-h-0 flex-col gap-3 overflow-y-auto">
          <Card className="p-3">
            <SectionTitle icon={<BrainCircuit size={13} aria-hidden className="text-agent-kg" />}>Graph size</SectionTitle>
            <div className="mt-2 grid grid-cols-3 gap-2 xl:grid-cols-1 2xl:grid-cols-3">
              <Stat icon={<Network size={12} aria-hidden />} label="Nodes" value={snap.nodes} delta={before ? snap.nodes - before.nodes : 0} />
              <Stat icon={<GitBranch size={12} aria-hidden />} label="Edges" value={snap.edges} delta={before ? snap.edges - before.edges : 0} />
              <Stat
                icon={<Sparkles size={12} aria-hidden />}
                label="Patterns + rules"
                value={snap.patterns}
                delta={before ? snap.patterns - before.patterns : 0}
              />
            </div>
          </Card>

          <Card className="p-3" testId="learned-this-step">
            <SectionTitle
              icon={<Sparkles size={13} aria-hidden className="text-accent" />}
              right={loop && <Badge tone={loopTone}>{loop === 'loop1' ? 'Loop 1' : 'Loop 2'}</Badge>}
            >
              Learned this step
            </SectionTitle>
            {learningOff ? (
              <p
                className="mt-2 flex gap-2 rounded-lg px-2.5 py-2 text-[12px] leading-snug text-warn"
                style={{ backgroundColor: tint('warn', 10) }}
              >
                <ZapOff size={14} aria-hidden className="mt-0.5 shrink-0" />
                KG learning is OFF. Nothing was written back.
              </p>
            ) : learned.length === 0 && reinforced.length === 0 && newEdges.length === 0 ? (
              <p className="mt-2 text-[12px] text-muted">Nothing new this step.</p>
            ) : (
              <ul className="mt-2 flex flex-col gap-1.5">
                {learned.map((n) => (
                  <li
                    key={n.id}
                    className="rounded-lg border-l-2 px-2.5 py-1.5"
                    style={{ borderColor: `var(--learned-${n.learnedIn === 'loop2' ? 'loop2' : 'loop1'})`, backgroundColor: tint(`learned-${n.learnedIn === 'loop2' ? 'loop2' : 'loop1'}`, 8) }}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <Badge tone={n.learnedIn === 'loop2' ? 'loop2' : 'loop1'}>{TYPE_LABEL[n.type]}</Badge>
                      {n.confidence !== undefined && <span className="text-[11px] tabular-nums text-muted">{n.confidence.toFixed(2)}</span>}
                    </div>
                    <p className="mt-1 text-[12px] leading-snug text-fg">{n.label.replace(/^(Pattern|Rule): /, '')}</p>
                  </li>
                ))}
                {reinforced.map((r) => (
                  <li key={`r-${r.node.id}`} className="rounded-lg border-l-2 border-ok px-2.5 py-1.5" style={{ backgroundColor: tint('ok', 8) }}>
                    <div className="flex items-center justify-between gap-2">
                      <Badge tone="ok" icon={<TrendingUp size={11} aria-hidden />}>
                        Reinforced
                      </Badge>
                      <span className="text-[11px] font-semibold tabular-nums text-ok">
                        {r.from.toFixed(2)} → {r.to.toFixed(2)}
                      </span>
                    </div>
                    <p className="mt-1 text-[12px] leading-snug text-fg">{r.node.label.replace(/^(Pattern|Rule): /, '')}</p>
                  </li>
                ))}
                {newEdges.length > 0 && (
                  <li className="px-1 pt-0.5 text-[11.5px] text-muted">
                    + {newEdges.length} new relationship{newEdges.length === 1 ? '' : 's'} (
                    {[...new Set(newEdges.map((e) => e.type.replace(/_/g, ' ').toLowerCase()))].slice(0, 3).join(', ')})
                  </li>
                )}
              </ul>
            )}
          </Card>
        </div>
      </div>
    </div>
  )
}
