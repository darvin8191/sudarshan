// SUDARSHAN P7 F2 (M7.27): the owner's inputs to the tax working paper (design p7 9.1; owner
// decisions 5, 7, 8, 9): losses brought forward from his filed returns beside SUDARSHAN's own
// figure, the prices of 31 Jan 2018 of the old holdings the paper lists, days marked as delivery,
// and how each account counts. Nothing is saved before its dry run has said, in the service's
// sentences, what it changes in the year on screen; a changed field asks for the dry run again.
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronRight, Plus } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { deleteTaxInput, getTaxInputs, PortfolioError, saveTaxInput } from '../api'
import { dayOfInstant } from '../days'
import { Box } from '../sheets/EntryFields'
import { DANGER, FIELD, LABEL, PRIMARY, ProblemLine, SECONDARY } from '../sheets/TabFields'
import { useSend } from '../sheets/useSend'
import { dayText, priceText } from '../tab/format'
import { ACCOUNT_KIND_LABELS, CARRY_BUCKETS, type TaxInputs, type TaxPack } from '../types'
import { fyLabel, fyOfDay, rs } from './format'
import {
  BUCKET_LABELS,
  countedAccounts,
  type Draft,
  draftOf,
  fmvWanted,
  type InputKind,
  KIND_WORDS,
  MODE_WORDS,
  newDraft,
  ownFigure,
  rowOf,
} from './inputs'
import { TAX_KEY } from './keys'

const ROW =
  'flex min-h-12 w-full items-center gap-2 py-1.5 text-left outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50'
const ADD = `${SECONDARY} w-full text-[color:var(--brand)]`

