import { useCallback, useEffect, useRef, useState } from 'react'

export interface HashState {
  /** 0-based step index. */
  stepIndex: number
  learningEnabled: boolean
}

export interface ParsedHash extends HashState {
  /** True when the hash carried an explicit step (shared link or refresh). */
  hasStep: boolean
}

/** Parses `#s=7&kg=off`. `s` is 1-based, matching the on-screen "7 / 22" counter. */
export function parseHash(hash: string, stepCount: number): ParsedHash {
  const params = new URLSearchParams(hash.replace(/^#/, ''))
  const raw = params.get('s')
  const n = raw === null ? Number.NaN : Number.parseInt(raw, 10)
  const hasStep = Number.isFinite(n)
  const stepIndex = hasStep ? Math.max(0, Math.min(stepCount - 1, n - 1)) : 0
  const learningEnabled = params.get('kg') !== 'off'
  return { stepIndex, learningEnabled, hasStep }
}

export function serializeHash(state: HashState): string {
  const parts = [`s=${state.stepIndex + 1}`]
  if (!state.learningEnabled) parts.push('kg=off')
  return `#${parts.join('&')}`
}

function readHash(): string {
  try {
    return window.location.hash
  } catch {
    return ''
  }
}

function writeHash(hash: string): void {
  try {
    if (window.location.hash === hash) return
    window.history.replaceState(null, '', hash)
  } catch (err) {
    console.warn('Could not update the URL hash', err)
  }
}

/** Step index + ablation switch, mirrored to the URL hash so refreshes and shared links resume. */
export function useHashState(stepCount: number) {
  const [initial] = useState(() => parseHash(readHash(), stepCount))
  const [state, setState] = useState<HashState>({
    stepIndex: initial.stepIndex,
    learningEnabled: initial.learningEnabled,
  })
  // A fresh visit (no hash) keeps a clean URL until the presenter does something.
  const untouched = useRef(state)

  useEffect(() => {
    // Identity check (not a run counter) so StrictMode's double effects don't write early.
    if (!initial.hasStep && state === untouched.current) return
    writeHash(serializeHash(state))
  }, [state, initial.hasStep])

  useEffect(() => {
    const onHashChange = () => {
      const parsed = parseHash(readHash(), stepCount)
      if (!parsed.hasStep) return
      setState((prev) =>
        prev.stepIndex === parsed.stepIndex && prev.learningEnabled === parsed.learningEnabled
          ? prev
          : { stepIndex: parsed.stepIndex, learningEnabled: parsed.learningEnabled },
      )
    }
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [stepCount])

  const clamp = useCallback((i: number) => Math.max(0, Math.min(stepCount - 1, i)), [stepCount])
  const go = useCallback((index: number) => setState((s) => ({ ...s, stepIndex: clamp(index) })), [clamp])
  const next = useCallback(() => setState((s) => ({ ...s, stepIndex: clamp(s.stepIndex + 1) })), [clamp])
  const back = useCallback(() => setState((s) => ({ ...s, stepIndex: clamp(s.stepIndex - 1) })), [clamp])
  const setLearning = useCallback(
    (learningEnabled: boolean, jumpTo?: number) =>
      setState((s) => ({ learningEnabled, stepIndex: jumpTo === undefined ? s.stepIndex : clamp(jumpTo) })),
    [clamp],
  )

  return { ...state, hadStepInHash: initial.hasStep, go, next, back, setLearning }
}
