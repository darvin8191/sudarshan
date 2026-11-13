/**
 * Rows that follow the money (M8.10, F11), free of React like `follow.ts`: how a row is named, the
 * line under it, the form's choices and the request it sends, and the resolver that asks the server
 * which contract a row stands on now (`resolve`, W15) and decides when to ask again.
 *
 * The server owns every rule (the expiry, the ATM, the band, the roll). The page only compares the
 * underlying's ticks with the band the server sent and asks again when one leaves it, at
 * `valid_until`, after a reconnect, on returning to the page and on the first tick after "waiting".
 * It never asks on a clock that repeats.
 */
import { rollOf } from './strikePicker'
import { CLOCK } from './ViewSheet'
import type {
  Contract,
  Expiry,
  ExpiryRule,
  FollowKind,
  OptRight,
  Resolved,
  ResolveItem,
  FollowRow as Rule,
} from './v2/types'

/** The chip's help text, from the design: said where a trader can read it, never only on hover. */
export const ATM_HELP =
  'Nearest strike, kept while the price stays inside its band. The option chain may show a neighbouring strike as ATM at the same moment.'

/** A strike line ("Moved from ...") and a roll line stay this long. */
export const NOTE_MS = 60_000

/** A row is asked about at most this often when its price leaves the band or the first price comes. */
export const GAP_MS = 2_000

/** The server takes at most this many rows in one `resolve`. */
export const MAX_ROWS = 100

const NUMBER = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 })
/** 25900 as "25,900" and 25912.35 as "25,912.35": the server's own spelling (`numfmt.num`). */
export const num = (value: number): string => NUMBER.format(value)

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
/** "2026-11-24" as "24 Nov" (F12: the middle of a strike row). */
export function dayShort(iso: string): string {
  const [, month, day] = iso.split('-').map(Number)
  return month && day ? `${day} ${MONTHS[month - 1]}` : iso
}

/** "2026-11-24" as "24 Nov 2026". */
export function dayLabel(iso: string): string {
  const [year, month, day] = iso.split('-').map(Number)
  return year && month && day ? `${day} ${MONTHS[month - 1]} ${year}` : iso
}

/** EXCHANGE:SYMBOL of what a row follows: the market-data cache key of its underlying. */
export const underlyingKey = (rule: Rule): string =>
  `${rule.underlying_exchange}:${rule.underlying}`

/** Where a row's contracts trade. */
export const derivativesExchange = (rule: Pick<Rule, 'underlying_exchange'>): string =>
  rule.underlying_exchange.startsWith('NSE') ? 'NFO' : 'BFO'

/** The chip: ATM, ATM+2, ATM-1, Current-month FUT (the server's `chip`), Fixed strike for a stored strike. */
export function chipOf(rule: Rule): string {
  if (rule.kind === 'month_future') return 'Current-month FUT'
  if (rule.strike_rule === 'fixed') return 'Fixed strike'
  const offset = rule.offset ?? 0
  return offset === 0 ? 'ATM' : `ATM${offset > 0 ? '+' : '-'}${Math.abs(offset)}`
}

/** The row's title once the contract is known: NIFTY 25,900 CE, NIFTY FUT. */
export function titleOf(rule: Rule, strike: number | null): string {
  if (rule.kind === 'month_future') return `${rule.underlying} FUT`
  const right = isPair(rule) ? (strike === null ? 'CE and PE' : '') : (rule.opt_right ?? '') // F12
  return strike === null
    ? `${rule.underlying} ${chipOf(rule)} ${right}`.trim()
    : `${rule.underlying} ${num(strike)} ${right}`.trim()
}

/** The line under the title: NFO · 24 Nov 2026 · follows NIFTY (no date while there is no contract). */
export function detailOf(rule: Rule, contract: Contract | null): string {
  const expiry = contract?.expiry ? ` · ${dayLabel(contract.expiry)}` : ''
  return `${derivativesExchange(rule)}${expiry} · follows ${rule.underlying}`
}

/** F12: a strike row follows the CE and the PE of one strike. */
export const isPair = (rule: Pick<Rule, 'kind'>): boolean => rule.kind === 'strike_pair'

