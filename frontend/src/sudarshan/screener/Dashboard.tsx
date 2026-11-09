import { Plus, RefreshCw } from 'lucide-react'
import { useCallback, useMemo, useRef, useState } from 'react'
import type { WatchlistItem } from '@/api/watchlist'
import { cn } from '@/lib/utils'
import { AccessNotice } from '@/sudarshan/AccessNotice'
import { usePriceAccess } from '@/sudarshan/access'
import {
  Confirm,
  EmptyCard,
  LoadingCards,
  OUTLINED,
  STALE,
  StaleBookNote,
} from '@/sudarshan/books/cards/parts'
import { useMarketOpen } from '@/sudarshan/dashboard/hooks'
import { anyOpen, keyOf, latest } from '@/sudarshan/dashboard/live'
import {
  CALENDAR_LOADING,
  type MarketCalendar,
  MarketCalendarSource,
  useIstDay,
} from '@/sudarshan/dashboard/MarketCalendar'
import { statusLine } from '@/sudarshan/watchlist/rows'
import { type LiveRow, useLiveRows } from '@/sudarshan/watchlist/useLiveRows'
import { AddWidgetSheet } from './AddWidgetSheet'
import { CountCard } from './CountCard'
import { type CardFeed, cardLine } from './cardLine'
import { cardKey } from './dashboardCache'
import { useLayoutWrites } from './dashboardWrites'
import type { Hearing } from './push'
import { RowSheets } from './RowSheets'
import { followedItems } from './resultItems'
import { type WidgetCard as Card, type ResultRow, type ScreenerState, WORDS } from './types'
import { useDashboard } from './useDashboard'
import type { Address } from './useScreenerAddress'
import { WidgetCard } from './WidgetCard'
import { WidgetMenu } from './WidgetMenu'
import { WidgetSheet } from './WidgetSheet'

/** The sentences the dashboard says when it has nothing to show (design 3.5). */
export const DASH_WORDS = {
  empty_title: 'Nothing on the dashboard yet',
  empty_hint:
    'Add a widget for any screen: its matches, with prices that tick, here at the top of the Screener.',
  last_read: 'These are your widgets as last read.',
} as const

/** One column on a phone, two from 768 px, three from 1,200 px. */
export const GRID = 'grid grid-cols-1 items-start gap-2 md:grid-cols-2 min-[1200px]:grid-cols-3'

/** The instruments the dashboard follows tick by tick: the open row's too, whichever card it is on. */
function itemsOf(followed: WatchlistItem[], shown: ResultRow[], openKey: string | null) {
  if (openKey === null || followed.some((item) => keyOf(item.symbol, item.exchange) === openKey))
    return followed
  const row = shown.find((each) => keyOf(each.symbol, each.exchange) === openKey)
  if (!row) return followed
  return [
    ...followed,
    {
      id: followed.length + 1,
      symbol: row.symbol,
      exchange: row.exchange,
      position: followed.length,
    },
  ]
}

const NO_CARDS: Card[] = []

/**
 * The Dashboard tab (design 3.1, 3.6, 3.7): the Add a widget button and the owner's widgets, one
 * card each, in the order the screener keeps them. A layout that is only a suggestion (version 0)
 * is shown like any other. One live subscription serves every card: the shown rows of all of them
 * in card order, at most 100 prices, so a card says "Real-time" only while its own rows tick. A
 * failed read says so and keeps the cards as last read, greyed.
 */
