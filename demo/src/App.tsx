import { useCallback, useMemo, useState } from 'react'
import { MotionConfig } from 'framer-motion'
import { ArrowLeft, ArrowRight } from 'lucide-react'
import type { Step } from './scenario/types'
import { STEP_COUNT, computeState, sceneStart } from './engine/state'
import { TopBar } from './components/TopBar'
import { StoryPanel } from './components/StoryPanel'
import { TracePanel } from './components/TracePanel'
import { TitleCard } from './components/TitleCard'
import { cx, focusRing } from './components/ui'
import { ViewSwitch } from './views/ViewSwitch'
import { useHashState } from './hooks/useHashState'
import { useKeyboardNav } from './hooks/useKeyboardNav'
import { useReducedMotion } from './hooks/useReducedMotion'
import { useTheme } from './hooks/useTheme'

export default function App() {
  const nav = useHashState(STEP_COUNT)
  const { stepIndex, learningEnabled, go, next, back, setLearning } = nav
  const reducedMotion = useReducedMotion()
  const { theme, toggle: toggleTheme } = useTheme()
  const [titleOpen, setTitleOpen] = useState(() => !nav.hadStepInHash && nav.stepIndex === 0)

  const options = useMemo(() => ({ learningEnabled }), [learningEnabled])
  const state = useMemo(() => computeState(stepIndex, options), [stepIndex, options])
  const prev = useMemo(() => (stepIndex > 0 ? computeState(stepIndex - 1, options) : null), [stepIndex, options])

  const dismissTitle = useCallback(() => setTitleOpen(false), [])
  const onNext = useCallback(() => (titleOpen ? setTitleOpen(false) : next()), [titleOpen, next])
  const onBack = useCallback(() => {
    if (!titleOpen) back()
  }, [titleOpen, back])
  const onRestart = useCallback(() => {
    setTitleOpen(false)
    go(0)
  }, [go])
  useKeyboardNav({ onNext, onBack, onRestart })

  const onScene = useCallback((scene: Step['scene']) => go(sceneStart(scene)), [go])
  const isLast = stepIndex >= STEP_COUNT - 1
  const traceKey = `${state.step.id}:${learningEnabled ? 'on' : 'off'}`

  return (
    <MotionConfig reducedMotion="user">
      <div className="flex min-h-full flex-col bg-bg text-fg lg:h-screen lg:overflow-hidden">
        <TopBar
          stepIndex={stepIndex}
          stepCount={STEP_COUNT}
          scene={state.step.scene}
          onScene={onScene}
          learningEnabled={learningEnabled}
          onToggleLearning={() => setLearning(!learningEnabled)}
          theme={theme}
          onToggleTheme={toggleTheme}
        />

        <div className="flex min-h-0 flex-1 flex-col gap-4 p-4 pb-24 lg:flex-row lg:pb-4 2xl:gap-5 2xl:p-5">
          <main className="min-w-0 flex-1 lg:min-h-0 lg:overflow-y-auto lg:pr-1" aria-label="Demo view">
            <ViewSwitch
              state={state}
              prev={prev}
              reducedMotion={reducedMotion}
              onAdvance={next}
              onJump={go}
              onReplay={(on, idx) => setLearning(on, idx)}
            />
          </main>

          <aside className="flex w-full shrink-0 flex-col gap-4 lg:min-h-0 lg:w-[360px] 2xl:w-[420px]" aria-label="Presenter panel">
            <StoryPanel
              step={state.step}
              stepIndex={stepIndex}
              stepCount={STEP_COUNT}
              learningEnabled={learningEnabled}
              onNext={next}
              onBack={back}
              onRestart={onRestart}
            />
            <TracePanel trace={state.trace} traceLog={state.traceLog} resetKey={traceKey} reducedMotion={reducedMotion} />
          </aside>
        </div>

        {/* Phone: keep the advance control in reach without scrolling. */}
        <nav
          aria-label="Step controls"
          className="fixed inset-x-0 bottom-0 z-30 flex items-center gap-2 border-t border-border bg-surface px-4 py-2.5 shadow-lg lg:hidden"
        >
          <button
            type="button"
            onClick={back}
            disabled={stepIndex === 0}
            aria-label="Previous step"
            className={cx('inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-border text-fg disabled:opacity-40', focusRing)}
          >
            <ArrowLeft size={18} aria-hidden />
          </button>
          <p className="min-w-0 flex-1 truncate text-[13px] font-medium text-fg">{state.step.title}</p>
          {!isLast && (
            <button
              type="button"
              onClick={next}
              className={cx(
                'inline-flex h-10 shrink-0 items-center gap-1.5 rounded-lg px-4 text-[13px] font-semibold text-accent-contrast',
                state.step.gate?.kind === 'approve_pm' || state.step.gate?.kind === 'approve_eng' ? 'bg-ok' : 'bg-accent',
                focusRing,
              )}
            >
              {state.step.gate ? (state.step.gate.kind === 'approve_pm' ? 'Approve (PM)' : state.step.gate.kind === 'approve_eng' ? 'Approve (Eng)' : state.step.gate.label) : 'Next'}
              <ArrowRight size={16} aria-hidden />
            </button>
          )}
        </nav>

        {titleOpen && <TitleCard onStart={dismissTitle} reducedMotion={reducedMotion} />}
      </div>
    </MotionConfig>
  )
}
