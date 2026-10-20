import { useCallback, useEffect, useRef } from 'react'

export interface SpacedReadOptions {
  /**
   * Coming back to the page after more than this long hidden reads once, even
   * if nothing asked meanwhile (null: only if something asked).
   */
  awayMs?: number | null
}

/**
 * A REST read asked for by many things (a new symbol in a book, a reconnect,
 * coming back to the page), made at most once per `gapMs`. A request inside
 * the gap becomes one read at its end, however many came; the first request
 * reads at once. A page that is hidden reads nothing; when it is seen again it
 * reads once if anything asked meanwhile or it was away longer than `awayMs`.
 * The return is handled here, with the rest, so that coming back owes one
 * read, not one now and another at the end of the gap.
 *
 * Every REST call to Upstox comes out of one budget that OpenAlgo shares among
 * every page and tab, so a book that gains a row with every fill must not
 * read its quotes again with every fill.
 */
export function useSpacedRead(
  read: () => void,
  gapMs: number,
  { awayMs = null }: SpacedReadOptions = {}
): () => void {
  const readRef = useRef(read)
  useEffect(() => {
    readRef.current = read
  }, [read])

  const lastAt = useRef<number | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const missed = useRef(false)

  const request = useCallback(() => {
    if (typeof document !== 'undefined' && document.visibilityState === 'hidden') {
      missed.current = true
      return
    }
    const wait = lastAt.current === null ? 0 : lastAt.current + gapMs - Date.now()
    if (wait <= 0) {
      if (timer.current) clearTimeout(timer.current)
      timer.current = null
      lastAt.current = Date.now()
      readRef.current()
      return
    }
    if (timer.current) return
    timer.current = setTimeout(() => {
      timer.current = null
      request()
    }, wait)
  }, [gapMs])

  useEffect(() => {
    if (typeof document === 'undefined') return
    let hiddenAt: number | null = document.visibilityState === 'hidden' ? Date.now() : null
    const onVisibility = () => {
      if (document.visibilityState === 'hidden') {
        hiddenAt ??= Date.now()
        return
      }
      const away = hiddenAt === null ? 0 : Date.now() - hiddenAt
      hiddenAt = null
      const longAway = awayMs !== null && away > awayMs
      if (!missed.current && !longAway) return
      missed.current = false
      request()
    }
    document.addEventListener('visibilitychange', onVisibility)
    return () => document.removeEventListener('visibilitychange', onVisibility)
  }, [request, awayMs])

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current)
      timer.current = null
    },
    []
  )

  return request
}
