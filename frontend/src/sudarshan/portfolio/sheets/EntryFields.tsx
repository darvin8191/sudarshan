// SUDARSHAN M7.21: the fields of the Add sheet that more than one kind of entry uses: account
// chips (with "+ New account"), a money or quantity box, the charges with their breakdown.
import { Plus } from 'lucide-react'
import { useRef, useState } from 'react'
import { cn } from '@/lib/utils'
import { portfolioCall } from '../api'
import {
  ACCOUNT_KIND_LABELS,
  ACCOUNT_KINDS,
  type Account,
  type AccountKind,
  CHARGE_PARTS,
  type ChargePart,
  type Member,
} from '../types'
import type { Form } from './entryForm'
import { CHOICE, FIELD, LABEL, OFF, ON, PRIMARY, ProblemLine, SECONDARY } from './TabFields'
import { useSend } from './useSend'

export const PART_LABELS: Record<ChargePart, string> = {
  brokerage: 'Brokerage',
  stt: 'STT',
  exchange: 'Exchange charges',
  sebi: 'SEBI fee',
  stamp: 'Stamp duty',
  gst: 'GST',
  dp: 'DP charges',
  other: 'Other',
}

export function Box({
  label,
  value,
  onChange,
  hint,
  inputMode = 'decimal',
  disabled,
  type = 'text',
}: {
  label: string
  value: string
  onChange: (value: string) => void
  hint?: string
  inputMode?: 'decimal' | 'numeric' | 'text'
  disabled?: boolean
  type?: 'text' | 'date'
}) {
  return (
    <label className="flex min-w-0 flex-col gap-1.5">
      <span className={LABEL}>{label}</span>
      <input
        type={type}
        className={FIELD}
        inputMode={type === 'date' ? undefined : inputMode}
        autoComplete="off"
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      />
      {hint && <span className="text-xs text-[color:var(--text-tertiary)]">{hint}</span>}
    </label>
  )
}

/** The accounts as chips, the last one used selected, and a way to make another. */
export function AccountChoice({
  label,
  accounts,
  members,
  value,
  onChange,
  outside,
  outsideLabel,
  onOutside,
  onCreated,
  disabled,
}: {
  label: string
  accounts: readonly Account[]
  members: readonly Member[]
  value: number | null
  onChange: (id: number) => void
  outside?: boolean
  outsideLabel?: string
  onOutside?: () => void
  onCreated: (account: Account) => void
  disabled?: boolean
}) {
  const [making, setMaking] = useState(false)
  const own = accounts.filter((a) => !a.system)
  return (
    <fieldset className="flex flex-col gap-1.5" disabled={disabled}>
      <legend className={LABEL}>{label}</legend>
      <div className="flex flex-wrap gap-2">
        {own.map((a) => (
          <button
            key={a.id}
            type="button"
            aria-pressed={!outside && value === a.id}
            onClick={() => onChange(a.id)}
            className={cn(CHOICE, !outside && value === a.id ? ON : OFF)}
          >
            {a.name}
          </button>
        ))}
        {onOutside && (
          <button
            type="button"
            aria-pressed={outside === true}
            onClick={onOutside}
            className={cn(CHOICE, outside ? ON : OFF)}
          >
            {outsideLabel}
          </button>
        )}
        <button type="button" onClick={() => setMaking((v) => !v)} className={cn(CHOICE, OFF)}>
          <Plus aria-hidden="true" className="size-4" />
          New account
        </button>
      </div>
      {own.length === 0 && !making && (
        <p className="text-xs text-[color:var(--text-tertiary)]">
          Add an account, for example the demat account that holds it.
        </p>
      )}
      {making && (
        <NewAccount
          members={members}
          onCancel={() => setMaking(false)}
          onMade={(account) => {
            setMaking(false)
            onCreated(account)
          }}
        />
      )}
    </fieldset>
  )
}

function NewAccount({
  members,
  onMade,
  onCancel,
}: {
  members: readonly Member[]
  onMade: (account: Account) => void
  onCancel: () => void
}) {
  const [name, setName] = useState('')
  const [kind, setKind] = useState<AccountKind>('demat')
  const [member, setMember] = useState<number | ''>(members.length === 1 ? members[0].id : '')
  const requestId = useRef(crypto.randomUUID())
  const send = useSend()
  const ready = name.trim().length > 0 && member !== ''

  const make = async () => {
    if (!ready) return
    await send.run(async () => {
      const result = await portfolioCall('account_create', {
        request_id: requestId.current,
        member_id: member,
        name: name.trim(),
        kind,
      })
      if (!result.enabled) throw new Error(result.message)
      onMade(result.data)
    })
  }

  return (
    <div className="flex flex-col gap-3 rounded-[0.875rem] border border-[color:var(--border-strong)] p-3">
      <Box
        label="Account name"
        value={name}
        inputMode="text"
        onChange={setName}
        disabled={send.pending}
      />
      <label className="flex flex-col gap-1.5">
        <span className={LABEL}>Kind</span>
        <select
          className={FIELD}
          value={kind}
          onChange={(e) => setKind(e.target.value as AccountKind)}
        >
          {ACCOUNT_KINDS.filter((k) => k !== 'upstox').map((k) => (
            <option key={k} value={k}>
              {ACCOUNT_KIND_LABELS[k]}
            </option>
          ))}
        </select>
      </label>
      {members.length > 1 && (
        <label className="flex flex-col gap-1.5">
          <span className={LABEL}>Whose</span>
          <select
            className={FIELD}
            value={member}
            onChange={(e) => setMember(e.target.value === '' ? '' : Number(e.target.value))}
          >
            <option value="">Choose</option>
            {members.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        </label>
      )}
      <ProblemLine problem={send.problem} />
      <div className="flex gap-2">
        <button type="button" className={`${SECONDARY} flex-1`} onClick={onCancel}>
          Cancel
        </button>
        <button
          type="button"
          className={`${PRIMARY} flex-1`}
          disabled={!ready || send.pending}
          onClick={() => void make()}
        >
          {send.pending ? 'Saving' : 'Add account'}
        </button>
      </div>
    </div>
  )
}

/** Charges as one total, or broken down into the usual parts (then the total is their sum). */
export function ChargesField({
  form,
  set,
  total,
  disabled,
}: {
  form: Form
  set: (patch: Partial<Form>) => void
  total: string
  disabled?: boolean
}) {
  return (
    <fieldset className="flex flex-col gap-2" disabled={disabled}>
      {form.useParts ? (
        <>
          <legend className={LABEL}>Charges: {total}</legend>
          <div className="grid grid-cols-2 gap-2">
            {CHARGE_PARTS.map((part) => (
              <Box
                key={part}
                label={PART_LABELS[part]}
                value={form.parts[part]}
                onChange={(value) => set({ parts: { ...form.parts, [part]: value } })}
              />
            ))}
          </div>
        </>
      ) : (
        <Box
          label="Charges (₹, optional)"
          value={form.charges}
          onChange={(charges) => set({ charges })}
        />
      )}
      <button
        type="button"
        className="min-h-12 self-start px-1 text-sm font-semibold text-primary"
        onClick={() => set({ useParts: !form.useParts })}
      >
        {form.useParts ? 'Enter one total instead' : 'Break the charges down'}
      </button>
    </fieldset>
  )
}
