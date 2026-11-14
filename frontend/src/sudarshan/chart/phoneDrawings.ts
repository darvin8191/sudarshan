/**
 * Drawings on the phone chart (M4.3, C7): six tools and a ruler, placed by
 * taps, saved for each instrument on this phone.
 *
 * - **Six tools**: horizontal line, trend line, ray, rectangle, Fib
 *   retracement and a text note; and Measure, which is never kept. The rest of
 *   the engine's tools stay in the full terminal.
 * - **Saved per instrument**, under the draw tier's own key rule
 *   (`lib/trading/drawingScope.ts`: exchange and symbol, each URI-encoded) in
 *   the phone chart's own storage namespace, so a level drawn on one
 *   instrument never shows on another. On this device only: the terminal's
 *   drawings are its panes', and a server copy is a later release.
 * - **The swap happens between two charts.** The host builds a chart anew per
 *   load, so the drawings of the instrument just loaded are put on the new
 *   chart, as the terminal does for a pane.
 * - **The draw tier loads on the first use of a tool**, or when the instrument
 *   opened has drawings saved; a chart nobody draws on never pays for it.
 *
 * Framework-agnostic, like the host: the page hears changes through
 * `subscribe` and reads `state`.
 */
import type { Chart } from 'openalgo-charts'
import type { DrawingController, DrawingsDocument } from 'openalgo-charts/draw'
import { resolveCssColor } from '@/lib/trading/chartTheme'
import { drawingsKey } from '@/lib/trading/drawingScope'

/** The tools the phone offers, in the row's order; the glyph ids are the engine's own. */
export const DRAW_TOOLS = [
  { id: 'horizontal-line', label: 'Horizontal line', taps: 1 },
  { id: 'trend-line', label: 'Trend line', taps: 2 },
  { id: 'ray', label: 'Ray', taps: 2 },
  { id: 'rectangle', label: 'Rectangle', taps: 2 },
  { id: 'fib-retracement', label: 'Fib retracement', taps: 2 },
  { id: 'text', label: 'Text note', taps: 1 },
  { id: 'measure', label: 'Measure', taps: 2 },
] as const

export type DrawToolId = (typeof DRAW_TOOLS)[number]['id']

/**
 * The three inks a drawing can take (C8), each with a line style of its own, so
 * two drawings differ by their stroke as well as their colour.
 */
export const DRAW_INKS = [
  { name: 'Ink', token: '--text-primary', lineStyle: 'solid' },
  { name: 'Peacock', token: '--brand', lineStyle: 'dashed' },
  { name: 'Amber', token: '--warning-text', lineStyle: 'dotted' },
] as const

/** Never saved and gone with the next tool: a reading, not a drawing. */
const TRANSIENT: ReadonlySet<string> = new Set(['measure'])

/** The storage entry of an instrument's drawings on this phone; null with no symbol. */
export function phoneDrawingsKey(symbol: string, exchange: string): string | null {
  const key = drawingsKey({ symbol, exchange })
  return key === null ? null : `sudarshan-chart-${key}`
}

