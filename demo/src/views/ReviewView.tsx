import { useState } from 'react'
import { motion } from 'framer-motion'
import { CheckCircle2, Clock, Code2, FileCheck2, FlaskConical, Pencil, ShieldCheck, Target, Users, XCircle } from 'lucide-react'
import type { Approval, Gate, Integration, RiskCheck } from '../scenario/types'
import { CodeBlock } from '../components/CodeBlock'
import { MappingsTable } from '../components/MappingsTable'
import { Avatar, Badge, type BadgeTone, Card, EmptyState, InitiatedByBadge, Meter, PEOPLE, SectionTitle, ViewHeader, cx, focusRing, tint } from '../components/ui'
import { roadmapItemFor, type ViewProps } from './shared'

const RISK_TONE: Record<RiskCheck['level'], BadgeTone> = { low: 'ok', medium: 'warn', high: 'bad' }
const STATUS_LABEL: Record<Integration['status'], { label: string; tone: BadgeTone }> = {
  planned: { label: 'Planned', tone: 'neutral' },
  in_review: { label: 'In review', tone: 'governance' },
  approved: { label: 'Approved', tone: 'ok' },
  deploying: { label: 'Deploying', tone: 'llm' },
  live: { label: 'Live', tone: 'ok' },
}

function pickIntegration(integrations: Integration[]): Integration | undefined {
  for (let i = integrations.length - 1; i >= 0; i--) {
    const it = integrations[i]
    if (it.status === 'in_review' || it.status === 'approved' || it.status === 'planned') return it
  }
  return integrations[integrations.length - 1]
}

