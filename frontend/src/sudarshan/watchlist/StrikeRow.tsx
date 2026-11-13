import { Clock } from 'lucide-react'
import { outsideNote, outsideSpeech } from './follow'
import { ATM_HELP, chipOf, dayShort, lineOf, num } from './followRows'
import { priceDecimals } from './rows'
import type { FollowState } from './useFollowRows'
import type { LiveRow } from './useLiveRows'
import { CLOCK } from './ViewSheet'
import { Figures, figuresOf } from './WatchlistRows'

const NO_FIGURES = {
  ltp: null,
  change: null,
  changePercent: null,
  direction: null,
  stale: false,
} as const

/** The words of a strike row: what the middle block says, its chip and the chip's help text. */
export function strikeNames(state: FollowState) {
  const { rule, shown } = state
  const strike = shown?.strike ?? null
  const expiry = shown?.contract.expiry ?? null
  return {
    middle:
      strike === null ? rule.underlying : `${num(strike)}${expiry ? ` · ${dayShort(expiry)}` : ''}`,
    chip: chipOf(rule),
    help: rule.strike_rule !== 'fixed' && rule.offset === 0 ? ATM_HELP : null,
  }
}

/** One side of the strike: its label, its price with the last tick's arrow and its signed change. */
function Leg({
  side,
  live,
  align,
}: {
  side: 'CE' | 'PE'
  live: LiveRow | null
  align: 'start' | 'end'
}) {
  const figures = live ? figuresOf(live) : NO_FIGURES
  return (
    <span
      data-leg={side}
      data-stale={live?.stale || undefined}
      className="flex min-w-0 flex-col gap-0.5"
    >
      <span
        className={`text-[0.6875rem] leading-none font-bold text-[color:var(--text-tertiary)] ${align === 'end' ? 'text-right' : 'text-left'}`}
      >
        {side}
      </span>
      <Figures {...figures} align={align} decimals={priceDecimals('NFO')} />
    </span>
  )
}

/**
 * One row that follows both legs of a strike (M8.11, F12): the CE's price and change on the left, the
 * strike and the expiry in the middle with the rule's chip, the PE's on the right. Each price carries
 * its own tick arrow and each change its sign, never colour alone. The one line under it is the one a
 * follow row has (a move, a roll, waiting, closed), and the server's own words when a side is not in
 * the contract list; the row then has no prices, and opens its sheet to say so again.
 */
export function StrikeRowItem({
  state,
  ce,
  pe,
  now,
  onOpen,
}: {
  state: FollowState
  ce: LiveRow | null
  pe: LiveRow | null
  now: number
  onOpen: (rowId: number) => void
}) {
  const names = strikeNames(state)
  const line = lineOf(state, now)
  const outside = ce?.outside ?? pe?.outside
  const at = ce?.view.at ?? pe?.view.at ?? null
  return (
    <li data-strike-row={state.id} data-stale={ce?.stale || pe?.stale || undefined}>
      <button
        type="button"
        aria-haspopup="dialog"
        data-outside={outside}
        onClick={() => onOpen(state.id)}
        className="flex min-h-14 w-full flex-col justify-center gap-1 px-4 py-2 text-left outline-none active:bg-accent focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
      >
        <span className="grid w-full grid-cols-[1fr_auto_1fr] items-center gap-2">
          <Leg side="CE" live={ce} align="start" />
          <span className="flex min-w-0 flex-col items-center gap-1 text-center">
            <span className="text-[0.9375rem] leading-tight font-semibold">{names.middle}</span>
            <span
              title={names.help ?? undefined}
              className="rounded-md bg-[color:var(--primary-tint)] px-1.5 py-0.5 text-[0.6875rem] leading-none font-bold text-primary"
            >
              {names.chip}
            </span>
            {names.help && <span className="sr-only">{names.help}</span>}
          </span>
          <Leg side="PE" live={pe} align="end" />
        </span>
        {line && (
          <span
            data-follow-line="true"
            className="text-xs font-medium text-[color:var(--text-secondary)]"
          >
            {line}
          </span>
        )}
        {outside && (
          <span className="flex items-start gap-1.5 text-xs font-medium text-[color:var(--stale)]">
            <Clock aria-hidden="true" className="mt-0.5 size-3 shrink-0" />
            <span>
              {at !== null ? `Price at ${CLOCK.format(at)}` : 'No price read yet'}
              {' · '}
              {outsideNote(outside)}
            </span>
            <span className="sr-only">{outsideSpeech(outside)}</span>
          </span>
        )}
      </button>
    </li>
  )
}
