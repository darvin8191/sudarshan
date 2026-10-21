import {
  type KeyboardEvent,
  type SyntheticEvent,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react'

/**
 * How long, after a sheet or a confirmation opens, a tap does nothing (M6.9).
 * The second tap of a double tap lands 20 to 250 ms after the first, on
 * whatever has just opened under the finger: a sheet's Chart, Add or Cancel,
 * a confirmation's own button, which on a tall phone sits where the sheet's
 * Cancel was, or the dim around a dialog smaller than the sheet it replaced,
 * which would close it. Tests that are not about this set it to 0
 * (`src/test/setup.ts`).
 */
export const OPEN_GUARD = { ms: 500 }

type Handler = (event: SyntheticEvent) => void

export interface OpenGuard {
  /** Inside the window: what it covers shows as disabled (`data-settling`, theme.css). */
  settling: boolean
  /** For the dialog's content element (Radix's): inside it, and the tap outside it. */
  handlers: {
    onPointerDownCapture: Handler
    onClickCapture: Handler
    onKeyDownCapture: (event: KeyboardEvent) => void
    onPointerDownOutside: (event: Event) => void
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
 * submit its form; Space there is only a space), and a tap outside it does not
 * close it. Escape still closes: a key is never the tail of a double tap.
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
    // Radix closes on a press outside unless its event is prevented. (Focus moving
    // outside never closes a modal dialog, so that one needs nothing.)
    const stay = (event: Event) => {
      if (Date.now() < until.current) event.preventDefault()
    }
    return {
      onPointerDownCapture: swallow,
      onClickCapture: swallow,
      onKeyDownCapture: (event: KeyboardEvent) => {
        if (event.key === 'Enter' || (event.key === ' ' && !typing(event.target))) swallow(event)
      },
      onPointerDownOutside: stay,
    }
  }, [])

  return { settling, handlers }
}
