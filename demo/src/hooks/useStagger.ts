import { useEffect, useState } from 'react'

/**
 * Reveals `total` items one at a time and restarts whenever `resetKey` changes.
 * Returns how many items are visible. With `instant`, everything shows at once.
 */
export function useStagger(total: number, intervalMs: number, resetKey: string, instant: boolean): number {
  const [state, setState] = useState({ key: resetKey, count: instant ? total : 0 })
  // During the render right after resetKey changes, don't flash the old count.
  const current = instant ? total : state.key === resetKey ? state.count : 0

  useEffect(() => {
    if (instant) return
    setState({ key: resetKey, count: 0 })
    if (total === 0) return
    let count = 0
    const timer = window.setInterval(() => {
      count += 1
      setState({ key: resetKey, count })
      if (count >= total) window.clearInterval(timer)
    }, intervalMs)
    return () => window.clearInterval(timer)
  }, [resetKey, total, intervalMs, instant])

  return Math.min(current, total)
}
