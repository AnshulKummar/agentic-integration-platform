import type { ReactNode } from 'react'

const KEYWORDS = new Set(['async', 'def', 'await', 'return', 'None', 'import', 'from', 'if', 'else', 'for', 'in', 'True', 'False'])

// comment | string | decorator | word | anything else
const TOKEN = /(#.*$)|("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')|(@[\w.]+)|([A-Za-z_]\w*)|([^#"'@A-Za-z_]+)/gm

function highlightLine(line: string, lineKey: number): ReactNode[] {
  const out: ReactNode[] = []
  let m: RegExpExecArray | null
  let i = 0
  TOKEN.lastIndex = 0
  while ((m = TOKEN.exec(line)) !== null) {
    if (m[0] === '') {
      TOKEN.lastIndex++
      continue
    }
    const key = `${lineKey}-${i++}`
    if (m[1]) out.push(<span key={key} className="italic text-muted">{m[1]}</span>)
    else if (m[2]) out.push(<span key={key} className="text-agent-mcp">{m[2]}</span>)
    else if (m[3]) out.push(<span key={key} className="text-agent-governance">{m[3]}</span>)
    else if (m[4]) {
      const w = m[4]
      if (KEYWORDS.has(w)) out.push(<span key={key} className="font-medium text-agent-llm">{w}</span>)
      else if (w === 'mcp' || w === 'patterns') out.push(<span key={key} className="text-agent-kg">{w}</span>)
      else out.push(w)
    } else out.push(m[0])
  }
  return out
}

export function CodeBlock({ code, label }: { code: string; label: string }) {
  const lines = code.split('\n')
  return (
    <pre
      aria-label={label}
      tabIndex={0}
      className="max-h-72 overflow-auto rounded-lg border border-border bg-surface-2 py-3 text-[12px] leading-[1.6] text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
    >
      <code className="block min-w-max">
        {lines.map((line, i) => (
          <span key={i} className="flex">
            <span className="w-10 shrink-0 select-none pr-3 text-right tabular-nums text-muted opacity-60">{i + 1}</span>
            <span className="pr-4">{highlightLine(line, i)}</span>
          </span>
        ))}
      </code>
    </pre>
  )
}
