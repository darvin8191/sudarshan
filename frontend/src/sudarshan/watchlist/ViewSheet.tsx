import { useQueryClient } from '@tanstack/react-query'
import { ArrowUpDown, Check, Columns3, Sunrise } from 'lucide-react'
import { type ReactNode, useCallback, useState } from 'react'
import { cn } from '@/lib/utils'
import { BottomSheet } from './BottomSheet'
import { FIGURES, MAX_FIGURES } from './figures'
import { OPEN_UNKNOWN } from './rows'
import {
  BASIS_LINE,
  DEFAULT_PREF,
  directions,
  isDefaultView,
  needsPrices,
  pickSort,
  SORTS,
  sortWords,
} from './sort'
import { callOp, problemOf } from './v2/api'
import type { Basis, Figure, Pref, View } from './v2/types'
import { viewKey } from './v2/useListView'

/**
 * The saved View of a list (M8.7, F6): what `view` answered, and the `pref` op that changes it.
 * The page takes a pick at once (the view in the cache is patched) and puts it back, with the
 * server's sentence, if the server refuses it, so phone and tablet end up agreeing. `held` (F7's
 * "Show what I hold") travels in the same `pref`.
 */
export function useViewPref(ref: string | null, view: View | undefined) {
  const client = useQueryClient()
  const [problem, setProblem] = useState<string | null>(null)
  const pref = view?.pref ?? DEFAULT_PREF
  const save = useCallback(
    async (next: Pref) => {
      if (ref === null) return
      const key = viewKey(ref)
      const before = client.getQueryData<View>(key)
      client.setQueryData<View>(key, (old) => old && { ...old, pref: next })
      setProblem(null)
      try {
        await callOp('pref', {
          ref,
          sort: next.sort,
          descending: next.descending,
          basis: next.basis,
          held: next.held, // F7
          ...(next.cols ? { cols: next.cols } : {}), // M8.3 GA6.1: never sent as none when not known
        })
        void client.invalidateQueries({ queryKey: key })
      } catch (error) {
        client.setQueryData<View>(key, (old) => old && before && { ...old, pref: before.pref })
        setProblem(problemOf(error).message)
      }
    },
    [client, ref]
  )
  return { ref, pref: view ? pref : undefined, save, problem }
}

export type ViewPref = ReturnType<typeof useViewPref>

const ROW =
  'flex min-h-12 w-full items-center gap-3 rounded-xl px-3 text-left text-[0.9375rem] font-semibold outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring'

function Choice({
  on,
  onClick,
  children,
}: {
  on: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onClick}
      className={cn(ROW, on && 'bg-[color:var(--primary-tint)] font-bold')}
    >
      {children}
      {on && <Check aria-hidden="true" className="ml-auto size-5" />}
    </button>
  )
}

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <fieldset className="m-0 border-0 p-0">
      <legend className="px-3 pt-3 pb-1 text-[0.8125rem] font-bold text-[color:var(--text-secondary)]">
        {title}
      </legend>
      {children}
    </fieldset>
  )
}

/** M8.3 GA6.2: the View sheet's names for the figures beside the price (W1). */
const PICK_NAMES: Record<Figure, string> = {
  high: 'Day high',
  low: 'Day low',
  open: 'Today’s open',
  prev: 'Previous close',
  volume: 'Volume',
  oi: 'Open interest (OI)',
  atp: 'Average price (ATP)',
  iv: 'IV (options)',
}
export const FIGURES_HELP =
  'Pick up to six. The first two show with the phone upright, up to six when it is turned on its side.'
export const FIGURES_DATA =
  'Day high, Day low, Volume, OI, ATP and IV come with every tick and use more mobile data than the price alone. IV is Upstox’s; the strike sheet works its own out.'
export const FIGURES_LEFT_OUT =
  'Not offered here: OI change, 52-week high and low, circuit limits, bid and ask (Market depth in the symbol sheet has them), Greeks and PCR (the strike sheet and the option tools), and charts in the rows.'
export const SIX_AT_MOST = 'Six figures at most. Remove one first.'
const NOTE = 'px-3 text-[0.8125rem] font-medium text-[color:var(--text-tertiary)]'

/** "Figures in the rows": each pick saved at once in the order made; a seventh is refused in place. */
function FigurePicker({
  picked,
  onChange,
}: {
  picked: readonly Figure[]
  onChange: (next: Figure[]) => void
}) {
  const [refused, setRefused] = useState(false)
  const toggle = (id: Figure) => {
    const on = picked.includes(id)
    if (!on && picked.length >= MAX_FIGURES) return setRefused(true)
    setRefused(false)
    onChange(on ? picked.filter((each) => each !== id) : [...picked, id])
  }
  return (
    <Group title="Figures in the rows">
      <p className={cn(NOTE, 'pb-1')}>{FIGURES_HELP}</p>
      {FIGURES.map(({ id }) => {
        const place = picked.indexOf(id) + 1
        return (
          // biome-ignore lint/a11y/useSemanticElements: a 48 dp row like the sheet's other choices, with its place beside the name; a native checkbox is a box, not the row
          <button
            key={id}
            type="button"
            role="checkbox"
            aria-checked={place > 0}
            aria-label={place > 0 ? `${PICK_NAMES[id]}, place ${place}` : PICK_NAMES[id]}
            onClick={() => toggle(id)}
            className={cn(ROW, place > 0 && 'bg-[color:var(--primary-tint)] font-bold')}
          >
            {PICK_NAMES[id]}
            {place > 0 && (
              <span className="ml-auto flex size-7 items-center justify-center rounded-full bg-[color:var(--brand)] text-sm font-bold text-white">
                {place}
              </span>
            )}
          </button>
        )
      })}
      <output className="block px-3 text-sm font-semibold text-[color:var(--warning-text)]">
        {refused ? SIX_AT_MOST : ''}
      </output>
      <p className={cn(NOTE, 'pt-1')}>{FIGURES_DATA}</p>
      <p className={cn(NOTE, 'pt-1')}>{FIGURES_LEFT_OUT}</p>
    </Group>
  )
}

