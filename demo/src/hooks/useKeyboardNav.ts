import { useEffect } from 'react'

interface Handlers {
  onNext: () => void
  onBack: () => void
  onRestart: () => void
}

function isTypingTarget(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false
  const tag = el.tagName
  // The scripted composer is read-only, so arrows keep driving the demo even when it has focus.
  if ((el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) && el.readOnly) return false
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable
}

function isActivatable(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false
  const role = el.getAttribute('role')
  return el.tagName === 'BUTTON' || el.tagName === 'A' || role === 'switch' || role === 'tab'
}

/** ArrowRight / Space = next (also satisfies a gate), ArrowLeft = back, R = restart. */
export function useKeyboardNav({ onNext, onBack, onRestart }: Handlers): void {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey) return
      if (isTypingTarget(e.target)) return
      if (e.key === 'ArrowRight') {
        e.preventDefault()
        onNext()
      } else if (e.key === ' ' || e.key === 'Spacebar') {
        // Space on a focused button clicks that button; don't also advance.
        if (isActivatable(e.target)) return
        e.preventDefault()
        onNext()
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault()
        onBack()
      } else if (e.key === 'r' || e.key === 'R') {
        e.preventDefault()
        onRestart()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onNext, onBack, onRestart])
}
