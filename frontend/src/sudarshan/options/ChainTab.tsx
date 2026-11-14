import { useEffect, useMemo, useRef, useState } from 'react'
import { cn } from '@/lib/utils'
import type { TickDirection } from '@/sudarshan/watchlist/rows'
import { ChainRow, type ChainView, rowSignature } from './ChainRow'
import type { ChainModel } from './model'
import { clockTime } from './TimedRefresh'
import { around, SHOWN_SIDE } from './window'

const VIEWS: Array<{ view: ChainView; label: string }> = [
  { view: 'price', label: 'Price' },
  { view: 'oi', label: 'OI' },
  { view: 'greeks', label: 'Greeks' },
]

const MORE =
  'inline-flex min-h-12 w-full items-center justify-center rounded-[0.875rem] border border-input bg-card text-sm font-semibold outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring'

/**
 * The Chain tab (M11.1, O5): the strikes around the money, the calls on the left and the puts on
 * the right. Ten strikes either side are drawn first; "Show more strikes" draws the rest of the
 * followed window, and then the far strikes, which are the snapshot's and grey, with their time.
 * The list opens with the at-the-money row in view and does not move under the reader after that.
 */
export function ChainTab({
  model,
  chainKey,
  directions,
  dim,
  onOpen,
}: {
  model: ChainModel
  /** Changes with the underlying or the expiry: the list starts again around the money. */
  chainKey: string
  directions: ReadonlyMap<string, TickDirection>
  /** The followed figures are not current (the socket is down, or they are stale). */
  dim: boolean
  onOpen: (strike: number, side: 'ce' | 'pe') => void
}) {
  const [view, setView] = useState<ChainView>('price')
  const [shown, setShown] = useState<{ key: string; stage: 'near' | 'window' | 'all' }>({
    key: chainKey,
    stage: 'near',
  })
  const stage = shown.key === chainKey ? shown.stage : 'near'
  const strikes = useMemo(() => model.rows.map((row) => row.strike), [model.rows])
  // The rows drawn are fixed when the chain opens: the money walking does not move the list.
  const [anchor, setAnchor] = useState<{ key: string; strike: number } | null>(null)
  const anchored = anchor !== null && anchor.key === chainKey
  const anchorStrike = anchored ? anchor.strike : model.atm
  useEffect(() => {
    if (!anchored && model.atm !== null) setAnchor({ key: chainKey, strike: model.atm })
  }, [anchored, chainKey, model.atm])
  const near = useMemo(
    () => new Set(around(strikes, anchorStrike, SHOWN_SIDE)),
    [strikes, anchorStrike]
  )
  const rows = model.rows.filter((row) =>
    stage === 'all'
      ? true
      : stage === 'window'
        ? row.live || near.has(row.strike)
        : near.has(row.strike)
  )
  const hiddenLive = model.rows.some((row) => row.live && !near.has(row.strike))
  const wings = model.rows.filter((row) => !row.live).length
  const maxOi = Math.max(0, ...rows.flatMap((row) => [row.ce?.oi ?? 0, row.pe?.oi ?? 0]))

  const list = useRef<HTMLUListElement>(null)
  // biome-ignore lint/correctness/useExhaustiveDependencies: once for each chain, when its rows are first drawn
  useEffect(() => {
    list.current?.querySelector('[data-atm]')?.scrollIntoView?.({ block: 'center' })
  }, [chainKey])

  return (
    <section aria-label="Option chain" className="flex flex-col gap-2">
      <fieldset className="grid grid-cols-3 gap-1 rounded-[0.875rem] bg-[color:var(--surface-2)] p-1">
        <legend className="sr-only">What the second line shows</legend>
        {VIEWS.map((each) => (
          <button
            key={each.view}
            type="button"
            aria-pressed={view === each.view}
            onClick={() => setView(each.view)}
            className={cn(
              'min-h-12 rounded-[0.625rem] text-sm font-semibold outline-none focus-visible:ring-2 focus-visible:ring-ring',
              view === each.view
                ? 'bg-card font-bold text-primary underline underline-offset-4 shadow-[var(--shadow-card)]'
                : 'text-[color:var(--text-secondary)]'
            )}
          >
            {each.label}
          </button>
        ))}
      </fieldset>
      <p
        aria-hidden="true"
        className="grid grid-cols-[minmax(0,1fr)_5.25rem_minmax(0,1fr)] px-2 text-xs font-bold text-[color:var(--text-tertiary)]"
      >
        <span>CALL</span>
        <span className="text-center">STRIKE</span>
        <span className="text-right">PUT</span>
      </p>
      <ul ref={list} data-chain-rows={stage} className="flex flex-col">
        {rows.map((row) => {
          const extra = {
            ceTick: (row.live && row.ce ? directions.get(row.ce.key) : null) ?? null,
            peTick: (row.live && row.pe ? directions.get(row.pe.key) : null) ?? null,
            grey: dim || !row.live,
            maxOi,
            itm:
              model.spot === null || row.strike === model.spot
                ? null
                : row.strike < model.spot
                  ? ('ce' as const)
                  : ('pe' as const),
          }
          return (
            <ChainRow
              key={row.strike}
              row={row}
              view={view}
              {...extra}
              onOpen={onOpen}
              signature={rowSignature(row, view, extra)}
            />
          )
        })}
      </ul>
      {stage === 'all' && wings > 0 && model.wingsAsOf !== null && (
        <p data-wings-note="true" className="text-xs font-semibold text-muted-foreground">
          Far strikes are as of {clockTime(model.wingsAsOf)} · not real-time
        </p>
      )}
      {stage === 'near' && hiddenLive && (
        <button
          type="button"
          onClick={() => setShown({ key: chainKey, stage: 'window' })}
          className={MORE}
        >
          Show more strikes
        </button>
      )}
      {(stage === 'window' || (stage === 'near' && !hiddenLive)) && wings > 0 && (
        <button
          type="button"
          onClick={() => setShown({ key: chainKey, stage: 'all' })}
          className={MORE}
        >
          Show {wings} far strikes (not real-time)
        </button>
      )}
    </section>
  )
}
