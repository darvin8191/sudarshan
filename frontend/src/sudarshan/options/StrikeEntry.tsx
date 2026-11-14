/**
 * The strike sheet of a chain row (M11.1, O5): the existing sheet (`watchlist/StrikeSheet.tsx`), as
 * it is, opened on the strike that was tapped. Buy and Sell on a leg open the app's own order
 * ticket with the ticket's product rule: this file holds no order code and is a new entry point,
 * not an order-path change.
 *
 * A chain row is not a watchlist row, so the sheet is asked for what fits (its four optional props):
 * no "Edit row" or "Remove row"; the side that was tapped first; a button a leg to that leg's own
 * sheet (`LegSheets.tsx`); and the same strike's other expiries, which come from the watchlists' own
 * route. That route answers only while the watchlists switch is on: with it off the section is left
 * out, and any other failed read says so with Try again. Its link to the option chain is left out:
 * this is the chain.
 */
import { useEffect } from 'react'
import type { QuotesData } from '@/api/trading'
import type { SymbolData } from '@/lib/MarketDataManager'
import type { FeedState } from '@/sudarshan/live/useFrameLines'
import { rowStale, rowView, type Snapshot, type TickDirection } from '@/sudarshan/watchlist/rows'
import { type Legs, type OtherRow, StrikeSheet } from '@/sudarshan/watchlist/StrikeSheet'
import type { OrderRequest } from '@/sudarshan/watchlist/SymbolSheet'
import type { LiveRow } from '@/sudarshan/watchlist/useLiveRows'
import { useSameStrike } from '@/sudarshan/watchlist/useSameStrike'
import type { Contract } from '@/sudarshan/watchlist/v2/types'
import type { Listing } from './api'
import type { ChainModel, LegView } from './model'
import { expiryLabel, strikeWords } from './words'

export const contractOf = (leg: LegView, expiry: string): Contract => ({
  exchange: leg.exchange,
  symbol: leg.symbol,
  key: leg.key,
  name: leg.symbol,
  lot: leg.lot,
  tick: leg.tick,
  expiry,
  isin: null,
})

export interface Feed {
  lines: ReadonlyMap<string, SymbolData>
  directions: ReadonlyMap<string, TickDirection>
  feed: FeedState
  now: number
  marketOpen: boolean | null
}

/** One instrument as the sheet's figures read it: the chain's own figure until its tick comes. */
export function liveRow(contract: Contract, seed: LegView | null, at: number, feed: Feed): LiveRow {
  const line = feed.lines.get(contract.key)
  const snapshot: Snapshot | undefined =
    seed?.ltp != null
      ? { quote: { ltp: seed.ltp, prev_close: seed.close ?? 0 } as QuotesData, at }
      : undefined
  const view = rowView(snapshot, line)
  return {
    item: { id: 0, symbol: contract.symbol, exchange: contract.exchange, position: 0 },
    key: contract.key,
    view,
    direction: feed.directions.get(contract.key) ?? null,
    stale: rowStale(view, feed.now, feed.marketOpen, feed.feed.connected),
    tickAt: line?.updateSource === 'websocket' ? (line.lastUpdate ?? null) : null,
  }
}

export function StrikeEntry({
  listing,
  expiry,
  model,
  asOf,
  strike,
  side,
  feed,
  onClose,
  onOrder,
  onLeg,
  onFollow,
}: {
  listing: Listing
  expiry: string
  model: ChainModel
  /** When the chain's own figures were read. */
  asOf: number
  strike: number
  /** The side that was tapped: it leads the sheet. */
  side: 'ce' | 'pe'
  feed: Feed
  onClose: () => void
  onOrder: (order: OrderRequest) => void
  /** Open a leg's own sheet. */
  onLeg: (side: 'ce' | 'pe') => void
  /** What the sheet needs followed while it is open (EXCHANGE:SYMBOL): its legs and the other expiries'. */
  onFollow: (keys: string[]) => void
}) {
  const row = model.rows.find((each) => each.strike === strike)
  const same = useSameStrike(
    row
      ? { underlying: listing.underlying, exchange: listing.spot.exchange, strike, after: expiry }
      : null
  )
  const wanted = [
    row?.ce?.key,
    row?.pe?.key,
    ...same.rows.flatMap((each) => [each.ce?.key, each.pe?.key]),
  ]
    .filter((key): key is string => key !== undefined)
    .join(',')
  useEffect(() => {
    onFollow(wanted === '' ? [] : wanted.split(','))
    return () => onFollow([])
  }, [wanted, onFollow])
  if (!row) return null
  const legs: Legs | null =
    row.ce && row.pe
      ? {
          ce: {
            contract: contractOf(row.ce, expiry),
            live: liveRow(contractOf(row.ce, expiry), row.ce, asOf, feed),
          },
          pe: {
            contract: contractOf(row.pe, expiry),
            live: liveRow(contractOf(row.pe, expiry), row.pe, asOf, feed),
          },
        }
      : null
  const others: OtherRow[] = same.rows.map((each) => ({
    expiry: each.expiry,
    ce: each.ce && feed.lines.has(each.ce.key) ? liveRow(each.ce, null, asOf, feed) : null,
    pe: each.pe && feed.lines.has(each.pe.key) ? liveRow(each.pe, null, asOf, feed) : null,
  }))
  const steps =
    model.atm === null ? null : model.rows.indexOf(row) - model.rows.findIndex((each) => each.atm)
  const chip =
    steps === null
      ? 'Strike'
      : steps === 0
        ? 'ATM'
        : `ATM ${steps > 0 ? '+' : '−'}${Math.abs(steps)}`
  return (
    <StrikeSheet
      title={`${listing.underlying} ${strikeWords(strike)}`}
      detail={`${listing.exchange} · ${expiryLabel(expiry, feed.now)}`}
      chip={chip}
      help={row.live ? null : 'A far strike: its prices are followed while this sheet is open.'}
      words={legs ? null : 'This strike lists one side only, so there is no pair to show.'}
      moved={null}
      strike={strike}
      expiry={expiry}
      legs={legs}
      lines={feed.lines}
      others={same.off ? undefined : { state: same.state, rows: others, retry: same.retry }}
      chain={null}
      first={side}
      onLeg={onLeg}
      feed={feed.feed}
      now={feed.now}
      marketOpen={feed.marketOpen}
      onClose={onClose}
      onOrder={onOrder}
    />
  )
}