/** The contract a resolved row stands on: its future, the side the rule asked for, or a pair's CE (F12). */
export function contractOf(rule: Rule, got: Resolved): Contract | null {
  if (rule.kind === 'month_future') return got.future
  if (isPair(rule)) return got.ce && got.pe ? got.ce : null // F12: both, or neither
  return rule.opt_right === 'PE' ? got.pe : rule.opt_right === 'CE' ? got.ce : null
}

/** The server's own words for a row that has just rolled to the next expiry. */
export const isRoll = (words: string): boolean => words.startsWith('Rolled to ')

/** Whether the price is on or past an edge of the band (the server's band is open at both ends). */
export function leftBand(
  price: number,
  band: { low: number | null; high: number | null }
): boolean {
  return (band.low !== null && price <= band.low) || (band.high !== null && price >= band.high)
}

/** "Moved from 25,850 CE at 10:12 (NIFTY 25,931)": the server's `words.dynamic.moved`, in the page's hands. */
export function movedWords(rule: Rule, from: number, at: number, price: number | null): string {
  const under = price === null ? '' : ` (${rule.underlying} ${num(price)})`
  const right = rule.opt_right ? ` ${rule.opt_right}` : '' // F12: a strike row has no side
  return `Moved from ${num(from)}${right} at ${CLOCK.format(at)}${under}`
}

// ---------------------------------------------------------------------------------------------
// The form

export const EXPIRIES: ReadonlyArray<{ rule: ExpiryRule; label: string; words: string }> = [
  { rule: 'nearest', label: 'Nearest', words: 'nearest expiry' },
  { rule: 'next', label: 'Next', words: 'next expiry' },
  { rule: 'month', label: "This month's", words: "this month's expiry" },
  { rule: 'next_month', label: "Next month's", words: "next month's expiry" },
]

/** The expiries a kind offers: a future has a monthly contract only, an option has all four. */
export const expiriesFor = (kind: FollowKind) =>
  kind === 'month_future' ? EXPIRIES.filter((each) => each.rule.includes('month')) : EXPIRIES

/** What a new row of this kind starts on. */
export const defaultExpiry = (kind: FollowKind): ExpiryRule =>
  kind === 'month_future' ? 'month' : 'nearest'

/** ATM, ATM+1 to ATM+10, ATM-1 to ATM-10, as the form offers them (the offset is in strike steps). */
export const STRIKE_CHOICES: readonly number[] = [
  0,
  ...Array.from({ length: 10 }, (_, index) => index + 1),
  ...Array.from({ length: 10 }, (_, index) => -(index + 1)),
]
export const offsetLabel = (offset: number): string =>
  offset === 0 ? 'ATM' : `ATM${offset > 0 ? '+' : '-'}${Math.abs(offset)}`

/** The form the phone has open (F11): the seam F13's search sheet opens the same form through. */
export type FollowForm =
  | {
      mode: 'add'
      kind: FollowKind
      underlying: string
      /** The underlying's exchange: NSE_INDEX, NSE, BSE_INDEX or BSE. */
      exchange: string
      /** The section of the symbol the row was asked for from, or null. */
      sectionId: number | null
      /**
       * F12, the seam of F14's strike picker ("Add as one row"): `kind: 'strike_pair'` with `fixed` adds a
       * row on this strike, instead of the ATM plus an offset. `expiry` is the rule the picked expiry
       * stands for (nearest when absent); every rule is relative, so the row still moves to another
       * expiry, and `expiries` (the open expiries the picker showed) lets the form say when (review M3).
       */
      fixed?: { strike: number; expiry?: ExpiryRule; expiries?: readonly Expiry[] }
    }
  | { mode: 'edit'; rowId: number }
  | { mode: 'remove'; rowId: number }

/** What the form holds. */
export interface Draft {
  /** `strike_pair` is the side "Both, as one row" (F12). */
  kind: FollowKind
  expiry: ExpiryRule
  offset: number
  right: OptRight
  /** F12: a row on one strike (from the strike picker), its expiry still moving by the rule; null follows the ATM. */
  strike: number | null
}

/** The form's start: from a new row of this kind, or from the row being edited. */
export function draftOf(
  kind: FollowKind,
  rule?: Rule,
  fixed?: { strike: number; expiry?: ExpiryRule }
): Draft {
  const strike = rule?.strike_rule === 'fixed' ? rule.strike : (fixed?.strike ?? null)
  return {
    kind,
    expiry: rule?.expiry_rule ?? fixed?.expiry ?? defaultExpiry(kind),
    offset: rule?.offset ?? 0,
    right: rule?.opt_right ?? 'CE',
    strike,
  }
}

