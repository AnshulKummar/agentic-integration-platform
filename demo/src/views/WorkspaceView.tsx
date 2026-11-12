import { useEffect, useRef } from 'react'
import { motion } from 'framer-motion'
import { ArrowRight, Bot, ClipboardList, MessageSquareText, Send, Sparkles, Zap } from 'lucide-react'
import type { ChatMessage, Gate, Integration } from '../scenario/types'
import { MappingsTable } from '../components/MappingsTable'
import { Avatar, Card, EmptyState, InitiatedByBadge, Meter, SectionTitle, ViewHeader, cx, focusRing, tint } from '../components/ui'
import { useTypewriter } from '../hooks/useTypewriter'
import { latestIntegration, type ViewProps } from './shared'

function Message({ msg, proactive, animate }: { msg: ChatMessage; proactive: boolean; animate: boolean }) {
  const isUser = msg.role === 'user'
  return (
    <motion.li
      initial={animate ? { opacity: 0, y: 8 } : false}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className="flex flex-col gap-2"
    >
      {proactive && (
        <div className="my-1 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-learned-loop2">
          <span className="h-px flex-1 bg-border" />
          <Sparkles size={12} aria-hidden />
          Agent-initiated · no one asked
          <span className="h-px flex-1 bg-border" />
        </div>
      )}
      <div className={cx('flex items-end gap-2', isUser && 'flex-row-reverse')}>
        {isUser ? (
          <Avatar who="pm" size={28} />
        ) : (
          <span
            className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-agent-llm"
            style={{ backgroundColor: tint('agent-llm', 15) }}
            aria-hidden
          >
            <Bot size={15} />
          </span>
        )}
        <div className={cx('flex max-w-[85%] flex-col gap-1.5', isUser && 'items-end')}>
          <p className="text-[10.5px] font-medium text-muted">{isUser ? 'Priya · PM' : 'PMAIP agent'}</p>
          <div
            className={cx(
              'rounded-2xl px-3.5 py-2.5 text-[13px] leading-relaxed',
              isUser ? 'rounded-br-md bg-accent text-accent-contrast' : 'rounded-bl-md border border-border bg-surface-2 text-fg',
            )}
          >
            {msg.text}
          </div>
          {msg.options && (
            <div className="flex flex-wrap gap-1.5">
              {msg.options.map((o, i) => (
                <span
                  key={o}
                  className={cx(
                    'rounded-full border px-2.5 py-1 text-[11.5px] font-medium',
                    i === 0 ? 'border-accent text-accent' : 'border-border text-muted',
                  )}
                  style={i === 0 ? { backgroundColor: tint('accent', 8) } : undefined}
                >
                  {o}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>
    </motion.li>
  )
}

function Composer({ gate, onSend, reducedMotion }: { gate: Gate | undefined; onSend: () => void; reducedMotion: boolean }) {
  const text = gate?.kind === 'type_intent' ? (gate.prefill ?? '') : ''
  const { typed, done } = useTypewriter(text, 25, reducedMotion)
  const active = gate?.kind === 'type_intent'
  return (
    <form
      className="flex items-end gap-2 border-t border-border p-3"
      onSubmit={(e) => {
        e.preventDefault()
        if (active) onSend()
      }}
    >
      <label htmlFor="intent-input" className="sr-only">
        Message to the agent
      </label>
      <div className="relative min-w-0 flex-1">
        <textarea
          id="intent-input"
          readOnly
          rows={2}
          value={active ? typed : ''}
          placeholder={active ? '' : 'Agents are working…'}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && active) {
              e.preventDefault()
              onSend()
            }
          }}
          className={cx(
            'block w-full resize-none rounded-xl border bg-surface px-3.5 py-2 text-[13px] leading-snug text-fg placeholder:text-muted',
            active ? 'border-accent' : 'border-border',
            focusRing,
          )}
        />
        {active && !done && (
          <span aria-hidden className="pointer-events-none absolute bottom-2.5 right-3 h-4 w-0.5 animate-pulse bg-accent" />
        )}
      </div>
      <button
        type="submit"
        disabled={!active}
        data-testid="chat-send"
        className={cx(
          'inline-flex shrink-0 items-center gap-1.5 rounded-xl bg-accent px-4 py-2.5 text-[13px] font-semibold text-accent-contrast shadow-sm transition-[filter,opacity] hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40',
          active && done && !reducedMotion && 'animate-pulse',
          focusRing,
        )}
      >
        <Send size={14} aria-hidden />
        {gate?.kind === 'type_intent' ? gate.label : 'Send'}
      </button>
    </form>
  )
}

function PlanCard({ integration }: { integration: Integration }) {
  return (
    <Card className="flex min-h-0 flex-col p-4" testId="integration-plan">
      <SectionTitle icon={<ClipboardList size={13} aria-hidden className="text-accent" />} right={<InitiatedByBadge by={integration.initiatedBy} />}>
        Integration plan
      </SectionTitle>
      <h3 className="mt-2 text-[15px] font-semibold leading-snug text-fg">{integration.name}</h3>
      <dl className="mt-3 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-[12.5px]">
        <dt className="text-muted">Trigger</dt>
        <dd className="flex items-center gap-1.5 font-medium text-fg">
          <Zap size={12} aria-hidden className="shrink-0 text-warn" />
          {integration.trigger}
        </dd>
        <dt className="text-muted">Flow</dt>
        <dd className="flex flex-wrap items-center gap-1.5 font-medium text-fg">
          {integration.source}
          <ArrowRight size={12} aria-label="to" className="text-muted" />
          {integration.target}
        </dd>
        <dt className="text-muted">Confidence</dt>
        <dd>
          <Meter value={integration.confidence} label="Plan confidence" />
        </dd>
        {integration.patternReused && (
          <>
            <dt className="text-muted">Pattern</dt>
            <dd className="font-medium text-ok">{integration.patternReused}</dd>
          </>
        )}
      </dl>
      <div className="mt-3 min-h-0 overflow-y-auto">
        <p className="mb-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">Mappings · {integration.mappings.length}</p>
        <MappingsTable rows={integration.mappings} compact />
      </div>
    </Card>
  )
}

export function WorkspaceView({ state, prev, onAdvance, reducedMotion }: ViewProps) {
  const listRef = useRef<HTMLDivElement>(null)
  const integration = latestIntegration(state)
  const prevCount = prev?.chat.length ?? 0

  useEffect(() => {
    const el = listRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [state.chat.length, state.stepIndex])

  return (
    <div className="flex h-full min-h-0 flex-col gap-4">
      <ViewHeader eyebrow="Integration workspace" title="From intent to integration plan" />
      <div className="grid grid-cols-[minmax(0,1fr)] min-h-0 flex-1 gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
        <Card className="flex min-h-[380px] flex-col overflow-hidden p-0">
          <div className="flex items-center gap-2 border-b border-border px-4 py-2.5">
            <MessageSquareText size={14} aria-hidden className="text-accent" />
            <h3 className="text-[13px] font-semibold text-fg">Conversation</h3>
            <span className="ml-auto text-[11px] text-muted">PM ↔ PMAIP agent</span>
          </div>
          <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto px-4 py-4" data-testid="chat">
            {state.chat.length === 0 ? (
              <EmptyState
                icon={<MessageSquareText size={22} />}
                title="Describe what you want in business language"
                body="No ticket, no spec. The agents work out the systems, fields and APIs."
              />
            ) : (
              <ol className="flex flex-col gap-4">
                {state.chat.map((m, i) => (
                  <Message
                    key={m.id}
                    msg={m}
                    proactive={m.role === 'agent' && i > 0 && state.chat[i - 1].role === 'agent'}
                    animate={!reducedMotion && i >= prevCount}
                  />
                ))}
              </ol>
            )}
          </div>
          <Composer key={state.step.id} gate={state.step.gate} onSend={onAdvance} reducedMotion={reducedMotion} />
        </Card>

        {integration ? (
          <PlanCard integration={integration} />
        ) : (
          <Card className="p-4">
            <EmptyState
              icon={<ClipboardList size={22} />}
              title="No integration plan yet"
              body="Send the intent and the agents will draft one."
            />
          </Card>
        )}
      </div>
    </div>
  )
}
