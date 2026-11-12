import { LayoutGroup, motion } from 'framer-motion'
import { AlertTriangle, CheckCircle2, CircleDashed, Eye, Loader, Lock, Sparkles, Target } from 'lucide-react'
import type { ReactNode } from 'react'
import type { RoadmapColumn, RoadmapItem, RoadmapStatus } from '../scenario/types'
import { Badge, type BadgeTone, ViewHeader, cx, tint } from '../components/ui'
import type { ViewProps } from './shared'

const COLUMNS: { id: RoadmapColumn; label: string; hint: string }[] = [
  { id: 'now', label: 'Now', hint: 'This quarter' },
  { id: 'next', label: 'Next', hint: 'Next quarter' },
  { id: 'later', label: 'Later', hint: 'Exploring' },
  { id: 'shipped', label: 'Shipped', hint: 'Live' },
]

const STATUS: Record<RoadmapStatus, { label: string; tone: BadgeTone; icon: ReactNode }> = {
  planned: { label: 'Planned', tone: 'neutral', icon: <CircleDashed size={11} aria-hidden /> },
  blocked: { label: 'Blocked', tone: 'bad', icon: <Lock size={11} aria-hidden /> },
  in_progress: { label: 'In progress', tone: 'llm', icon: <Loader size={11} aria-hidden /> },
  in_review: { label: 'In review', tone: 'governance', icon: <Eye size={11} aria-hidden /> },
  shipped: { label: 'Shipped', tone: 'ok', icon: <CheckCircle2 size={11} aria-hidden /> },
}

function RoadmapCard({ item, emphasis, reducedMotion }: { item: RoadmapItem; emphasis: boolean; reducedMotion: boolean }) {
  const s = STATUS[item.status]
  const fit = item.fitScore
  const fitTone: BadgeTone = fit === undefined ? 'neutral' : fit >= 0.8 ? 'ok' : fit >= 0.5 ? 'warn' : 'neutral'
  const impactOnly = fit === undefined && item.fitRationale
  return (
    <motion.article
      layout={!reducedMotion}
      layoutId={reducedMotion ? undefined : item.id}
      transition={{ type: 'spring', stiffness: 260, damping: 30 }}
      data-testid={`roadmap-${item.id}`}
      data-highlighted={emphasis || undefined}
      className={cx(
        'rounded-xl border bg-surface p-3 shadow-sm',
        emphasis ? 'border-accent ring-2 ring-accent' : 'border-border',
      )}
      style={emphasis ? { boxShadow: `0 0 0 6px ${tint('accent', 12)}` } : undefined}
    >
      <div className="flex items-center justify-between gap-2">
        <Badge tone={s.tone} icon={s.icon}>
          {s.label}
        </Badge>
        <span className="shrink-0 text-[11px] tabular-nums text-muted">{item.quarter}</span>
      </div>
      <h3 className="mt-1.5 text-[13.5px] font-semibold leading-snug text-fg">{item.title}</h3>
      <blockquote className="mt-2 border-l-2 border-border pl-2 text-[12px] italic leading-snug text-muted">
        “{item.intent}”
      </blockquote>
      {item.status === 'blocked' && item.blockedReason && (
        <p
          className="mt-2 flex gap-1.5 rounded-lg px-2 py-1.5 text-[11.5px] leading-snug text-bad"
          style={{ backgroundColor: tint('bad', 9) }}
        >
          <Lock size={12} aria-hidden className="mt-0.5 shrink-0" />
          {item.blockedReason}
        </p>
      )}
      {fit !== undefined && (
        <div className="mt-2.5 rounded-lg border border-border bg-surface-2 px-2 py-1.5">
          <div className="flex items-center justify-between text-[11px]">
            <span className="flex items-center gap-1 font-semibold text-fg">
              <Target size={11} aria-hidden className="text-accent" />
              Roadmap fit
            </span>
            <span className="font-semibold tabular-nums text-fg">{fit.toFixed(2)}</span>
          </div>
          <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-surface" role="meter" aria-label={`Fit score for ${item.title}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(fit * 100)}>
            <motion.div
              className="h-full rounded-full"
              initial={reducedMotion ? false : { width: 0 }}
              animate={{ width: `${fit * 100}%` }}
              transition={{ duration: 0.8, ease: 'easeOut' }}
              style={{ backgroundColor: `var(--${fitTone === 'ok' ? 'ok' : fitTone === 'warn' ? 'warn' : 'muted'})` }}
            />
          </div>
          {item.fitRationale && <p className="mt-1.5 text-[11.5px] leading-snug text-muted">{item.fitRationale}</p>}
        </div>
      )}
      {impactOnly && (
        <p
          className={cx('mt-2 flex gap-1.5 rounded-lg px-2 py-1.5 text-[11.5px] leading-snug', item.fitRationale?.startsWith('Impacted') ? 'text-warn' : 'text-ok')}
          style={{ backgroundColor: tint(item.fitRationale?.startsWith('Impacted') ? 'warn' : 'ok', 9) }}
        >
          {item.fitRationale?.startsWith('Impacted') ? (
            <AlertTriangle size={12} aria-hidden className="mt-0.5 shrink-0" />
          ) : (
            <Sparkles size={12} aria-hidden className="mt-0.5 shrink-0" />
          )}
          {item.fitRationale}
        </p>
      )}
      <p className="mt-2.5 truncate text-[11px] text-muted">{item.owner}</p>
    </motion.article>
  )
}

export function RoadmapView({ state, prev, reducedMotion }: ViewProps) {
  const changed = new Set<string>()
  for (const item of state.roadmap) {
    const before = prev?.roadmap.find((r) => r.id === item.id)
    if (prev && JSON.stringify(before) !== JSON.stringify(item)) changed.add(item.id)
  }
  const emphasis = (id: string) => changed.has(id) || state.highlight.includes(id)

  return (
    <div className="flex flex-col gap-4">
      <ViewHeader
        eyebrow="Product roadmap · CRM Platform"
        title="Stored intents, in business language"
        right={
          <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted">
            {(['blocked', 'in_progress', 'in_review', 'shipped'] as const).map((k) => (
              <Badge key={k} tone={STATUS[k].tone} icon={STATUS[k].icon}>
                {STATUS[k].label}
              </Badge>
            ))}
          </div>
        }
      />
      <LayoutGroup>
        <div className="grid grid-cols-[minmax(0,1fr)] gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {COLUMNS.map((col) => {
            const items = state.roadmap.filter((r) => r.column === col.id)
            return (
              <section
                key={col.id}
                aria-label={`${col.label} column`}
                className="flex min-w-0 flex-col gap-2 rounded-xl border border-border bg-surface-2 p-2"
              >
                <header className="flex items-center justify-between px-1.5 pt-0.5">
                  <h3 className="text-[12px] font-semibold uppercase tracking-[0.08em] text-fg">
                    {col.label}
                    <span className="ml-1.5 font-normal normal-case tracking-normal text-muted">{col.hint}</span>
                  </h3>
                  <span className="rounded-full bg-surface px-1.5 text-[11px] font-semibold tabular-nums text-muted ring-1 ring-border">
                    {items.length}
                  </span>
                </header>
                {items.map((item) => (
                  <RoadmapCard key={item.id} item={item} emphasis={emphasis(item.id)} reducedMotion={reducedMotion} />
                ))}
                {items.length === 0 && <p className="px-2 py-4 text-center text-[12px] text-muted">Nothing here yet</p>}
              </section>
            )
          })}
        </div>
      </LayoutGroup>
    </div>
  )
}
