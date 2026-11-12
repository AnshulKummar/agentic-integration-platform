import type { JSX } from 'react'
import { X } from 'lucide-react'
import type { KGEdge, KGNode } from '../../scenario/types'
import { LEARNED_LABEL, TYPE_LABEL, edgeTypeLabel, learnedColor } from './encoding'

function LearnedBadge({ node }: { node: Pick<KGNode, 'learnedIn'> }): JSX.Element {
  const color = learnedColor(node.learnedIn) ?? 'var(--muted)'
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium"
      style={{ borderColor: color, color }}
    >
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: color }} />
      {LEARNED_LABEL[node.learnedIn]}
    </span>
  )
}

export interface NodeDetailProps {
  node: KGNode
  systemName?: string
  connections: number
  pinned: boolean
  onClose?: () => void
}

export function NodeDetail({ node, systemName, connections, pinned, onClose }: NodeDetailProps): JSX.Element {
  return (
    <div
      data-testid={pinned ? 'kg-detail-card' : 'kg-tooltip'}
      role={pinned ? 'dialog' : 'tooltip'}
      aria-label={pinned ? `Details for ${node.label}` : undefined}
      className={`rounded-lg border border-border bg-surface text-fg shadow-lg ${pinned ? 'w-72 p-3.5' : 'max-w-64 px-3 py-2'}`}
    >
      <div className="flex items-start gap-2">
        <div className={`flex-1 font-semibold leading-snug ${pinned ? 'text-sm' : 'text-[13px]'}`}>{node.label}</div>
        {pinned && onClose && (
          <button
            type="button"
            onClick={onClose}
            aria-label="Close details"
            className="-m-1 rounded p-1 text-muted hover:bg-surface-2 hover:text-fg"
          >
            <X size={14} />
          </button>
        )}
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs text-muted">
        <span>{TYPE_LABEL[node.type]}</span>
        {systemName && (
          <>
            <span aria-hidden>·</span>
            <span>{systemName}</span>
          </>
        )}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <LearnedBadge node={node} />
        {node.confidence !== undefined && (
          <span className="text-xs text-muted">
            Confidence <span className="font-semibold tabular-nums text-fg">{Math.round(node.confidence * 100)}%</span>
          </span>
        )}
      </div>
      {node.note && <p className="mt-2 text-xs leading-relaxed text-muted">{node.note}</p>}
      {pinned && (
        <p className="mt-2 text-[11px] text-muted">
          {connections} connection{connections === 1 ? '' : 's'} · id <code className="text-[11px]">{node.id}</code>
        </p>
      )}
    </div>
  )
}

export interface EdgeTooltipProps {
  edge: KGEdge
  sourceLabel: string
  targetLabel: string
}

export function EdgeTooltip({ edge, sourceLabel, targetLabel }: EdgeTooltipProps): JSX.Element {
  return (
    <div
      data-testid="kg-tooltip"
      role="tooltip"
      className="max-w-72 rounded-lg border border-border bg-surface px-3 py-2 text-xs text-fg shadow-lg"
    >
      <div className="font-mono text-[11px] font-semibold uppercase tracking-wide">{edgeTypeLabel(edge.type)}</div>
      <div className="mt-1 text-muted">
        {sourceLabel} <span aria-hidden>→</span> {targetLabel}
      </div>
      <div className="mt-1.5">
        <LearnedBadge node={edge} />
      </div>
    </div>
  )
}