/** The form's line for a row kept on one strike: the strike is fixed, the expiry is not (review M3). */
export const fixedWords = (strike: number): string =>
  `Strike ${num(strike)}, fixed: it does not move with the price.`

/** When a row on a fixed strike moves to another expiry, when the dates are not known (an edit). */
const MOVES: Record<ExpiryRule, string> = {
  nearest: 'When it expires, this row moves to the next expiry.',
  next: 'When the nearest expiry ends, this row moves to the one after it.',
  month: "When it expires, this row moves to next month's expiry.",
  next_month: "When this month's expiry ends, this row moves to the month after.",
}

/**
 * The preview line over the buttons: what the row will follow, in the words of the form. A row on a
 * fixed strike still moves to another expiry, because every rule the server keeps is relative (a rule
 * on a date needs a schema step), so the line says the date it is on today and when it moves, from the
 * open expiries the strike picker showed: "NIFTY 25,900 CE and PE as one row, next expiry (1 Dec
 * today). When 24 Nov expires, this row moves to 8 Dec." (review M3).
 */
export function previewOf(name: string, draft: Draft, expiries?: readonly Expiry[]): string {
  const expiry = EXPIRIES.find((each) => each.rule === draft.expiry)?.words ?? ''
  if (draft.kind === 'month_future') return `${name} future, ${expiry}.`
  const side = draft.kind === 'strike_pair' ? 'CE and PE as one row' : draft.right // F12
  if (draft.strike !== null) {
    const what = `${name} ${num(draft.strike)} ${side}, ${expiry}`
    const roll = expiries ? rollOf(expiries, draft.expiry) : null
    if (!roll) return `${what}. ${MOVES[draft.expiry]}`
    const to = roll.to ? dayShort(roll.to) : 'the expiry after it'
    return `${what} (${dayShort(roll.on)} today). When ${dayShort(roll.ends)} expires, this row moves to ${to}.`
  }
  return `${name} ${offsetLabel(draft.offset)} ${side}, ${expiry}. The strike is picked from the price when the row is added, and moves with it.`
}

/** The fields of `row_add` for a draft (the list and its version are the caller's). */
export function addBody(
  draft: Draft,
  under: { underlying: string; underlying_exchange: string },
  section: number | null
) {
  const base = {
    kind: draft.kind,
    underlying: under.underlying,
    underlying_exchange: under.underlying_exchange,
    expiry_rule: draft.expiry,
    ...(section === null ? {} : { section_id: section }),
  }
  return draft.kind === 'month_future' ? base : { ...base, ...optionFields(draft) }
}

/** F12: what an option row says besides its expiry: ATM and an offset, or a fixed strike; a side, or both. */
function optionFields(draft: Draft) {
  const strike =
    draft.strike === null
      ? { strike_rule: 'atm' as const, offset: draft.offset }
      : { strike_rule: 'fixed' as const, strike: draft.strike }
  return draft.kind === 'strike_pair' ? strike : { ...strike, opt_right: draft.right }
}

/** The fields of `row_edit` for a draft: what the form can change, nothing of the underlying. */
export function editBody(draft: Draft) {
  return draft.kind === 'month_future'
    ? { expiry_rule: draft.expiry }
    : { expiry_rule: draft.expiry, kind: draft.kind, ...optionFields(draft) }
}

// ---------------------------------------------------------------------------------------------
// The resolver

/** What the page shows of a row: the contract it last resolved to. */
export interface Shown {
  contract: Contract
  strike: number | null
  /** F12: a strike row's two legs (`contract` is its CE). */
  pair?: { ce: Contract; pe: Contract }
}

/** One row as the resolver holds it. */
export interface RowState {
  rule: Rule
  /** The last answer, or null before the first. */
  answer: Resolved | null
  /** The last answer that carried a contract: what the row stands on, kept while it waits. */
  shown: Shown | null
  /** The line the page composes (a move, the server's roll words) and when it stops showing. */
  note: { text: string; until: number } | null
  /** A sentence for a read that failed, shown while the row has no contract. */
  problem: string | null
  /** When a tick last made the row ask (a band exit or the first price), and whether an ask is in flight. */
  askedAt: number | null
  flying: boolean
}

export type Reason = 'first' | 'band' | 'valid' | 'reconnect' | 'return' | 'unwait'

