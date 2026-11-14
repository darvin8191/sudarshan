import { Clock } from 'lucide-react'
import { useState } from 'react'
import { cn } from '@/lib/utils'
import { formatPrice } from '@/sudarshan/dashboard/format'
import type { ChainModel, LegView, StrikeView } from './model'
import { clockTime } from './TimedRefresh'
import { oiWords, strikeWords } from './words'

type Lead = 'oi' | 'change'

const TAG = 'rounded px-1 text-[0.6875rem] leading-tight font-bold whitespace-nowrap'
const HATCH = 'repeating-linear-gradient(135deg, var(--border-strong) 0 2px, transparent 2px 5px)'

const signedOi = (value: number) => `${value > 0 ? '+' : value < 0 ? '−' : ''}${oiWords(value)}`

const dash = (
  <>
    <span aria-hidden="true">&mdash;</span>
    <span className="sr-only">not available</span>
  </>
)

/** The words under the figures: where the spot stands against max pain. */
export function painWords(maxPain: number, spot: number | null): string {
  if (spot === null) return `Max pain ${strikeWords(maxPain)}`
  const gap = spot - maxPain
  const where =
    Math.abs(gap) < 0.005
      ? 'spot is at it'
      : `spot is ${formatPrice(Math.abs(gap), 0)} ${gap > 0 ? 'above' : 'below'}`
  return `Max pain ${strikeWords(maxPain)} · ${where}`
}

function Bars({
  leg,
  side,
  lead,
  maxOi,
  maxChange,
  grey,
}: {
  leg: LegView | null
  side: 'ce' | 'pe'
  lead: Lead
  maxOi: number
  maxChange: number
  grey: boolean
}) {
  const call = side === 'ce'
  const oi = leg?.oi ?? null
  const change = leg?.oiChange ?? null
  const oiShare = oi !== null && maxOi > 0 ? oi / maxOi : 0
  const changeShare = change !== null && maxChange > 0 ? Math.abs(change) / maxChange : 0
  const wide = lead === 'oi' ? oiShare : changeShare
  const thin = lead === 'oi' ? changeShare : oiShare
  const fell = change !== null && change < 0
  const width = (share: number) => `${share > 0 ? Math.max(2, Math.round(share * 100)) : 0}%`
  /** A fall is hatched as well as signed; a rise, and the open interest itself, are solid. */
  const fill = (isChange: boolean, solid: string) =>
    isChange && fell ? { backgroundImage: HATCH } : { backgroundColor: solid }
  return (
    <span
      data-side={side.toUpperCase()}
      className={cn(
        'relative flex min-h-14 flex-col justify-center gap-0.5 px-1.5 pt-1 pb-3',
        call ? 'items-start' : 'items-end'
      )}
    >
      <span
        aria-hidden="true"
        data-bar="wide"
        className={cn('absolute top-1 bottom-3 rounded-sm', call ? 'right-0' : 'left-0')}
        style={{ width: width(wide), ...fill(lead === 'change', 'var(--primary-tint)') }}
      />
      <span
        aria-hidden="true"
        data-bar="thin"
        data-fell={lead === 'oi' && fell ? 'true' : undefined}
        className={cn('absolute bottom-1 h-1.5 rounded-sm', call ? 'right-0' : 'left-0')}
        style={{ width: width(thin), ...fill(lead === 'oi', 'var(--brand)') }}
      />
      <span
        className={cn(
          'relative text-sm leading-tight font-bold',
          grey && 'text-[color:var(--stale)]'
        )}
      >
        <span className="sr-only">{call ? 'call' : 'put'} open interest </span>
        {oi === null ? dash : oiWords(oi)}
      </span>
      <span
        className={cn(
          'relative text-xs leading-tight font-semibold',
          grey ? 'text-[color:var(--stale)]' : 'text-[color:var(--text-secondary)]'
        )}
      >
        <span className="sr-only">change today </span>
        {change === null ? dash : signedOi(change)}
      </span>
    </span>
  )
}

function Row({
  row,
  tags,
  lead,
  maxOi,
  maxChange,
  grey,
}: {
  row: StrikeView
  tags: string[]
  lead: Lead
  maxOi: number
  maxChange: number
  grey: boolean
}) {
  return (
    <li
      data-oi-strike={row.strike}
      className="grid grid-cols-[minmax(0,1fr)_5.5rem_minmax(0,1fr)] items-stretch border-b border-[color:var(--divider)]"
    >
      <Bars leg={row.ce} side="ce" lead={lead} maxOi={maxOi} maxChange={maxChange} grey={grey} />
      <span className="flex flex-col items-center justify-center gap-0.5 px-1 py-1 text-center">
        <span className="text-sm leading-tight font-bold">{strikeWords(row.strike)}</span>
        {tags.map((tag) => (
          <span key={tag} className={cn(TAG, 'bg-[color:var(--primary-tint)] text-primary')}>
            {tag}
          </span>
        ))}
      </span>
      <Bars leg={row.pe} side="pe" lead={lead} maxOi={maxOi} maxChange={maxChange} grey={grey} />
    </li>
  )
}

const total = (rows: StrikeView[], side: 'ce' | 'pe'): number | null => {
  const known = rows.map((row) => row[side]?.oi).filter((oi): oi is number => oi != null)
  return known.length ? known.reduce((a, b) => a + b, 0) : null
}

/** A strike outside the followed window: its figures are the snapshot's. */
function isFar(row: StrikeView): boolean {
  return !row.live
}

