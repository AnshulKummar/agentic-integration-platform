import { useEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { Activity, ArrowRight, ChevronDown, GitFork } from 'lucide-react'
import type { TraceEvent } from '../scenario/types'
import { useStagger } from '../hooks/useStagger'
import { AgentChip, cx, focusRing, tint } from './ui'

interface Props {
  trace: TraceEvent[]
  traceLog: TraceEvent[]
  resetKey: string
  reducedMotion: boolean
}

function toneStyle(tone: TraceEvent['tone']) {
  if (tone === 'warn') return { className: 'border-warn', style: { backgroundColor: tint('warn', 9) } }
  if (tone === 'success') return { className: 'border-ok', style: { backgroundColor: tint('ok', 8) } }
  return { className: 'border-border', style: undefined }
}

function TraceRow({ ev, compact = false }: { ev: TraceEvent; compact?: boolean }) {
  const t = toneStyle(ev.tone)
  return (
    <div
      className={cx('rounded-lg border-l-2 px-2.5', compact ? 'py-1' : 'py-1.5', t.className)}
      style={t.style}
      data-tone={ev.tone ?? 'info'}
    >
      <div className="flex flex-wrap items-center gap-1">
        <AgentChip agent={ev.agent} size="sm" />
        {ev.to && (
          <>
            <ArrowRight size={12} className="text-muted" aria-label="to" />
            <AgentChip agent={ev.to} size="sm" />
          </>
        )}
        {ev.lane !== undefined && (
          <span
            className="ml-auto inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-accent"
            style={{ backgroundColor: tint('accent', 10) }}
            title={`Runs in parallel (lane ${ev.lane})`}
          >
            <GitFork size={10} aria-hidden />
            parallel · {ev.lane}
          </span>
        )}
      </div>
      <p
        className={cx(
          'mt-1 leading-snug',
          compact ? 'text-[11.5px]' : 'text-[12.5px]',
          ev.tone === 'warn' ? 'text-warn' : ev.tone === 'success' ? 'text-ok' : 'text-fg',
        )}
      >
        {ev.text}
      </p>
    </div>
  )
}

export function TracePanel({ trace, traceLog, resetKey, reducedMotion }: Props) {
  const shown = useStagger(trace.length, 350, resetKey, reducedMotion)
  const [logOpen, setLogOpen] = useState(false)
  const hasParallel = trace.some((t) => t.lane !== undefined)
  const listRef = useRef<HTMLDivElement>(null)

  // Keep the newest event in view as rows are revealed.
  useEffect(() => {
    const el = listRef.current
    if (!el || shown === 0) return
    el.scrollTo?.({ top: el.scrollHeight, behavior: reducedMotion ? 'auto' : 'smooth' })
  }, [shown, reducedMotion])

  return (
    <section
      aria-label="Agent trace"
      className="flex min-h-0 flex-col rounded-xl border border-border bg-surface shadow-sm lg:flex-1"
    >
      <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-2.5">
        <h2 className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
          <Activity size={14} aria-hidden className="text-accent" />
          Agent trace
        </h2>
        {hasParallel && (
          <span className="text-[11px] text-muted">
            <GitFork size={11} aria-hidden className="mr-1 inline text-accent" />
            parallel lanes (Fig. 1)
          </span>
        )}
      </div>

      <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto px-3 py-3 lg:min-h-[120px]" aria-live="polite" aria-relevant="additions" data-testid="trace">
        {trace.length === 0 ? (
          <p className="px-1 py-2 text-[12.5px] text-muted">No agent activity on this step.</p>
        ) : (
          <ol className="flex flex-col gap-1.5">
            {trace.slice(0, shown).map((ev) => (
              <motion.li
                key={ev.id}
                initial={reducedMotion ? false : { opacity: 0, x: -6 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ duration: 0.22 }}
                style={ev.lane !== undefined ? { marginLeft: 8 } : undefined}
              >
                <TraceRow ev={ev} />
              </motion.li>
            ))}
          </ol>
        )}
      </div>

      <div className="border-t border-border">
        <button
          type="button"
          onClick={() => setLogOpen((o) => !o)}
          aria-expanded={logOpen}
          aria-controls="trace-full-log"
          className={cx(
            'flex w-full items-center justify-between gap-2 rounded-b-xl px-4 py-2 text-[12px] font-medium text-muted hover:bg-surface-2 hover:text-fg',
            focusRing,
          )}
        >
          <span>Full log · {traceLog.length} events</span>
          <ChevronDown size={14} aria-hidden className={cx('transition-transform', logOpen && 'rotate-180')} />
        </button>
        {logOpen && (
          <ol id="trace-full-log" className="flex max-h-60 flex-col gap-1 overflow-y-auto px-3 pb-3">
            {traceLog.length === 0 && <li className="px-1 text-[12px] text-muted">Nothing logged yet.</li>}
            {traceLog.map((ev, i) => (
              <li key={`${ev.id}-${i}`}>
                <TraceRow ev={ev} compact />
              </li>
            ))}
          </ol>
        )}
      </div>
    </section>
  )
}
