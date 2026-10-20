/**
 * Animation frames a test runs by hand.
 *
 * A screen that draws once per frame (`sudarshan/live/useFrameLines`) holds
 * its ticks until the browser's next frame. jsdom's own frames run on a timer
 * outside React's `act`, so a test could neither see a tick at once nor tell
 * one frame from the next. This replaces `requestAnimationFrame` with a queue
 * that runs only when the test says so: push ticks, then `frames.flush()`
 * inside `act`, and exactly one frame has passed.
 *
 * Fake timers replace `requestAnimationFrame` too unless told not to; use
 * `fakeClock()` instead of `vi.useFakeTimers()` so these frames stay in place.
 */
import { vi } from 'vitest'

const queue = new Map<number, FrameRequestCallback>()
let nextId = 1

export const frames = {
  /** How many frames are waiting to run. */
  get pending(): number {
    return queue.size
  },

  /** Replace the browser's frames with this queue. Call from `beforeEach`. */
  install(): void {
    queue.clear()
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
      const id = nextId++
      queue.set(id, callback)
      return id
    })
    vi.stubGlobal('cancelAnimationFrame', (id: number) => {
      queue.delete(id)
    })
  },

  /** Run one frame: every callback waiting now, not those they ask for. */
  flush(): void {
    const due = [...queue.values()]
    queue.clear()
    const at = performance.now()
    for (const callback of due) callback(at)
  },
}

/** Fake timers and a fake clock that leave the frames above alone. */
export function fakeClock(): void {
  vi.useFakeTimers({
    shouldAdvanceTime: true,
    toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'],
  })
}