/** The far strikes on one side of the window, folded into one row; a tap lists them. */
function Wing({
  label,
  rows,
  asOf,
  render,
}: {
  label: string
  rows: StrikeView[]
  asOf: number
  render: (row: StrikeView) => React.ReactNode
}) {
  const [open, setOpen] = useState(false)
  if (rows.length === 0) return null
  const calls = total(rows, 'ce')
  const puts = total(rows, 'pe')
  return (
    <>
      <li data-oi-wing={label}>
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen(!open)}
          className="grid min-h-12 w-full grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-2 border-b border-[color:var(--divider)] px-1.5 text-[color:var(--stale)] outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset"
        >
          <span className="text-left text-sm font-bold">
            {calls === null ? dash : oiWords(calls)}
          </span>
          <span className="flex flex-col items-center text-xs leading-tight font-semibold">
            <span>
              {rows.length} {label} strikes
            </span>
            <span className="inline-flex items-center gap-1">
              <Clock aria-hidden="true" className="size-3" />
              as of {clockTime(asOf)}
            </span>
          </span>
          <span className="text-right text-sm font-bold">
            {puts === null ? dash : oiWords(puts)}
          </span>
        </button>
      </li>
      {open && rows.map(render)}
    </>
  )
}

/**
 * The OI tab (M11.3, M11.4, O6): open interest by strike for the followed window, the calls' bars
 * growing left of the strike and the puts' right, each with its number in lakh and, under it, a
 * narrower bar and number for today's change (a fall is hatched and signed, never told by colour
 * alone). Above: the totals, PCR, max pain and the two walls, which count every strike of the
 * expiry and say the far strikes' time. The far strikes are folded into two rows.
 */
export function OiTab({ model, dim }: { model: ChainModel; dim: boolean }) {
  const [lead, setLead] = useState<Lead>('oi')
  const live = model.rows.filter((row) => !isFar(row))
  const first = live[0]?.strike ?? Number.POSITIVE_INFINITY
  const lastStrike = live[live.length - 1]?.strike ?? Number.NEGATIVE_INFINITY
  const lower = model.rows.filter((row) => isFar(row) && row.strike < first)
  const higher = model.rows.filter((row) => isFar(row) && row.strike > lastStrike)
  const legs = model.rows.flatMap((row) => [row.ce, row.pe])
  const maxOi = Math.max(0, ...legs.map((leg) => leg?.oi ?? 0))
  const maxChange = Math.max(0, ...legs.map((leg) => Math.abs(leg?.oiChange ?? 0)))
  const tagsOf = (row: StrikeView) => [
    ...(row.atm ? ['ATM'] : []),
    ...(row.strike === model.maxPain ? ['Max pain'] : []),
    ...(row.strike === model.callWall?.strike ? ['Call wall'] : []),
    ...(row.strike === model.putWall?.strike ? ['Put wall'] : []),
  ]
  const render = (row: StrikeView) => (
    <Row
      key={row.strike}
      row={row}
      tags={tagsOf(row)}
      lead={lead}
      maxOi={maxOi}
      maxChange={maxChange}
      grey={dim || !row.live}
    />
  )
  const wings = model.wingsAsOf !== null ? ` · far strikes as of ${clockTime(model.wingsAsOf)}` : ''
  return (
    <section aria-label="Open interest" className="flex flex-col gap-2">
      <div
        data-oi-facts="true"
        className="flex flex-col gap-1 rounded-[1.125rem] border bg-card px-4 py-3 text-sm font-semibold shadow-[var(--shadow-card)]"
      >
        <p>
          Calls {model.ceOi === null ? dash : oiWords(model.ceOi)} · Puts{' '}
          {model.peOi === null ? dash : oiWords(model.peOi)}
        </p>
        <p>
          PCR {model.pcr === null ? dash : model.pcr.toFixed(2)}
          {wings}
        </p>
        <p>
          {model.maxPain === null
            ? 'Max pain not worked out: no open interest yet.'
            : painWords(model.maxPain, model.spot)}
        </p>
        <p>
          Call wall{' '}
          {model.callWall
            ? `${strikeWords(model.callWall.strike)} (${oiWords(model.callWall.oi)})`
            : dash}
          {' · '}Put wall{' '}
          {model.putWall
            ? `${strikeWords(model.putWall.strike)} (${oiWords(model.putWall.oi)})`
            : dash}
        </p>
      </div>
      <fieldset className="grid grid-cols-2 gap-1 rounded-[0.875rem] bg-[color:var(--surface-2)] p-1">
        <legend className="sr-only">Which bar is the wide one</legend>
        {(['oi', 'change'] as const).map((each) => (
          <button
            key={each}
            type="button"
            aria-pressed={lead === each}
            onClick={() => setLead(each)}
            className={cn(
              'min-h-12 rounded-[0.625rem] text-sm font-semibold outline-none focus-visible:ring-2 focus-visible:ring-ring',
              lead === each
                ? 'bg-card font-bold text-primary underline underline-offset-4 shadow-[var(--shadow-card)]'
                : 'text-[color:var(--text-secondary)]'
            )}
          >
            {each === 'oi' ? 'OI' : 'Change'}
          </button>
        ))}
      </fieldset>
      <p
        aria-hidden="true"
        className="grid grid-cols-[minmax(0,1fr)_5.5rem_minmax(0,1fr)] px-1.5 text-xs font-bold text-[color:var(--text-tertiary)]"
      >
        <span>CALL OI</span>
        <span className="text-center">STRIKE</span>
        <span className="text-right">PUT OI</span>
      </p>
      <ul data-oi-rows={lead} className="flex flex-col">
        <Wing label="lower" rows={lower} asOf={model.asOf} render={render} />
        {live.map(render)}
        <Wing label="higher" rows={higher} asOf={model.asOf} render={render} />
      </ul>
      <p className="text-xs font-medium text-muted-foreground">
        Under each figure: the change in open interest since yesterday. A hatched bar and a minus
        are a fall.
      </p>
    </section>
  )
}