function Choice({
  label,
  value,
  onChange,
  options,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  options: readonly { value: string; label: string }[]
}) {
  return (
    <label className="flex min-w-0 flex-col gap-1.5">
      <span className={LABEL}>{label}</span>
      <select className={FIELD} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">Choose</option>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  )
}

function Form({
  draft,
  inputs,
  fy,
  onDone,
  onBack,
}: {
  draft: Draft
  inputs: TaxInputs
  fy: number
  onDone: () => void
  onBack: () => void
}) {
  const [fields, setFields] = useState(draft.fields)
  const [effect, setEffect] = useState<string[] | null>(null)
  const [removing, setRemoving] = useState(false)
  const send = useSend()
  const requestId = useRef(crypto.randomUUID())
  const set = (name: string) => (value: string) => {
    setFields((was) => ({ ...was, [name]: value }))
    setEffect(null) // the dry run spoke of other values
    send.clear()
  }
  const args = {
    kind: draft.kind,
    row: rowOf({ ...draft, fields }, inputs.member_id),
    id: draft.id,
    version: draft.version,
  }
  const check = () =>
    send.run(async () => {
      const result = await saveTaxInput({ ...args, dry_run: true, fy })
      if (result.enabled) setEffect(result.data.effect)
    })
  const save = async () => {
    if (await send.run(() => saveTaxInput({ ...args, request_id: requestId.current }))) onDone()
  }
  const remove = async () => {
    const { id, version } = draft
    if (id === undefined || version === undefined) return
    if (await send.run(() => deleteTaxInput({ id, version, kind: draft.kind }))) onDone()
  }
  const running = fyOfDay(dayOfInstant(Date.now()))
  return (
    <div className="flex flex-col gap-3">
      <h3 className="text-base font-bold">{KIND_WORDS[draft.kind].title}</h3>
      {draft.kind === 'carry' && (
        <>
          <Choice
            label="Financial year of the loss"
            value={fields.loss_fy ?? ''}
            onChange={set('loss_fy')}
            options={Array.from({ length: 8 }, (_, n) => running - 1 - n).map((year) => ({
              value: String(year),
              label: fyLabel(year),
            }))}
          />
          <Choice
            label="Kind of loss"
            value={fields.bucket ?? ''}
            onChange={set('bucket')}
            options={CARRY_BUCKETS.map((bucket) => ({
              value: bucket,
              label: BUCKET_LABELS[bucket],
            }))}
          />
          <Box
            label="Loss carried forward, in rupees"
            value={fields.amount ?? ''}
            onChange={set('amount')}
            hint="As your filed return for that year shows it."
          />
        </>
      )}
      {draft.kind === 'day_override' && (
        <Choice
          label="Account"
          value={fields.account_id ?? ''}
          onChange={set('account_id')}
          options={countedAccounts(inputs).map((a) => ({ value: String(a.id), label: a.name }))}
        />
      )}
      {draft.kind !== 'carry' && (
        <Box
          label="ISIN of the share"
          value={fields.isin ?? ''}
          onChange={set('isin')}
          inputMode="text"
          hint="12 letters and digits, such as INE002A01018."
        />
      )}
      {draft.kind === 'fmv' && (
        <Box
          label="Highest price on 31 January 2018, in rupees"
          value={fields.price ?? ''}
          onChange={set('price')}
          hint="For one share, not adjusted for a later split or bonus."
        />
      )}
      {draft.kind === 'day_override' && (
        <Box
          label="Day to count as delivery"
          type="date"
          value={fields.day ?? ''}
          onChange={set('day')}
        />
      )}
      <ProblemLine problem={send.problem} />
      {effect !== null && (
        <section
          aria-label="What this changes"
          className="rounded-[0.875rem] border border-[color:var(--brand)] bg-[color:var(--brand-tint)] px-3 py-2"
        >
          <h4 className="text-sm font-bold">What this changes</h4>
          {effect.map((line) => (
            <p key={line} className="text-sm">
              {line}
            </p>
          ))}
          <p className="pt-1 text-xs text-[color:var(--text-secondary)]">
            Nothing is saved until you tap Save.
          </p>
        </section>
      )}
      {effect === null ? (
        <button
          type="button"
          className={PRIMARY}
          disabled={send.pending}
          onClick={() => void check()}
        >
          {send.pending ? 'Working it out' : 'See what it changes'}
        </button>
      ) : (
        <button
          type="button"
          className={PRIMARY}
          disabled={send.pending}
          onClick={() => void save()}
        >
          {send.pending ? 'Saving' : 'Save'}
        </button>
      )}
      <button type="button" className={SECONDARY} disabled={send.pending} onClick={onBack}>
        Back to the list
      </button>
      {draft.id !== undefined &&
        (removing ? (
          <button
            type="button"
            className={DANGER}
            disabled={send.pending}
            onClick={() => void remove()}
          >
            Yes, remove this entry
          </button>
        ) : (
          <button type="button" className={SECONDARY} onClick={() => setRemoving(true)}>
            Remove this entry
          </button>
        ))}
    </div>
  )
}

function Group({
  kind,
  children,
  onAdd,
  empty,
}: {
  kind: InputKind
  children: React.ReactNode
  onAdd: () => void
  empty: boolean
}) {
  return (
    <section aria-label={KIND_WORDS[kind].title} className="flex flex-col gap-1">
      <h3 className="text-base font-bold">{KIND_WORDS[kind].title}</h3>
      {empty && (
        <p className="text-sm text-[color:var(--text-secondary)]">{KIND_WORDS[kind].none}</p>
      )}
      <ul className="flex flex-col divide-y divide-[color:var(--divider)]">{children}</ul>
      <button type="button" className={ADD} onClick={onAdd}>
        <Plus aria-hidden="true" className="size-4" />
        {KIND_WORDS[kind].add}
      </button>
    </section>
  )
}

function Item({
  title,
  sub,
  figure,
  onOpen,
}: {
  title: string
  sub?: string | null
  figure?: string
  onOpen?: () => void
}) {
  const body = (
    <>
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="text-[0.9375rem] font-semibold break-words">{title}</span>
        {sub && <span className="text-xs text-[color:var(--text-tertiary)]">{sub}</span>}
      </span>
      {figure && <span className="shrink-0 text-[0.9375rem] whitespace-nowrap">{figure}</span>}
      {onOpen && <ChevronRight aria-hidden="true" className="size-4 shrink-0" />}
    </>
  )
  return (
    <li>
      {onOpen ? (
        <button type="button" className={ROW} onClick={onOpen}>
          {body}
        </button>
      ) : (
        <div className={ROW}>{body}</div>
      )}
    </li>
  )
}

function Lists({
  inputs,
  pack,
  onDraft,
}: {
  inputs: TaxInputs
  pack: TaxPack | undefined
  onDraft: (draft: Draft) => void
}) {
  const mine = inputs.carries.filter((row) => row.source === 'owner')
  const computed = inputs.carries.filter((row) => row.source === 'computed')
  const wanted = fmvWanted(pack, inputs)
  const names = new Map(inputs.accounts.map((a) => [a.id, a.name]))
  return (
    <div className="flex flex-col gap-5">
      <Group kind="carry" empty={mine.length === 0} onAdd={() => onDraft(newDraft('carry'))}>
        {mine.map((row) => {
          const own = ownFigure(pack, row)
          return (
            <Item
              key={row.id}
              title={`${BUCKET_LABELS[row.bucket]} of ${fyLabel(row.loss_fy)}`}
              sub={
                own
                  ? `Your figure is used. SUDARSHAN’s own figure: ${rs(own.paise)}`
                  : 'Your figure'
              }
              figure={rs(row.paise)}
              onOpen={() => onDraft(draftOf('carry', row))}
            />
          )
        })}
        {computed.map((row) => (
          <Item
            key={row.id}
            title={`${BUCKET_LABELS[row.bucket]} of ${fyLabel(row.loss_fy)}`}
            sub="SUDARSHAN’s own figure, from the years it can read"
            figure={rs(row.paise)}
          />
        ))}
      </Group>
      <Group
        kind="fmv"
        empty={inputs.fmvs.length === 0 && wanted.length === 0}
        onAdd={() => onDraft(newDraft('fmv'))}
      >
        {wanted.map((isin) => (
          <Item
            key={isin}
            title={isin}
            sub="A sale waits for this price"
            figure="Add"
            onOpen={() => onDraft(newDraft('fmv', { isin }))}
          />
        ))}
        {inputs.fmvs.map((row) => (
          <Item
            key={row.id}
            title={row.isin}
            figure={priceText(row.fmv_u)}
            onOpen={() => onDraft(draftOf('fmv', row))}
          />
        ))}
      </Group>
      <Group
        kind="day_override"
        empty={inputs.day_overrides.length === 0}
        onAdd={() => onDraft(newDraft('day_override'))}
      >
        {inputs.day_overrides.map((row) => (
          <Item
            key={row.id}
            title={`${row.isin} on ${dayText(row.day)}`}
            sub={names.get(row.account_id) ?? null}
            onOpen={() => onDraft(draftOf('day_override', row))}
          />
        ))}
      </Group>
      <section aria-label="Accounts" className="flex flex-col gap-1">
        <h3 className="text-base font-bold">Accounts</h3>
        <ul className="flex flex-col divide-y divide-[color:var(--divider)]">
          {inputs.accounts.map((account) => (
            <Item
              key={account.id}
              title={account.name}
              sub={`${ACCOUNT_KIND_LABELS[account.kind]} · ${MODE_WORDS[account.tax_mode]}`}
            />
          ))}
        </ul>
        <p className="text-xs text-[color:var(--text-tertiary)]">
          How an account counts cannot be changed on this screen yet.
        </p>
      </section>
    </div>
  )
}

export function InputsSheet({
  open,
  onClose,
  pack,
  member,
  fy,
}: {
  open: boolean
  onClose: () => void
  pack: TaxPack | undefined
  member: number | undefined
  fy: number
}) {
  const queries = useQueryClient()
  const [draft, setDraft] = useState<Draft | null>(null)
  const inputs = useQuery({
    queryKey: [...TAX_KEY, 'inputs', member ?? null] as const,
    queryFn: async ({ signal }) => {
      const result = await getTaxInputs({ member }, signal)
      if (!result.enabled) {
        throw new PortfolioError({ code: 'unavailable', message: result.message, entryId: null })
      }
      return result.data
    },
    enabled: open,
    retry: false,
    refetchOnWindowFocus: false,
  })
  useEffect(() => {
    if (!open) setDraft(null)
  }, [open])
  return (
    <BottomSheet
      open={open}
      onOpenChange={(next) => !next && onClose()}
      title="Inputs"
      description="For the working paper. Verify with your CA."
      step={draft === null ? 'list' : `form-${draft.kind}-${draft.id ?? 'new'}`}
    >
      {inputs.error ? (
        <ProblemLine problem={inputs.error as PortfolioError} />
      ) : inputs.data === undefined ? (
        <p className="py-4 text-sm text-[color:var(--text-secondary)]">Reading your inputs.</p>
      ) : draft === null ? (
        <Lists inputs={inputs.data} pack={pack} onDraft={setDraft} />
      ) : (
        <Form
          key={`${draft.kind}-${draft.id ?? 'new'}`}
          draft={draft}
          inputs={inputs.data}
          fy={fy}
          onBack={() => setDraft(null)}
          onDone={() => {
            setDraft(null)
            // The inputs and every year's paper rest on what was just changed.
            void queries.invalidateQueries({ queryKey: TAX_KEY })
          }}
        />
      )}
    </BottomSheet>
  )
}
