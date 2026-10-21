import {
  type KeyboardEvent,
  type SyntheticEvent,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react'

/**
 * How long, after a sheet or a confirmation opens, its contents ignore a tap
 * (M6.9). The second tap of a double tap lands 20 to 250 ms after the first,
 * on whatever has just opened under the finger: a sheet's Chart, Add or
 * Cancel, or a confirmation's own button, which on a tall phone sits where
 * the sheet's Cancel was. Tests that are not about this set it to 0
 * (`src/test/setup.ts`).
 */
export const OPEN_GUARD = { ms: 500 }

type Handler = (event: SyntheticEvent) => void

export interface OpenGuard {
  /** Inside the window: an action button may show as disabled. */
  settling: boolean
  /** For the element whose contents the window covers. */
  handlers: {
    onPointerDownCapture: Handler
    onClickCapture: Handler
    onKeyDownCapture: (event: KeyboardEvent) => void
  }
}

const TEXT_INPUTS = new Set(['text', 'search', 'email', 'number', 'tel', 'url', 'password'])

/** A field that takes typing: Enter and Space there are text, not a tap. */
function typing(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  if (target.isContentEditable || target instanceof HTMLTextAreaElement) return true
  return target instanceof HTMLInputElement && TEXT_INPUTS.has(target.type)
}

/**
 * For `OPEN_GUARD.ms` after `open` turns true: a pointer press, a click, Enter,
 * and Space anywhere but in a text field, inside the element given the
 * handlers, are swallowed before they reach it (Enter in a field can still
 * submit its form; Space there is only a space). Escape and a tap outside
 * still close: closing is always safe.
 */
export function useOpenGuard(open: boolean): OpenGuard {
  const until = useRef(0)
  const [settling, setSettling] = useState(false)

  // Before the first paint of what opened, so no event can reach it unguarded.
  useLayoutEffect(() => {
    if (!open) {
      until.current = 0
      setSettling(false)
      return
    }
    const ms = OPEN_GUARD.ms
    if (ms <= 0) return
    until.current = Date.now() + ms
    setSettling(true)
    const timer = setTimeout(() => setSettling(false), ms)
    return () => clearTimeout(timer)
  }, [open])

  const handlers = useMemo(() => {
    const swallow: Handler = (event) => {
      if (Date.now() >= until.current) return
      event.preventDefault()
      event.stopPropagation()
    }
    return {
      onPointerDownCapture: swallow,
      onClickCapture: swallow,
      onKeyDownCapture: (event: KeyboardEvent) => {
        if (event.key === 'Enter' || (event.key === ' ' && !typing(event.target))) swallow(event)
      },
    }
  }, [])

  return { settling, handlers }
}
