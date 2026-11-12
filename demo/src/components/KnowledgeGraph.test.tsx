import { fireEvent, render, screen } from '@testing-library/react'
import { KnowledgeGraph } from './KnowledgeGraph'
import { resetGraphMemory } from './knowledge-graph/layout'
import { computeState } from '../engine/state'
import { STEPS } from '../scenario/data'

class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

beforeAll(() => {
  vi.stubGlobal('ResizeObserver', ResizeObserverStub)
  vi.stubGlobal(
    'matchMedia',
    (query: string): MediaQueryList =>
      ({
        matches: false,
        media: query,
        onchange: null,
        addEventListener: () => {},
        removeEventListener: () => {},
        addListener: () => {},
        removeListener: () => {},
        dispatchEvent: () => false,
      }) as MediaQueryList,
  )
})

afterAll(() => {
  vi.unstubAllGlobals()
})

beforeEach(() => {
  resetGraphMemory()
})

const ON = { learningEnabled: true }
const learnIndex = STEPS.findIndex((s) => s.id === 'l1-learn')

function nodeEls(container: HTMLElement): Element[] {
  return Array.from(container.querySelectorAll('[data-node-id]'))
}

function edgeEls(container: HTMLElement): Element[] {
  return Array.from(container.querySelectorAll('[data-edge-id]'))
}

describe('KnowledgeGraph', () => {
  it('renders one svg element per node and per edge', () => {
    const s = computeState(0, ON)
    const { container } = render(<KnowledgeGraph nodes={s.nodes} edges={s.edges} highlight={[]} />)
    expect(container.querySelector('svg')).toBeInTheDocument()
    expect(nodeEls(container)).toHaveLength(s.nodes.length)
    expect(edgeEls(container)).toHaveLength(s.edges.length)
    expect(new Set(nodeEls(container).map((el) => el.getAttribute('data-node-id')))).toEqual(
      new Set(s.nodes.map((n) => n.id)),
    )
    expect(new Set(edgeEls(container).map((el) => el.getAttribute('data-edge-id')))).toEqual(
      new Set(s.edges.map((e) => e.id)),
    )
  })

  it('positions nodes after the initial layout', () => {
    const s = computeState(0, ON)
    const { container } = render(<KnowledgeGraph nodes={s.nodes} edges={s.edges} highlight={[]} />)
    for (const el of nodeEls(container)) {
      expect(el.getAttribute('transform')).toMatch(/^translate\(-?\d+(\.\d+)?,-?\d+(\.\d+)?\)$/)
    }
    const path = container.querySelector('[data-edge-id] path')
    expect(path?.getAttribute('d')).toMatch(/^M/)
  })

  it('marks highlighted nodes', () => {
    const s = computeState(learnIndex, ON)
    expect(s.highlight.length).toBeGreaterThan(0)
    const { container } = render(<KnowledgeGraph nodes={s.nodes} edges={s.edges} highlight={s.highlight} />)
    const highlighted = Array.from(container.querySelectorAll('[data-highlighted="true"]')).map((el) =>
      el.getAttribute('data-node-id'),
    )
    expect(new Set(highlighted)).toEqual(new Set(s.highlight))
    const dimmed = container.querySelector('[data-node-id="f-contact-email"]')
    expect(dimmed?.getAttribute('data-highlighted')).toBe('false')
    expect(Number(dimmed?.getAttribute('opacity'))).toBeLessThan(1)
  })

  it('keeps existing nodes (and their positions) when nodes are added', () => {
    const before = computeState(learnIndex - 1, ON)
    const after = computeState(learnIndex, ON)
    const { container, rerender } = render(
      <KnowledgeGraph nodes={before.nodes} edges={before.edges} highlight={before.highlight} />,
    )
    const original = new Map(nodeEls(container).map((el) => [el.getAttribute('data-node-id'), el]))
    const positions = new Map(
      nodeEls(container).map((el) => [el.getAttribute('data-node-id'), el.getAttribute('transform')]),
    )

    rerender(<KnowledgeGraph nodes={after.nodes} edges={after.edges} highlight={after.highlight} />)

    expect(nodeEls(container)).toHaveLength(after.nodes.length)
    expect(edgeEls(container)).toHaveLength(after.edges.length)
    for (const [id, el] of original) {
      const now = container.querySelector(`[data-node-id="${id}"]`)
      expect(now).toBe(el)
      expect(now?.getAttribute('transform')).toBe(positions.get(id))
    }
    const added = after.nodes.filter((n) => !original.has(n.id))
    expect(added.map((n) => n.id)).toContain('pat-crm-billing')
    for (const n of added) {
      const el = container.querySelector(`[data-node-id="${n.id}"]`)
      expect(el).toBeInTheDocument()
      expect(el?.getAttribute('class')).toContain('kg-enter')
    }
  })

  it('shows the legend with live counts, and hides it when compact', () => {
    const s = computeState(learnIndex, ON)
    const { rerender } = render(<KnowledgeGraph nodes={s.nodes} edges={s.edges} highlight={[]} />)
    const legend = screen.getByTestId('kg-legend')
    expect(legend).toBeInTheDocument()
    expect(legend).toHaveTextContent(String(s.nodes.length))
    expect(legend).toHaveTextContent('Learned in Loop 1')

    rerender(<KnowledgeGraph nodes={s.nodes} edges={s.edges} highlight={[]} compact />)
    expect(screen.queryByTestId('kg-legend')).not.toBeInTheDocument()
  })

  it('compact mode labels only highlighted nodes', () => {
    const s = computeState(0, ON)
    const { container } = render(
      <KnowledgeGraph nodes={s.nodes} edges={s.edges} highlight={['sys-crm']} compact height={240} />,
    )
    expect(container.querySelector('[data-label-id="sys-crm"] text')).toBeInTheDocument()
    expect(container.querySelector('[data-label-id="sys-billing"] text')).not.toBeInTheDocument()
  })

  it('pins a detail card on click', () => {
    const s = computeState(learnIndex, ON)
    const { container } = render(<KnowledgeGraph nodes={s.nodes} edges={s.edges} highlight={[]} />)
    const node = container.querySelector('[data-node-id="pat-crm-billing"]')
    expect(node).not.toBeNull()
    fireEvent.click(node as Element)
    const card = screen.getByTestId('kg-detail-card')
    expect(card).toHaveTextContent('Pattern: CRM → Billing customer sync with tax')
    expect(card).toHaveTextContent('Learned in Loop 1')
    expect(card).toHaveTextContent('81%')
    fireEvent.click(screen.getByRole('button', { name: 'Close details' }))
    expect(screen.queryByTestId('kg-detail-card')).not.toBeInTheDocument()
  })

  it('exposes labelled zoom controls', () => {
    const s = computeState(0, ON)
    render(<KnowledgeGraph nodes={s.nodes} edges={s.edges} highlight={[]} />)
    for (const name of ['Zoom in', 'Zoom out', 'Zoom to fit']) {
      expect(screen.getByRole('button', { name })).toBeInTheDocument()
    }
  })

  it('ignores edges whose endpoints are missing', () => {
    const s = computeState(0, ON)
    const edges = [
      ...s.edges,
      { id: 'ghost', source: 'nope', target: 'sys-crm', type: 'MAPS_TO' as const, learnedIn: 'seed' as const },
    ]
    const { container } = render(<KnowledgeGraph nodes={s.nodes} edges={edges} highlight={[]} />)
    expect(container.querySelector('[data-edge-id="ghost"]')).toBeNull()
  })
})
