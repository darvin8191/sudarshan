import { useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { cn } from '@/lib/utils'
import { toast } from '@/utils/toast'
import { BottomSheet } from './BottomSheet'
import {
  addBody,
  chipOf,
  type Draft,
  draftOf,
  editBody,
  expiriesFor,
  type FollowForm,
  fixedWords,
  num,
  offsetLabel,
  previewOf,
  STRIKE_CHOICES,
  titleOf,
} from './followRows'
import { ownId } from './ListStrip'
import { callOp, problemOf } from './v2/api'
import type { FollowRow, OptRight, View } from './v2/types'
import { viewKey } from './v2/useListView'

const BUTTON =
  'inline-flex min-h-12 flex-1 items-center justify-center rounded-[0.875rem] px-5 text-[0.9375rem] font-semibold outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50'
const CHOICE =
  'flex min-h-12 flex-1 items-center justify-center rounded-[0.6875rem] border px-3 text-sm font-semibold has-[:checked]:border-primary has-[:checked]:bg-[color:var(--primary-tint)] has-[:checked]:text-primary has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring'

/**
 * One row write, sent once with the version `view` answered. The list is read again whatever the
 * answer (after a conflict the list is the other screen's). Resolves to the sentence to show, or
 * null when it was done, and the server's words when it said anything.
 */
async function writeRow(
  client: ReturnType<typeof useQueryClient>,
  view: View,
  send: (version: string) => Promise<{ words: string | null }>
): Promise<{ said: string | null; words: string | null }> {
  let said: string | null = null
  let words: string | null = null
  try {
    words = (await send(view.version)).words
  } catch (error) {
    said = problemOf(error).message
  }
  await client.invalidateQueries({ queryKey: viewKey(view.ref) })
  return { said, words }
}

/** A sentence the server refused with, or nothing. */
function Problem({ text }: { text: string | null }) {
  return text ? (
    <p role="alert" className="text-sm font-medium text-[color:var(--down)]">
      {text}
    </p>
  ) : null
}

function Choices<T extends string>({
  legend,
  name,
  options,
  value,
  onChange,
}: {
  legend: string
  name: string
  options: ReadonlyArray<{ value: T; label: string }>
  value: T
  onChange: (next: T) => void
}) {
  return (
    <fieldset className="flex flex-col gap-1">
      <legend className="pb-1 text-xs font-semibold text-[color:var(--text-secondary)]">
        {legend}
      </legend>
      <div className="flex flex-wrap gap-2">
        {options.map((option) => (
          <label key={option.value} className={CHOICE}>
            <input
              type="radio"
              name={name}
              value={option.value}
              checked={option.value === value}
              onChange={() => onChange(option.value)}
              className="sr-only"
            />
            {option.label}
          </label>
        ))}
      </div>
    </fieldset>
  )
}

/** The form of a row: Expiry, Strike (ATM, ATM+1 to +10, ATM-1 to -10), Side, and what the row will follow. */
function Form({
  form,
  view,
  rule,
  onClose,
  onRemove,
}: {
  form: Extract<FollowForm, { mode: 'add' | 'edit' }>
  view: View
  rule: FollowRow | null
  onClose: () => void
  onRemove: (rowId: number) => void
}) {
  const client = useQueryClient()
  const kind = form.mode === 'add' ? form.kind : (rule?.kind ?? 'atm_option')
  const name = form.mode === 'add' ? form.underlying : (rule?.underlying ?? '')
  const fixed = form.mode === 'add' ? form.fixed : undefined // F12
  const [draft, setDraft] = useState<Draft>(() => draftOf(kind, rule ?? undefined, fixed))
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const sent = useRef(false)
  const option = kind !== 'month_future'
  const listId = ownId(view.ref)

  const submit = async () => {
    if (sent.current || listId === null) return
    sent.current = true
    setBusy(true)
    setProblem(null)
    const out =
      form.mode === 'add'
        ? await writeRow(client, view, (version) =>
            callOp('row_add', {
              list: listId,
              version,
              ...addBody(
                draft,
                { underlying: form.underlying, underlying_exchange: form.exchange },
                form.sectionId
              ),
            })
          )
        : await writeRow(client, view, (version) =>
            callOp('row_edit', { id: form.rowId, version, ...editBody(draft) })
          )
    setBusy(false)
    if (out.said) {
      sent.current = false
      setProblem(out.said)
      return
    }
    toast.success(
      out.words ??
        (form.mode !== 'add'
          ? 'Row changed.'
          : draft.strike !== null
            ? `Added a row on ${name} ${num(draft.strike)}.` // review M3: its contract is not picked from the price
            : `Added a row that follows ${name}. It picks its contract from ${name}'s price.`)
    )
    onClose()
  }

  const strikeOptions = STRIKE_CHOICES.map((offset) => ({
    offset,
    label: offsetLabel(offset),
  }))
  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(event) => {
        event.preventDefault()
        void submit()
      }}
    >
      <Choices
        legend="Expiry"
        name="follow-expiry"
        options={expiriesFor(kind).map((each) => ({ value: each.rule, label: each.label }))}
        value={draft.expiry}
        onChange={(expiry) => setDraft({ ...draft, expiry })}
      />
      {option && draft.strike !== null && (
        <p className="text-sm font-semibold" data-follow-fixed="true">
          {fixedWords(draft.strike)}
        </p>
      )}
      {option && draft.strike === null && (
        <label className="flex flex-col gap-1">
          <span className="text-xs font-semibold text-[color:var(--text-secondary)]">Strike</span>
          <select
            value={draft.offset}
            onChange={(event) => setDraft({ ...draft, offset: Number(event.target.value) })}
            className="min-h-12 rounded-[0.6875rem] border border-input bg-card px-3 text-base font-semibold"
          >
            {strikeOptions.map((each) => (
              <option key={each.offset} value={each.offset}>
                {each.label}
              </option>
            ))}
          </select>
        </label>
      )}
      {option && (
        <Choices<OptRight | 'both'>
          legend="Side"
          name="follow-side"
          options={[
            { value: 'CE', label: 'CE' },
            { value: 'PE', label: 'PE' },
            { value: 'both', label: 'Both, as one row' }, // F12
          ]}
          value={draft.kind === 'strike_pair' ? 'both' : draft.right}
          onChange={(side) =>
            setDraft(
              side === 'both'
                ? { ...draft, kind: 'strike_pair' }
                : { ...draft, kind: 'atm_option', right: side }
            )
          }
        />
      )}
      <p
        data-follow-preview="true"
        className="text-sm font-medium text-[color:var(--text-secondary)]"
      >
        {previewOf(name, draft, fixed?.expiries)}
      </p>
      <Problem text={problem} />
      <div className="flex gap-2">
        <button
          type="button"
          onClick={onClose}
          className={cn(BUTTON, 'border border-input bg-card hover:bg-accent')}
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={busy}
          className={cn(BUTTON, 'bg-primary text-primary-foreground hover:bg-primary/90')}
        >
          {form.mode === 'add' ? (busy ? 'Adding' : 'Add row') : busy ? 'Saving' : 'Save'}
        </button>
      </div>
      {form.mode === 'edit' && (
        <button
          type="button"
          onClick={() => onRemove(form.rowId)}
          className={cn(BUTTON, 'justify-start px-1 text-[color:var(--down)] hover:bg-accent')}
        >
          Remove this row
        </button>
      )}
    </form>
  )
}

