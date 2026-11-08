import { cn } from '@/lib/utils'
import { OUTLINED } from '@/sudarshan/books/cards/parts'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { BUILDUP_STATES, OPERATORS, PATTERNS } from './catalog'
import type { Clause } from './types'
import { stateWords } from './words'

const INPUT =
  'min-h-12 w-full rounded-xl border border-input bg-card px-3 text-[0.9375rem] font-semibold outline-none focus-visible:ring-2 focus-visible:ring-ring'

/** The state names an `is` clause can name: candle patterns and the open interest build-up states. */
export const IS_NAMES = [...PATTERNS, ...BUILDUP_STATES.filter((name) => name !== 'none')]

/** What an operator needs by way of arguments, with a starting value when it has just been chosen. */
const STARTING: Record<string, Record<string, string | number>> = {
  between: { low: '30', high: '70' },
  within_pct: { pct: '0.5' },
  rising: { n: 3 },
  falling: { n: 3 },
  is: { name: 'doji' },
}

/** The clause after its operator is changed: unary operators drop the right side, binary ones get one. */
export function withOperator(clause: Clause, op: string): Clause {
  const info = OPERATORS.find((each) => each.id === op)
  if (!info) return clause
  const next: Clause = { ...clause, op }
  delete next.args
  if (STARTING[op]) next.args = { ...STARTING[op] }
  if (info.arity === 'unary') delete next.right
  else if (!next.right) next.right = { num: '0' }
  return next
}

/**
 * The operator's sheet: every operator in the owner's words, and the numbers or names the chosen
 * one needs (a percent, a number of candles, a low and a high, a pattern). Changes apply as they
 * are made; closing the sheet keeps them.
 */
export function OperatorSheet({
  clause,
  onChange,
  onClose,
}: {
  clause: Clause | null
  onChange: (next: Clause) => void
  onClose: () => void
}) {
  const info = clause ? OPERATORS.find((each) => each.id === clause.op) : undefined
  const setArg = (name: string, value: string | number) =>
    clause && onChange({ ...clause, args: { ...(clause.args ?? {}), [name]: value } })
  return (
    <BottomSheet
      open={clause !== null}
      onOpenChange={(next) => !next && onClose()}
      title="How they compare"
    >
      {clause && (
        <div className="flex flex-col gap-3">
          <fieldset
            aria-label="Operator"
            className="m-0 min-w-0 border-0 p-0 flex flex-col gap-1.5"
          >
            {OPERATORS.map((each) => (
              <button
                key={each.id}
                type="button"
                aria-pressed={clause.op === each.id}
                onClick={() => clause.op !== each.id && onChange(withOperator(clause, each.id))}
                className={cn(
                  OUTLINED,
                  'justify-start',
                  clause.op === each.id &&
                    'border-primary bg-[color:var(--brand-tint)] font-bold text-primary'
                )}
              >
                {each.label}
              </button>
            ))}
          </fieldset>
          {info && info.args.length > 0 && (
            <div className="flex flex-col gap-2">
              {info.args.map((name) => {
                const value = clause.args?.[name]
                if (name === 'name') {
                  return (
                    <label key={name} className="flex flex-col gap-1 text-sm font-semibold">
                      Which one
                      <select
                        value={typeof value === 'string' ? value : ''}
                        onChange={(event) => setArg(name, event.target.value)}
                        className={INPUT}
                      >
                        {IS_NAMES.map((state) => (
                          <option key={state} value={state}>
                            {stateWords(state)}
                          </option>
                        ))}
                      </select>
                    </label>
                  )
                }
                const label =
                  {
                    pct: 'Within how many percent',
                    n: 'For how many candles',
                    low: 'From',
                    high: 'To',
                  }[name] ?? name
                return (
                  <label key={name} className="flex flex-col gap-1 text-sm font-semibold">
                    {label}
                    <input
                      type="text"
                      inputMode={name === 'n' ? 'numeric' : 'decimal'}
                      value={value === undefined ? '' : String(value)}
                      onChange={(event) =>
                        setArg(
                          name,
                          name === 'n'
                            ? Number(event.target.value.replace(/\D/g, '')) || 0
                            : event.target.value
                        )
                      }
                      className={INPUT}
                    />
                  </label>
                )
              })}
            </div>
          )}
          <button
            type="button"
            onClick={onClose}
            className={cn(OUTLINED, 'bg-primary text-primary-foreground hover:bg-primary/90')}
          >
            Done
          </button>
        </div>
      )}
    </BottomSheet>
  )
}
