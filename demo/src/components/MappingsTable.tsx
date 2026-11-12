import { AlertTriangle, ArrowRight } from 'lucide-react'
import type { MappingRow } from '../scenario/types'
import { Meter, OriginBadge, confidenceTone, cx, tint, toneToken } from './ui'

/** Field mappings with origin and confidence. `compact` drops the transform column for narrow cards. */
export function MappingsTable({ rows, compact = false }: { rows: MappingRow[]; compact?: boolean }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-separate border-spacing-0 text-left text-[12.5px]">
        <thead>
          <tr className="text-[10.5px] uppercase tracking-[0.08em] text-muted">
            <th scope="col" className="border-b border-border py-1.5 pr-2 font-semibold">
              Source → target
            </th>
            {!compact && (
              <th scope="col" className="hidden border-b border-border py-1.5 pr-2 font-semibold md:table-cell">
                Transform
              </th>
            )}
            <th scope="col" className="border-b border-border py-1.5 pr-2 font-semibold">
              Origin
            </th>
            <th scope="col" className={cx('border-b border-border py-1.5 font-semibold', compact ? 'w-12 text-right' : 'w-20 sm:w-28')}>
              {compact ? 'Conf.' : 'Confidence'}
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={r.id}
              data-origin={r.origin}
              style={r.flag ? { backgroundColor: tint('warn', 7) } : undefined}
              className="align-top"
            >
              <td className={cx('border-b border-border py-1.5 pr-2', r.flag && 'pl-1.5')}>
                <div className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5 font-mono text-[11.5px] text-fg">
                  <span>{r.source}</span>
                  <ArrowRight size={11} aria-label="maps to" className="shrink-0 text-muted" />
                  <span>{r.target}</span>
                </div>
                {compact && r.transform && <div className="mt-0.5 font-mono text-[10.5px] text-muted">{r.transform}</div>}
                {!compact && r.transform && (
                  <div className="mt-0.5 font-mono text-[10.5px] text-muted md:hidden">{r.transform}</div>
                )}
                {r.flag && (
                  <div className="mt-1 flex items-center gap-1 text-[11px] font-medium text-warn">
                    <AlertTriangle size={11} aria-hidden className="shrink-0" />
                    {r.flag}
                  </div>
                )}
              </td>
              {!compact && (
                <td className="hidden border-b border-border py-1.5 pr-2 font-mono text-[11.5px] text-muted md:table-cell">
                  {r.transform ?? '—'}
                </td>
              )}
              <td className="border-b border-border py-1.5 pr-2">
                <OriginBadge origin={r.origin} />
              </td>
              <td className={cx('border-b border-border py-1.5', compact && 'text-right')}>
                {compact ? (
                  <span
                    className="text-[12px] font-semibold tabular-nums"
                    style={{ color: `var(--${toneToken(confidenceTone(r.confidence))})` }}
                    title={`Confidence ${r.confidence.toFixed(2)}`}
                  >
                    {r.confidence.toFixed(2)}
                  </span>
                ) : (
                  <Meter value={r.confidence} label={`Confidence ${r.source} to ${r.target}`} />
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
