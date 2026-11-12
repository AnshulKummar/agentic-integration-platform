import type { CSSProperties, ReactNode } from 'react'
import type { AgentKind, Integration, MappingOrigin } from '../scenario/types'

/** Tint a design token with transparency, e.g. tint('agent-llm', 12). Never a hard-coded colour. */
export function tint(token: string, pct: number): string {
  return `color-mix(in srgb, var(--${token}) ${pct}%, transparent)`
}

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(' ')
}

// ---------------------------------------------------------------- agents
export const AGENTS: Record<AgentKind, { label: string; short: string; token: string; text: string }> = {
  user: { label: 'User', short: 'User', token: 'agent-user', text: 'text-agent-user' },
  llm: { label: 'LLM agent', short: 'LLM', token: 'agent-llm', text: 'text-agent-llm' },
  kg: { label: 'KG agent', short: 'KG', token: 'agent-kg', text: 'text-agent-kg' },
  mcp: { label: 'MCP agent', short: 'MCP', token: 'agent-mcp', text: 'text-agent-mcp' },
  governance: { label: 'Governance', short: 'Gov', token: 'agent-governance', text: 'text-agent-governance' },
  context: { label: 'Context store', short: 'Context', token: 'agent-context', text: 'text-agent-context' },
  external: { label: 'External API', short: 'API', token: 'agent-external', text: 'text-agent-external' },
}

export function AgentChip({ agent, size = 'md' }: { agent: AgentKind; size?: 'sm' | 'md' }) {
  const a = AGENTS[agent]
  return (
    <span
      className={cx(
        'inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full font-semibold',
        size === 'sm' ? 'px-2 py-0.5 text-[11px]' : 'px-2.5 py-0.5 text-xs',
        a.text,
      )}
      style={{ backgroundColor: tint(a.token, 13), boxShadow: `inset 0 0 0 1px ${tint(a.token, 28)}` }}
    >
      <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: `var(--${a.token})` }} aria-hidden />
      {a.label}
    </span>
  )
}

// ---------------------------------------------------------------- badges
export type BadgeTone = 'neutral' | 'accent' | 'ok' | 'warn' | 'bad' | 'llm' | 'kg' | 'mcp' | 'governance' | 'loop1' | 'loop2'

const TONE_TOKEN: Record<BadgeTone, { token: string; text: string }> = {
  neutral: { token: 'muted', text: 'text-muted' },
  accent: { token: 'accent', text: 'text-accent' },
  ok: { token: 'ok', text: 'text-ok' },
  warn: { token: 'warn', text: 'text-warn' },
  bad: { token: 'bad', text: 'text-bad' },
  llm: { token: 'agent-llm', text: 'text-agent-llm' },
  kg: { token: 'agent-kg', text: 'text-agent-kg' },
  mcp: { token: 'agent-mcp', text: 'text-agent-mcp' },
  governance: { token: 'agent-governance', text: 'text-agent-governance' },
  loop1: { token: 'learned-loop1', text: 'text-learned-loop1' },
  loop2: { token: 'learned-loop2', text: 'text-learned-loop2' },
}

export function Badge({
  tone = 'neutral',
  icon,
  children,
  className,
  title,
}: {
  tone?: BadgeTone
  icon?: ReactNode
  children: ReactNode
  className?: string
  title?: string
}) {
  const t = TONE_TOKEN[tone]
  return (
    <span
      title={title}
      className={cx(
        'inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-md px-1.5 py-0.5 text-[11px] font-semibold leading-4',
        t.text,
        className,
      )}
      style={{ backgroundColor: tint(t.token, 13) }}
    >
      {icon}
      {children}
    </span>
  )
}

export function toneToken(tone: BadgeTone): string {
  return TONE_TOKEN[tone].token
}

