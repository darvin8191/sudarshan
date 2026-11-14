import { useQuery } from '@tanstack/react-query'
import { Search } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link } from 'react-router'
import { webClient } from '@/api/client'
import { cn } from '@/lib/utils'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'

/** The index underlyings, offered first. */
export const INDICES: Array<{ exchange: 'NFO' | 'BFO'; underlying: string }> = [
  { exchange: 'NFO', underlying: 'NIFTY' },
  { exchange: 'NFO', underlying: 'BANKNIFTY' },
  { exchange: 'NFO', underlying: 'FINNIFTY' },
  { exchange: 'NFO', underlying: 'MIDCPNIFTY' },
  { exchange: 'BFO', underlying: 'SENSEX' },
  { exchange: 'BFO', underlying: 'BANKEX' },
]

const ROW =
  'flex min-h-12 w-full items-center justify-between gap-3 rounded-[0.75rem] px-3 text-left text-[0.9375rem] font-semibold outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring'

/** The underlyings with options on an F&O exchange, from the master (read once a visit). */
async function underlyingsOf(exchange: string): Promise<string[]> {
  const answer = await webClient.get('/search/api/underlyings', {
    params: { exchange },
    timeout: 15_000,
  })
  const list = (answer.data as { underlyings?: unknown } | null)?.underlyings
  return Array.isArray(list) ? list.filter((each): each is string => typeof each === 'string') : []
}

/**
 * The underlying picker (M11.1, O4): the indices first, then a search over every NFO and BFO
 * underlying the master lists. MCX is not offered here (Upstox has no chain for it): the wide
 * option chain is one tap away.
 */
export function UnderlyingSheet({
  current,
  onPick,
  onClose,
}: {
  current: string
  onPick: (exchange: 'NFO' | 'BFO', underlying: string) => void
  onClose: () => void
}) {
  const [text, setText] = useState('')
  const nfo = useQuery({
    queryKey: ['sd-options-underlyings', 'NFO'],
    queryFn: () => underlyingsOf('NFO'),
    staleTime: 3_600_000,
    retry: false,
  })
  const bfo = useQuery({
    queryKey: ['sd-options-underlyings', 'BFO'],
    queryFn: () => underlyingsOf('BFO'),
    staleTime: 3_600_000,
    retry: false,
  })
  const wanted = text.trim().toUpperCase()
  const found = useMemo(() => {
    if (wanted === '') return []
    const all = [
      ...(nfo.data ?? []).map((underlying) => ({ exchange: 'NFO' as const, underlying })),
      ...(bfo.data ?? []).map((underlying) => ({ exchange: 'BFO' as const, underlying })),
    ]
    const starts = all.filter((each) => each.underlying.startsWith(wanted))
    const holds = all.filter(
      (each) => !each.underlying.startsWith(wanted) && each.underlying.includes(wanted)
    )
    return [...starts, ...holds].slice(0, 30)
  }, [wanted, nfo.data, bfo.data])
  const shown = wanted === '' ? INDICES : found
  const failed = wanted !== '' && (nfo.isError || bfo.isError) && found.length === 0
  return (
    <BottomSheet
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
      title="Underlying"
      description="Indices and F&O stocks with options"
    >
      <div data-underlying-sheet="true" className="flex flex-col gap-2">
        <label className="flex min-h-12 items-center gap-2 rounded-[0.875rem] border border-input bg-card px-3">
          <Search
            aria-hidden="true"
            className="size-5 shrink-0 text-[color:var(--text-secondary)]"
          />
          <span className="sr-only">Search an underlying</span>
          <input
            type="search"
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder="Search: RELIANCE, SENSEX"
            autoCapitalize="characters"
            autoCorrect="off"
            className="min-h-12 w-full bg-transparent text-base font-medium outline-none"
          />
        </label>
        <ul className="flex flex-col">
          {shown.map((each) => (
            <li key={`${each.exchange}:${each.underlying}`}>
              <button
                type="button"
                aria-current={each.underlying === current ? 'true' : undefined}
                onClick={() => onPick(each.exchange, each.underlying)}
                className={cn(ROW, each.underlying === current && 'bg-[color:var(--primary-tint)]')}
              >
                <span>{each.underlying}</span>
                <span className="text-xs font-semibold text-[color:var(--text-tertiary)]">
                  {each.exchange}
                  {each.underlying === current ? ' · open' : ''}
                </span>
              </button>
            </li>
          ))}
        </ul>
        {wanted !== '' && found.length === 0 && (
          <p className="px-1 text-sm font-medium text-[color:var(--text-secondary)]">
            {failed
              ? 'The list of underlyings could not be read. Close this and try again.'
              : nfo.isLoading || bfo.isLoading
                ? 'Reading the underlyings.'
                : `No underlying with options matches ${wanted}.`}
          </p>
        )}
        <Link
          to="/optionchain"
          className="inline-flex min-h-12 items-center text-sm font-semibold text-[color:var(--brand)] underline underline-offset-2"
        >
          MCX options: open the wide option chain
        </Link>
      </div>
    </BottomSheet>
  )
}