export function Dashboard({
  address,
  state,
  hearing,
  openScreen,
  openSheet,
  closeSheet,
}: {
  address: Address
  state: ScreenerState
  hearing: Hearing
  openScreen: (id: number) => void
  openSheet: (sheet: string, extra?: Record<string, string>) => void
  closeSheet: () => void
}) {
  const { query, failed, retry } = useDashboard()
  const data = query.data
  const problem = query.error
  const cards = data?.widgets ?? NO_CARDS
  const layout = useLayoutWrites(data)
  const [removing, setRemoving] = useState<Card | null>(null)

  const access = usePriceAccess()
  const apiKey = access.apiKey
  const [market, setMarket] = useState<MarketCalendar>(CALENDAR_LOADING)
  const day = useIstDay()
  const { isOpen } = useMarketOpen(market)
  const openKey = address.from ? address.key : null
  const shown = useMemo(
    () => cards.flatMap((card) => (card.kind === 'table' ? card.rows_shown : [])),
    [cards]
  )
  const followed = useMemo(() => followedItems(shown), [shown])
  const capped = useMemo(
    () => new Set(followed.map((item) => keyOf(item.symbol, item.exchange))),
    [followed]
  )
  const items = useMemo(() => itemsOf(followed, shown, openKey), [followed, shown, openKey])
  const live = useLiveRows({ items, apiKey, marketOpen: isOpen })
  const liveByKey = useMemo(() => new Map(live.rows.map((row) => [row.key, row])), [live.rows])
  const screens = useMemo(() => new Map(state.screens.map((row) => [row.id, row])), [state.screens])

  /** Where this card's own rows stand: the line over the followed ones alone. */
  const feedOf = (card: Card): CardFeed => {
    const own = card.rows_shown
      .map((row) => liveByKey.get(keyOf(row.symbol, row.exchange)))
      .filter((row): row is LiveRow => row !== undefined)
      .filter((row) => capped.has(row.key))
    if (own.length === 0) return { followed: 0, line: null, now: live.now }
    return {
      followed: own.length,
      now: live.now,
      line: statusLine({
        now: live.now,
        marketOpen: anyOpen(own.map((row) => isOpen(row.item.exchange))),
        lastTickAt: latest(...own.map((row) => row.tickAt)),
        lastUpdateAt: latest(...own.map((row) => row.view.at)),
        connected: live.feed.connected,
        fallback: live.feed.fallback,
      }),
    }
  }
  const named = (key: typeof address.widget) =>
    key ? cards.find((card) => card.screen_id === key.screen && card.kind === key.kind) : undefined
  const menu = address.sheet === 'menu' ? named(address.widget) : undefined
  const settings = address.sheet === 'widget' ? named(address.widget) : undefined
  const nameOf = (card: Card) => screens.get(card.screen_id)?.name ?? card.name
  const place = (card: Card | undefined) => (card ? cards.indexOf(card) : -1)
  // The callbacks a card is given never change identity, so a tick frame redraws no card for them.
  const latestRef = useRef({ openSheet, openScreen })
  latestRef.current = { openSheet, openScreen }
  const options = useCallback(
    (card: Card) =>
      latestRef.current.openSheet('menu', { s: String(card.screen_id), k: card.kind }),
    []
  )
  const openCard = useCallback((id: number) => latestRef.current.openScreen(id), [])
  const openRow = useCallback(
    (from: Card, key: string) =>
      latestRef.current.openSheet('symbol', { k: key, s: String(from.screen_id), wk: from.kind }),
    []
  )
  const opened = address.from
    ? cards.find(
        (card) => card.screen_id === address.from?.screen && card.kind === address.from.kind
      )
    : undefined

  return (
    <div className="flex flex-col gap-2">
      <MarketCalendarSource key={day} onChange={setMarket} />
      <div className="flex items-center justify-between gap-3 px-1">
        <button
          type="button"
          onClick={() => openSheet('add')}
          className="inline-flex min-h-12 items-center gap-1.5 rounded-[0.875rem] bg-primary px-4 text-[0.9375rem] font-semibold text-primary-foreground outline-none hover:bg-primary/90 focus-visible:ring-[3px] focus-visible:ring-ring/50"
        >
          <Plus aria-hidden="true" className="size-5" />
          Add a widget
        </button>
        {problem && (
          <button
            type="button"
            disabled={query.isFetching}
            onClick={() => {
              retry()
              void query.refetch()
            }}
            className={OUTLINED}
          >
            <RefreshCw
              aria-hidden="true"
              className={cn('size-4', query.isFetching && 'animate-spin')}
            />
            Try again
          </button>
        )}
      </div>
      {problem && (
        <p role="alert" className="px-1 text-[0.8125rem] font-semibold">
          {problem.code === 'unanswered' || problem.code === 'failed'
            ? WORDS.not_answering
            : problem.message}
        </p>
      )}
      <AccessNotice access={access} />
      {problem && data && <StaleBookNote text={DASH_WORDS.last_read} />}
      {!data ? (
        query.isPending ? (
          <LoadingCards />
        ) : null
      ) : data.widgets.length === 0 ? (
        <EmptyCard title={DASH_WORDS.empty_title} hint={DASH_WORDS.empty_hint} />
      ) : (
        <ul className={cn(GRID, problem && STALE)}>
          {cards.map((card) => {
            const screen = screens.get(card.screen_id)
            const own = {
              card,
              name: screen?.name ?? card.name,
              where: screen?.where ?? card.where,
              status: screen?.status ?? card.status,
              line: cardLine({
                card,
                screen,
                engine: state.engine,
                hearing,
                feed: feedOf(card),
                access: access.state,
              }),
              onOpenScreen: openCard,
              onOptions: options,
            }
            if (card.kind === 'count') return <CountCard key={cardKey(card)} {...own} />
            return (
              <WidgetCard
                key={cardKey(card)}
                {...own}
                liveByKey={liveByKey}
                now={live.now}
                unreadable={failed.has(cardKey(card))}
                onRetry={retry}
                onOpenRow={openRow}
              />
            )
          })}
        </ul>
      )}
      <RowSheets
        address={address}
        row={openKey ? (liveByKey.get(openKey) ?? null) : null}
        listName={opened?.list?.name ?? opened?.name ?? 'Screener'}
        apiKey={apiKey}
        isOpen={isOpen}
        feed={live.feed}
        now={live.now}
        openSheet={openSheet}
        closeSheet={closeSheet}
      />
      <AddWidgetSheet
        open={address.sheet === 'add'}
        state={state}
        cards={cards}
        version={data?.version ?? 0}
        onClose={closeSheet}
      />
      <WidgetMenu
        card={menu ?? null}
        name={menu ? nameOf(menu) : ''}
        first={place(menu) === 0}
        last={place(menu) === cards.length - 1}
        busy={layout.pending}
        onSettings={(card) => openSheet('widget', { s: String(card.screen_id), k: card.kind })}
        onMove={(card, by) => void layout.move(card, by).then((ok) => ok && closeSheet())}
        onOpenScreen={openScreen}
        onRemove={(card) => {
          setRemoving(card)
          closeSheet()
        }}
        onClose={closeSheet}
      />
      <WidgetSheet
        card={settings ?? null}
        screen={settings ? screens.get(settings.screen_id) : undefined}
        version={data?.version ?? 0}
        onClose={closeSheet}
      />
      <Confirm
        open={removing !== null}
        title="Remove from the dashboard?"
        description={`${removing ? nameOf(removing) : ''} stays as a screen, with its list and its history. Only this widget is removed.`}
        cancelLabel="Keep it"
        confirmLabel="Remove"
        onConfirm={() => removing && void layout.remove(removing)}
        onClose={() => setRemoving(null)}
      />
    </div>
  )
}
