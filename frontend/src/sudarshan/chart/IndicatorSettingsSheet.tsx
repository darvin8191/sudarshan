/**
 * One study's settings on the phone chart (M4.2, C4): the engine's own inputs
 * for it, drawn as a phone form in a sheet. Every control is 48 dp tall, a
 * number opens the number pad and is checked as it is committed with the
 * terminal's own rules (`numberProblem`), and a control that only means
 * something beside another shows only then (`conditionHolds`). A change
 * applies at once, to every instrument; colours stay with the full terminal.
 */
import { useEffect, useId, useState } from 'react'
import { Switch } from '@/components/ui/switch'
import { conditionHolds } from '@/lib/trading/inputConditions'
import { numberProblem } from '@/lib/trading/inputValidation'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import type { StudyField, StudyForm } from './phoneStudies'

const BOX =
  'h-12 w-full rounded-xl border border-[color:var(--border-strong)] bg-card px-3 text-[0.9375rem] font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'

export function IndicatorSettingsSheet({
  open,
  onOpenChange,
  form,
  intervals,
  onChange,
  onDefaults,
}: {
  open: boolean
  onOpenChange(open: boolean): void
  /** Null while the indicator tier loads, or for a study the server no longer has. */
  form: StudyForm | null
  /** The broker's timeframes, for a study's own timeframe choice. */
  intervals: ReadonlyArray<{ label: string; value: string }>
  onChange(patch: Record<string, unknown>): void
  onDefaults(): void
}) {
  const fields =
    form?.fields.filter(
      (field) => !field.visibleWhen || conditionHolds(field.visibleWhen as never, form.values)
    ) ?? []
  return (
    <BottomSheet
      open={open}
      onOpenChange={onOpenChange}
      title={form?.name ?? 'Indicator settings'}
      description="For every instrument on this phone"
    >
      {form === null ? (
        <p className="py-3 text-sm text-muted-foreground">Reading this indicator's settings</p>
      ) : (
        <div className="flex flex-col gap-3">
          {fields.map((field) => (
            <Field
              key={field.key}
              field={field}
              value={form.values[field.key]}
              intervals={intervals}
              onCommit={(value) => onChange({ [field.key]: value })}
            />
          ))}
          {fields.length === 0 && (
            <p className="text-sm text-muted-foreground">
              This indicator has no settings to change.
            </p>
          )}
        </div>
      )}
      <p className="pt-3 text-xs text-muted-foreground">
        Colours and line styles are in the full terminal.
      </p>
      <button
        type="button"
        onClick={onDefaults}
        className="mt-1 inline-flex h-12 items-center rounded-xl px-3 text-sm font-semibold text-primary hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        Back to defaults
      </button>
    </BottomSheet>
  )
}

function Field({
  field,
  value,
  intervals,
  onCommit,
}: {
  field: StudyField
  value: unknown
  intervals: ReadonlyArray<{ label: string; value: string }>
  onCommit(value: unknown): void
}) {
  const id = useId()
  const [draft, setDraft] = useState(String(value ?? ''))
  const [problem, setProblem] = useState<string | null>(null)
  // A reset to defaults, or the value applied, is what the box shows next.
  useEffect(() => {
    setDraft(String(value ?? ''))
    setProblem(null)
  }, [value])

  if (field.type === 'boolean')
    return (
      <label className="flex min-h-12 items-center justify-between gap-3 text-[0.9375rem] font-semibold">
        {field.label}
        <Switch checked={value === true} onCheckedChange={(on) => onCommit(on)} />
      </label>
    )

  const label = (
    <label htmlFor={id} className="text-sm font-semibold text-[color:var(--text-secondary)]">
      {field.label}
    </label>
  )
  if (field.type === 'select' || field.type === 'interval') {
    const options =
      field.type === 'interval'
        ? [{ label: 'Chart timeframe', value: '' }, ...intervals]
        : field.options
    return (
      <div className="flex flex-col gap-1">
        {label}
        <select
          id={id}
          value={String(value ?? '')}
          onChange={(e) => onCommit(e.target.value)}
          className={BOX}
        >
          {(options ?? []).map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </div>
    )
  }

  const number = field.type === 'number'
  const commit = () => {
    if (draft === String(value ?? '')) return
    const found = number ? numberProblem(field, draft) : null
    setProblem(found)
    if (!found) onCommit(number ? Number(draft) : draft)
  }
  return (
    <div className="flex flex-col gap-1">
      {label}
      <input
        id={id}
        type="text"
        inputMode={number ? 'decimal' : 'text'}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') commit()
        }}
        aria-invalid={problem !== null}
        aria-describedby={problem ? `${id}-problem` : undefined}
        className={BOX}
      />
      {problem && (
        <p id={`${id}-problem`} className="text-xs font-semibold text-[color:var(--loss)]">
          {problem}
        </p>
      )}
      {field.tooltip && !problem && (
        <p className="text-xs text-muted-foreground">{field.tooltip}</p>
      )}
    </div>
  )
}