/** Remove a row, after saying which one and from where. It sends once however it is tapped. */
function RemoveRow({ view, rule, onClose }: { view: View; rule: FollowRow; onClose: () => void }) {
  const client = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const sent = useRef(false)
  const remove = async () => {
    if (sent.current) return
    sent.current = true
    setBusy(true)
    const out = await writeRow(client, view, (version) =>
      callOp('row_remove', { id: rule.id, version })
    )
    setBusy(false)
    if (out.said) {
      sent.current = false
      setProblem(out.said)
      return
    }
    onClose()
  }
  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm font-medium">
        {`${titleOf(rule, null)} (${chipOf(rule)}) stops following ${rule.underlying} and leaves ${view.name}.`}
      </p>
      <Problem text={problem} />
      <div className="flex gap-2">
        <button
          type="button"
          onClick={onClose}
          className={cn(BUTTON, 'border border-input bg-card hover:bg-accent')}
        >
          Cancel
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => void remove()}
          className={cn(BUTTON, 'bg-destructive text-white hover:bg-destructive/90')}
        >
          {busy ? 'Removing' : 'Remove'}
        </button>
      </div>
    </div>
  )
}

/**
 * The sheet that adds, changes or removes a row that follows the price (F11). `form` says which; it
 * is opened from the symbol sheet of an underlying, from a row's own sheet (Edit, Remove) and, in
 * F13, from search, always as `{ mode: 'add', kind, underlying, exchange, sectionId }`. Every write
 * carries the list's version, and the list is read again after it.
 */
export function FollowRowSheet({
  form,
  view,
  onClose,
  onSwap,
}: {
  form: FollowForm | null
  view: View | undefined
  onClose: () => void
  onSwap: (next: FollowForm) => void
}) {
  if (!form || !view) return null
  const rule =
    form.mode === 'add' ? null : (view.follow.find((row) => row.id === form.rowId) ?? null)
  if (form.mode !== 'add' && !rule) return null
  const title =
    form.mode === 'add'
      ? form.kind === 'month_future'
        ? 'Add current-month FUT row'
        : form.fixed
          ? 'Add as one row' // F12
          : 'Add ATM row'
      : form.mode === 'edit'
        ? 'Change this row'
        : 'Remove this row'
  return (
    <BottomSheet
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
      title={title}
      description={
        form.mode === 'add' ? `Follows ${form.underlying}` : `Follows ${rule?.underlying}`
      }
    >
      {form.mode === 'remove' && rule ? (
        <RemoveRow view={view} rule={rule} onClose={onClose} />
      ) : form.mode !== 'remove' ? (
        <Form
          key={
            form.mode === 'add'
              ? `add-${form.kind}-${form.underlying}-${form.fixed?.strike ?? ''}`
              : `row-${form.rowId}`
          }
          form={form}
          view={view}
          rule={rule}
          onClose={onClose}
          onRemove={(rowId) => onSwap({ mode: 'remove', rowId })}
        />
      ) : null}
    </BottomSheet>
  )
}