function read(key: string | null): string | null {
  if (key === null) return null
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

/** Whether an instrument has drawings saved on this phone. */
export function hasSavedDrawings(symbol: string, exchange: string): boolean {
  const raw = read(phoneDrawingsKey(symbol, exchange))
  if (!raw) return false
  try {
    const drawings = (JSON.parse(raw) as { drawings?: unknown } | null)?.drawings
    return Array.isArray(drawings) && drawings.length > 0
  } catch {
    return false
  }
}

/** What the page draws its controls from. */
export interface DrawState {
  /** The tool held, waiting for its taps; null when none is. */
  tool: DrawToolId | null
  /** Drawings on this instrument, a ruler not counted. */
  count: number
  canUndo: boolean
  /** The draw tier could not be loaded: said beside the tools. */
  failed: boolean
  /** The drawing a tap selected (C8): what it is, and which ink it has, if one of the three. */
  selected: { id: string; tool: string; label: string; ink: number | null } | null
  /** Drawings locked where they stand: a tap on the chart no longer selects them. */
  locked: number
}

const IDLE: DrawState = {
  tool: null,
  count: 0,
  canUndo: false,
  failed: false,
  selected: null,
  locked: 0,
}

type Tier = typeof import('openalgo-charts/draw')

export class PhoneDrawings {
  private tier: Tier | null = null
  private loading: Promise<Tier | null> | null = null
  private chart: Chart | null = null
  private key: string | null = null
  private ctl: DrawingController | null = null
  private value: DrawState = IDLE
  private failed = false
  private readonly listeners = new Set<() => void>()
  /** A text note was placed and waits for its words. */
  private readonly onNote: (id: string) => void

  constructor(onNote: (id: string) => void = () => {}) {
    this.onNote = onNote
  }

  readonly state = (): DrawState => this.value

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  /**
   * A chart was built for an instrument: its saved drawings go on it. The tier
   * is fetched for them only when there are some; otherwise the first tool does.
   */
  attach(chart: Chart, symbol: string, exchange: string): void {
    this.detach()
    this.chart = chart
    this.key = phoneDrawingsKey(symbol, exchange)
    if (this.tier) this.build(chart)
    else if (hasSavedDrawings(symbol, exchange)) void this.ensure()
    this.sync()
  }

  /** The chart is going: its controller goes with it. What was drawn is already saved. */
  detach(): void {
    const ctl = this.ctl
    this.ctl = null
    this.chart = null
    if (ctl && !ctl.isDestroyed) {
      try {
        ctl.destroy()
      } catch {
        /* went with its chart */
      }
    }
    this.sync()
  }

  /** Hold a tool for its taps, or none. A ruler left on the chart goes first. */
  async setTool(tool: DrawToolId | null): Promise<void> {
    const ctl = await this.ensure()
    if (!ctl) return
    this.clearTransient(ctl)
    ctl.cancel()
    ctl.setTool(tool)
    this.sync()
  }

  /** Leave the tool and whatever of a drawing was half placed. */
  cancel(): void {
    const ctl = this.ctl
    if (!ctl) return
    ctl.cancel()
    ctl.setTool(null)
    this.clearTransient(ctl)
    this.sync()
  }

  /** Take back the last step: a drawing placed, moved or deleted. */
  undo(): void {
    if (this.ctl?.undo()) this.changed()
  }

  /** The words of a text note; an empty note is taken off the chart. */
  setNote(id: string, words: string): void {
    const ctl = this.ctl
    const drawing = ctl?.get(id)
    if (!ctl || !drawing) return
    const value = words.trim()
    if (value === '') ctl.remove(id)
    else ctl.update(id, { text: { ...drawing.text, value } })
    this.changed()
  }

  /** Delete the selected drawing (C8). Undo brings it back. */
  removeSelected(): void {
    const id = this.ctl?.selected()
    if (id && this.ctl?.remove(id)) this.changed()
  }

  /**
   * Lock the selected drawing where it stands: it is still drawn, and a tap on
   * the chart no longer selects or moves it. `unlockAll` is the way back.
   */
  lockSelected(): void {
    const ctl = this.ctl
    const id = ctl?.selected()
    if (!ctl || !id) return
    ctl.update(id, { locked: true })
    ctl.select(null)
    this.changed()
  }

  unlockAll(): void {
    const ctl = this.ctl
    if (!ctl) return
    const locked = ctl.drawings().filter((d) => d.locked)
    if (!locked.length) return
    ctl.updateMany(locked.map((d) => ({ id: d.id, patch: { locked: false } })))
    this.changed()
  }

  /** Give the selected drawing one of the three inks, with that ink's line style. */
  setInk(index: number): void {
    const ctl = this.ctl
    const id = ctl?.selected()
    const ink = DRAW_INKS[index]
    const drawing = id ? ctl?.get(id) : undefined
    if (!ctl || !id || !ink || !drawing) return
    const color = resolveCssColor(`var(${ink.token})`)
    ctl.update(id, {
      style: { ...drawing.style, color, lineStyle: ink.lineStyle },
      ...(drawing.text ? { text: { ...drawing.text, color } } : {}),
    })
    this.changed()
  }

  /** Let the selection go. */
  deselect(): void {
    this.ctl?.select(null)
    this.sync()
  }

  /** The words a note carries now. */
  noteOf(id: string): string {
    return this.ctl?.get(id)?.text?.value ?? ''
  }

  /** The tier and a controller on the current chart; null when the tier would not load. */
  private async ensure(): Promise<DrawingController | null> {
    if (!this.tier) {
      this.loading ??= import('openalgo-charts/draw').then(
        (tier) => tier,
        () => null
      )
      const tier = await this.loading
      this.loading = null
      this.failed = tier === null
      this.tier ??= tier
    }
    if (this.tier && this.chart && !this.ctl) this.build(this.chart)
    this.sync()
    return this.ctl
  }

  private build(chart: Chart): void {
    const tier = this.tier
    if (!tier || chart.isDestroyed) return
    const ctl = new tier.DrawingController(chart, {
      // Placing is tap, tap: the weak magnet takes a tap near a candle's price to it.
      magnet: 'weak',
      stayInDrawingMode: false,
      clipboard: null,
      inputAnchors: false,
      gestures: { snapModifier: false, measure: false, boxSelect: false, dragCopy: false },
      defaultStyle: {
        color: resolveCssColor('var(--text-primary)'),
        lineWidth: 1.5,
        lineStyle: 'solid',
      },
    })
    this.ctl = ctl
    const saved = read(this.key)
    if (saved) {
      try {
        ctl.untracked(() => ctl.fromJSON(tier.migrateDrawings(JSON.parse(saved))))
      } catch {
        /* a shape this build cannot read: better an empty chart than a broken one */
      }
    }
    const mine = () => this.ctl === ctl
    chart.on('draw:tool', () => mine() && this.sync())
    chart.on('drawing:select', () => mine() && this.sync())
    chart.on('drawing:change', () => mine() && this.changed())
    chart.on('draw:add', (event) => {
      const drawing = (event as { drawing?: { id: string; tool: string }; history?: true }).drawing
      // A note is nothing until it has words: placing one asks for them at once.
      if (mine() && drawing?.tool === 'text' && !(event as { history?: true }).history)
        this.onNote(drawing.id)
    })
  }

  private clearTransient(ctl: DrawingController): void {
    const gone = ctl.drawings().filter((d) => TRANSIENT.has(d.tool))
    if (gone.length)
      ctl.untracked(() =>
        ctl.removeMany(
          gone.map((d) => d.id),
          { force: true }
        )
      )
  }

  /** Something on the chart changed: save it for this instrument, and tell the page. */
  private changed(): void {
    const ctl = this.ctl
    if (ctl && this.key) {
      const all = ctl.toJSON()
      const kept: DrawingsDocument = {
        ...all,
        drawings: all.drawings.filter((d) => !TRANSIENT.has(d.tool)),
      }
      try {
        localStorage.setItem(this.key, JSON.stringify(kept))
      } catch {
        /* storage is full or blocked: the drawing stays on the chart for this visit */
      }
    }
    this.sync()
  }

  private sync(): void {
    const ctl = this.ctl
    const tool = (ctl?.activeTool() ?? null) as DrawToolId | null
    const kept = ctl ? ctl.drawings().filter((d) => !TRANSIENT.has(d.tool)) : []
    const picked = ctl?.selected() ? kept.find((d) => d.id === ctl.selected()) : undefined
    const next: DrawState = {
      tool,
      count: kept.length,
      canUndo: ctl?.canUndo() ?? false,
      failed: this.failed,
      selected: picked
        ? {
            id: picked.id,
            tool: picked.tool,
            label: DRAW_TOOLS.find((one) => one.id === picked.tool)?.label ?? 'Drawing',
            ink: inkOf(picked.style),
          }
        : null,
      locked: kept.filter((d) => d.locked).length,
    }
    const before = this.value
    if (
      before.tool === next.tool &&
      before.count === next.count &&
      before.canUndo === next.canUndo &&
      before.failed === next.failed &&
      before.locked === next.locked &&
      before.selected?.id === next.selected?.id &&
      before.selected?.ink === next.selected?.ink
    )
      return
    this.value = next
    for (const listener of [...this.listeners]) listener()
  }
}

/** Which of the three inks a style is drawn in: told by its line style, as the eye tells it. */
function inkOf(style: { lineStyle?: string } | undefined): number | null {
  const index = DRAW_INKS.findIndex((ink) => ink.lineStyle === (style?.lineStyle ?? 'solid'))
  return index < 0 ? null : index
}
