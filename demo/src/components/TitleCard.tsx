import { useEffect, useRef } from 'react'
import { motion } from 'framer-motion'
import { ArrowRight, BrainCircuit, Network, Plug, Workflow, X } from 'lucide-react'
import { cx, focusRing, tint } from './ui'

export function TitleCard({ onStart, reducedMotion }: { onStart: () => void; reducedMotion: boolean }) {
  const startRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    startRef.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onStart()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onStart])

  const pillars = [
    { icon: <Workflow size={16} aria-hidden />, token: 'agent-llm', label: 'LLM agents', text: 'turn intent into a plan' },
    { icon: <Network size={16} aria-hidden />, token: 'agent-kg', label: 'Knowledge graph', text: 'remembers every integration' },
    { icon: <Plug size={16} aria-hidden />, token: 'agent-mcp', label: 'MCP agents', text: 'wrap partner APIs as tools' },
  ]

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto p-4"
      style={{ backgroundColor: tint('bg', 82), backdropFilter: 'blur(6px)' }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="title-card-heading"
    >
      <motion.div
        initial={reducedMotion ? false : { opacity: 0, y: 12, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ duration: 0.35, ease: 'easeOut' }}
        className="relative w-full max-w-2xl rounded-2xl border border-border bg-surface p-6 shadow-xl sm:p-10"
      >
        <button
          type="button"
          onClick={onStart}
          aria-label="Close introduction"
          className={cx('absolute right-3 top-3 rounded-lg p-1.5 text-muted hover:bg-surface-2 hover:text-fg', focusRing)}
        >
          <X size={18} aria-hidden />
        </button>

        <div className="flex items-center gap-2">
          <span className="inline-flex h-9 w-9 items-center justify-center rounded-lg bg-accent text-accent-contrast">
            <BrainCircuit size={20} aria-hidden />
          </span>
          <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-accent">DSI 2026 · Live demo</span>
        </div>

        <h1 id="title-card-heading" className="mt-5 text-2xl font-semibold leading-tight tracking-tight text-fg sm:text-[32px]">
          Agentic integration of platform APIs for digital product management
        </h1>
        <p className="mt-3 text-sm text-muted">Shiv Yadav · Anshul Kumar · Nitin Joglekar</p>

        <p className="mt-6 border-l-2 border-accent pl-4 text-base leading-relaxed text-fg sm:text-lg">
          Every integration teaches the platform; every new partner API is matched to the roadmap automatically.
        </p>

        <ul className="mt-6 grid gap-2 sm:grid-cols-3">
          {pillars.map((p) => (
            <li key={p.label} className="rounded-xl border border-border bg-surface-2 px-3 py-2.5">
              <span className="flex items-center gap-1.5 text-[13px] font-semibold" style={{ color: `var(--${p.token})` }}>
                {p.icon}
                {p.label}
              </span>
              <span className="mt-0.5 block text-xs text-muted">{p.text}</span>
            </li>
          ))}
        </ul>

        <div className="mt-8 flex flex-wrap items-center justify-between gap-4">
          <p className="text-xs text-muted">
            How to use: <kbd className="rounded border border-border bg-surface-2 px-1.5 py-0.5 font-sans">→</kbd> next,{' '}
            <kbd className="rounded border border-border bg-surface-2 px-1.5 py-0.5 font-sans">←</kbd> back
          </p>
          <button
            ref={startRef}
            type="button"
            onClick={onStart}
            className={cx(
              'group inline-flex items-center gap-2 rounded-xl bg-accent px-5 py-2.5 text-sm font-semibold text-accent-contrast shadow-sm hover:brightness-110',
              focusRing,
            )}
          >
            Start demo
            <ArrowRight size={16} aria-hidden className="transition-transform group-hover:translate-x-0.5" />
          </button>
        </div>
      </motion.div>
    </div>
  )
}
