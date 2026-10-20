import { useEffect, useRef } from 'react'

/** Coming back to a page after more than this away re-syncs what it shows. */
export const AWAY_MS = 15_000

/**
 * Call `onReturn` when the page comes back into view after more than `awayMs`
 * hidden (the phone woke, the trader came back from another app or tab).
 *
 * Its own listener rather than `usePageVisibility`'s `timeSinceHidden`, which
 * read zero by the time `wasHidden` was set (fixed there too), and which
 * delivers a return a task late and only for 100 ms, so a check against it is
 * easy to miss.
 */
export function useOnReturn(onReturn: () => void, awayMs: number | null = AWAY_MS, enabled = true) {
  const handler = useRef(onReturn)
  useEffect(() => {
    handler.current = onReturn
  }, [onReturn])

  useEffect(() => {
    if (!enabled || awayMs === null || typeof document === 'undefined') return
    let hiddenAt: number | null = document.visibilityState === 'hidden' ? Date.now() : null
    const onVisibility = () => {
      if (document.visibilityState === 'hidden') {
        hiddenAt ??= Date.now()
        return
      }
      const away = hiddenAt === null ? 0 : Date.now() - hiddenAt
      hiddenAt = null
      if (away > awayMs) handler.current()
    }
    document.addEventListener('visibilitychange', onVisibility)
    return () => document.removeEventListener('visibilitychange', onVisibility)
  }, [awayMs, enabled])
}