// ---------------------------------------------------------------- layout
export function Card({
  children,
  className,
  style,
  as: Tag = 'section',
  testId,
}: {
  children: ReactNode
  className?: string
  style?: CSSProperties
  as?: 'section' | 'div' | 'article'
  testId?: string
}) {
  return (
    <Tag className={cx('rounded-xl border border-border bg-surface shadow-sm', className)} style={style} data-testid={testId}>
      {children}
    </Tag>
  )
}

export function SectionTitle({ icon, children, right }: { icon?: ReactNode; children: ReactNode; right?: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <h3 className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
        {icon}
        {children}
      </h3>
      {right}
    </div>
  )
}

export function ViewHeader({ eyebrow, title, right }: { eyebrow: string; title: string; right?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-accent">{eyebrow}</p>
        <h2 className="mt-0.5 text-lg font-semibold tracking-tight text-fg sm:text-xl">{title}</h2>
      </div>
      {right}
    </div>
  )
}

// ---------------------------------------------------------------- meters
export function confidenceTone(v: number): BadgeTone {
  if (v >= 0.8) return 'ok'
  if (v >= 0.65) return 'warn'
  return 'bad'
}

export function Meter({
  value,
  tone,
  label,
  showValue = true,
  className,
}: {
  value: number
  tone?: BadgeTone
  label: string
  showValue?: boolean
  className?: string
}) {
  const t = tone ?? confidenceTone(value)
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100)
  return (
    <div className={cx('flex items-center gap-2', className)}>
      <div
        className="h-1.5 min-w-[40px] flex-1 overflow-hidden rounded-full bg-surface-2"
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
      >
        <div className="h-full rounded-full transition-[width] duration-500" style={{ width: `${pct}%`, backgroundColor: `var(--${toneToken(t)})` }} />
      </div>
      {showValue && <span className="w-8 shrink-0 text-right text-xs tabular-nums text-muted">{value.toFixed(2)}</span>}
    </div>
  )
}

// ---------------------------------------------------------------- people
export const PEOPLE = {
  pm: { name: 'Priya', role: 'Product Manager', short: 'PM', initial: 'P', token: 'accent' },
  eng: { name: 'Marco', role: 'Engineering', short: 'Eng', initial: 'M', token: 'agent-mcp' },
} as const

export function Avatar({ who, size = 28 }: { who: 'pm' | 'eng'; size?: number }) {
  const p = PEOPLE[who]
  return (
    <span
      aria-hidden
      className="inline-flex shrink-0 items-center justify-center rounded-full font-semibold"
      style={{
        width: size,
        height: size,
        fontSize: Math.round(size * 0.42),
        backgroundColor: tint(p.token, 18),
        color: `var(--${p.token})`,
        boxShadow: `inset 0 0 0 1px ${tint(p.token, 35)}`,
      }}
    >
      {p.initial}
    </span>
  )
}

// ---------------------------------------------------------------- domain badges
export function InitiatedByBadge({ by }: { by: Integration['initiatedBy'] }) {
  return by === 'user_intent' ? (
    <Badge tone="accent">Requested by PM</Badge>
  ) : (
    <Badge tone="loop2">Started by agent from roadmap intent</Badge>
  )
}

export const ORIGIN: Record<MappingOrigin, { label: string; tone: BadgeTone }> = {
  kg_reused: { label: 'Reused from KG', tone: 'ok' },
  kg_inferred: { label: 'Inferred from KG', tone: 'kg' },
  llm_new: { label: 'New (LLM)', tone: 'llm' },
  human_edit: { label: 'Human edit', tone: 'accent' },
}

export function OriginBadge({ origin }: { origin: MappingOrigin }) {
  const o = ORIGIN[origin]
  return <Badge tone={o.tone}>{o.label}</Badge>
}

export function EmptyState({ icon, title, body }: { icon: ReactNode; title: string; body?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-border px-6 py-10 text-center">
      <span className="text-muted">{icon}</span>
      <p className="text-sm font-medium text-fg">{title}</p>
      {body && <p className="max-w-sm text-xs text-muted">{body}</p>}
    </div>
  )
}

export const focusRing =
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-bg'
