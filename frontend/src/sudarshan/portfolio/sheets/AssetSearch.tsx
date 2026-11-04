// SUDARSHAN M7.21: the asset field of the Add sheet (design 4.10). It searches in this order:
//   1. listed shares and ETFs, NSE and BSE cash only, through the app's instrument search;
//   2. mutual funds, through the service (AMFI), which may not be ready yet;
//   3. this tab's own manual assets;
//   4. "+ New manual asset".
// Choosing a result gives the service's asset (made on the spot when it is new to the tab), with
// the last price known for it, so the sheet can prefill the price.
import { useQuery } from '@tanstack/react-query'
import { Loader2, Plus, Search } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { cn } from '@/lib/utils'
import { usePriceAccess } from '@/sudarshan/access'
import { type SearchResult, searchSymbols } from '@/sudarshan/watchlist/api'
import { PortfolioError, portfolioCall, problemOf } from '../api'
import {
  ASSET_CLASS_LABELS,
  ASSET_CLASSES,
  type Asset,
  type AssetClass,
  MF_SEARCH_MAX,
  type MfScheme,
} from '../types'
import type { AssetPick } from './entryForm'
import { dayToInput, inputToDay, parseRupees } from './parse'
import { FIELD, LABEL, PRIMARY, ProblemLine, SECONDARY } from './TabFields'
import { useSend } from './useSend'

export const SEARCH_MIN = 2
export const SEARCH_DEBOUNCE_MS = 250
export const MF_NOT_READY = 'Mutual fund search is not ready yet'
const CASH = new Set(['NSE', 'BSE'])
const ROW =
  'flex min-h-12 w-full items-center justify-between gap-3 rounded-[0.875rem] px-3 text-left outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50'

function useSettled(value: string, ms: number): string {
  const [settled, setSettled] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), ms)
    return () => clearTimeout(timer)
  }, [value, ms])
  return settled
}

interface MfAnswer {
  items: MfScheme[]
  /** The service said AMFI is not ready (an error), or gave a note of its own. */
  notReady: boolean
  note: string | null
}

function unwrap<T>(result: { enabled: true; data: T } | { enabled: false; message: string }): T {
  if (!result.enabled) {
    throw new PortfolioError({ code: 'unavailable', message: result.message, entryId: null })
  }
  return result.data
}

