import { act, fireEvent, render, screen, within } from '@testing-library/react'
import App from './App'
import { STEP_COUNT } from './engine/state'
import { STEPS } from './scenario/data'
import { parseHash, serializeHash } from './hooks/useHashState'

// The KG visualisation is owned and tested separately; keep these tests about the shell and flow.
vi.mock('./components/KnowledgeGraph', () => ({
  KnowledgeGraph: ({ nodes }: { nodes: unknown[] }) => <div data-testid="knowledge-graph">{nodes.length} nodes</div>,
}))

function mockMatchMedia() {
  // Reduced motion ON: typewriter, trace stagger and pipeline animations become instant.
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    configurable: true,
    value: (query: string): MediaQueryList =>
      ({
        matches: query.includes('prefers-reduced-motion'),
        media: query,
        onchange: null,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
        addListener: () => undefined,
        removeListener: () => undefined,
        dispatchEvent: () => false,
      }) as MediaQueryList,
  })
}

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

beforeEach(() => {
  mockMatchMedia()
  vi.stubGlobal('ResizeObserver', ResizeObserverStub)
  window.history.replaceState(null, '', '/')
  try {
    window.localStorage.clear()
  } catch {
    // jsdom always has storage; nothing to clean otherwise.
  }
})

afterEach(() => {
  vi.unstubAllGlobals()
})

const counter = () => screen.getByTestId('step-counter').textContent?.replace(/\s+/g, ' ').trim()

function startDemo() {
  render(<App />)
  fireEvent.click(screen.getByRole('button', { name: /start demo/i }))
}

function clickToEnd() {
  for (let i = 1; i < STEP_COUNT; i++) {
    fireEvent.click(screen.getByTestId('advance'))
  }
}

describe('App shell', () => {
  it('renders step 0 with the title card, which can be dismissed', () => {
    render(<App />)
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText(/Agentic integration of platform APIs/i)).toBeInTheDocument()
    expect(within(dialog).getByText(/Every integration teaches the platform/i)).toBeInTheDocument()
    expect(counter()).toBe(`1 / ${STEP_COUNT}`)
    expect(screen.getByTestId('view-roadmap')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /start demo/i }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1, name: STEPS[0].title })).toBeInTheDocument()
  })

  it('clicks through every gate and Next to the Proof view; Loop 2 needs 0 human corrections', () => {
    startDemo()
    clickToEnd()
    expect(counter()).toBe(`${STEP_COUNT} / ${STEP_COUNT}`)
    expect(screen.getByTestId('view-learning')).toBeInTheDocument()
    expect(screen.getByTestId('loop1-human-corrections')).toHaveTextContent('3')
    expect(screen.getByTestId('loop2-human-corrections')).toHaveTextContent('0')
    expect(screen.queryByTestId('advance')).not.toBeInTheDocument()
  })

  it('with KG learning OFF, Loop 2 needs the same 3 human corrections', () => {
    startDemo()
    const sw = screen.getByRole('switch', { name: /KG learning/i })
    fireEvent.click(sw)
    expect(sw).toHaveAttribute('aria-checked', 'false')
    expect(window.location.hash).toContain('kg=off')
    clickToEnd()
    expect(screen.getByTestId('view-learning')).toBeInTheDocument()
    expect(screen.getByTestId('loop2-human-corrections')).toHaveTextContent('3')
  })

  it('ArrowRight advances and ArrowLeft goes back', () => {
    startDemo()
    fireEvent.keyDown(window, { key: 'ArrowRight' })
    expect(counter()).toBe(`2 / ${STEP_COUNT}`)
    fireEvent.keyDown(window, { key: 'ArrowRight' })
    expect(counter()).toBe(`3 / ${STEP_COUNT}`)
    fireEvent.keyDown(window, { key: 'ArrowLeft' })
    expect(counter()).toBe(`2 / ${STEP_COUNT}`)
    fireEvent.keyDown(window, { key: 'r' })
    expect(counter()).toBe(`1 / ${STEP_COUNT}`)
  })

  it('the first ArrowRight on the title card only dismisses it', () => {
    render(<App />)
    fireEvent.keyDown(window, { key: 'ArrowRight' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(counter()).toBe(`1 / ${STEP_COUNT}`)
  })

  it('resumes from the URL hash without the title card', () => {
    window.history.replaceState(null, '', '/#s=7&kg=off')
    render(<App />)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(counter()).toBe(`7 / ${STEP_COUNT}`)
    expect(screen.getByRole('switch', { name: /KG learning/i })).toHaveAttribute('aria-checked', 'false')
  })

  it('the type-intent composer shows the prefill and Send advances', () => {
    startDemo()
    fireEvent.click(screen.getByRole('button', { name: /Loop 1/i }))
    const gate = STEPS.find((s) => s.id === 'l1-intent')?.gate
    expect(screen.getByLabelText(/message to the agent/i)).toHaveValue(gate?.prefill)
    const before = counter()
    fireEvent.click(screen.getByTestId('chat-send'))
    expect(counter()).not.toBe(before)
    expect(screen.getByTestId('integration-plan')).toBeInTheDocument()
  })

  it('Proof replay switches learning OFF and jumps to the Loop 2 draft', () => {
    startDemo()
    fireEvent.click(screen.getByRole('button', { name: /Proof/i }))
    act(() => {
      fireEvent.click(screen.getByRole('button', { name: /Replay Loop 2 with KG learning OFF/i }))
    })
    const draftIdx = STEPS.findIndex((s) => s.id === 'l2-draft')
    expect(counter()).toBe(`${draftIdx + 1} / ${STEP_COUNT}`)
    expect(screen.getByRole('switch', { name: /KG learning/i })).toHaveAttribute('aria-checked', 'false')
  })
})

describe('hash state', () => {
  it('round-trips step and learning flag', () => {
    expect(serializeHash({ stepIndex: 6, learningEnabled: false })).toBe('#s=7&kg=off')
    expect(parseHash('#s=7&kg=off', 22)).toEqual({ stepIndex: 6, learningEnabled: false, hasStep: true })
  })

  it('clamps and tolerates junk', () => {
    expect(parseHash('#s=999', 22).stepIndex).toBe(21)
    expect(parseHash('#s=abc', 22)).toEqual({ stepIndex: 0, learningEnabled: true, hasStep: false })
    expect(parseHash('', 22).hasStep).toBe(false)
  })
})
