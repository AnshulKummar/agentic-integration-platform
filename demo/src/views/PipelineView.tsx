import { motion } from 'framer-motion'
import { CheckCircle2, ChevronRight, Circle, FlaskConical, Globe, Loader2, Rocket, Server, Split } from 'lucide-react'
import type { ReactNode } from 'react'
import type { PipelineStage, PipelineStageName, PipelineStageStatus } from '../scenario/types'
import { Badge, Card, EmptyState, InitiatedByBadge, ViewHeader, cx, tint } from '../components/ui'
import { useStagger } from '../hooks/useStagger'
import { latestIntegration, roadmapItemFor, type ViewProps } from './shared'

const STAGE: Record<PipelineStageName, { label: string; icon: ReactNode }> = {
  sandbox: { label: 'Sandbox', icon: <FlaskConical size={16} aria-hidden /> },
  staging: { label: 'Staging', icon: <Server size={16} aria-hidden /> },
  canary: { label: 'Canary', icon: <Split size={16} aria-hidden /> },
  production: { label: 'Production', icon: <Globe size={16} aria-hidden /> },
}

const ORDER: Record<PipelineStageStatus, number> = { pending: 0, running: 1, passed: 2 }

function StageCard({ stage, status }: { stage: PipelineStage; status: PipelineStageStatus }) {
  const s = STAGE[stage.name]
  const token = status === 'passed' ? 'ok' : status === 'running' ? 'accent' : 'border'
  return (
    <div
      className={cx(
        'relative flex h-full flex-col gap-2 rounded-xl border bg-surface p-3.5 shadow-sm transition-colors duration-300',
        status === 'passed' ? 'border-ok' : status === 'running' ? 'border-accent' : 'border-border',
      )}
      style={status !== 'pending' ? { backgroundColor: tint(token, 6) } : undefined}
      data-testid={`stage-${stage.name}`}
      data-status={status}
    >
      <div className="flex items-center justify-between gap-2">
        <span className={cx('flex items-center gap-2 text-[13px] font-semibold', status === 'pending' ? 'text-muted' : 'text-fg')}>
          <span
            className={cx('inline-flex h-7 w-7 items-center justify-center rounded-lg', status === 'passed' ? 'text-ok' : status === 'running' ? 'text-accent' : 'text-muted')}
            style={{ backgroundColor: tint(status === 'pending' ? 'muted' : token, 14) }}
          >
            {s.icon}
          </span>
          {s.label}
        </span>
        {status === 'passed' && <CheckCircle2 size={18} aria-label="passed" className="text-ok" />}
        {status === 'running' && <Loader2 size={18} aria-label="running" className="animate-spin text-accent motion-reduce:animate-none" />}
        {status === 'pending' && <Circle size={18} aria-label="pending" className="text-border" />}
      </div>
      <p className={cx('text-[12px] leading-snug', status === 'pending' ? 'text-muted' : 'text-fg')}>
        {status === 'running' ? 'Running…' : stage.note}
      </p>
    </div>
  )
}

export function PipelineView({ state, reducedMotion }: ViewProps) {
  const latest = latestIntegration(state)
  const pipelineId =
    state.step.pipeline?.integrationId ?? (latest && state.pipelines[latest.id] ? latest.id : Object.keys(state.pipelines).pop())
  const stages = pipelineId ? state.pipelines[pipelineId] : undefined
  const integration = state.integrations.find((i) => i.id === pipelineId)
  // Each stage takes two ticks: running, then passed.
  const ticks = useStagger((stages?.length ?? 0) * 2, 350, `${state.step.id}-${pipelineId}`, reducedMotion)

  if (!stages || !integration) {
    return <EmptyState icon={<Rocket size={22} />} title="No deployment yet" body="Deployments start after product and engineering both approve." />
  }

  const shown = (i: number, data: PipelineStageStatus): PipelineStageStatus => {
    const anim: PipelineStageStatus = ticks >= i * 2 + 2 ? 'passed' : ticks >= i * 2 + 1 ? 'running' : 'pending'
    return ORDER[anim] < ORDER[data] ? anim : data
  }
  const statuses = stages.map((s, i) => shown(i, s.status))
  const live = statuses.every((s) => s === 'passed')
  const roadmap = roadmapItemFor(state, integration.id)

  return (
    <div className="flex flex-col gap-5">
      <ViewHeader eyebrow={`Staged rollout · Loop ${integration.loop}`} title={integration.name} right={<InitiatedByBadge by={integration.initiatedBy} />} />

      <Card className="p-4 sm:p-5">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[12.5px] text-muted">
          <span>
            Trigger <strong className="font-medium text-fg">{integration.trigger}</strong>
          </span>
          <span>
            Flow{' '}
            <strong className="font-medium text-fg">
              {integration.source} → {integration.target}
            </strong>
          </span>
          {integration.approvals.pm && integration.approvals.eng && <Badge tone="ok">PM + Eng approved</Badge>}
        </div>
        <ol className="mt-4 grid grid-cols-[minmax(0,1fr)] gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-label="Deployment stages">
          {stages.map((s, i) => (
            <li key={s.name} className="relative">
              <StageCard stage={s} status={statuses[i]} />
              {i < stages.length - 1 && (
                <ChevronRight size={18} aria-hidden className="absolute -right-[15px] top-1/2 z-10 hidden -translate-y-1/2 text-muted xl:block" />
              )}
            </li>
          ))}
        </ol>
      </Card>

      {live && (
        <motion.div
          initial={reducedMotion ? false : { opacity: 0, y: 10, scale: 0.98 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          transition={{ type: 'spring', stiffness: 220, damping: 22 }}
          className="flex flex-col gap-3 rounded-xl border border-ok p-4 sm:flex-row sm:items-center sm:p-5"
          style={{ backgroundColor: tint('ok', 10) }}
          role="status"
          data-testid="live-banner"
        >
          <span className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-ok text-accent-contrast">
            <Rocket size={22} aria-hidden />
          </span>
          <div className="min-w-0">
            <p className="text-lg font-semibold text-ok">Live in production</p>
            {roadmap && (
              <p className="text-[13.5px] text-fg">
                Roadmap item shipped: <strong className="font-semibold">{roadmap.title}</strong>
              </p>
            )}
          </div>
          <div className="flex items-center gap-1.5 sm:ml-auto">
            <span className="relative flex h-2.5 w-2.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-ok opacity-60 motion-reduce:hidden" />
              <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-ok" />
            </span>
            <span className="text-[12px] font-medium text-ok">Healthy</span>
          </div>
        </motion.div>
      )}
    </div>
  )
}
