import { EllipsisVertical, Power, PowerOff, Replace, TriangleAlert } from 'lucide-react'
import { useId } from 'react'
import { cn } from '@/lib/utils'
import { OPERATOR_BY_ID } from './catalog'
import type { Clause, Operand } from './types'
import { clauseSentence, operandChips, operatorChip } from './words'

/** A chip of a clause's sentence: at least 48 dp tall when it can be tapped. */
export const CHIP =
  'inline-flex min-h-12 items-center rounded-xl border border-input bg-card px-3 text-[0.9375rem] font-semibold text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring'
const CHIP_TAPPABLE = `${CHIP} hover:bg-accent active:bg-accent`
/** A chip that only reads: the same shape, quieter. */
const CHIP_READ =
  'inline-flex min-h-12 items-center rounded-xl bg-[color:var(--surface-2)] px-3 text-[0.9375rem] font-semibold'

export type Side = 'left' | 'right'

/**
 * One condition as a card (design 5.2): its left edge is its on/off switch (the word On or Off
 * under an icon, never colour alone), then its sentence of chips, then its menu. A line that is
 * off keeps its words and says so; the screen ignores it.
 */
export function ClauseCard({
  clause,
  problem,
  onToggle,
  onOperator,
  onOperand,
  onNumber,
  onMenu,
}: {
  clause: Clause
  problem?: string | null
  onToggle: (on: boolean) => void
  onOperator: () => void
  /** Open the operand's sheet; without it the operand chips only read. */
  onOperand?: (side: Side) => void
  onNumber: (side: Side, value: string) => void
  onMenu: () => void
}) {
  const info = OPERATOR_BY_ID.get(clause.op)
  const binary = info?.arity !== 'unary'
  const problemId = useId()
  return (
    <fieldset
      data-clause={clause.id}
      data-on={clause.on}
      aria-label={`Condition: ${clauseSentence(clause)}${clause.on ? '' : ', off'}`}
      aria-describedby={problem ? problemId : undefined}
      className={cn(
        'm-0 flex min-w-0 overflow-hidden rounded-[1rem] border bg-card p-0 shadow-[var(--shadow-card)]',
        !clause.on && 'border-dashed border-[color:var(--border-strong)]',
        problem && 'border-destructive'
      )}
    >
      <button
        type="button"
        aria-pressed={clause.on}
        aria-label="This condition is checked"
        onClick={() => onToggle(!clause.on)}
        className={cn(
          'flex w-14 shrink-0 flex-col items-center justify-center gap-0.5 border-r text-[0.6875rem] font-bold outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring',
          clause.on
            ? 'bg-[color:var(--brand-tint)] text-[color:var(--brand)]'
            : 'bg-[color:var(--surface-2)] text-[color:var(--text-secondary)]'
        )}
      >
        {clause.on ? (
          <Power aria-hidden="true" className="size-5" />
        ) : (
          <PowerOff aria-hidden="true" className="size-5" />
        )}
        {clause.on ? 'On' : 'Off'}
      </button>
      <div
        className={cn('flex min-w-0 flex-1 flex-col gap-1 px-2.5 py-2', !clause.on && 'opacity-80')}
      >
        <div className="flex flex-wrap items-center gap-1.5">
          <OperandChips
            operand={clause.left}
            side="left"
            onOperand={onOperand}
            onNumber={onNumber}
          />
          <button
            type="button"
            onClick={onOperator}
            className={cn(CHIP_TAPPABLE, 'border-primary text-primary')}
          >
            {operatorChip(clause)}
          </button>
          {binary && (
            <OperandChips
              operand={clause.right}
              side="right"
              onOperand={onOperand}
              onNumber={onNumber}
            />
          )}
        </div>
        {problem && (
          <p
            id={problemId}
            className="flex items-start gap-1.5 text-[0.8125rem] font-semibold text-destructive"
          >
            <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
            {problem}
          </p>
        )}
      </div>
      <button
        type="button"
        aria-label="More for this condition"
        onClick={onMenu}
        className="flex w-12 shrink-0 items-center justify-center text-[color:var(--text-secondary)] outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
      >
        <EllipsisVertical aria-hidden="true" className="size-5" />
      </button>
    </fieldset>
  )
}

/** The chips of one operand: [timeframe] [value] [offset], or an input for a typed number. */
function OperandChips({
  operand,
  side,
  onOperand,
  onNumber,
}: {
  operand: Operand | undefined
  side: Side
  onOperand?: (side: Side) => void
  onNumber: (side: Side, value: string) => void
}) {
  const chips = operandChips(operand)
  const part = (text: string, label: string) =>
    onOperand ? (
      <button
        type="button"
        aria-label={`${label}: ${text}`}
        onClick={() => onOperand(side)}
        className={CHIP_TAPPABLE}
      >
        {text}
      </button>
    ) : (
      <span className={CHIP_READ}>{text}</span>
    )
  if (operand && 'num' in operand) {
    return (
      <span className="inline-flex items-center gap-1">
        <input
          type="text"
          inputMode="decimal"
          aria-label={`Number, ${side === 'left' ? 'first' : 'second'} side`}
          value={operand.num}
          onChange={(event) => onNumber(side, event.target.value)}
          className="min-h-12 w-24 rounded-xl border border-input bg-card px-3 text-[0.9375rem] font-semibold outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
        {onOperand && (
          <button
            type="button"
            aria-label={`Pick a different value, ${side === 'left' ? 'first' : 'second'} side`}
            onClick={() => onOperand(side)}
            className="flex size-12 items-center justify-center rounded-xl text-[color:var(--text-secondary)] outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Replace aria-hidden="true" className="size-5" />
          </button>
        )}
      </span>
    )
  }
  return (
    <>
      {chips.tf && part(chips.tf, 'Candles')}
      {part(chips.text, 'Value')}
      {chips.at && part(chips.at, 'Which candle')}
    </>
  )
}