/** What is known of an underlying's price: its latest tick. */
export interface Tick {
  price: number
  at: number
}

export interface Deps {
  send: (rows: ResolveItem[], connected: boolean) => Promise<Resolved[]>
  now: () => number
  /** The words of a failed read, in a trader's terms. */
  problem: (error: unknown) => string
}

/** A rule's identity: a change of any field asks the server again. */
const signature = (rule: Rule): string =>
  [
    rule.kind,
    rule.underlying_exchange,
    rule.underlying,
    rule.expiry_rule,
    rule.strike_rule,
    rule.offset,
    rule.strike,
    rule.opt_right,
  ].join('|')

/**
 * Keeps each follow row's answer and decides when to ask again. Free of React: the hook gives it
 * events (ticks, a reconnect, a return, a timer that fires once at `valid_until`) and reads its
 * rows. One ask for a set of rows is one `resolve` (up to 100); a row never has two in flight.
 */
export class FollowResolver {
  readonly rows = new Map<number, RowState>()
  /** Changes whenever what a row shows changes. */
  rev = 0
  /** Whether the page's socket is up (it goes in every ask). */
  connected = true
  private ticks = new Map<string, Tick>()
  private signatures = new Map<number, string>()
  private listeners = new Set<() => void>()
  /** Rows a flight could not take, to be asked once it lands, with why. */
  private later = new Map<number, Reason>()
  private held = new Map<number, number | null>()
  private disposed = false

  private deps: Deps

  constructor(deps: Deps) {
    this.deps = deps
  }

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  getRev = (): number => this.rev

  dispose(): void {
    this.disposed = true
  }

  revive(): void {
    this.disposed = false
  }

  private changed(): void {
    this.rev += 1
    for (const listener of this.listeners) listener()
  }

  /** The rows of the open list: a new or changed rule is asked about at once, a removed one is forgotten. */
  sync(rules: readonly Rule[]): void {
    const keep = new Set(rules.map((rule) => rule.id))
    let moved = false
    for (const id of [...this.rows.keys()])
      if (!keep.has(id)) {
        this.rows.delete(id)
        this.signatures.delete(id)
        this.later.delete(id)
        moved = true
      }
    const fresh: number[] = []
    for (const rule of rules) {
      const sig = signature(rule)
      const had = this.rows.get(rule.id)
      if (!had || this.signatures.get(rule.id) !== sig) {
        this.rows.set(rule.id, {
          rule,
          answer: null,
          shown: null,
          note: null,
          problem: null,
          askedAt: had?.askedAt ?? null,
          flying: had?.flying ?? false,
        })
        this.signatures.set(rule.id, sig)
        fresh.push(rule.id)
        moved = true
      } else if (had) {
        had.rule = rule
      }
    }
    if (moved) this.changed()
    if (fresh.length > 0) void this.ask('first', fresh)
  }

  /** The latest tick of each underlying (EXCHANGE:SYMBOL), from the page's socket. */
  tick(prices: ReadonlyMap<string, Tick>, connected: boolean): void {
    this.connected = connected
    this.ticks = new Map(prices)
    const out: number[] = []
    const first: number[] = []
    for (const [id, row] of this.rows) {
      const price = prices.get(underlyingKey(row.rule))
      if (!price) continue
      if (row.answer?.state === 'waiting' || (row.answer === null && row.problem !== null))
        first.push(id)
      else if (row.answer?.band && leftBand(price.price, row.answer.band)) out.push(id)
    }
    if (out.length > 0) void this.ask('band', out)
    if (first.length > 0) void this.ask('unwait', first)
  }

  /**
   * Ask the server about rows (all of them when `ids` is absent). Band and waiting asks keep to one in
   * `GAP_MS` for a row; any ask skips a row that already has one in flight, and is made again when
   * that one lands, so a reconnect during a flight is not lost.
   */
  async ask(reason: Reason, ids?: readonly number[]): Promise<void> {
    if (this.disposed) return
    const now = this.deps.now()
    const send: number[] = []
    for (const id of ids ?? [...this.rows.keys()]) {
      const row = this.rows.get(id)
      if (!row) continue
      if (row.flying) {
        if (reason !== 'band' && reason !== 'unwait') this.later.set(id, reason)
        continue
      }
      const paced = reason === 'band' || reason === 'unwait'
      if (paced && row.askedAt !== null && now - row.askedAt < GAP_MS) continue
      send.push(id)
    }
    for (let from = 0; from < send.length; from += MAX_ROWS)
      await this.flight(reason, send.slice(from, from + MAX_ROWS), now)
  }

