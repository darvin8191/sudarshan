import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Ban, LoaderCircle, Plus, RefreshCw, TriangleAlert } from 'lucide-react'
import { useRef } from 'react'
import { cn } from '@/lib/utils'
import { ACTION, CARD, EmptyCard, LoadingCards, OUTLINED } from '@/sudarshan/books/cards/parts'
import { toast } from '@/utils/toast'
import { type ScreenerError, screenerApi } from './api'
import { PRESETS_KEY, STATE_KEY } from './keys'
import { putRow } from './stateCache'
import type { PresetCard, ScreenerState, ScreenRow } from './types'
import { WORDS } from './types'
import { useSend } from './useSend'

/** What the owner is told once a card has become a screen. */
export const MADE_WORDS = 'Added to your screens, Paused. Open it, read it, then Resume.'

function usePresets() {
  return useQuery<{ presets: PresetCard[] }, ScreenerError>({
    queryKey: PRESETS_KEY,
    queryFn: () => screenerApi.presets(),
    staleTime: 10 * 60_000,
    retry: false,
  })
}

/**
 * One library card. "Use this" makes a Paused copy of the preset, once: a second tap while the
 * request is out does nothing, and the one request id per card means a resend after a lost answer
 * returns the screen already made. A preset that cannot be used says why, with an icon, and has no
 * button.
 */
function Card({ card, onMade }: { card: PresetCard; onMade: (row: ScreenRow) => void }) {
  const client = useQueryClient()
  const send = useSend()
  const requestId = useRef(crypto.randomUUID())
  /** The screen this card already made: another tap opens it rather than making a second. */
  const made = useRef<ScreenRow | null>(null)
  const use = async () => {
    const spec = card.spec
    if (!spec) return
    if (made.current) {
      onMade(made.current)
      return
    }
    let row: ScreenRow | null = null
    const ok = await send.run(async () => {
      row = await screenerApi.create({ spec, request_id: requestId.current, status: 'paused' })
    })
    if (ok && row) {
      const next: ScreenRow = row
      made.current = next
      client.setQueryData<ScreenerState>(STATE_KEY, (state) =>
        state ? putRow(state, next) : state
      )
      toast.success(MADE_WORDS)
      onMade(next)
    }
  }
  return (
    <li data-preset={card.key} className={cn(CARD, 'flex flex-col gap-2 px-4 py-3.5')}>
      <div className="flex flex-col gap-0.5">
        <h2 className="text-base font-bold">{card.name}</h2>
        <p className="text-[0.875rem] font-medium text-[color:var(--text-secondary)]">
          {card.about}
        </p>
        {card.where && (
          <p className="text-[0.8125rem] font-semibold text-[color:var(--text-tertiary)]">
            {card.where}
          </p>
        )}
      </div>
      {card.usable ? (
        <>
          <button
            type="button"
            disabled={send.pending}
            onClick={() => void use()}
            className={cn(
              ACTION,
              'self-start bg-primary text-primary-foreground hover:bg-primary/90'
            )}
          >
            {send.pending ? (
              <LoaderCircle aria-hidden="true" className="size-5 animate-spin" />
            ) : (
              <Plus aria-hidden="true" className="size-5" />
            )}
            {send.pending ? 'Adding' : 'Use this'}
          </button>
          {send.problem && (
            <p
              role="alert"
              className="flex items-start gap-1.5 text-sm font-semibold text-destructive"
            >
              <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
              {send.problem.code === 'unanswered' || send.problem.code === 'failed'
                ? WORDS.not_confirmed
                : send.problem.message}
            </p>
          )}
        </>
      ) : (
        <p
          data-unavailable="true"
          className="flex items-start gap-1.5 text-[0.8125rem] font-semibold text-[color:var(--warning-text)]"
        >
          <Ban aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          {card.reason ?? 'Not available yet.'}
        </p>
      )}
    </li>
  )
}

/** The Library tab (design 4.6): ready-made screens, each one tap from a Paused copy of your own. */
export function Library({ onMade }: { onMade: (row: ScreenRow) => void }) {
  const query = usePresets()
  const cards = query.data?.presets
  if (!cards) {
    if (query.isPending) return <LoadingCards />
    return (
      <section
        role="alert"
        className="flex flex-col items-start gap-3 rounded-[1.125rem] border border-destructive bg-card px-4 py-3.5"
      >
        <p className="flex items-start gap-2 text-sm font-semibold">
          <TriangleAlert
            aria-hidden="true"
            className="mt-0.5 size-4 shrink-0 text-[color:var(--warning-text)]"
          />
          {query.error?.message ?? WORDS.unreadable}
        </p>
        <button type="button" onClick={() => void query.refetch()} className={OUTLINED}>
          <RefreshCw aria-hidden="true" className="size-4" />
          Try again
        </button>
      </section>
    )
  }
  if (cards.length === 0)
    return <EmptyCard title="The library is empty" hint="Ready-made screens will be listed here." />
  return (
    <ul className="flex flex-col gap-2">
      {cards.map((card) => (
        <Card key={card.key} card={card} onMade={onMade} />
      ))}
    </ul>
  )
}