function ApprovalSlot({
  role,
  approval,
  canApprove,
  wrongUser,
  onApprove,
  reducedMotion,
}: {
  role: 'pm' | 'eng'
  approval: Approval | null
  canApprove: boolean
  wrongUser: boolean
  onApprove: () => void
  reducedMotion: boolean
}) {
  const p = PEOPLE[role]
  const title = role === 'pm' ? 'Product' : 'Engineering'
  return (
    <div
      className={cx('rounded-xl border p-3', approval ? 'border-ok' : canApprove || wrongUser ? 'border-accent' : 'border-border')}
      style={approval ? { backgroundColor: tint('ok', 7) } : undefined}
      data-testid={`approval-${role}`}
    >
      <div className="flex items-center justify-between gap-2">
        <p className="text-[12px] font-semibold text-fg">{title}</p>
        {approval ? (
          <Badge tone="ok" icon={<CheckCircle2 size={11} aria-hidden />}>
            Approved
          </Badge>
        ) : (
          <Badge tone="neutral" icon={<Clock size={11} aria-hidden />}>
            Pending
          </Badge>
        )}
      </div>
      {approval ? (
        <motion.div initial={reducedMotion ? false : { opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} className="mt-2 flex items-start gap-2">
          <Avatar who={role} size={26} />
          <div className="min-w-0">
            <p className="text-[12.5px] font-medium text-fg">{approval.by}</p>
            <p className="text-[12px] italic leading-snug text-muted">“{approval.comment}”</p>
          </div>
        </motion.div>
      ) : canApprove ? (
        <button
          type="button"
          onClick={onApprove}
          className={cx(
            'mt-2 flex w-full items-center justify-center gap-1.5 rounded-lg bg-ok px-3 py-1.5 text-[12.5px] font-semibold text-accent-contrast hover:brightness-110',
            focusRing,
          )}
        >
          <CheckCircle2 size={14} aria-hidden /> Approve as {p.name}
        </button>
      ) : (
        <p className="mt-2 text-[12px] text-muted">
          {wrongUser ? `Sign in as ${p.name} · ${p.short} to approve` : `Awaiting ${p.name} · ${p.short}`}
        </p>
      )}
    </div>
  )
}

function Approvals({ integration, gate, onAdvance, reducedMotion }: { integration: Integration; gate: Gate | undefined; onAdvance: () => void; reducedMotion: boolean }) {
  const gateRole = gate?.kind === 'approve_pm' ? 'pm' : gate?.kind === 'approve_eng' ? 'eng' : null
  const [signedIn, setSignedIn] = useState<'pm' | 'eng'>(gateRole ?? 'pm')
  return (
    <Card className="p-4">
      <SectionTitle icon={<Users size={13} aria-hidden className="text-agent-governance" />}>Approvals · policy needs both</SectionTitle>
      {gateRole && (
        <div className="mt-2.5 flex flex-wrap items-center gap-2">
          <span className="text-[11px] text-muted">Signed in as</span>
          <div role="radiogroup" aria-label="Signed-in reviewer" className="inline-flex rounded-lg border border-border bg-surface-2 p-0.5">
            {(['pm', 'eng'] as const).map((r) => (
              <button
                key={r}
                type="button"
                role="radio"
                aria-checked={signedIn === r}
                onClick={() => setSignedIn(r)}
                className={cx(
                  'inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[12px] font-medium',
                  signedIn === r ? 'bg-surface text-fg shadow-sm' : 'text-muted hover:text-fg',
                  focusRing,
                )}
              >
                <Avatar who={r} size={18} />
                {PEOPLE[r].name} · {PEOPLE[r].short}
              </button>
            ))}
          </div>
        </div>
      )}
      <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2">
        {(['pm', 'eng'] as const).map((r) => (
          <ApprovalSlot
            key={r}
            role={r}
            approval={integration.approvals[r]}
            canApprove={gateRole === r && signedIn === r}
            wrongUser={gateRole === r && signedIn !== r}
            onApprove={onAdvance}
            reducedMotion={reducedMotion}
          />
        ))}
      </div>
    </Card>
  )
}

export function ReviewView({ state, onAdvance, reducedMotion }: ViewProps) {
  const integration = pickIntegration(state.integrations)
  if (!integration) {
    return <EmptyState icon={<FileCheck2 size={22} />} title="Nothing to review" body="Review packets appear when an agent drafts an integration." />
  }
  const roadmap = roadmapItemFor(state, integration.id)
  const passed = integration.tests.filter((t) => t.passed).length
  const allPass = integration.tests.length > 0 && passed === integration.tests.length
  const status = STATUS_LABEL[integration.status]
  const flagged = integration.mappings.filter((m) => m.flag).length

  return (
    <div className="flex flex-col gap-4">
      <ViewHeader eyebrow={`Review packet · Loop ${integration.loop}`} title={integration.name} />
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4 xl:grid-cols-[minmax(0,1.5fr)_minmax(300px,1fr)]">
        <div className="flex min-w-0 flex-col gap-4">
          <Card className="p-4">
            <div className="flex flex-wrap items-center gap-2">
              <InitiatedByBadge by={integration.initiatedBy} />
              <Badge tone={status.tone}>{status.label}</Badge>
              {integration.patternReused && <Badge tone="ok">Pattern reused: {integration.patternReused}</Badge>}
              <span className="ml-auto inline-flex items-center gap-1.5 text-[12px] text-muted">
                <Pencil size={12} aria-hidden />
                Human edits: <strong className="tabular-nums text-fg">{integration.humanEdits}</strong>
              </span>
            </div>
            <div className="mt-3 grid gap-3 sm:grid-cols-[minmax(0,1fr)_200px]">
              {roadmap ? (
                <div>
                  <p className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
                    <Target size={12} aria-hidden className="text-accent" /> Serves roadmap intent · {roadmap.title}
                  </p>
                  <blockquote className="mt-1 border-l-2 border-accent pl-2.5 text-[13px] italic leading-snug text-fg">“{roadmap.intent}”</blockquote>
                </div>
              ) : (
                <p className="text-[12px] text-muted">Not linked to a roadmap item.</p>
              )}
              <div>
                <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">Confidence</p>
                <Meter className="mt-1.5" value={integration.confidence} label="Integration confidence" />
              </div>
            </div>
          </Card>

          <Card className="p-4">
            <SectionTitle
              right={
                flagged > 0 ? (
                  <Badge tone="warn">{flagged} flagged</Badge>
                ) : (
                  <span className="text-[11px] text-muted">{integration.mappings.length} mappings</span>
                )
              }
            >
              Field mappings
            </SectionTitle>
            <div className="mt-2">
              <MappingsTable rows={integration.mappings} />
            </div>
          </Card>

          <Card className="p-4">
            <SectionTitle icon={<Code2 size={13} aria-hidden className="text-agent-llm" />} right={<span className="text-[11px] text-muted">Python · generated</span>}>
              Generated code
            </SectionTitle>
            <div className="mt-2">
              <CodeBlock code={integration.code} label={`Generated code for ${integration.name}`} />
            </div>
          </Card>
        </div>

        <div className="flex min-w-0 flex-col gap-4">
          <Card className="p-4">
            <SectionTitle
              icon={<FlaskConical size={13} aria-hidden className="text-agent-mcp" />}
              right={
                integration.tests.length > 0 && (
                  <Badge tone={allPass ? 'ok' : 'bad'}>
                    {passed}/{integration.tests.length} passing
                  </Badge>
                )
              }
            >
              Synthetic tests
            </SectionTitle>
            {integration.tests.length === 0 ? (
              <p className="mt-2 text-[12px] text-muted">Tests run once the plan is submitted for review.</p>
            ) : (
              <ul className="mt-2 flex flex-col gap-1">
                {integration.tests.map((t) => (
                  <li key={t.name} className="flex items-start gap-2 text-[12.5px] leading-snug">
                    {t.passed ? (
                      <CheckCircle2 size={15} aria-label="passed" className="mt-px shrink-0 text-ok" />
                    ) : (
                      <XCircle size={15} aria-label="failed" className="mt-px shrink-0 text-bad" />
                    )}
                    <span className={t.passed ? 'text-fg' : 'font-medium text-bad'}>{t.name}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card className="p-4">
            <SectionTitle icon={<ShieldCheck size={13} aria-hidden className="text-agent-governance" />}>Governance checks</SectionTitle>
            {integration.risks.length === 0 ? (
              <p className="mt-2 text-[12px] text-muted">No checks recorded yet.</p>
            ) : (
              <ul className="mt-2 flex flex-col gap-2">
                {integration.risks.map((r) => (
                  <li key={r.label} className="text-[12.5px]">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium text-fg">{r.label}</span>
                      <Badge tone={RISK_TONE[r.level]}>{r.level} risk</Badge>
                    </div>
                    <p className="mt-0.5 text-[12px] leading-snug text-muted">{r.note}</p>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Approvals key={state.step.id} integration={integration} gate={state.step.gate} onAdvance={onAdvance} reducedMotion={reducedMotion} />
        </div>
      </div>
    </div>
  )
}