  private async flight(reason: Reason, ids: number[], now: number): Promise<void> {
    const items: ResolveItem[] = ids.map((id) => {
      const row = this.rows.get(id) as RowState
      const tick = this.ticks.get(underlyingKey(row.rule))
      // After a roll the strike last shown belongs to the old expiry's ladder.
      const held = reason === 'valid' ? null : (row.shown?.strike ?? null)
      this.held.set(id, held)
      return {
        id,
        price: tick?.price ?? null,
        price_at: tick?.at ?? null,
        source: tick ? 'tick' : null,
        held,
      }
    })
    for (const id of ids) {
      const row = this.rows.get(id)
      if (row) {
        row.flying = true
        if (reason === 'band' || reason === 'unwait') row.askedAt = now
      }
    }
    let answers: Resolved[] | null = null
    let said = ''
    try {
      answers = await this.deps.send(items, this.connected)
    } catch (error) {
      said = this.deps.problem(error)
    }
    if (this.disposed) return
    const landed = this.deps.now()
    for (const id of ids) {
      const row = this.rows.get(id)
      if (!row) continue
      row.flying = false
      const got = answers?.find((each) => each.id === id)
      if (got) this.apply(row, got, landed)
      else row.problem = said || row.problem
    }
    this.changed()
    const again = [...this.later.keys()].filter((id) => ids.includes(id))
    if (again.length > 0) {
      const why = this.later.get(again[0]) ?? 'reconnect'
      for (const id of again) this.later.delete(id)
      await this.ask(why, again)
    }
  }

  private apply(row: RowState, got: Resolved, at: number): void {
    const { rule } = row
    const before = row.shown
    row.answer = got
    row.problem = null
    const contract = got.state === 'ok' || got.state === 'closed' ? contractOf(rule, got) : null
    if (got.state === 'missing') row.shown = null
    else if (contract)
      row.shown = {
        contract,
        strike: got.strike,
        ...(isPair(rule) && got.ce && got.pe ? { pair: { ce: got.ce, pe: got.pe } } : {}), // F12
      }
    if (!contract) return
    if (isRoll(got.words)) {
      row.note = { text: got.words, until: at + NOTE_MS }
    } else if (
      rule.strike_rule !== 'fixed' &&
      rule.kind !== 'month_future' &&
      before &&
      before.strike !== null &&
      got.strike !== null &&
      before.strike !== got.strike &&
      before.contract.expiry === contract.expiry
    ) {
      row.note = {
        text: movedWords(rule, before.strike, got.price_at ?? at, got.price),
        until: at + NOTE_MS,
      }
    }
  }

  /** The earliest `valid_until` still ahead of `now`, or null. */
  nextValid(now: number): number | null {
    let first: number | null = null
    for (const row of this.rows.values()) {
      const at = row.answer?.valid_until
      if (typeof at === 'number' && at > now && (first === null || at < first)) first = at
    }
    return first
  }

  /** The rows whose `valid_until` has come. */
  due(now: number): number[] {
    return [...this.rows]
      .filter(
        ([, row]) => typeof row.answer?.valid_until === 'number' && row.answer.valid_until <= now
      )
      .map(([id]) => id)
  }
}

/** The line under a row now, or null: the page's note while it lasts, else the server's words for the state. */
export function lineOf(row: RowState, now: number): string | null {
  if (row.note && now < row.note.until) return row.note.text
  const state = row.answer?.state
  if (state === 'waiting' || state === 'closed' || state === 'missing')
    return row.answer?.words ?? null
  return row.shown ? null : row.problem
}

/** What a row's sheet says once the row has moved on from the contract the sheet opened with. */
export function followsWords(rule: Rule, shown: Shown | null): string {
  if (!shown) return `This row follows ${rule.underlying} again`
  if (rule.kind === 'month_future')
    return `This row now follows ${rule.underlying} FUT${shown.contract.expiry ? `, ${dayLabel(shown.contract.expiry)}` : ''}`
  return `This row now follows ${shown.strike === null ? '' : `${num(shown.strike)} `}${rule.opt_right ?? ''}`.trim()
}
