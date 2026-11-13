import { Clock } from 'lucide-react'
import { outsideNote, outsideSpeech } from './follow'
import { ATM_HELP, chipOf, detailOf, lineOf, titleOf } from './followRows'
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

/** The name of a follow row: the title with its chip, and the detail under it. */
export function followNames(state: FollowState) {
  const { rule, shown } = state
  return {
    title: titleOf(rule, shown?.strike ?? null),
    chip: chipOf(rule),
    detail: detailOf(rule, shown?.contract ?? null),
    help:
      rule.kind !== 'month_future' && rule.strike_rule !== 'fixed' && rule.offset === 0
        ? ATM_HELP
        : null,
  }
}

/**
 * One row that follows the price (F11). It looks like any row (the contract's price and change, live
 * from the feed) with a chip for its rule and, under it, the one line that says what happened to it:
 * a move, a roll, waiting, closed, or the server's words for a contract it cannot find. A row with no
 * contract yet opens its form instead of a sheet.
 */
export function FollowRowItem({
  state,
  live,
  now,
  onOpen,
}: {
  state: FollowState
  live: LiveRow | null
  now: number
  onOpen: (rowId: number) => void
}) {
  const names = followNames(state)
  const line = lineOf(state, now)
  const outside = live?.outside
  const figures = live ? figuresOf(live) : NO_FIGURES
  const exchange = state.shown?.contract.exchange ?? 'NFO'
  return (
    <li data-follow-row={state.id} data-stale={live?.stale || undefined}>
      <button
        type="button"
        aria-haspopup="dialog"
        data-outside={outside}
        onClick={() => onOpen(state.id)}
        className="flex min-h-14 w-full flex-col justify-center gap-1 px-4 py-2 text-left outline-none active:bg-accent focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
      >
        <span className="flex w-full items-center gap-3">
          <span className="flex min-w-0 flex-1 flex-col gap-0.5">
            <span className="flex min-w-0 items-center gap-2">
              <span className="truncate text-[0.9375rem] leading-tight font-semibold">
                {names.title}
              </span>
              <span
                title={names.help ?? undefined}
                className="shrink-0 rounded-md bg-[color:var(--primary-tint)] px-1.5 py-0.5 text-[0.6875rem] leading-none font-bold text-primary"
              >
                {names.chip}
              </span>
              {names.help && <span className="sr-only">{names.help}</span>}
            </span>
            <span className="min-w-0 truncate text-xs leading-tight font-medium text-[color:var(--text-tertiary)]">
              {names.detail}
            </span>
          </span>
          <Figures {...figures} outside={outside} decimals={priceDecimals(exchange)} />
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
              {live?.view.at != null
                ? `Price at ${CLOCK.format(live.view.at)}`
                : 'No price read yet'}
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