export function AssetSearch({
  value,
  onPick,
  assets,
  manualOnly = false,
  disabled = false,
  label = 'Asset',
  optional = false,
  today,
}: {
  value: AssetPick | null
  onPick: (pick: AssetPick | null) => void
  /** Every asset the tab's entries name (from the tab view): a result that is one of them reuses it. */
  assets: readonly Asset[]
  /** "Add a valuable": only manual assets, and the new-asset form is open. */
  manualOnly?: boolean
  disabled?: boolean
  label?: string
  optional?: boolean
  /** Today as YYYYMMDD, the default date of a new manual asset's first value. */
  today: number
}) {
  const access = usePriceAccess()
  const [text, setText] = useState('')
  const [making, setMaking] = useState(false)
  const q = useSettled(text.trim(), SEARCH_DEBOUNCE_MS)
  const searching = q.length >= SEARCH_MIN
  const send = useSend()

  const listed = useQuery({
    queryKey: ['sudarshan', 'symbol-search', q.toUpperCase()],
    queryFn: () => searchSymbols(access.apiKey ?? '', q),
    enabled: searching && !manualOnly && access.apiKey !== null,
    staleTime: 5 * 60_000,
    retry: false,
  })
  const funds = useQuery({
    queryKey: ['sudarshan', 'portfolio', 'mf-search', q.toUpperCase()],
    queryFn: async (): Promise<MfAnswer> => {
      try {
        const data = unwrap(await portfolioCall('mf_search', { q, limit: MF_SEARCH_MAX }))
        return { items: data.items, notReady: false, note: data.items.length ? null : data.note }
      } catch (error) {
        if (problemOf(error).code === 'not_ready') return { items: [], notReady: true, note: null }
        throw error
      }
    },
    enabled: searching && !manualOnly,
    staleTime: 60_000,
    retry: false,
  })

  const manual = assets.filter(
    (a) => a.kind === 'manual' && (!searching || a.name.toLowerCase().includes(q.toLowerCase()))
  )
  const stocks = (listed.data ?? []).filter((r) => CASH.has(r.exchange.toUpperCase())).slice(0, 10)

  const pickKnown = (asset: Asset, priceU: number | null = null) => {
    onPick({ asset, priceU })
    setText('')
  }

  const chooseListed = async (r: SearchResult) => {
    const known = assets.find(
      (a) => a.kind === 'listed' && a.symbol === r.symbol && a.exchange === r.exchange
    )
    if (known) return pickKnown(known)
    await send.run(async () => {
      const made = unwrap(
        await portfolioCall('asset_create', {
          request_id: crypto.randomUUID(),
          kind: 'listed',
          symbol: r.symbol,
          exchange: r.exchange,
        })
      )
      pickKnown(made)
    })
  }

  const chooseFund = async (f: MfScheme) => {
    const known = assets.find((a) => a.kind === 'mf' && a.amfi_code === f.amfi_code)
    if (known) return pickKnown(known, f.nav_u)
    await send.run(async () => {
      const made = unwrap(
        await portfolioCall('asset_create', {
          request_id: crypto.randomUUID(),
          kind: 'mf',
          amfi_code: f.amfi_code,
        })
      )
      pickKnown(made, f.nav_u)
    })
  }

  if (value) {
    return (
      <div className="flex flex-col gap-1.5">
        <span className={LABEL}>{label}</span>
        <div className="flex min-h-12 items-center justify-between gap-2 rounded-[0.875rem] border border-[color:var(--border-strong)] bg-card px-3">
          <span className="min-w-0">
            <span className="block truncate text-base font-bold">{value.asset.name}</span>
            <span className="block text-xs text-[color:var(--text-tertiary)]">
              {value.asset.symbol ? `${value.asset.symbol} · ` : ''}
              {ASSET_CLASS_LABELS[value.asset.asset_class]}
            </span>
          </span>
          <button
            type="button"
            className="min-h-12 px-2 text-sm font-semibold text-primary"
            disabled={disabled}
            onClick={() => onPick(null)}
          >
            Change
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-1.5">
      <label className="flex flex-col gap-1.5">
        <span className={LABEL}>
          {label}
          {optional ? ' (optional)' : ''}
        </span>
        {!manualOnly && (
          <span className="flex min-h-12 items-center gap-2 rounded-[0.875rem] border border-input bg-card px-3 focus-within:ring-[3px] focus-within:ring-ring/50">
            <Search
              aria-hidden="true"
              className="size-5 shrink-0 text-[color:var(--text-tertiary)]"
            />
            <input
              type="search"
              autoComplete="off"
              spellCheck={false}
              value={text}
              disabled={disabled}
              onChange={(event) => setText(event.target.value)}
              placeholder="Share, fund or your own asset"
              className="h-full min-h-12 min-w-0 flex-1 bg-transparent text-base outline-none"
            />
            {(listed.isFetching || funds.isFetching || send.pending) && (
              <Loader2 aria-hidden="true" className="size-5 shrink-0 animate-spin" />
            )}
          </span>
        )}
      </label>
      <ProblemLine problem={send.problem} />
      {!making && (
        <div className="flex flex-col gap-2" aria-live="polite">
          {!searching && !manualOnly && (
            <p className="text-sm text-[color:var(--text-secondary)]">
              Type at least {SEARCH_MIN} letters.
            </p>
          )}
          {searching && (
            <Group title="Shares and ETFs">
              {access.apiKey === null ? (
                <Note>Share search needs today’s Upstox login.</Note>
              ) : listed.isError ? (
                <Note>Share search is not answering. Try again in a moment.</Note>
              ) : stocks.length === 0 && !listed.isFetching ? (
                <Note>No share or ETF matches.</Note>
              ) : (
                stocks.map((r) => (
                  <button
                    key={`${r.exchange}:${r.symbol}`}
                    type="button"
                    className={ROW}
                    disabled={send.pending}
                    onClick={() => void chooseListed(r)}
                  >
                    <span className="min-w-0 truncate">
                      <span className="font-bold">{r.symbol}</span>
                      {r.name ? <span className="text-sm"> · {r.name}</span> : null}
                    </span>
                    <span className="text-xs font-semibold text-[color:var(--text-tertiary)]">
                      {r.exchange}
                    </span>
                  </button>
                ))
              )}
            </Group>
          )}
          {searching && !manualOnly && (
            <Group title="Mutual funds">
              {funds.data?.notReady ? (
                <Note>{MF_NOT_READY}</Note>
              ) : funds.isError ? (
                <Note>{problemOf(funds.error).message}</Note>
              ) : funds.data && funds.data.items.length === 0 && !funds.isFetching ? (
                <Note>{funds.data.note ?? 'No mutual fund matches.'}</Note>
              ) : (
                (funds.data?.items ?? []).map((f) => (
                  <button
                    key={f.amfi_code}
                    type="button"
                    className={ROW}
                    disabled={send.pending}
                    onClick={() => void chooseFund(f)}
                  >
                    <span className="min-w-0 truncate font-semibold">{f.name}</span>
                    <span className="text-xs font-semibold text-[color:var(--text-tertiary)]">
                      {ASSET_CLASS_LABELS[f.asset_class]}
                    </span>
                  </button>
                ))
              )}
            </Group>
          )}
          {manual.length > 0 && (
            <Group title="Your own assets in this tab">
              {manual.map((a) => (
                <button key={a.id} type="button" className={ROW} onClick={() => pickKnown(a)}>
                  <span className="min-w-0 truncate font-semibold">{a.name}</span>
                  <span className="text-xs font-semibold text-[color:var(--text-tertiary)]">
                    {ASSET_CLASS_LABELS[a.asset_class]}
                  </span>
                </button>
              ))}
            </Group>
          )}
        </div>
      )}
      {making ? (
        <NewManualAsset
          today={today}
          onCancel={() => setMaking(false)}
          onMade={(asset, priceU) => {
            setMaking(false)
            pickKnown(asset, priceU)
          }}
        />
      ) : (
        <button type="button" className={SECONDARY} onClick={() => setMaking(true)}>
          <Plus aria-hidden="true" className="size-4" />
          New manual asset
        </button>
      )}
    </div>
  )
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col">
      <h3 className="px-1 text-xs font-semibold text-muted-foreground">{title}</h3>
      {children}
    </div>
  )
}

