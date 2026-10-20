/**
 * Press and hold to read a candle, on a touch screen.
 *
 * Hold a finger still on the chart for `HOLD_MS` and the chart stops panning
 * under it: a crosshair follows the finger and the candle under it is read
 * out. Lifting the finger leaves that reading up; the next touch on the chart
 * clears it. A finger that moves before the hold is a pan and two fingers are
 * a pinch, and both stay the chart's own.
 *
 * Installed on an element that contains the chart, in the capture phase, so
 * while a candle is being read the moves never reach the chart and it cannot
 * pan. The engine's own crosshair is driven by a hovering mouse; a finger
 * cannot hover, which is what this is for.
 */

export const HOLD_MS = 450
/** How far a finger may drift, in CSS pixels, and still be holding still. */
export const SLOP_PX = 8

export interface PressPoint {
  /** Pixels from the element's left and top edges. */
  x: number
  y: number
}

export interface LongPressOptions {
  /** The hold completed: start reading at this point. */
  onStart(point: PressPoint): void
  /** The held finger moved. */
  onMove(point: PressPoint): void
  /** The finger lifted after a hold; the reading stays up. */
  onEnd(): void
  /** A new touch: whatever reading was up goes. */
  onClear(): void
  holdMs?: number
  setTimer?(run: () => void, ms: number): unknown
  clearTimer?(handle: unknown): void
}

export class LongPress {
  private readonly el: HTMLElement
  private readonly opts: LongPressOptions
  private readonly pointers = new Set<number>()
  private timer: unknown = null
  private start: { id: number; x: number; y: number } | null = null
  private reading = false
  private shown = false

  constructor(el: HTMLElement, options: LongPressOptions) {
    this.el = el
    this.opts = options
    el.addEventListener('pointerdown', this.onDown, true)
    el.addEventListener('pointermove', this.onMove, true)
    el.addEventListener('pointerup', this.onUp, true)
    el.addEventListener('pointercancel', this.onUp, true)
    el.addEventListener('contextmenu', this.onContextMenu, true)
  }

  destroy(): void {
    this.cancelHold()
    this.el.removeEventListener('pointerdown', this.onDown, true)
    this.el.removeEventListener('pointermove', this.onMove, true)
    this.el.removeEventListener('pointerup', this.onUp, true)
    this.el.removeEventListener('pointercancel', this.onUp, true)
    this.el.removeEventListener('contextmenu', this.onContextMenu, true)
  }

  private point(event: PointerEvent): PressPoint {
    const rect = this.el.getBoundingClientRect()
    return { x: event.clientX - rect.left, y: event.clientY - rect.top }
  }

  private cancelHold(): void {
    if (this.timer === null) return
    if (this.opts.clearTimer) this.opts.clearTimer(this.timer)
    else clearTimeout(this.timer as ReturnType<typeof setTimeout>)
    this.timer = null
  }

  private readonly onDown = (event: PointerEvent): void => {
    if (event.pointerType !== 'touch') return
    this.pointers.add(event.pointerId)
    if (this.shown) {
      this.shown = false
      this.opts.onClear()
    }
    if (this.pointers.size > 1) {
      // A second finger is a pinch: give the gesture back to the chart. A
      // reading in progress was cleared just above, with the touch.
      this.cancelHold()
      this.start = null
      if (this.reading) {
        this.reading = false
        this.opts.onEnd()
      }
      return
    }
    const at = this.point(event)
    this.start = { id: event.pointerId, ...at }
    this.cancelHold()
    const run = () => {
      this.timer = null
      if (!this.start || this.start.id !== event.pointerId) return
      this.reading = true
      this.shown = true
      this.opts.onStart({ x: this.start.x, y: this.start.y })
    }
    const wait = this.opts.holdMs ?? HOLD_MS
    this.timer = this.opts.setTimer ? this.opts.setTimer(run, wait) : setTimeout(run, wait)
  }

  private readonly onMove = (event: PointerEvent): void => {
    if (event.pointerType !== 'touch' || !this.start || event.pointerId !== this.start.id) return
    const at = this.point(event)
    if (this.reading) {
      event.stopPropagation()
      event.preventDefault()
      this.opts.onMove(at)
      return
    }
    if (Math.hypot(at.x - this.start.x, at.y - this.start.y) > SLOP_PX) {
      // It moved first: a pan, and the chart's.
      this.cancelHold()
      this.start = null
    }
  }

  private readonly onUp = (event: PointerEvent): void => {
    if (event.pointerType !== 'touch') return
    this.pointers.delete(event.pointerId)
    if (!this.start || event.pointerId !== this.start.id) return
    this.cancelHold()
    this.start = null
    if (this.reading) {
      this.reading = false
      this.opts.onEnd()
    }
  }

  /** A long press raises the browser's context menu on a phone; reading a candle is not that. */
  private readonly onContextMenu = (event: Event): void => {
    if (this.start || this.reading || this.shown) {
      event.preventDefault()
      event.stopPropagation()
    }
  }
}
