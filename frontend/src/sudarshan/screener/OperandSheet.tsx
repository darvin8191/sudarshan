import { Search } from 'lucide-react'
import { type ReactNode, useId, useState } from 'react'
import { cn } from '@/lib/utils'
import { ACTION, OUTLINED } from '@/sudarshan/books/cards/parts'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { INDICATOR_BY_ID, OPTION_BY_ID, type ParamInfo } from './catalog'
import {
  AGO_WORDS,
  type Item,
  itemsOf,
  NUMBER_WORDS,
  OPERAND_TABS,
  type OperandTab,
  offsetRefusal,
  operandProblems,
  type Pick,
  pickOf,
  rangeWords,
  readsCandles,
  searchItems,
  starting,
  tabOfPick,
  timeframesOf,
} from './operandForm'
import type { IndOperand, Offset, OiOperand, Operand, OptOperand, TimeFrame } from './types'
import { offsetWords, operandChips, tfWords } from './words'

const INPUT =
  'min-h-12 w-full rounded-xl border border-input bg-card px-3 text-[0.9375rem] font-semibold outline-none focus-visible:ring-2 focus-visible:ring-ring'
const CHOICE = (on: boolean) =>
  cn(OUTLINED, on && 'border-primary bg-[color:var(--brand-tint)] font-bold text-primary')

/** The number a typed operand can be: digits with an optional sign and decimals. */
export const isNumber = (text: string): boolean => /^-?\d+(\.\d+)?$/.test(text.trim())

/** A labelled input with a line under it: the label alone is the input's name, the line its description. */
function Field({
  label,
  hint,
  bad = false,
  children,
}: {
  label: string
  hint?: string
  bad?: boolean
  children: (props: {
    id: string
    'aria-describedby'?: string
    'aria-invalid': boolean
  }) => ReactNode
}) {
  const id = useId()
  const hintId = `${id}-hint`
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-sm font-semibold">
        {label}
      </label>
      {children({ id, 'aria-describedby': hint ? hintId : undefined, 'aria-invalid': bad })}
      {hint && (
        <span
          id={hintId}
          className={cn(
            'text-[0.8125rem]',
            bad
              ? 'font-semibold text-destructive'
              : 'font-medium text-[color:var(--text-secondary)]'
          )}
        >
          {hint}
        </span>
      )}
    </div>
  )
}

const humanise = (name: string) => {
  const spaced = name.replace(/_/g, ' ')
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}

/**
 * The operand's sheet (design 5.2): tabs Price, Indicators, Volume, OI & options and Number, a
 * search box over all of them, and for the item chosen its parameters at their defaults, the
 * timeframe chips and the offset chips (Latest candle, N candles ago, Today's first candle,
 * Yesterday's last candle). Nothing applies until "Use this"; closing the sheet keeps what the
 * condition had.
 */
