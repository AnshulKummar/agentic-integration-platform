import { useEffect, useState } from 'react'

/** Types `text` out at `msPerChar`. Instant when `instant` is true. */
export function useTypewriter(text: string, msPerChar: number, instant: boolean): { typed: string; done: boolean } {
  const [state, setState] = useState({ text, length: instant ? text.length : 0 })
  const length = instant ? text.length : state.text === text ? state.length : 0

  useEffect(() => {
    if (instant) return
    setState({ text, length: 0 })
    let i = 0
    const timer = window.setInterval(() => {
      i += 1
      setState({ text, length: i })
      if (i >= text.length) window.clearInterval(timer)
    }, msPerChar)
    return () => window.clearInterval(timer)
  }, [text, msPerChar, instant])

  return { typed: text.slice(0, length), done: length >= text.length }
}
