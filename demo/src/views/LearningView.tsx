import { useMemo } from 'react'
import { ArrowRight, BrainCircuit, CalendarClock, FlaskConical, LineChart, Repeat, ZapOff } from 'lucide-react'
import type { ReactNode } from 'react'
import type { DemoOptions, Integration } from '../scenario/types'
import { STEPS } from '../scenario/data'
import { computeState, resolveStep } from '../engine/state'
import { Badge, Card, SectionTitle, ViewHeader, cx, focusRing, tint } from '../components/ui'
import { KGGrowthChart } from './KGGrowthChart'
import { stepIndexById, type ViewProps } from './shared'

/** Confidence as first drafted: the integration's confidence at the step that created it. */
function draftConfidence(id: string, options: DemoOptions): number | undefined {
  const idx = STEPS.findIndex((s) => resolveStep(s, options).integration?.id === id)
  if (idx === -1) return undefined
  return computeState(idx, options).integrations.find((i) => i.id === id)?.confidence
}

interface Metric {
  key: string
  label: string
  icon: ReactNode
  l1: string
  l2: string
  /** true when Loop 2 is better, false when worse, null when not comparable. */
  better: boolean | null
}

function reused(i: Integration | undefined): { n: number; total: number } {
  if (!i) return { n: 0, total: 0 }
  return { n: i.mappings.filter((m) => m.origin === 'kg_reused').length, total: i.mappings.length }
}

const MECHANISMS: { name: string; where: string; stepId: string }[] = [
  { name: 'Intelligent automation', where: 'Agents plan in parallel, generate code and tests, and deploy in stages.', stepId: 'l1-plan' },
  { name: 'Democratization', where: 'A PM states intent in plain language. No ticket, no spec.', stepId: 'l1-intent' },
  { name: 'Hyper-personalization', where: "A partner's new API is matched to this product's own stored roadmap intents.", stepId: 'l2-match' },
  { name: 'Collaborative innovation', where: 'A partner release, PM + engineering approvals, and human corrections shared through the KG.', stepId: 'l2-release' },
]