export function OperandSheet({
  operand,
  runTf,
  onApply,
  onClose,
}: {
  operand: Operand
  /** The screen's own candles: a new operand starts on them. */
  runTf: TimeFrame
  onApply: (next: Operand) => void
  onClose: () => void
}) {
  const [draft, setDraft] = useState<Operand>(operand)
  const [choosing, setChoosing] = useState(false)
  const [tab, setTab] = useState<OperandTab>(() => tabOfPick(pickOf(operand)))
  const [text, setText] = useState('')
  const searchId = useId()
  const found = searchItems(text)
  const items = text.trim() ? found : itemsOf(tab)

  const choose = (item: Item) => {
    setDraft(starting(item.pick, runTf))
    setChoosing(false)
    setText('')
  }

  return (
    <BottomSheet open onOpenChange={(next) => !next && onClose()} title="Pick a value">
      {choosing ? (
        <div className="flex flex-col gap-3">
          <label htmlFor={searchId} className="relative block">
            <span className="sr-only">Search</span>
            <Search
              aria-hidden="true"
              className="pointer-events-none absolute top-3.5 left-3 size-5 text-[color:var(--text-tertiary)]"
            />
            <input
              id={searchId}
              type="search"
              value={text}
              onChange={(event) => setText(event.target.value)}
              placeholder="Search"
              className={cn(INPUT, 'pl-10')}
            />
          </label>
          {!text.trim() && (
            <div role="tablist" aria-label="Kinds of value" className="flex flex-wrap gap-1.5">
              {OPERAND_TABS.map(({ id, label }) => (
                <button
                  key={id}
                  type="button"
                  role="tab"
                  aria-selected={id === tab}
                  onClick={() => setTab(id)}
                  className={CHOICE(id === tab)}
                >
                  {label}
                </button>
              ))}
            </div>
          )}
          <ul className="flex flex-col gap-1.5">
            {items.map((item) => (
              <li key={item.key}>
                <button
                  type="button"
                  onClick={() => choose(item)}
                  className={cn(OUTLINED, 'w-full justify-start')}
                >
                  {item.label}
                </button>
              </li>
            ))}
            {text.trim() && items.length === 0 && (
              <li className="px-1 text-sm font-medium text-[color:var(--text-secondary)]">
                Nothing is called that. Try the first letters of an indicator, or a price.
              </li>
            )}
          </ul>
          <button type="button" onClick={() => setChoosing(false)} className={OUTLINED}>
            Back to this value
          </button>
        </div>
      ) : (
        <Editor
          draft={draft}
          onChange={setDraft}
          onChoose={() => setChoosing(true)}
          onApply={() => onApply(draft)}
        />
      )}
    </BottomSheet>
  )
}

function Editor({
  draft,
  onChange,
  onChoose,
  onApply,
}: {
  draft: Operand
  onChange: (next: Operand) => void
  onChoose: () => void
  onApply: () => void
}) {
  const pick = pickOf(draft)
  const chips = operandChips(draft)
  const problems = operandProblems(draft)
  const numberBad = 'num' in draft && !isNumber(draft.num)
  const blocked = problems.size > 0 || numberBad
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-2 rounded-xl bg-[color:var(--surface-2)] px-3 py-2">
        <p className="min-w-0 text-base font-bold">{chips.text}</p>
        <button type="button" onClick={onChoose} className={OUTLINED}>
          Change
        </button>
      </div>

      {'math' in draft && (
        <p className="text-sm font-medium text-[color:var(--text-secondary)]">
          This is a calculation from a ready-made screen. Change it to a single value to edit it.
        </p>
      )}

      {'num' in draft && (
        <Field label="Number" hint={numberBad ? NUMBER_WORDS : undefined} bad={numberBad}>
          {(props) => (
            <input
              {...props}
              type="text"
              inputMode="decimal"
              value={draft.num}
              onChange={(event) => onChange({ num: event.target.value })}
              className={INPUT}
            />
          )}
        </Field>
      )}

      {'ind' in draft && <Params draft={draft} onChange={onChange} problems={problems} />}

      {'opt' in draft && <Option draft={draft} onChange={onChange} />}
      {'oi' in draft && <OpenInterest draft={draft} onChange={onChange} />}

      {readsCandles(pick) && <Candles pick={pick} draft={draft as Candled} onChange={onChange} />}

      <button
        type="button"
        disabled={blocked}
        onClick={onApply}
        className={cn(
          ACTION,
          'bg-primary text-primary-foreground hover:bg-primary/90 disabled:bg-muted disabled:text-[color:var(--text-tertiary)]'
        )}
      >
        Use this
      </button>
    </div>
  )
}

