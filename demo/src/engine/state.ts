// Pure state engine: folds steps 0..index into a DemoState.
// Deterministic, so Back/Reset/ablation replays are just recomputation.
import type {
  ApiRelease,
  DemoOptions,
  DemoState,
  Integration,
  KGEdge,
  KGNode,
  MappingRow,
  RoadmapItem,
  Step,
  StepEffects,
} from '../scenario/types'
import { SEED_EDGES, SEED_NODES, SEED_ROADMAP, STEPS } from '../scenario/data'

/** The step as it plays under the given options (applies the ablation overrides). */
export function resolveStep(step: Step, options: DemoOptions): Step {
  if (options.learningEnabled || !step.learningOff) return step
  const { learningOff, ...base } = step
  return { ...base, ...learningOff }
}

function upsertById<T extends { id: string }>(list: T[], patch: Partial<T> & { id: string }, create: () => T): T[] {
  const idx = list.findIndex((x) => x.id === patch.id)
  if (idx === -1) return [...list, { ...create(), ...patch }]
  const next = list.slice()
  next[idx] = { ...next[idx], ...patch }
  return next
}

function mergeMappings(rows: MappingRow[], patch: MappingRow[]): MappingRow[] {
  const out = rows.slice()
  for (const row of patch) {
    const idx = out.findIndex((r) => r.id === row.id)
    if (idx === -1) out.push(row)
    else out[idx] = row
  }
  return out
}

const emptyIntegration = (id: string): Integration => ({
  id,
  name: id,
  loop: 1,
  source: '',
  target: '',
  trigger: '',
  status: 'planned',
  initiatedBy: 'user_intent',
  mappings: [],
  code: '',
  tests: [],
  risks: [],
  approvals: { pm: null, eng: null },
  humanEdits: 0,
  turns: 0,
  confidence: 0,
})

const emptyRelease = (id: string): ApiRelease => ({
  id,
  app: '',
  version: '',
  previousVersion: '',
  publishedAt: '',
  status: 'new',
  changes: [],
})

type Accum = Omit<DemoState, 'stepIndex' | 'step' | 'options' | 'trace' | 'highlight'>

function applyEffects(acc: Accum, fx: StepEffects): Accum {
  let { nodes, edges, roadmap, apiReleases, integrations, pipelines, chat, traceLog } = acc

  if (fx.kg) {
    const addNodes: KGNode[] = (fx.kg.addNodes ?? []).filter((nn) => !nodes.some((x) => x.id === nn.id))
    nodes = [...nodes, ...addNodes]
    for (const u of fx.kg.updateNodes ?? []) {
      nodes = nodes.map((x) => (x.id === u.id ? { ...x, ...u } : x))
    }
    const addEdges: KGEdge[] = (fx.kg.addEdges ?? []).filter((ee) => !edges.some((x) => x.id === ee.id))
    edges = [...edges, ...addEdges]
  }
  for (const r of fx.roadmap ?? []) {
    roadmap = upsertById<RoadmapItem>(roadmap, r, () => {
      throw new Error(`Unknown roadmap item ${r.id}`)
    })
  }
  if (fx.apiRelease) apiReleases = upsertById(apiReleases, fx.apiRelease, () => emptyRelease(fx.apiRelease!.id))
  if (fx.integration) {
    integrations = upsertById(integrations, fx.integration, () => emptyIntegration(fx.integration!.id))
  }
  if (fx.mappings) {
    const { integrationId, rows } = fx.mappings
    integrations = integrations.map((i) => (i.id === integrationId ? { ...i, mappings: mergeMappings(i.mappings, rows) } : i))
  }
  if (fx.pipeline) pipelines = { ...pipelines, [fx.pipeline.integrationId]: fx.pipeline.stages }
  if (fx.chat) chat = [...chat, ...fx.chat]
  if (fx.trace) traceLog = [...traceLog, ...fx.trace]

  return { ...acc, nodes, edges, roadmap, apiReleases, integrations, pipelines, chat, traceLog }
}

export function computeState(index: number, options: DemoOptions, steps: Step[] = STEPS): DemoState {
  if (steps.length === 0) throw new Error('Scenario has no steps')
  const clamped = Math.max(0, Math.min(index, steps.length - 1))
  let acc: Accum = {
    nodes: SEED_NODES,
    edges: SEED_EDGES,
    roadmap: SEED_ROADMAP,
    apiReleases: [],
    integrations: [],
    pipelines: {},
    chat: [],
    traceLog: [],
    kgHistory: [],
  }
  for (let i = 0; i <= clamped; i++) {
    acc = applyEffects(acc, resolveStep(steps[i], options))
    acc = {
      ...acc,
      kgHistory: [
        ...acc.kgHistory,
        {
          stepIndex: i,
          nodes: acc.nodes.length,
          edges: acc.edges.length,
          patterns: acc.nodes.filter((x) => x.type === 'pattern' || x.type === 'rule').length,
        },
      ],
    }
  }
  const step = resolveStep(steps[clamped], options)
  return {
    ...acc,
    stepIndex: clamped,
    step,
    options,
    trace: step.trace ?? [],
    highlight: step.highlight ?? [],
  }
}

export const STEP_COUNT = STEPS.length

/** First step index of a scene, for the scene jump bar. */
export function sceneStart(scene: Step['scene'], steps: Step[] = STEPS): number {
  const idx = steps.findIndex((s) => s.scene === scene)
  if (idx === -1) throw new Error(`Unknown scene ${scene}`)
  return idx
}