export function LearningView({ state, onReplay, onJump }: ViewProps) {
  const learningOn = state.options.learningEnabled
  const int1 = state.integrations.find((i) => i.loop === 1)
  const int2 = state.integrations.find((i) => i.loop === 2)

  const draft = useMemo(
    () => ({
      l1: int1 ? draftConfidence(int1.id, state.options) : undefined,
      l2: int2 ? draftConfidence(int2.id, state.options) : undefined,
    }),
    [int1, int2, state.options],
  )

  const r1 = reused(int1)
  const r2 = reused(int2)
  const fmt = (v: number | undefined) => (v === undefined ? '—' : v.toFixed(2))
  const who = (i: Integration | undefined) => (!i ? '—' : i.initiatedBy === 'user_intent' ? 'PM request' : 'Agent, from roadmap')

  const metrics: Metric[] = [
    {
      key: 'human-corrections',
      label: 'Human corrections',
      icon: <FlaskConical size={13} aria-hidden />,
      l1: String(int1?.humanEdits ?? '—'),
      l2: String(int2?.humanEdits ?? '—'),
      better: int1 && int2 ? int2.humanEdits < int1.humanEdits : null,
    },
    {
      key: 'turns',
      label: 'Conversation turns',
      icon: <Repeat size={13} aria-hidden />,
      l1: String(int1?.turns ?? '—'),
      l2: String(int2?.turns ?? '—'),
      better: int1 && int2 ? int2.turns < int1.turns : null,
    },
    {
      key: 'reused',
      label: 'Mappings reused from KG',
      icon: <BrainCircuit size={13} aria-hidden />,
      l1: `${r1.n} / ${r1.total}`,
      l2: `${r2.n} / ${r2.total}`,
      better: r2.total > 0 ? r2.n / r2.total > (r1.total ? r1.n / r1.total : 0) : null,
    },
    {
      key: 'draft-confidence',
      label: 'Confidence at draft',
      icon: <LineChart size={13} aria-hidden />,
      l1: fmt(draft.l1),
      l2: fmt(draft.l2),
      better: draft.l1 !== undefined && draft.l2 !== undefined ? draft.l2 > draft.l1 : null,
    },
    {
      key: 'initiated',
      label: 'Who initiated',
      icon: <ArrowRight size={13} aria-hidden />,
      l1: who(int1),
      l2: who(int2),
      better: null,
    },
  ]

  const l1Learn = stepIndexById('l1-learn')
  const l2Learn = stepIndexById('l2-learn')
  const markers = [
    { stepIndex: l1Learn, label: learningOn ? 'Loop 1 learned' : 'Loop 1 (off)', token: 'learned-loop1' },
    { stepIndex: l2Learn, label: learningOn ? 'Loop 2 learned' : 'Loop 2 (off)', token: 'learned-loop2' },
  ]

  return (
    <div className="flex flex-col gap-4">
      <ViewHeader
        eyebrow="Proof · compound advantage"
        title={
          learningOn
            ? 'The second integration was cheaper because the first one taught the platform'
            : "Without learning, the second integration repeated the first one's mistakes"
        }
        right={
          learningOn ? (
            <Badge tone="ok" icon={<BrainCircuit size={11} aria-hidden />}>
              KG learning ON
            </Badge>
          ) : (
            <Badge tone="warn" icon={<ZapOff size={11} aria-hidden />}>
              KG learning OFF (ablation)
            </Badge>
          )
        }
      />

      <div className="grid grid-cols-[minmax(0,1fr)] gap-4 xl:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
        <Card className="p-4">
          <div className="grid grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_minmax(0,1fr)] items-end gap-x-3 border-b border-border pb-2">
            <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">Metric</span>
            <span className="flex items-center gap-1.5 text-[12px] font-semibold text-fg">
              <span className="h-2 w-2 rounded-full bg-learned-loop1" aria-hidden />
              Loop 1 · intent
            </span>
            <span className="flex items-center gap-1.5 text-[12px] font-semibold text-fg">
              <span className="h-2 w-2 rounded-full bg-learned-loop2" aria-hidden />
              Loop 2 · new API
            </span>
          </div>
          <dl className="divide-y divide-border">
            {metrics.map((m) => (
              <div key={m.key} className="grid grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_minmax(0,1fr)] items-center gap-x-3 py-2.5">
                <dt className="flex items-center gap-1.5 text-[12.5px] text-muted">
                  <span className="text-accent">{m.icon}</span>
                  {m.label}
                </dt>
                <dd className={cx('font-semibold tabular-nums text-fg', m.key === 'initiated' ? 'text-[12.5px]' : 'text-xl')} data-testid={`loop1-${m.key}`}>
                  {m.l1}
                </dd>
                <dd className="flex flex-wrap items-center gap-1.5">
                  <span
                    className={cx(
                      'font-semibold tabular-nums',
                      m.key === 'initiated' ? 'text-[12.5px]' : 'text-xl',
                      m.better === true ? 'text-ok' : m.better === false ? 'text-warn' : 'text-fg',
                    )}
                    data-testid={`loop2-${m.key}`}
                  >
                    {m.l2}
                  </span>
                  {m.better === true && <Badge tone="ok">better</Badge>}
                  {m.better === false && <Badge tone="warn">no gain</Badge>}
                </dd>
              </div>
            ))}
          </dl>
        </Card>

        <Card className="p-4">
          <SectionTitle icon={<LineChart size={13} aria-hidden className="text-accent" />}>Knowledge graph growth</SectionTitle>
          <div className="mt-3">
            <KGGrowthChart history={state.kgHistory} markers={markers} />
          </div>
        </Card>
      </div>

      <div className="grid grid-cols-[minmax(0,1fr)] gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
        <Card
          className={cx('flex flex-col gap-3 p-4', learningOn ? 'border-border' : 'border-warn')}
          style={learningOn ? undefined : { backgroundColor: tint('warn', 7) }}
        >
          <SectionTitle icon={<ZapOff size={13} aria-hidden className={learningOn ? 'text-muted' : 'text-warn'} />}>Ablation</SectionTitle>
          <p className="text-[13px] leading-relaxed text-fg">
            {learningOn
              ? 'What if the knowledge graph never learned? Replay Loop 2 with write-back disabled and watch the same three corrections come back.'
              : 'Learning is OFF: Loop 2 repeated the Loop 1 mistakes and needed the same three human corrections. Turn learning back on to compare.'}
          </p>
          <button
            type="button"
            onClick={() => onReplay(!learningOn, stepIndexById('l2-draft'))}
            className={cx(
              'inline-flex items-center justify-center gap-2 self-start rounded-xl px-4 py-2 text-[13px] font-semibold shadow-sm hover:brightness-110',
              learningOn ? 'bg-warn text-accent-contrast' : 'bg-ok text-accent-contrast',
              focusRing,
            )}
          >
            <Repeat size={14} aria-hidden />
            {learningOn ? 'Replay Loop 2 with KG learning OFF' : 'Replay Loop 2 with KG learning ON'}
          </button>
          <div className="mt-auto flex items-center gap-3 rounded-lg border border-border bg-surface px-3 py-2.5">
            <CalendarClock size={20} aria-hidden className="shrink-0 text-accent" />
            <p className="text-[12.5px] leading-snug text-fg">
              Paper finding: integration cycle <strong className="font-semibold">1–4 weeks → 1–4 days</strong>
            </p>
          </div>
        </Card>

        <Card className="p-4">
          <SectionTitle>How this maps to the paper · Wessel et al. (2025)</SectionTitle>
          <table className="mt-2 w-full text-left text-[12.5px]">
            <thead className="sr-only">
              <tr>
                <th scope="col">Mechanism</th>
                <th scope="col">Where it appeared</th>
                <th scope="col">Jump</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {MECHANISMS.map((m) => {
                const idx = stepIndexById(m.stepId)
                return (
                  <tr key={m.name} className="align-top">
                    <th scope="row" className="w-[34%] py-2 pr-3 font-semibold text-fg">
                      {m.name}
                    </th>
                    <td className="py-2 pr-2 leading-snug text-muted">{m.where}</td>
                    <td className="py-2 text-right">
                      <button
                        type="button"
                        onClick={() => onJump(idx)}
                        className={cx('whitespace-nowrap rounded-md px-1.5 py-0.5 text-[11.5px] font-medium text-accent hover:bg-surface-2', focusRing)}
                        aria-label={`Go to step ${idx + 1}: ${STEPS[idx].title}`}
                      >
                        Step {idx + 1} →
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </Card>
      </div>
    </div>
  )
}