function Params({
  draft,
  onChange,
  problems,
}: {
  draft: IndOperand
  onChange: (next: Operand) => void
  problems: Map<string, string>
}) {
  const info = INDICATOR_BY_ID.get(draft.ind)
  if (!info) return null
  const set = (name: string, value: number | string) =>
    onChange({ ...draft, p: { ...(draft.p ?? {}), [name]: value } })
  const out = draft.out ?? info.outs[0]
  return (
    <div className="flex flex-col gap-3">
      {info.params.map((param: ParamInfo) => {
        const value = draft.p?.[param.name] ?? param.default
        if (param.kind === 'choice') {
          return (
            <fieldset key={param.name} className="m-0 flex min-w-0 flex-col gap-1.5 border-0 p-0">
              <legend className="pb-1 text-sm font-semibold">{humanise(param.name)}</legend>
              <div className="flex flex-wrap gap-1.5">
                {param.choices.map((choice) => (
                  <button
                    key={choice}
                    type="button"
                    aria-pressed={String(value) === choice}
                    onClick={() => set(param.name, choice)}
                    className={CHOICE(String(value) === choice)}
                  >
                    {humanise(choice)}
                  </button>
                ))}
              </div>
            </fieldset>
          )
        }
        return (
          <Field
            key={param.name}
            label={humanise(param.name)}
            hint={problems.get(param.name) ?? `Starts at ${param.default}; ${rangeWords(param)}.`}
            bad={problems.has(param.name)}
          >
            {(props) => (
              <input
                {...props}
                type="text"
                inputMode={param.kind === 'int' ? 'numeric' : 'decimal'}
                value={String(value)}
                onChange={(event) => set(param.name, event.target.value)}
                className={INPUT}
              />
            )}
          </Field>
        )
      })}
      {info.outs.length > 1 && (
        <fieldset className="m-0 flex min-w-0 flex-col gap-1.5 border-0 p-0">
          <legend className="pb-1 text-sm font-semibold">Which line</legend>
          <div className="flex flex-wrap gap-1.5">
            {info.outs.map((each) => (
              <button
                key={each}
                type="button"
                aria-pressed={out === each}
                onClick={() => onChange({ ...draft, out: each })}
                className={CHOICE(out === each)}
              >
                {humanise(each)}
              </button>
            ))}
          </div>
        </fieldset>
      )}
    </div>
  )
}

function Option({ draft, onChange }: { draft: OptOperand; onChange: (next: Operand) => void }) {
  const info = OPTION_BY_ID.get(draft.opt)
  if (!info) return null
  return (
    <>
      {info.underlying && (
        <fieldset className="m-0 flex min-w-0 flex-col gap-1.5 border-0 p-0">
          <legend className="pb-1 text-sm font-semibold">Of which</legend>
          <div className="flex flex-wrap gap-1.5">
            {[
              ['NIFTY', 'NIFTY'],
              ['BANKNIFTY', 'BANKNIFTY'],
              ['self', "The row's own underlying"],
            ].map(([id, label]) => (
              <button
                key={id}
                type="button"
                aria-pressed={draft.underlying === id}
                onClick={() => onChange({ ...draft, underlying: id })}
                className={CHOICE(draft.underlying === id)}
              >
                {label}
              </button>
            ))}
          </div>
        </fieldset>
      )}
      {info.timed && (
        <TimeFrames
          value={(draft as { tf?: TimeFrame }).tf}
          options={timeframesOf({ kind: 'opt', id: draft.opt })}
          onPick={(tf) => onChange({ ...draft, tf })}
        />
      )}
    </>
  )
}

function OpenInterest({
  draft,
  onChange,
}: {
  draft: OiOperand
  onChange: (next: Operand) => void
}) {
  const candles = draft.window.kind === 'candles'
  return (
    <>
      <fieldset className="m-0 flex min-w-0 flex-col gap-1.5 border-0 p-0">
        <legend className="pb-1 text-sm font-semibold">Over</legend>
        <div className="flex flex-wrap gap-1.5">
          <button
            type="button"
            aria-pressed={!candles}
            onClick={() => onChange({ ...draft, window: { kind: 'day' }, tf: undefined })}
            className={CHOICE(!candles)}
          >
            Today
          </button>
          <button
            type="button"
            aria-pressed={candles}
            onClick={() =>
              onChange({
                ...draft,
                window: { kind: 'candles', n: 3 },
                tf: (draft as { tf?: TimeFrame }).tf ?? 15,
              })
            }
            className={CHOICE(candles)}
          >
            The last few candles
          </button>
        </div>
      </fieldset>
      {draft.window.kind === 'candles' && (
        <>
          <Field label="How many candles">
            {(props) => (
              <input
                {...props}
                type="text"
                inputMode="numeric"
                value={String(draft.window.kind === 'candles' ? draft.window.n : '')}
                onChange={(event) =>
                  onChange({
                    ...draft,
                    window: {
                      kind: 'candles',
                      n: Number(event.target.value.replace(/\D/g, '')) || 0,
                    },
                  })
                }
                className={INPUT}
              />
            )}
          </Field>
          <TimeFrames
            value={(draft as { tf?: TimeFrame }).tf}
            options={[1, 3, 5, 15, 30, 60]}
            onPick={(tf) => onChange({ ...draft, tf })}
          />
        </>
      )}
    </>
  )
}

