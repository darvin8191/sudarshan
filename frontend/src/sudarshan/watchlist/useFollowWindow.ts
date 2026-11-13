import { useEffect, useState } from 'react'
import { FOLLOW_CAP, SETTLE_MS, type Visible, WINDOW, windowStart } from './follow'

/** The rows on screen, read from the page: every row of a long list carries its place in `data-row-index`. */
export function domVisible(): Visible | null {
  let first = Number.POSITIVE_INFINITY
  let last = -1
  for (const element of document.querySelectorAll<HTMLElement>('[data-row-index]')) {
    const box = element.getBoundingClientRect()
    if (box.bottom <= 0 || box.top >= window.innerHeight) continue
    const index = Number(element.dataset.rowIndex)
    first = Math.min(first, index)
    last = Math.max(last, index)
  }
  return last < 0 ? null : { first, last }
}

/**
 * Where the window of a list longer than the cap starts (F9). Recomputed `SETTLE_MS` after the
 * scrolling stops, and moved only when the visible rows leave the middle half of the window. It starts
 * at the top whenever another list opens. A list no longer than the cap never has a window.
 */
export function useFollowWindow(
  count: number,
  listKey: string,
  measure: () => Visible | null = domVisible
): number {
  const [state, setState] = useState({ listKey, start: 0 })
  const long = count > FOLLOW_CAP
  useEffect(() => {
    if (!long) return
    let timer: ReturnType<typeof setTimeout> | undefined
    const settle = () => {
      clearTimeout(timer)
      timer = setTimeout(() => {
        const visible = measure()
        if (!visible) return
        setState((held) => {
          const current = held.listKey === listKey ? held.start : null
          const start = windowStart(count, visible, current)
          return held.listKey === listKey && held.start === start ? held : { listKey, start }
        })
      }, SETTLE_MS)
    }
    window.addEventListener('scroll', settle, { capture: true, passive: true })
    window.addEventListener('resize', settle)
    return () => {
      clearTimeout(timer)
      window.removeEventListener('scroll', settle, { capture: true })
      window.removeEventListener('resize', settle)
    }
  }, [long, count, listKey, measure])
  return long && state.listKey === listKey ? Math.min(state.start, count - WINDOW) : 0
}
