/**
 * The writes this page made itself, so the push of one is not read again (design 2.5).
 *
 * A write's answer carries `lists_version`: the version its bump produced, which is the `v` of
 * the push that bump sends. The page notes it when the answer arrives, and a push with that `v`
 * is recognised and skipped, whichever of the two arrives first: a push is held (by `afterWrites`)
 * while a write of this page is still in flight, so the answer can be noted before the push is
 * read. A push from another screen has another `v` and is read as before. What is kept is small:
 * the versions of this page's own writes not yet matched, dropped after `KEEP_MS`.
 */

/** How long a noted version waits for its push. */
export const KEEP_MS = 30_000
/** How long a push is held for a write still in flight. */
export const HOLD_MS = 3_000

const mine = new Map<string, number>()
const waiting = new Set<() => void>()
let inFlight = 0

function forget(now: number) {
  for (const [version, at] of mine) if (now - at > KEEP_MS) mine.delete(version)
}

/** Run a write of this page, counted while it is in flight. */
export async function trackWrite<T>(run: () => Promise<T>): Promise<T> {
  inFlight += 1
  try {
    const answer = await run()
    const version = (answer as { lists_version?: unknown } | null)?.lists_version
    if (typeof version === 'string' && version) {
      forget(Date.now())
      mine.set(version, Date.now())
    }
    return answer
  } finally {
    inFlight -= 1
    if (inFlight === 0) for (const wake of [...waiting]) wake()
  }
}

/** True once, and forgets it, when `version` is the push of a write this page made. */
export function takeOwn(version: string): boolean {
  forget(Date.now())
  return mine.delete(version)
}

/** Run `fn` now, or when the writes in flight have answered (at most `HOLD_MS` later). */
export function afterWrites(fn: () => void): () => void {
  if (inFlight === 0) {
    fn()
    return () => {}
  }
  let done = false
  const go = () => {
    if (done) return
    done = true
    clearTimeout(timer)
    waiting.delete(go)
    fn()
  }
  const timer = setTimeout(go, HOLD_MS)
  waiting.add(go)
  return () => {
    done = true
    clearTimeout(timer)
    waiting.delete(go)
  }
}

/** For tests: forget everything. */
export function resetOwnWrites() {
  mine.clear()
  waiting.clear()
  inFlight = 0
}