/** The View sheet: Sort by, High first or A to Z, Change from. F7's switch goes where `extra` does. */
export function ViewSheet({
  open,
  onClose,
  pref,
  onChange,
  problem,
  noOpen,
  total,
  extra,
}: {
  open: boolean
  onClose: () => void
  pref: Pref
  onChange: (next: Pref) => void
  problem: string | null
  /** How many of the list's `total` symbols have no day open read yet. */
  noOpen: number
  total: number
  /** F7: "Show what I hold". */
  extra?: ReactNode
}) {
  const basis = (value: Basis) => onChange({ ...pref, basis: value })
  return (
    <BottomSheet open={open} onOpenChange={(next) => !next && onClose()} title="View">
      <Group title="Sort by">
        {SORTS.map(({ sort, label }) => (
          <Choice key={sort} on={pref.sort === sort} onClick={() => onChange(pickSort(pref, sort))}>
            {label}
          </Choice>
        ))}
      </Group>
      {pref.sort !== 'list' && (
        <Group title="Order">
          {directions(pref.sort).map(({ descending, label }) => (
            <Choice
              key={label}
              on={pref.descending === descending}
              onClick={() => onChange({ ...pref, descending })}
            >
              {label}
            </Choice>
          ))}
        </Group>
      )}
      <Group title="Change from">
        <Choice on={pref.basis === 'close'} onClick={() => basis('close')}>
          Previous close
        </Choice>
        <Choice on={pref.basis === 'open'} onClick={() => basis('open')}>
          Today&rsquo;s open
        </Choice>
        {pref.basis === 'open' && noOpen > 0 && (
          <p className="px-3 py-1 text-[0.8125rem] font-medium text-[color:var(--text-tertiary)]">
            {OPEN_UNKNOWN} for {noOpen} of {total} {total === 1 ? 'symbol' : 'symbols'}. They show a
            dash until it is read.
          </p>
        )}
      </Group>
      <FigurePicker
        picked={pref.cols ?? []}
        onChange={(cols) => onChange({ ...pref, cols })} // M8.3
      />
      {extra}
      {problem && (
        <p role="alert" className="px-3 pt-2 text-sm font-medium text-[color:var(--down)]">
          {problem}
        </p>
      )}
    </BottomSheet>
  )
}

export const CLOCK = new Intl.DateTimeFormat('en-IN', {
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
  timeZone: 'Asia/Kolkata',
})

/**
 * The line under the tabs when the View is not the default: what applies, and Sort again (48 dp)
 * for a sort on prices, which is the only way its order moves besides picking again.
 */
export function ViewLine({
  pref,
  at,
  readAt,
  onAgain,
  figures,
}: {
  pref: Pref
  at: number | null
  /** F9: when the prices of a list longer than the window were read, when that is what it was ranked on. */
  readAt?: number | null
  onAgain: () => void
  /** M8.3: the figures picked, how many the rows draw at this width, and whether rows differ. */
  figures?: { picked: number; shown: number; uneven: boolean }
}) {
  const fewer = figures !== undefined && figures.shown < figures.picked
  if (isDefaultView(pref) && !fewer) return null
  const sorted = pref.sort !== 'list'
  const prices = needsPrices(pref.sort)
  return (
    <p
      data-view-line="true"
      className="flex flex-wrap items-center gap-x-2 px-1 text-xs font-semibold text-[color:var(--text-secondary)]"
    >
      {sorted && (
        <span className="flex items-center gap-1.5">
          <ArrowUpDown aria-hidden="true" className="size-3.5" />
          {`Sorted by ${sortWords(pref)}${prices && at !== null ? `, at ${CLOCK.format(at)}` : ''}${prices && at !== null && readAt != null ? `, from prices read at ${CLOCK.format(readAt)}` : ''}`}
        </span>
      )}
      {sorted && prices && <span aria-hidden="true">&middot;</span>}
      {sorted && prices && (
        <button
          type="button"
          onClick={onAgain}
          className="inline-flex min-h-12 items-center font-semibold text-[color:var(--brand)] underline underline-offset-2 outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          Sort again
        </button>
      )}
      {sorted && pref.basis === 'open' && <span aria-hidden="true">&middot;</span>}
      {pref.basis === 'open' && (
        <span className="flex items-center gap-1.5">
          <Sunrise aria-hidden="true" className="size-3.5" />
          {BASIS_LINE}
        </span>
      )}
      {figures && fewer && (
        <span data-figures-line="true" className="flex items-center gap-1.5">
          <Columns3 aria-hidden="true" className="size-3.5" />
          {`Figures: ${figures.uneven ? 'up to ' : ''}${figures.shown} of ${figures.picked} shown here; turn the phone on its side for the rest.`}
        </span>
      )}
    </p>
  )
}
