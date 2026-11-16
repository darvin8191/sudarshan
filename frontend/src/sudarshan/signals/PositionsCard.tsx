// SUDARSHAN M13.9 (P8 U6): "Your positions" against TRINETRA's regime, owner only, on /trinetra.
// Worked out in the browser from the position book the app already reads (OpenAlgo's own position
// book, read again by the books' rules: after a fill, on a reconnect, on coming back, every 45 s
// while F&O is open), so nothing about his positions is sent anywhere. Per index: where he stands
// (stance.ts), the regime's bias beside it, and "With the regime" or "Against the regime", each
// with its icon. Direction only: sizes are not weighed.
import {
  CircleCheck,
  CircleHelp,
  CircleMinus,
  LoaderCircle,
  Scale,
  Shuffle,
  TriangleAlert,
  Wallet,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { tradingApi } from '@/api/trading'
import { onModeChange } from '@/stores/themeStore'
import { AccessNotice } from '@/sudarshan/AccessNotice'
import { usePriceAccess } from '@/sudarshan/access'
import { useBookSync, useLatestRead } from '@/sudarshan/books/useBookSync'
import { useMarketOpen } from '@/sudarshan/dashboard/hooks'
import type { MarketOpen } from '@/sudarshan/dashboard/live'
import {
  CALENDAR_LOADING,
  type MarketCalendar,
  MarketCalendarSource,
  useIstDay,
} from '@/sudarshan/dashboard/MarketCalendar'
import type { Position } from '@/types/trading'
import { BiasMark } from './BiasMark'
import {
  directionOf,
  directionWords,
  legWords,
  STANCE_WORDS,
  type Stance,
  stanceOf,
} from './stance'
import { INSTRUMENTS, type Instrument, type State } from './types'
import { lapsedAt } from './useSignals'
import { confidenceWords, type FreshNow } from './words'

const STANCE_ICON: Record<Stance, typeof CircleCheck> = {
  with: CircleCheck,
  against: TriangleAlert,
  mixed: Shuffle,
  neutral: Scale,
  none: CircleMinus,
  unknown: CircleHelp,
}

const STANCE_TONE: Record<Stance, string> = {
  with: 'text-[color:var(--text-primary)]',
  against: 'text-[color:var(--warning-text)]',
  mixed: 'text-[color:var(--text-primary)]',
  neutral: 'text-[color:var(--text-primary)]',
  none: 'text-[color:var(--text-secondary)]',
  unknown: 'text-[color:var(--text-secondary)]',
}

/** The position book, read by the books' rules; null until the first answer. */
export function usePositionBook(
  apiKey: string | null,
  marketOpen: MarketOpen
): { rows: Position[] | null; failed: boolean } {
  const [rows, setRows] = useState<Position[] | null>(null)
  const [failed, setFailed] = useState(false)
  const begin = useLatestRead()
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  const load = useCallback(async () => {
    if (!apiKey) return
    const latest = begin()
    try {
      const response = await tradingApi.getPositions(apiKey)
      if (!alive.current || !latest()) return
      if (response.status === 'success' && Array.isArray(response.data)) {
        setRows(response.data)
        setFailed(false)
      } else {
        setFailed(true)
      }
    } catch {
      if (alive.current && latest()) setFailed(true)
    }
  }, [apiKey, begin])

  // The first read, and again for a mode change: Live and Analyze hold different books.
  useEffect(() => {
    if (!apiKey) {
      setRows(null)
      return
    }
    void load()
    return onModeChange(() => void load())
  }, [apiKey, load])

  const open = useCallback(() => marketOpen('NFO'), [marketOpen])
  useBookSync(load, { enabled: apiKey !== null, marketOpen: open })
  return { rows, failed }
}

function IndexRow({
  instrument,
  rows,
  state,
  fresh,
  now,
}: {
  instrument: Instrument
  rows: Position[]
  state: State
  fresh: FreshNow
  now: number
}) {
  const reading = state.readings.find(
    (each) =>
      each.module === 'regime' &&
      each.instrument === instrument &&
      each.quality !== 'no_data' &&
      !lapsedAt(each, now)
  )
  const { direction, legs } = directionOf(rows, instrument)
  const stance = stanceOf(direction, reading?.bias ?? null)
  const Icon = STANCE_ICON[stance]
  const figure = reading ? confidenceWords(reading.confidence) : null
  const legsSaid = legWords(legs)
  return (
    <li data-instrument={instrument} data-stance={stance} className="flex flex-col gap-1 py-2.5">
      <p className="text-[0.9375rem] font-semibold">
        {directionWords(direction, instrument)}
        {legsSaid && (
          <span className="font-medium text-[color:var(--text-secondary)]"> ({legsSaid})</span>
        )}
      </p>
      {reading ? (
        <p className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-[0.875rem]">
          <span>The regime reads</span>
          <BiasMark bias={reading.bias} colour={fresh.colour} />
          <span className="text-[color:var(--text-secondary)]">
            ({reading.label}
            {figure !== null ? `, confidence ${figure}` : ''})
          </span>
        </p>
      ) : (
        <p className="text-[0.875rem] text-[color:var(--text-secondary)]">
          TRINETRA has no current regime reading for {instrument}.
        </p>
      )}
      <p className={`flex items-center gap-1.5 text-[0.9375rem] font-bold ${STANCE_TONE[stance]}`}>
        <Icon aria-hidden="true" className="size-4 shrink-0" />
        {STANCE_WORDS[stance]}
      </p>
      {reading && fresh.status !== 'fresh' && fresh.age !== null && (
        <p className="text-[0.8125rem] font-medium text-[color:var(--warning-text)]">
          Compared with TRINETRA's last reading, {fresh.age} old.
        </p>
      )}
    </li>
  )
}

export function PositionsCard({
  state,
  fresh,
  now,
}: {
  state: State
  fresh: FreshNow
  now: number
}) {
  const access = usePriceAccess()
  const day = useIstDay()
  const [market, setMarket] = useState<MarketCalendar>(CALENDAR_LOADING)
  const { isOpen } = useMarketOpen(market)
  const book = usePositionBook(access.apiKey, isOpen)
  return (
    <section
      aria-labelledby="sd-trinetra-positions"
      data-testid="trinetra-positions"
      className="flex flex-col gap-1 rounded-[1.125rem] border bg-card px-4 pt-3 pb-2 shadow-[var(--shadow-card)]"
    >
      <MarketCalendarSource key={day} onChange={setMarket} />
      <h2 id="sd-trinetra-positions" className="flex items-center gap-2 text-[0.9375rem] font-bold">
        <Wallet aria-hidden="true" className="size-4" />
        Your positions
      </h2>
      <p className="text-[0.8125rem] text-[color:var(--text-secondary)]">
        Your open NIFTY and BANKNIFTY futures and options beside TRINETRA's regime. Direction only:
        sizes are not weighed.
      </p>
      {access.state !== 'ready' ? (
        <AccessNotice access={access} className="my-1" />
      ) : book.rows === null && !book.failed ? (
        <output className="flex min-h-12 items-center gap-2 text-[0.875rem] text-[color:var(--text-secondary)]">
          <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
          Reading your positions
        </output>
      ) : (
        <>
          {book.failed && (
            <p
              role="alert"
              className="flex items-start gap-1.5 text-[0.8125rem] font-medium text-[color:var(--warning-text)]"
            >
              <TriangleAlert aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" />
              {book.rows === null
                ? 'Your positions could not be read just now. They are read again after your next order, and within a minute while F&O is open.'
                : 'Your positions could not be read again just now; the last read is shown.'}
            </p>
          )}
          {book.rows !== null && (
            <ul className="flex flex-col divide-y divide-[color:var(--divider)]">
              {INSTRUMENTS.map((instrument) => (
                <IndexRow
                  key={instrument}
                  instrument={instrument}
                  rows={book.rows ?? []}
                  state={state}
                  fresh={fresh}
                  now={now}
                />
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  )
}
