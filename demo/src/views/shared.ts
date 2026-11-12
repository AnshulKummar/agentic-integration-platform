import type { DemoState, Integration, RoadmapItem } from '../scenario/types'
import { STEPS } from '../scenario/data'

export interface ViewProps {
  state: DemoState
  /** State at the previous step (null at step 0), for "what changed" highlights. */
  prev: DemoState | null
  reducedMotion: boolean
  /** Advance to the next step (satisfies the current gate). */
  onAdvance: () => void
  onJump: (index: number) => void
  /** Switch the ablation and jump, e.g. replay Loop 2 with learning OFF. */
  onReplay: (learningEnabled: boolean, index: number) => void
}

export function stepIndexById(id: string): number {
  const i = STEPS.findIndex((s) => s.id === id)
  if (i === -1) throw new Error(`Unknown step id ${id}`)
  return i
}

export function latestIntegration(state: DemoState): Integration | undefined {
  return state.integrations[state.integrations.length - 1]
}

export function roadmapItemFor(state: DemoState, integrationId: string): RoadmapItem | undefined {
  return state.roadmap.find((r) => r.integrationId === integrationId)
}

export function currentLoop(state: DemoState): 'loop1' | 'loop2' | null {
  const s = state.step.scene
  return s === 'loop1' || s === 'loop2' ? s : null
}
