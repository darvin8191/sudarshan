import { useCallback, useLayoutEffect, useRef } from 'react'

/**
 * A function that keeps its identity while calling the newest `fn` it was
 * handed. The upstream pages build their handlers afresh on every render (once
 * a second, for the age on their own status line); the cards take them through
 * this, so a card is not redrawn because its page was.
 */
export function useLatest<A extends unknown[], R>(fn: (...args: A) => R): (...args: A) => R {
  const ref = useRef(fn)
  useLayoutEffect(() => {
    ref.current = fn
  })
  return useCallback((...args: A) => ref.current(...args), [])
}

/**
 * The value, or the last one that was not null: what a dialog keeps saying
 * while it fades out after Back has taken its entry away.
 */
export function useHeld<T>(value: T | null): T | null {
  const last = useRef<T | null>(value)
  if (value !== null) last.current = value
  return value ?? last.current
}
