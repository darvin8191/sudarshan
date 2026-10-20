import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { HOLD_MS, LongPress, type LongPressOptions, SLOP_PX } from './longPress'

/** jsdom has no PointerEvent; a MouseEvent carrying the pointer fields is enough here. */
function pointer(type: string, init: { id?: number; x?: number; y?: number; kind?: string }) {
  const event = new MouseEvent(type, {
    bubbles: true,
    cancelable: true,
    clientX: init.x ?? 0,
    clientY: init.y ?? 0,
  })
  Object.assign(event, { pointerId: init.id ?? 1, pointerType: init.kind ?? 'touch' })
  return event
}

let el: HTMLDivElement
let child: HTMLDivElement
let press: LongPress
let opts: { [K in keyof LongPressOptions]-?: LongPressOptions[K] } & {
  onStart: ReturnType<typeof vi.fn>
  onMove: ReturnType<typeof vi.fn>
  onEnd: ReturnType<typeof vi.fn>
  onClear: ReturnType<typeof vi.fn>
}

beforeEach(() => {
  vi.useFakeTimers()
  el = document.createElement('div')
  child = document.createElement('div')
  el.appendChild(child)
  document.body.appendChild(el)
  el.getBoundingClientRect = () => ({ left: 10, top: 100 }) as DOMRect
  opts = {
    onStart: vi.fn(),
    onMove: vi.fn(),
    onEnd: vi.fn(),
    onClear: vi.fn(),
    holdMs: HOLD_MS,
    setTimer: (run, ms) => setTimeout(run, ms),
    clearTimer: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
  }
  press = new LongPress(el, opts)
})

afterEach(() => {
  press.destroy()
  el.remove()
  vi.useRealTimers()
})

describe('press and hold to read a candle', () => {
  it('starts reading where the finger is once it has been held still', () => {
    child.dispatchEvent(pointer('pointerdown', { x: 60, y: 300 }))
    vi.advanceTimersByTime(HOLD_MS - 1)
    expect(opts.onStart).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    // Pixels from the chart's own corner, not the screen's.
    expect(opts.onStart).toHaveBeenCalledWith({ x: 50, y: 200 })
  })

  it('leaves a finger that moves first to the chart, as a pan', () => {
    child.dispatchEvent(pointer('pointerdown', { x: 60, y: 300 }))
    child.dispatchEvent(pointer('pointermove', { x: 60 + SLOP_PX + 1, y: 300 }))
    vi.advanceTimersByTime(HOLD_MS * 2)
    expect(opts.onStart).not.toHaveBeenCalled()
  })

  it('tolerates the drift of a finger held still', () => {
    child.dispatchEvent(pointer('pointerdown', { x: 60, y: 300 }))
    child.dispatchEvent(pointer('pointermove', { x: 63, y: 303 }))
    vi.advanceTimersByTime(HOLD_MS)
    expect(opts.onStart).toHaveBeenCalledOnce()
  })

  it('follows the held finger and keeps its moves from panning the chart', () => {
    const chartHeard = vi.fn()
    child.addEventListener('pointermove', chartHeard)
    child.dispatchEvent(pointer('pointerdown', { x: 60, y: 300 }))
    vi.advanceTimersByTime(HOLD_MS)
    const move = pointer('pointermove', { x: 90, y: 280 })
    child.dispatchEvent(move)
    expect(opts.onMove).toHaveBeenCalledWith({ x: 80, y: 180 })
    expect(chartHeard).not.toHaveBeenCalled()
    expect(move.defaultPrevented).toBe(true)
  })

  it('leaves the reading up when the finger lifts, and clears it on the next touch', () => {
    child.dispatchEvent(pointer('pointerdown', { x: 60, y: 300 }))
    vi.advanceTimersByTime(HOLD_MS)
    child.dispatchEvent(pointer('pointerup', { x: 60, y: 300 }))
    expect(opts.onEnd).toHaveBeenCalledOnce()
    expect(opts.onClear).not.toHaveBeenCalled()
    child.dispatchEvent(pointer('pointerdown', { id: 2, x: 200, y: 300 }))
    expect(opts.onClear).toHaveBeenCalledOnce()
  })

  it('gives a second finger back to the chart, as a pinch', () => {
    child.dispatchEvent(pointer('pointerdown', { id: 1, x: 60, y: 300 }))
    child.dispatchEvent(pointer('pointerdown', { id: 2, x: 160, y: 300 }))
    vi.advanceTimersByTime(HOLD_MS * 2)
    expect(opts.onStart).not.toHaveBeenCalled()
  })

  it('ends a reading when a second finger lands during it', () => {
    child.dispatchEvent(pointer('pointerdown', { id: 1, x: 60, y: 300 }))
    vi.advanceTimersByTime(HOLD_MS)
    child.dispatchEvent(pointer('pointerdown', { id: 2, x: 160, y: 300 }))
    expect(opts.onEnd).toHaveBeenCalledOnce()
    expect(opts.onClear).toHaveBeenCalledOnce()
  })

  it('leaves a mouse alone: the chart has its own crosshair for one', () => {
    child.dispatchEvent(pointer('pointerdown', { x: 60, y: 300, kind: 'mouse' }))
    vi.advanceTimersByTime(HOLD_MS * 2)
    expect(opts.onStart).not.toHaveBeenCalled()
  })

  it("keeps the browser's long-press menu away while a candle is read", () => {
    child.dispatchEvent(pointer('pointerdown', { x: 60, y: 300 }))
    vi.advanceTimersByTime(HOLD_MS)
    const menu = new MouseEvent('contextmenu', { bubbles: true, cancelable: true })
    child.dispatchEvent(menu)
    expect(menu.defaultPrevented).toBe(true)
  })

  it('stops listening once destroyed', () => {
    press.destroy()
    child.dispatchEvent(pointer('pointerdown', { x: 60, y: 300 }))
    vi.advanceTimersByTime(HOLD_MS * 2)
    expect(opts.onStart).not.toHaveBeenCalled()
    expect(vi.getTimerCount()).toBe(0)
  })
})
