import { computeState, sceneStart, STEP_COUNT } from './state'
import { STEPS } from '../scenario/data'

const ON = { learningEnabled: true }
const OFF = { learningEnabled: false }
const last = STEP_COUNT - 1

describe('scenario integrity', () => {
  it.each([ON, OFF])('every edge endpoint and highlight exists at every step (%o)', (opts) => {
    for (let i = 0; i < STEP_COUNT; i++) {
      const s = computeState(i, opts)
      const ids = new Set(s.nodes.map((n) => n.id))
      for (const e of s.edges) {
        expect(ids.has(e.source), `${STEPS[i].id}: ${e.id} source`).toBe(true)
        expect(ids.has(e.target), `${STEPS[i].id}: ${e.id} target`).toBe(true)
      }
      for (const h of s.highlight) expect(ids.has(h), `${STEPS[i].id}: highlight ${h}`).toBe(true)
    }
  })

  it('has unique step, node and edge ids', () => {
    const s = computeState(last, ON)
    expect(new Set(STEPS.map((x) => x.id)).size).toBe(STEPS.length)
    expect(new Set(s.nodes.map((x) => x.id)).size).toBe(s.nodes.length)
    expect(new Set(s.edges.map((x) => x.id)).size).toBe(s.edges.length)
  })

  it('clamps out-of-range indexes', () => {
    expect(computeState(-5, ON).stepIndex).toBe(0)
    expect(computeState(999, ON).stepIndex).toBe(last)
  })
})

describe('loop 1: intent-centric', () => {
  it('ships the billing sync after both approvals and learns a pattern', () => {
    const s = computeState(sceneStart('loop2') - 1, ON)
    const int1 = s.integrations.find((i) => i.id === 'int-1')!
    expect(int1.status).toBe('live')
    expect(int1.humanEdits).toBe(3)
    expect(int1.approvals.pm && int1.approvals.eng).toBeTruthy()
    expect(s.roadmap.find((r) => r.id === 'rm-billing-sync')!.status).toBe('shipped')
    expect(s.nodes.some((n) => n.id === 'pat-crm-billing')).toBe(true)
  })
})

describe('loop 2: new API, agent-initiated', () => {
  it('unblocks and ships contract-to-cash, reusing the learned pattern', () => {
    const s = computeState(last, ON)
    const int2 = s.integrations.find((i) => i.id === 'int-2')!
    expect(int2.initiatedBy).toBe('agent_from_roadmap')
    expect(int2.humanEdits).toBe(0)
    expect(int2.mappings.filter((m) => m.origin === 'kg_reused')).toHaveLength(7)
    const c2c = s.roadmap.find((r) => r.id === 'rm-contract-to-cash')!
    expect(c2c.status).toBe('shipped')
    expect(c2c.blockedReason).toBeUndefined()
    expect(s.nodes.find((n) => n.id === 'pat-crm-billing')!.confidence).toBe(0.92)
  })

  it('needs the same human fixes again when KG learning is off (ablation)', () => {
    const s = computeState(last, OFF)
    const int2 = s.integrations.find((i) => i.id === 'int-2')!
    expect(int2.humanEdits).toBe(3)
    expect(int2.mappings.filter((m) => m.origin === 'kg_reused')).toHaveLength(0)
    expect(s.nodes.some((n) => n.id === 'pat-crm-billing')).toBe(false)
    expect(s.nodes.length).toBeLessThan(computeState(last, ON).nodes.length)
  })

  it('records KG growth over time', () => {
    const s = computeState(last, ON)
    expect(s.kgHistory).toHaveLength(STEP_COUNT)
    expect(s.kgHistory.at(-1)!.nodes).toBeGreaterThan(s.kgHistory[0].nodes)
  })
})