function TimeFrames({
  value,
  options,
  onPick,
}: {
  value: TimeFrame | undefined
  options: TimeFrame[]
  onPick: (tf: TimeFrame) => void
}) {
  return (
    <fieldset className="m-0 flex min-w-0 flex-col gap-1.5 border-0 p-0">
      <legend className="pb-1 text-sm font-semibold">Candles</legend>
      <div className="flex flex-wrap gap-1.5">
        {options.map((tf) => (
          <button
            key={String(tf)}
            type="button"
            aria-pressed={value === tf}
            onClick={() => onPick(tf)}
            className={CHOICE(value === tf)}
          >
            {tfWords(tf)}
          </button>
        ))}
      </div>
    </fieldset>
  )
}

type Candled = Extract<Operand, { field: string } | { ind: string }>

function Candles({
  pick,
  draft,
  onChange,
}: {
  pick: Pick
  draft: Candled
  onChange: (next: Operand) => void
}) {
  const at: Offset = draft.at ?? 0
  const ago = typeof at === 'object' ? at.ago : null
  const minute = typeof draft.tf === 'number'
  const set = (next: Offset) => onChange({ ...draft, at: next })
  return (
    <>
      <TimeFrames
        value={draft.tf}
        options={timeframesOf(pick)}
        onPick={(tf) => {
          // "Today's first candle" and "Yesterday's last candle" are for minute candles; a day or
          // week candle drops them.
          const keepAt = offsetRefusal(at, tf) ? 0 : at
          onChange({ ...draft, tf, at: keepAt })
        }}
      />
      <fieldset className="m-0 flex min-w-0 flex-col gap-1.5 border-0 p-0">
        <legend className="pb-1 text-sm font-semibold">Which candle</legend>
        <div className="flex flex-wrap gap-1.5">
          <button
            type="button"
            aria-pressed={at === 0}
            onClick={() => set(0)}
            className={CHOICE(at === 0)}
          >
            {offsetWords(0)}
          </button>
          <button
            type="button"
            aria-pressed={ago !== null}
            onClick={() => set({ ago: ago ?? 1 })}
            className={CHOICE(ago !== null)}
          >
            Some candles ago
          </button>
          <button
            type="button"
            aria-pressed={at === 'first_today'}
            disabled={!minute}
            onClick={() => set('first_today')}
            className={CHOICE(at === 'first_today')}
          >
            {offsetWords('first_today')}
          </button>
          <button
            type="button"
            aria-pressed={at === 'last_prev_day'}
            disabled={!minute}
            onClick={() => set('last_prev_day')}
            className={CHOICE(at === 'last_prev_day')}
          >
            {offsetWords('last_prev_day')}
          </button>
        </div>
        {!minute &&
          (['first_today', 'last_prev_day'] as const).map((each) => (
            <p
              key={each}
              className="text-[0.8125rem] font-medium text-[color:var(--text-secondary)]"
            >
              {offsetRefusal(each, draft.tf)}
            </p>
          ))}
        {ago !== null && (
          <Field
            label="How many candles ago"
            hint={ago > 100 ? AGO_WORDS : 'From 1 to 100 candles ago.'}
            bad={ago < 1 || ago > 100}
          >
            {(props) => (
              <input
                {...props}
                type="text"
                inputMode="numeric"
                value={String(ago)}
                onChange={(event) =>
                  set({ ago: Number(event.target.value.replace(/\D/g, '')) || 0 })
                }
                className={INPUT}
              />
            )}
          </Field>
        )}
      </fieldset>
    </>
  )
}
