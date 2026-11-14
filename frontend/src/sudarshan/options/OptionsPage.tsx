/**
 * `/options`: the option chain on the phone (M11.1, O4). One chain model, read by every tab.
 *
 * The address is `/options/<NFO|BFO>/<underlying>?expiry=YYYY-MM-DD&strike=N` (`words.optionsPath`;
 * the underlying's own exchange is taken too, so another screen can link with what it has). A bare
 * `/options` opens the underlying and expiry last used on this phone, NIFTY's nearest the first
 * time. Prices, open interest, IV and the Greeks move with ticks for the followed window; the far
 * strikes are the snapshot's and say their time.
 *
 * No order code here: the tabs open the existing strike sheet and, from it, the existing ticket.
 */
import { useQuery } from '@tanstack/react-query'
import { Unplug } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { Link, Navigate, useNavigate, useParams, useSearchParams } from 'react-router'
import { PlaceOrderDialog } from '@/components/trading/PlaceOrderDialog'
import { cn } from '@/lib/utils'
import { AccessNotice } from '@/sudarshan/AccessNotice'
import { usePriceAccess } from '@/sudarshan/access'
import { useMarketOpen, useNow } from '@/sudarshan/dashboard/hooks'
import {
  CALENDAR_LOADING,
  type MarketCalendar,
  MarketCalendarSource,
  useIstDay,
} from '@/sudarshan/dashboard/MarketCalendar'
import { readListing } from './api'
import { ChainTab } from './ChainTab'
import { Header } from './Header'
import { OiTab } from './OiTab'
import { readLastChain, saveLastChain } from './prefs'
import { StrikeEntry } from './StrikeEntry'
import { useSheets } from './sheets'
import { clockTime } from './TimedRefresh'
import { UnderlyingSheet } from './UnderlyingSheet'
import { useChainModel } from './useChainModel'
import { foExchange, optionsPath } from './words'

const ACTION =
  'inline-flex min-h-12 items-center justify-center rounded-[0.875rem] bg-primary px-6 text-base font-semibold text-primary-foreground outline-none hover:bg-primary/90 focus-visible:ring-[3px] focus-visible:ring-ring/50'
const QUIET =
  'inline-flex min-h-12 items-center rounded-sm text-sm font-semibold text-[color:var(--brand)] underline underline-offset-2 outline-none focus-visible:ring-2 focus-visible:ring-ring'
const NOTE = 'text-sm font-semibold text-[color:var(--text-secondary)]'

type Tab = 'chain' | 'oi'
const TABS: Array<{ tab: Tab; label: string }> = [
  { tab: 'chain', label: 'Chain' },
  { tab: 'oi', label: 'OI' },
]
const tabOf = (text: string | null): Tab => TABS.find((each) => each.tab === text)?.tab ?? 'chain'

export const LOGIN_NEEDED = "Today's Upstox login is needed to read the option chain."
const WIDE_CHAIN = 'Open the wide option chain'

/** The card that stands in for the chain while today's Upstox login is missing. */
function LoginNeeded({ words }: { words: string }) {
  return (
    <section
      data-options-state="signin"
      className="flex flex-col items-start gap-2 rounded-[1.125rem] border border-[color:var(--gold)] bg-card px-4 py-3.5 shadow-[var(--shadow-card)]"
    >
      <h2 className="flex items-start gap-2.5 text-[0.9375rem] font-bold">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-[0.625rem] bg-[color:var(--gold-tint)] text-[color:var(--gold-ink)]">
          <Unplug aria-hidden="true" className="size-[1.125rem]" />
        </span>
        <span className="pt-1">{words}</span>
      </h2>
      <Link to="/broker" className={ACTION}>
        Reconnect
      </Link>
    </section>
  )
}

