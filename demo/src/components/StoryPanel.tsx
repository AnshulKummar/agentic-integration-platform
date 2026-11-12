import { motion } from 'framer-motion'
import { ArrowLeft, ArrowRight, BookOpen, Check, RotateCcw, Send, Wand2 } from 'lucide-react'
import type { Gate, Step } from '../scenario/types'
import { SCENES } from '../scenario/data'
import { Avatar, PEOPLE, cx, focusRing, tint } from './ui'

interface Props {
  step: Step
  stepIndex: number
  stepCount: number
  learningEnabled: boolean
  onNext: () => void
  onBack: () => void
  onRestart: () => void
}

export function GateButton({ gate, onClick, testId }: { gate: Gate; onClick: () => void; testId?: string }) {
  if (gate.kind === 'approve_pm' || gate.kind === 'approve_eng') {
    const who = gate.kind === 'approve_pm' ? 'pm' : 'eng'
    const p = PEOPLE[who]
    return (
      <button
        type="button"
        onClick={onClick}
        data-testid={testId}
        className={cx(
          'group flex w-full items-center gap-3 rounded-xl bg-ok px-3 py-2.5 text-left text-accent-contrast shadow-sm transition-[filter] hover:brightness-110',
          focusRing,
        )}
      >
        <span className="rounded-full bg-surface p-0.5">
          <Avatar who={who} size={30} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5 text-sm font-semibold">
            <Check size={16} strokeWidth={2.6} aria-hidden />
            {gate.label}
          </span>
          <span className="block text-[11px] opacity-85">
            Signed in as {p.name} · {p.short}
          </span>
        </span>
        <ArrowRight size={16} aria-hidden className="opacity-80 transition-transform group-hover:translate-x-0.5" />
      </button>
    )
  }
  const Icon = gate.kind === 'type_intent' ? Send : Wand2
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={testId}
      className={cx(
        'group flex w-full items-center justify-center gap-2 rounded-xl bg-accent px-4 py-2.5 text-sm font-semibold text-accent-contrast shadow-sm transition-[filter] hover:brightness-110',
        focusRing,
      )}
    >
      <Icon size={16} aria-hidden />
      {gate.label}
    </button>
  )
}

export function StoryPanel({ step, stepIndex, stepCount, learningEnabled, onNext, onBack, onRestart }: Props) {
  const scene = SCENES.find((s) => s.id === step.scene)
  const isLast = stepIndex >= stepCount - 1
  const ablated = !learningEnabled && Boolean(step.learningOff)

  return (
    <section aria-label="Story" className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-4 shadow-sm">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted">
          {scene?.label} · Step {stepIndex + 1}
        </p>
        {ablated && (
          <span
            className="rounded-md px-1.5 py-0.5 text-[11px] font-semibold text-warn"
            style={{ backgroundColor: tint('warn', 13) }}
          >
            Ablation
          </span>
        )}
      </div>

      <motion.div
          key={`${step.id}-${learningEnabled}`}
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.25 }}
          className="flex flex-col gap-2.5"
        >
          <h1 className="text-lg font-semibold leading-snug tracking-tight text-fg 2xl:text-xl">{step.title}</h1>
          <p className="text-[13.5px] leading-relaxed text-fg 2xl:text-[15px]">{step.narration}</p>
          {step.paperRef && (
            <p>
              <span className="inline-flex max-w-full items-start gap-1.5 rounded-2xl border border-border bg-surface-2 px-2.5 py-1 text-[11.5px] font-medium text-muted">
                <BookOpen size={13} aria-hidden className="mt-px shrink-0 text-accent" />
                <span>Paper: {step.paperRef}</span>
              </span>
            </p>
          )}
        </motion.div>

      <div className="mt-1 flex flex-col gap-2">
        {step.gate ? (
          <GateButton gate={step.gate} onClick={onNext} testId="advance" />
        ) : (
          !isLast && (
            <button
              type="button"
              onClick={onNext}
              data-testid="advance"
              className={cx(
                'group flex w-full items-center justify-center gap-2 rounded-xl bg-accent px-4 py-2.5 text-sm font-semibold text-accent-contrast shadow-sm transition-[filter] hover:brightness-110',
                focusRing,
              )}
            >
              Next
              <ArrowRight size={16} aria-hidden className="transition-transform group-hover:translate-x-0.5" />
            </button>
          )
        )}
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onBack}
            disabled={stepIndex === 0}
            className={cx(
              'inline-flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-[13px] font-medium text-fg transition-colors hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-40',
              focusRing,
            )}
          >
            <ArrowLeft size={14} aria-hidden /> Back
          </button>
          <button
            type="button"
            onClick={onRestart}
            className={cx(
              'inline-flex flex-1 items-center justify-center gap-1.5 rounded-lg border px-3 py-1.5 text-[13px] font-medium transition-colors',
              isLast ? 'border-accent text-accent hover:bg-surface-2' : 'border-border text-fg hover:bg-surface-2',
              focusRing,
            )}
          >
            <RotateCcw size={14} aria-hidden /> Restart
          </button>
        </div>
        <p className="hidden text-center text-[11px] text-muted sm:block">
          <kbd className="font-sans">→</kbd> / <kbd className="font-sans">Space</kbd> next · <kbd className="font-sans">←</kbd> back ·{' '}
          <kbd className="font-sans">R</kbd> restart
        </p>
      </div>
    </section>
  )
}
