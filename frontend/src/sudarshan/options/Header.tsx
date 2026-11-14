import { ChevronDown, RefreshCw } from 'lucide-react'
import { cn } from '@/lib/utils'
import { directionOf, formatPercent, formatPrice } from '@/sudarshan/dashboard/format'
import { LiveLine } from '@/sudarshan/dashboard/LiveLine'
import { TREND_TEXT, TrendArrow } from '@/sudarshan/dashboard/Trend'
import type { Listing } from './api'
import { clockTime } from './TimedRefresh'
import type { LiveChain } from './useChainModel'
import { expiryLabel, expirySpeech, strikeWords } from './words'

/** The far strikes' time is said once it is this old. */
export const WINGS_OLD_MS = 90_000

const dash = (
  <>
    <span aria-hidden="true">&mdash;</span>
    <span className="sr-only">not available</span>
  </>
)

const signed = (value: number, decimals = 2) =>
  `${value > 0 ? '+' : value < 0 ? '−' : ''}${formatPrice(Math.abs(value), decimals)}`

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <span className="inline-flex items-baseline gap-1 whitespace-nowrap">
      <span className="text-xs font-semibold text-[color:var(--text-secondary)]">{label}</span>
      <span className="text-sm font-bold">{children}</span>
    </span>
  )
}

/**
 * The header strip of the options screen (M11.1, O4): the underlying (a tap opens the picker), the
 * spot and its change, the future and its basis, ATM IV, PCR and max pain, one line for the
 * followed window with the far strikes' time beside it once that is old, and the expiry chips.
 */
export function Header({
  listing,
  expiry,
  live,
  now,
  onUnderlying,
  onExpiry,
  bare = false,
}: {
  listing: Listing
  expiry: string
  live: LiveChain
  now: number
  onUnderlying: () => void
  onExpiry: (expiry: string) => void
  /** Only the underlying and the expiries: nothing that would read as a current figure. */
  bare?: boolean
}) {
  const { model, spot, future } = live
  const direction = spot.change === null ? 'flat' : directionOf(spot.change)
  const wingsAt = model?.wingsAsOf ?? null
  const wingsOld = wingsAt !== null && now - wingsAt > WINGS_OLD_MS
  return (
    <header className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={onUnderlying}
          aria-haspopup="dialog"
          className="-ml-1 inline-flex min-h-12 items-center gap-1 rounded-[0.75rem] px-1 outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring"
        >
          <h1 className="sd-display text-[1.75rem] leading-none font-bold">{listing.underlying}</h1>
          <ChevronDown aria-hidden="true" className="size-5 text-[color:var(--text-secondary)]" />
          <span className="sr-only">Change the underlying</span>
        </button>
        {!bare && (
          <button
            type="button"
            onClick={live.refresh}
            className="inline-flex size-12 items-center justify-center rounded-[0.75rem] text-[color:var(--text-secondary)] outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring"
          >
            <RefreshCw aria-hidden="true" className="size-5" />
            <span className="sr-only">Read the option chain again</span>
          </button>
        )}
      </div>

      {!bare && (
        <>
          <p data-spot="true" className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
            <span className="sd-display text-[1.5rem] leading-none font-bold">
              {spot.ltp === null ? dash : formatPrice(spot.ltp)}
            </span>
            {spot.changePct !== null && (
              <span
                className={cn(
                  'inline-flex items-center gap-1 text-sm font-bold',
                  TREND_TEXT[direction]
                )}
              >
                <TrendArrow direction={direction} className="size-2.5" />
                {formatPercent(spot.changePct)}
              </span>
            )}
            {future && (
              <span
                data-future="true"
                className="text-sm font-semibold text-[color:var(--text-secondary)]"
              >
                Fut {future.ltp === null ? dash : formatPrice(future.ltp)}
                {future.basis !== null && ` (${signed(future.basis)})`}
              </span>
            )}
          </p>

          <p data-chain-facts="true" className="flex flex-wrap gap-x-4 gap-y-0.5">
            <Fact label="ATM IV">{model?.atmIv != null ? model.atmIv.toFixed(1) : dash}</Fact>
            <Fact label="PCR">{model?.pcr != null ? model.pcr.toFixed(2) : dash}</Fact>
            <Fact label="Max pain">
              {model?.maxPain != null ? strikeWords(model.maxPain) : dash}
            </Fact>
          </p>

          <p className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
            <LiveLine line={live.line} now={now} />
            {wingsOld && wingsAt !== null && (
              <span data-wings="true" className="text-xs font-semibold text-muted-foreground">
                · far strikes as of {clockTime(wingsAt)}
              </span>
            )}
          </p>
        </>
      )}

      <ul aria-label="Expiry" className="flex gap-2 overflow-x-auto pb-1">
        {listing.expiries.map((each) => {
          const chosen = each.date === expiry
          return (
            <li key={each.date} className="shrink-0">
              <button
                type="button"
                aria-pressed={chosen}
                aria-label={expirySpeech(each.date, now)}
                onClick={() => onExpiry(each.date)}
                className={cn(
                  'inline-flex min-h-12 items-center rounded-full border px-4 text-sm font-semibold whitespace-nowrap outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  chosen
                    ? 'border-[color:var(--brand)] bg-[color:var(--primary-tint)] font-bold text-primary underline underline-offset-4'
                    : 'border-input bg-card text-foreground hover:bg-accent'
                )}
              >
                {expiryLabel(each.date, now)}
              </button>
            </li>
          )
        })}
      </ul>
    </header>
  )
}
