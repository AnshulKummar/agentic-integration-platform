import { BrainCircuit, Moon, Sun, Workflow } from 'lucide-react'
import type { Step } from '../scenario/types'
import { SCENES } from '../scenario/data'
import type { Theme } from '../hooks/useTheme'
import { cx, focusRing, tint } from './ui'

interface Props {
  stepIndex: number
  stepCount: number
  scene: Step['scene']
  onScene: (scene: Step['scene']) => void
  learningEnabled: boolean
  onToggleLearning: () => void
  theme: Theme
  onToggleTheme: () => void
}

export function TopBar({ stepIndex, stepCount, scene, onScene, learningEnabled, onToggleLearning, theme, onToggleTheme }: Props) {
  const progress = ((stepIndex + 1) / stepCount) * 100
  return (
    <header className="relative z-20 border-b border-border bg-surface">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2.5 lg:flex-nowrap lg:px-5">
        {/* Product mark */}
        <div className="flex min-w-0 items-center gap-2.5">
          <span className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent text-accent-contrast">
            <Workflow size={18} strokeWidth={2.2} aria-hidden />
          </span>
          <div className="min-w-0 leading-tight">
            <p className="truncate text-sm font-semibold tracking-tight text-fg">
              PMAIP <span className="font-normal text-muted">· Agentic Integration Demo</span>
            </p>
          </div>
        </div>

        {/* Right cluster on mobile sits on the first row */}
        <div className="ml-auto flex items-center gap-2 lg:order-last">
          <span className="text-xs font-medium tabular-nums text-muted" data-testid="step-counter" aria-live="off">
            {stepIndex + 1} / {stepCount}
          </span>
          <button
            type="button"
            onClick={onToggleTheme}
            aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
            className={cx(
              'inline-flex h-8 w-8 items-center justify-center rounded-lg border border-border text-muted transition-colors hover:bg-surface-2 hover:text-fg',
              focusRing,
            )}
          >
            {theme === 'dark' ? <Sun size={16} aria-hidden /> : <Moon size={16} aria-hidden />}
          </button>
        </div>

        {/* Scene tabs */}
        <nav aria-label="Scenes" className="order-last -mx-1 w-full overflow-x-auto [scrollbar-width:none] lg:order-none lg:mx-auto lg:w-auto [&::-webkit-scrollbar]:hidden">
          <ol className="flex min-w-max items-center gap-1 px-1 py-0.5">
            {SCENES.map((s, i) => {
              const active = s.id === scene
              return (
                <li key={s.id}>
                  <button
                    type="button"
                    onClick={() => onScene(s.id)}
                    aria-current={active ? 'step' : undefined}
                    className={cx(
                      'inline-flex items-center gap-2 whitespace-nowrap rounded-lg px-3 py-1.5 text-[13px] font-medium transition-colors',
                      active ? 'bg-surface-2 text-fg shadow-sm ring-1 ring-border' : 'text-muted hover:bg-surface-2 hover:text-fg',
                      focusRing,
                    )}
                  >
                    <span
                      className={cx(
                        'inline-flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-semibold tabular-nums',
                        active ? 'bg-accent text-accent-contrast' : 'bg-surface-2 text-muted ring-1 ring-border',
                      )}
                    >
                      {i + 1}
                    </span>
                    {s.label}
                  </button>
                </li>
              )
            })}
          </ol>
        </nav>

        {/* Ablation switch */}
        <button
          type="button"
          role="switch"
          aria-checked={learningEnabled}
          onClick={onToggleLearning}
          className={cx(
            'inline-flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-[13px] font-medium transition-colors',
            learningEnabled ? 'border-border text-fg hover:bg-surface-2' : 'border-warn text-warn',
            focusRing,
          )}
          style={learningEnabled ? undefined : { backgroundColor: tint('warn', 10) }}
          title="Ablation: turn off knowledge-graph write-back and replay"
        >
          <BrainCircuit size={16} aria-hidden className={learningEnabled ? 'text-agent-kg' : 'text-warn'} />
          <span className="whitespace-nowrap">
            KG learning: <strong className="font-semibold">{learningEnabled ? 'ON' : 'OFF'}</strong>
          </span>
          <span
            aria-hidden
            className={cx(
              'relative inline-flex h-4 w-7 items-center rounded-full transition-colors',
              learningEnabled ? 'bg-ok' : 'bg-surface-2 ring-1 ring-border',
            )}
          >
            <span
              className={cx(
                'absolute h-3 w-3 rounded-full bg-surface shadow transition-transform',
                learningEnabled ? 'translate-x-3.5' : 'translate-x-0.5',
              )}
            />
          </span>
        </button>
      </div>
      <div className="absolute inset-x-0 bottom-0 h-0.5 bg-surface-2" aria-hidden>
        <div className="h-full bg-accent transition-[width] duration-500 ease-out" style={{ width: `${progress}%` }} />
      </div>
    </header>
  )
}
