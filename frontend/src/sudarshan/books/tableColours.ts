/**
 * The colours of the books' wide tables (B8: M6.6, M1.18), from the D look's tokens and never
 * from a fixed red or green, so the colour-blind mode and the sunlight contrast reach the tables
 * as they reach the cards. Each colour here has a word, a sign or an arrow beside it where it is
 * used: BUY and SELL are written, a P&L carries its sign and its arrow, a status its word.
 *
 * `--up` and `--down` are gains and losses; `--buy-solid` and `--sell-solid` are sides.
 */

export const UP_TEXT = 'text-[color:var(--up)]'
export const DOWN_TEXT = 'text-[color:var(--down)]'

/** A gain or a loss by its sign: 0 reads as a gain, as the tables have it. */
export const pnlTone = (value: number): string => (value >= 0 ? UP_TEXT : DOWN_TEXT)

/** A money figure with its sign: a plus on a gain, the minus the formatter writes on a loss. */
export const signedMoney = (value: number, format: (value: number) => string): string =>
  `${value > 0 ? '+' : ''}${format(value)}`

/** BUY and SELL, white on the side's solid colour (5:1 or better), with the word. */
export const SIDE_BADGE: Record<string, string> = {
  BUY: 'border-transparent bg-[color:var(--buy-solid)] text-white',
  SELL: 'border-transparent bg-[color:var(--sell-solid)] text-white',
}
export const BUY_INK = 'text-[color:var(--buy-solid)]'
export const SELL_INK = 'text-[color:var(--sell-solid)]'
export const BUY_OUTLINE =
  'border-[color:var(--buy-solid)] text-[color:var(--buy-solid)] hover:bg-accent'
export const SELL_OUTLINE =
  'border-[color:var(--sell-solid)] text-[color:var(--sell-solid)] hover:bg-accent'

/** A destructive action's outline, and the dot that says a filter is on. */
export const DANGER_OUTLINE = 'border-destructive/50 text-destructive hover:bg-destructive/10'
export const FILTER_DOT = 'bg-[color:var(--brand)]'

/** A caution: gold tint, the readable warning ink. */
export const WARNING_BOX = 'border-[color:var(--gold)] bg-[color:var(--gold-tint)]'
export const WARNING_INK = 'text-[color:var(--warning-text)]'

/** An exchange or a product: one quiet chip, its word is the meaning. */
export const QUIET_CHIP =
  'border-transparent bg-[color:var(--surface-2)] text-[color:var(--text-secondary)]'

/** A GTT's status: a tint and the same hue's ink, with its word. */
const TINTS = {
  up: 'border-transparent bg-[color:var(--up-tint)] text-[color:var(--up)]',
  down: 'border-transparent bg-[color:var(--down-tint)] text-[color:var(--down)]',
  working: 'border-transparent bg-[color:var(--gold-tint)] text-[color:var(--warning-text)]',
  info: 'border-transparent bg-[color:var(--brand-tint)] text-[color:var(--brand)]',
  muted: 'border-transparent bg-[color:var(--surface-2)] text-[color:var(--neutral)]',
} as const
export const GTT_STATUS: Record<string, string> = {
  active: TINTS.info,
  triggered: TINTS.up,
  rejected: TINTS.down,
  disabled: TINTS.working,
  cancelled: TINTS.muted,
  expired: TINTS.muted,
  deleted: TINTS.muted,
}
export const GTT_STATUS_OTHER = TINTS.muted