export default function OptionsPage() {
  const params = useParams<{ exchange: string; underlying: string }>()
  const [query, setQuery] = useSearchParams()
  const navigate = useNavigate()
  const exchange = foExchange(params.exchange)
  const underlying = (params.underlying ?? '').toUpperCase()
  const named = exchange !== null && /^[A-Z0-9&_-]{1,40}$/.test(underlying)

  const access = usePriceAccess()
  const [market, setMarket] = useState<MarketCalendar>(CALENDAR_LOADING)
  const day = useIstDay()
  const { isOpen } = useMarketOpen(market)
  const now = useNow()
  const sheets = useSheets()
  const picking = sheets.sheet?.sheet === 'underlying'
  /** What the open strike sheet needs followed, beside the window. */
  const [sheetFollows, setSheetFollows] = useState<string[]>([])

  // The expiries: read when the chain is named and again on a new day in India.
  const read = useQuery({
    queryKey: ['sd-options-listing', exchange, underlying, day],
    queryFn: ({ signal }) => readListing(exchange ?? '', underlying, signal),
    enabled: named && exchange !== 'MCX',
    staleTime: 60_000,
    retry: false,
  })
  const listing = read.data?.listing ?? null
  const asked = query.get('expiry')
  const [remembered] = useState(() => readLastChain())
  const dates = listing?.expiries.map((each) => each.date) ?? []
  const expiry =
    (asked && dates.includes(asked) ? asked : null) ??
    (remembered.underlying === underlying && remembered.expiry && dates.includes(remembered.expiry)
      ? remembered.expiry
      : null) ??
    dates[0] ??
    null

  useEffect(() => {
    if (listing && expiry && (listing.exchange === 'NFO' || listing.exchange === 'BFO'))
      saveLastChain({ exchange: listing.exchange, underlying: listing.underlying, expiry })
  }, [listing, expiry])

  const ready = access.state === 'ready'
  const live = useChainModel({
    listing,
    expiry,
    apiKey: access.apiKey,
    marketOpen: listing ? isOpen(listing.exchange) : null,
    now,
    sheet: sheetFollows,
    enabled: ready,
  })
  const openStrike = useCallback(
    (strike: number, side: 'ce' | 'pe') => sheets.open({ sheet: 'strike', strike, side }),
    [sheets.open]
  )

  if (!named) {
    const last = readLastChain()
    return (
      <Navigate replace to={optionsPath(last.exchange, last.underlying, { expiry: last.expiry })} />
    )
  }

  // The picker's own history entry is replaced by the chain picked, so Back leaves the page.
  const pick = (to: 'NFO' | 'BFO', name: string) =>
    navigate(optionsPath(to, name), { replace: true })
  const pickUnderlying = () => sheets.open({ sheet: 'underlying' })
  const sheet = picking && (
    <UnderlyingSheet current={underlying} onPick={pick} onClose={sheets.close} />
  )
  const other = (
    <button type="button" onClick={pickUnderlying} className={QUIET}>
      Pick another underlying
    </button>
  )
  const calendar = <MarketCalendarSource key={day} onChange={setMarket} />

  if (!listing || !expiry) {
    const state =
      exchange === 'MCX' ? 'none' : (read.data?.state ?? (read.isError ? 'unavailable' : 'reading'))
    const words =
      exchange === 'MCX'
        ? 'Upstox has no option chain for MCX. Use the wide option chain for it.'
        : (read.data?.words ??
          (state === 'reading'
            ? 'Reading the expiries.'
            : 'The expiries could not be read. Try again in a moment.'))
    return (
      <div data-options-page="true" className="flex flex-col gap-3">
        {calendar}
        <h1 className="sd-display text-[1.75rem] leading-none font-bold">{underlying} options</h1>
        <div data-options-state={state} className="flex flex-col items-start gap-1">
          <p className={NOTE}>{words}</p>
          {state === 'none' && (
            <Link to="/optionchain" className={QUIET}>
              {WIDE_CHAIN}
            </Link>
          )}
          {state === 'unavailable' && (
            <button type="button" onClick={() => void read.refetch()} className={QUIET}>
              Try again
            </button>
          )}
          {state !== 'reading' && other}
        </div>
        {sheet}
      </div>
    )
  }

  const { status, model } = live
  const ticket = sheets.sheet?.sheet === 'ticket' ? sheets.sheet.order : null
  const dim = live.line.kind !== 'live' && live.line.kind !== 'closed' && !live.feed.connected
  const tab = tabOf(query.get('tab'))
  const strikeAsked = Number(query.get('strike'))
  const startAt = query.get('strike') && Number.isFinite(strikeAsked) ? strikeAsked : null
  const pickTab = (next: Tab) => {
    const params = new URLSearchParams(query)
    if (next === 'chain') params.delete('tab')
    else params.set('tab', next)
    setQuery(params, { replace: true })
  }
  const loggedOut = access.state === 'upstox-logged-out' || status.state === 'signin'
  const heldAt = model ? clockTime(model.asOf) : null
  return (
    <div data-options-page="true" className="flex flex-col gap-3">
      {calendar}
      <Header
        listing={listing}
        expiry={expiry}
        live={live}
        now={now}
        bare={!ready || loggedOut}
        onUnderlying={pickUnderlying}
        onExpiry={(next) => {
          const params = new URLSearchParams(query)
          params.set('expiry', next)
          setQuery(params, { replace: true })
        }}
      />
      {loggedOut ? (
        <LoginNeeded
          words={status.state === 'signin' && status.words ? status.words : LOGIN_NEEDED}
        />
      ) : !ready ? (
        <AccessNotice access={access} />
      ) : (
        <>
          {status.state === 'reading' && !model && (
            <output data-options-state="reading" className={NOTE}>
              Reading the option chain.
            </output>
          )}
          {status.state !== 'ok' && status.state !== 'reading' && (
            <div data-options-state={status.state} className="flex flex-col items-start">
              <p className={NOTE}>
                {status.words}
                {model &&
                  ` Prices keep moving; open interest is as of ${heldAt}.${status.source === 'fallback' ? ' OI change is not available.' : ''}`}
              </p>
              {status.state === 'none' ? (
                <Link to="/optionchain" className={QUIET}>
                  {WIDE_CHAIN}
                </Link>
              ) : (
                <button type="button" onClick={live.refresh} className={QUIET}>
                  Try again
                </button>
              )}
            </div>
          )}
          {model && (
            <div
              role="tablist"
              aria-label="Views of the chain"
              className="grid grid-flow-col auto-cols-fr gap-1 border-b border-[color:var(--border-subtle)]"
            >
              {TABS.map((each) => (
                <button
                  key={each.tab}
                  type="button"
                  role="tab"
                  id={`sd-options-tab-${each.tab}`}
                  aria-selected={tab === each.tab}
                  aria-controls="sd-options-panel"
                  onClick={() => pickTab(each.tab)}
                  className={cn(
                    '-mb-px min-h-12 border-b-[3px] text-[0.9375rem] font-semibold outline-none focus-visible:ring-2 focus-visible:ring-ring',
                    tab === each.tab
                      ? 'border-[color:var(--brand)] font-bold text-primary'
                      : 'border-transparent text-[color:var(--text-secondary)]'
                  )}
                >
                  {each.label}
                </button>
              ))}
            </div>
          )}
          {model && (
            <div role="tabpanel" id="sd-options-panel" aria-labelledby={`sd-options-tab-${tab}`}>
              {tab === 'oi' && <OiTab model={model} dim={dim} />}
              {tab === 'chain' && (
                <ChainTab
                  model={model}
                  chainKey={`${listing.exchange}:${listing.underlying}:${expiry}`}
                  directions={live.directions}
                  dim={dim}
                  startAt={startAt}
                  onOpen={openStrike}
                />
              )}
            </div>
          )}
          {model && sheets.sheet?.sheet === 'strike' && (
            <StrikeEntry
              listing={listing}
              expiry={expiry}
              model={model}
              asOf={model.asOf}
              strike={sheets.sheet.strike}
              feed={{
                lines: live.lines,
                directions: live.directions,
                feed: live.feed,
                now,
                marketOpen: isOpen(listing.exchange),
              }}
              onClose={sheets.close}
              onOrder={(order) => sheets.open({ sheet: 'ticket', order })}
              onFollow={setSheetFollows}
            />
          )}
        </>
      )}
      {/* The app's own order ticket, opened by the strike sheet's Buy and Sell: no order code here. */}
      <PlaceOrderDialog
        open={ticket !== null}
        onOpenChange={(next) => {
          if (!next) sheets.close()
        }}
        symbol={ticket?.item.symbol ?? ''}
        exchange={ticket?.item.exchange ?? ''}
        action={ticket?.action ?? 'BUY'}
        lotSize={ticket?.contract.lotSize ?? 1}
        tickSize={ticket?.contract.tickSize ?? 0.05}
        product={ticket?.product}
        strategy="Options"
      />
      {sheet}
    </div>
  )
}
