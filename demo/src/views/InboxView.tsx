import { motion } from 'framer-motion'
import { AlertTriangle, ArrowRight, Calendar, CheckCircle2, Eye, Minus, PenLine, Plus, Radar, Target, Wrench } from 'lucide-react'
import type { ReactNode } from 'react'
import type { ApiChange, ApiRelease } from '../scenario/types'
import { SYSTEM_LABELS } from '../scenario/data'
import { Badge, Card, EmptyState, SectionTitle, ViewHeader, cx, tint } from '../components/ui'
import type { ViewProps } from './shared'

const CHANGE: Record<ApiChange['kind'], { icon: ReactNode; token: string; text: string; label: string }> = {
  added: { icon: <Plus size={12} strokeWidth={2.6} aria-hidden />, token: 'ok', text: 'text-ok', label: 'Added' },
  deprecated: { icon: <Minus size={12} strokeWidth={2.6} aria-hidden />, token: 'warn', text: 'text-warn', label: 'Deprecated' },
  changed: { icon: <PenLine size={12} aria-hidden />, token: 'agent-llm', text: 'text-agent-llm', label: 'Changed' },
}

function StatusPill({ status }: { status: ApiRelease['status'] }) {
  if (status === 'new') {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-semibold text-accent" style={{ backgroundColor: tint('accent', 12) }}>
        <span className="relative flex h-2 w-2">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent opacity-60 motion-reduce:hidden" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-accent" />
        </span>
        New release
      </span>
    )
  }
  if (status === 'analyzing') return <Badge tone="llm">Analyzing…</Badge>
  return (
    <Badge tone="ok" icon={<CheckCircle2 size={11} aria-hidden />}>
      Analyzed
    </Badge>
  )
}

function ReleaseCard({ rel, reducedMotion }: { rel: ApiRelease; reducedMotion: boolean }) {
  return (
    <Card className="p-4 sm:p-5" testId={`release-${rel.id}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">API release</p>
          <h3 className="mt-0.5 flex flex-wrap items-center gap-2 text-lg font-semibold text-fg">
            {rel.app}
            <span className="inline-flex items-center gap-1.5 font-mono text-[13px] font-medium">
              <span className="text-muted line-through decoration-1">{rel.previousVersion}</span>
              <ArrowRight size={13} aria-label="to" className="text-muted" />
              <span className="text-accent">{rel.version}</span>
            </span>
          </h3>
          <p className="mt-1 flex items-center gap-1.5 text-[12px] text-muted">
            <Calendar size={12} aria-hidden />
            Published {rel.publishedAt}
          </p>
        </div>
        <StatusPill status={rel.status} />
      </div>

      <div className="mt-4">
        <SectionTitle>Changes in spec</SectionTitle>
        <ul className="mt-2 flex flex-col gap-1.5">
          {rel.changes.map((c, i) => {
            const k = CHANGE[c.kind]
            return (
              <motion.li
                key={`${c.method}-${c.path}`}
                initial={reducedMotion ? false : { opacity: 0, x: -6 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: i * 0.08 }}
                className="flex flex-wrap items-center gap-x-2.5 gap-y-1 rounded-lg border border-border px-2.5 py-2"
                style={{ backgroundColor: tint(k.token, 6) }}
              >
                <span className={cx('inline-flex h-5 w-5 items-center justify-center rounded-md', k.text)} style={{ backgroundColor: tint(k.token, 16) }} title={k.label}>
                  {k.icon}
                </span>
                <span className="sr-only">{k.label}:</span>
                <span className="w-16 shrink-0 font-mono text-[11px] font-semibold text-muted">{c.method}</span>
                <code className={cx('font-mono text-[12.5px] font-medium', c.kind === 'deprecated' ? 'text-warn line-through decoration-1' : 'text-fg')}>{c.path}</code>
                <span className="basis-full text-[12px] text-muted sm:ml-auto sm:basis-auto">{c.summary}</span>
              </motion.li>
            )
          })}
        </ul>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <div>
          <SectionTitle icon={<Wrench size={12} aria-hidden className="text-agent-mcp" />}>Generated MCP tools</SectionTitle>
          {rel.mcpTools && rel.mcpTools.length > 0 ? (
            <ul className="mt-2 flex flex-wrap gap-1.5">
              {rel.mcpTools.map((t, i) => (
                <motion.li
                  key={t}
                  initial={reducedMotion ? false : { opacity: 0, scale: 0.9 }}
                  animate={{ opacity: 1, scale: 1 }}
                  transition={{ delay: 0.2 + i * 0.1 }}
                >
                  <code className="inline-block rounded-md border px-2 py-1 font-mono text-[11.5px] text-agent-mcp" style={{ borderColor: tint('agent-mcp', 35), backgroundColor: tint('agent-mcp', 9) }}>
                    {t}()
                  </code>
                </motion.li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-[12px] text-muted">Not generated yet. The MCP agent wraps new endpoints after it reads the spec.</p>
          )}
        </div>
        <div>
          <SectionTitle icon={<Target size={12} aria-hidden className="text-accent" />}>Impact on our product</SectionTitle>
          {rel.impact && rel.impact.length > 0 ? (
            <ul className="mt-2 flex flex-col gap-1.5">
              {rel.impact.map((line) => {
                const breaking = /breaking|deprecat/i.test(line)
                return (
                  <li key={line} className={cx('flex gap-1.5 text-[12.5px] leading-snug', breaking ? 'text-warn' : 'text-fg')}>
                    {breaking ? (
                      <AlertTriangle size={13} aria-hidden className="mt-0.5 shrink-0" />
                    ) : (
                      <CheckCircle2 size={13} aria-hidden className="mt-0.5 shrink-0 text-ok" />
                    )}
                    {line}
                  </li>
                )
              })}
            </ul>
          ) : (
            <p className="mt-2 text-[12px] text-muted">Pending roadmap match.</p>
          )}
        </div>
      </div>
    </Card>
  )
}

export function InboxView({ state, reducedMotion }: ViewProps) {
  const watched = ['sys-esign', 'sys-billing', 'sys-tax']
  const releasedApps = new Set(state.apiReleases.map((r) => r.app))
  return (
    <div className="flex flex-col gap-4">
      <ViewHeader eyebrow="Partner API watch" title="New partner capabilities, read as they ship" />
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4 xl:grid-cols-[minmax(0,1fr)_260px]">
        <div className="flex min-w-0 flex-col gap-4">
          {state.apiReleases.length === 0 ? (
            <EmptyState icon={<Radar size={22} />} title="No new releases" body="The MCP agent polls partner OpenAPI specs and changelogs." />
          ) : (
            [...state.apiReleases].reverse().map((r) => <ReleaseCard key={r.id} rel={r} reducedMotion={reducedMotion} />)
          )}
        </div>
        <Card className="h-fit p-4">
          <SectionTitle icon={<Eye size={12} aria-hidden className="text-accent" />}>Watching</SectionTitle>
          <ul className="mt-2 flex flex-col divide-y divide-border">
            {watched.map((id) => {
              const label = SYSTEM_LABELS[id]
              const hasNew = releasedApps.has(label)
              return (
                <li key={id} className="flex items-center justify-between gap-2 py-2 text-[12.5px]">
                  <span className="font-medium text-fg">{label}</span>
                  {hasNew ? <Badge tone="accent">1 release</Badge> : <span className="text-[11px] text-muted">No changes</span>}
                </li>
              )
            })}
          </ul>
          <p className="mt-2 text-[11px] leading-snug text-muted">Specs are diffed on publish; new endpoints become MCP tools automatically.</p>
        </Card>
      </div>
    </div>
  )
}