function Note({ children }: { children: React.ReactNode }) {
  return <p className="px-3 py-2 text-sm text-[color:var(--text-secondary)]">{children}</p>
}

/** A name, a class, a unit, and a first value on a date: a flat, gold, a plot, a loan to a friend. */
function NewManualAsset({
  today,
  onMade,
  onCancel,
}: {
  today: number
  onMade: (asset: Asset, priceU: number | null) => void
  onCancel?: () => void
}) {
  const [name, setName] = useState('')
  const [assetClass, setAssetClass] = useState<AssetClass>('gold')
  const [unit, setUnit] = useState('')
  const [first, setFirst] = useState('')
  const [date, setDate] = useState(dayToInput(today))
  const requestId = useRef(crypto.randomUUID())
  const send = useSend()
  const price = first.trim() === '' ? null : parseRupees(first)
  const day = inputToDay(date)
  const ready = name.trim().length > 0 && price !== null && day !== null

  const make = async () => {
    if (!ready || price === null || day === null) return
    await send.run(async () => {
      const made = unwrap(
        await portfolioCall('asset_create', {
          request_id: requestId.current,
          kind: 'manual',
          name: name.trim(),
          asset_class: assetClass,
          ...(unit.trim() ? { unit_label: unit.trim() } : {}),
          first_value: { day, price_u: price * 100 },
        })
      )
      onMade(made, price * 100)
    })
  }

  return (
    <fieldset
      className="flex flex-col gap-3 rounded-[0.875rem] border border-[color:var(--border-strong)] p-3"
      disabled={send.pending}
    >
      <legend className="px-1 text-sm font-bold">New manual asset</legend>
      <label className="flex flex-col gap-1.5">
        <span className={LABEL}>Name</span>
        <input
          className={FIELD}
          value={name}
          maxLength={80}
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      <label className="flex flex-col gap-1.5">
        <span className={LABEL}>Class</span>
        <select
          className={FIELD}
          value={assetClass}
          onChange={(e) => setAssetClass(e.target.value as AssetClass)}
        >
          {ASSET_CLASSES.map((c) => (
            <option key={c} value={c}>
              {ASSET_CLASS_LABELS[c]}
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1.5">
        <span className={LABEL}>One unit is (for example g, sq ft)</span>
        <input
          className={FIELD}
          value={unit}
          maxLength={12}
          onChange={(e) => setUnit(e.target.value)}
        />
      </label>
      <div className="grid grid-cols-2 gap-2">
        <label className="flex flex-col gap-1.5">
          <span className={LABEL}>Value of one unit (₹)</span>
          <input
            className={FIELD}
            inputMode="decimal"
            value={first}
            onChange={(e) => setFirst(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1.5">
          <span className={LABEL}>On</span>
          <input
            type="date"
            className={FIELD}
            value={date}
            onChange={(e) => setDate(e.target.value)}
          />
        </label>
      </div>
      <ProblemLine problem={send.problem} />
      <div className="flex gap-2">
        {onCancel && (
          <button type="button" className={cn(SECONDARY, 'flex-1')} onClick={onCancel}>
            Cancel
          </button>
        )}
        <button
          type="button"
          className={cn(PRIMARY, 'flex-1')}
          disabled={!ready || send.pending}
          onClick={() => void make()}
        >
          {send.pending ? 'Saving' : 'Add this asset'}
        </button>
      </div>
    </fieldset>
  )
}
