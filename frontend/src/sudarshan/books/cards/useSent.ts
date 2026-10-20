import { useCallback, useEffect, useState } from 'react'

/** How long an exit or a cancel sent from the cards holds its button, at most (M6.9). */
export const SENT_HOLD_MS = 15_000

/** One exit or cancel sent: when, and what the row said then. */
export interface SentMark<T> {
  at: number
  was: T
}

const NONE: ReadonlyMap<string, never> = new Map<string, never>()

/**
 * The exits or cancels sent from a phone book, by row. Each holds its row's
 * button from the tap until the book shows the row moved on (the board calls
 * `settle` with the rows it sees go), or `SENT_HOLD_MS` pass. Upstox's REST
 * book can say the old quantity for a second or more after an exit, and
 * /closeposition sizes its order from that book, so a second Exit in that
 * moment would reverse the position rather than close it. A send the page
 * reports as refused (`false`) is let go at once, so it can be tried again.
 */
export function useSent<T>(): {
  marks: ReadonlyMap<string, SentMark<T>>
  /** Hold these rows while `send` runs, and for as long as it went through. */
  hold: (rows: Array<[string, T]>, send: () => unknown) => Promise<void>
  /** Let go of rows the book shows have moved on. */
  settle: (ids: string[]) => void
} {
  const [marks, setMarks] = useState<ReadonlyMap<string, SentMark<T>>>(NONE)

  const stamp = useCallback((rows: Array<[string, T]>) => {
    setMarks((current) => {
      const next = new Map(current)
      const at = Date.now()
      for (const [id, was] of rows) next.set(id, { at, was })
      return next
    })
  }, [])

  const settle = useCallback((ids: string[]) => {
    if (ids.length === 0) return
    setMarks((current) => {
      if (!ids.some((id) => current.has(id))) return current
      const next = new Map(current)
      for (const id of ids) next.delete(id)
      return next
    })
  }, [])

  const hold = useCallback(
    async (rows: Array<[string, T]>, send: () => unknown) => {
      stamp(rows)
      let went: unknown
      try {
        went = await send()
      } catch {
        went = false
      }
      const ids = rows.map(([id]) => id)
      // Refused: nothing went, so nothing to wait for. Sent: the wait starts now.
      if (went === false) settle(ids)
      else stamp(rows)
    },
    [settle, stamp]
  )

  // Each mark lets go once its time is up, the book having said nothing.
  useEffect(() => {
    if (marks.size === 0) return
    let first = Number.POSITIVE_INFINITY
    for (const mark of marks.values()) first = Math.min(first, mark.at)
    const timer = setTimeout(
      () => {
        setMarks((current) => {
          const now = Date.now()
          const next = new Map<string, SentMark<T>>()
          for (const [id, mark] of current) if (now - mark.at < SENT_HOLD_MS) next.set(id, mark)
          // A new map even when none was due yet, so the next one is timed again.
          return next
        })
      },
      Math.max(0, first + SENT_HOLD_MS - Date.now())
    )
    return () => clearTimeout(timer)
  }, [marks])

  return { marks, hold, settle }
}
