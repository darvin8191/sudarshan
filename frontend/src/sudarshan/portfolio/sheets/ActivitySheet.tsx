// SUDARSHAN M7.21: the Add sheet, and Edit and Duplicate of an entry from the list (design 4.2,
// 4.3 and 4.10). Chips pick the kind (Buy, Sell, Dividend, More); each kind shows its own fields.
// 300 ms after typing stops the service checks the entry (a dry run) and the sheet says what it
// would do, or why not, in words. Save is sent once with the tab version and a request id that a
// resend reuses, so a lost answer is never a second entry.
import { type InfiniteData, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, TriangleAlert } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { cn } from '@/lib/utils'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { PortfolioError, portfolioCall, problemOf } from '../api'
import { dayOfInstant } from '../days'
import { PORTFOLIO_KEY } from '../tab/ActivityTab'
import { tabViewOptions } from '../tab/tabView'
import {
  type Account,
  type ActivitiesPage,
  type Activity,
  type Member,
  type TabInfo,
  WORDS,
} from '../types'
import { AssetSearch } from './AssetSearch'
import { AccountChoice, Box, ChargesField } from './EntryFields'
import {
  type AssetPick,
  buildEntry,
  chargesOf,
  emptyForm,
  type Form,
  formOf,
  MAIN_MODES,
  MODE_LABELS,
  MORE_MODES,
  type Mode,
  workedAmount,
} from './entryForm'
import { inputToDay, parseRupees, priceInput, rupeesInput } from './parse'
import { CHOICE, FIELD, LABEL, OFF, ON, PRIMARY, SECONDARY } from './TabFields'
import { useSend } from './useSend'

/** What the two buttons of an old draft do, in plain words. */
export const DRAFT_WORDS =
  'This entry is a draft, so it is not counted in your holdings or returns. "Save as draft" keeps it out. "Save and count it" adds it from its date.'

export const DRY_RUN_MS = 300
const LAST_ACCOUNT_KEY = 'sudarshan.portfolio.last-account'

export type AddMode =
  | { kind: 'add' }
  | { kind: 'edit'; activityId: number }
  | { kind: 'duplicate'; activityId: number }

function lastAccount(): number | null {
  try {
    const raw = window.localStorage.getItem(LAST_ACCOUNT_KEY)
    return raw && /^\d+$/.test(raw) ? Number(raw) : null
  } catch {
    return null
  }
}

function rememberAccount(id: number | null) {
  if (id === null) return
  try {
    window.localStorage.setItem(LAST_ACCOUNT_KEY, String(id))
  } catch {
    // Kept for this visit only.
  }
}

/** The account to start from: the last one used if it is still there, else the only one. */
function startAccount(accounts: readonly Account[]): number | null {
  const own = accounts.filter((a) => !a.system)
  const last = lastAccount()
  if (last !== null && own.some((a) => a.id === last)) return last
  return own.length === 1 ? own[0].id : null
}

type Effect =
  | { state: 'idle' }
  | { state: 'checking' }
  | { state: 'ok'; words: string }
  | { state: 'refused'; words: string }

function Chip({
  on,
  children,
  onClick,
  disabled,
}: {
  on: boolean
  children: React.ReactNode
  onClick: () => void
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      aria-pressed={on}
      disabled={disabled}
      onClick={onClick}
      className={cn(CHOICE, on ? ON : OFF)}
    >
      {children}
    </button>
  )
}

export function ActivitySheet({
  open,
  onClose,
  tab,
  accounts,
  members,
  mode,
  onSaved,
  onAccountCreated,
}: {
  open: boolean
  onClose: () => void
  tab: TabInfo | null
  accounts: readonly Account[]
  members: readonly Member[]
  mode: AddMode
  /** The entry was saved: the page reads the tab again and closes the sheet. */
  onSaved: () => void
  /** An account was made here: the page reads the accounts again. */
  onAccountCreated: () => void
}) {
  const queries = useQueryClient()
  const today = dayOfInstant(Date.now())
  const view = useQuery({ ...tabViewOptions(tab?.id ?? 0), enabled: open && tab !== null })
  const assets = view.data?.assets ?? []
  const editing = mode.kind === 'edit'
  const copying = mode.kind === 'duplicate'
  const entryId = mode.kind === 'add' ? null : mode.activityId

  // The entry to fill in: the list the owner came from still holds it.
  const entry: Activity | null = useMemo(() => {
    if (!open || !tab || entryId === null) return null
    const pages = queries.getQueryData<InfiniteData<ActivitiesPage>>([
      ...PORTFOLIO_KEY,
      'activities',
      tab.id,
    ])
    return pages?.pages.flatMap((p) => p.items).find((a) => a.id === entryId) ?? null
  }, [open, tab, entryId, queries])

  const [form, setForm] = useState<Form>(() => emptyForm('BUY', today, null))
  const [more, setMore] = useState(false)
  const [effect, setEffect] = useState<Effect>({ state: 'idle' })
  const requestId = useRef(crypto.randomUUID())
  const send = useSend()
  const ready = entryId === null || (entry !== null && view.data !== undefined)

  // biome-ignore lint/correctness/useExhaustiveDependencies: a fresh form on each opening, and once an entry to fill in is loaded
  useEffect(() => {
    if (!open || !ready) return
    requestId.current = crypto.randomUUID()
    send.clear()
    setEffect({ state: 'idle' })
    if (entry) {
      const filled = formOf(entry, assets, today)
      setForm(copying ? { ...filled, date: emptyForm('BUY', today, null).date } : filled)
      setMore(!MAIN_MODES.includes(filled.mode))
    } else {
      setForm(emptyForm('BUY', today, startAccount(accounts)))
      setMore(false)
    }
  }, [open, ready, entry?.id, mode.kind])

  const set = (patch: Partial<Form>) => setForm((f) => ({ ...f, ...patch }))
  // A draft whose date has come stays a draft when edited, unless the owner chooses to count it.
  const keepDraft = editing && entry?.status === 'draft'
  const built = useMemo(() => buildEntry(form, today, keepDraft), [form, today, keepDraft])
  const amountShown = form.amountTouched ? form.amount : workedAmount(form) || form.amount
  const chargesTotal = (() => {
    const paise = chargesOf(form)
    return paise === null ? '—' : `₹${rupeesInput(paise) || '0'}`
  })()

  const rowPrice = (assetId: number): number | null =>
    view.data?.rows.find((r) => r.asset.id === assetId)?.price.price_u ?? null
  const pick = (picked: AssetPick | null) => {
    if (!picked) return set({ asset: null })
    const priceU = picked.priceU ?? rowPrice(picked.asset.id)
    setForm((f) => ({
      ...f,
      asset: { ...picked, priceU },
      price: f.price.trim() === '' && priceU ? priceInput(priceU) : f.price,
    }))
  }

  const changeMode = (next: Mode) =>
    setForm((f) => ({
      ...emptyForm(next, today, f.accountId),
      date: f.date,
      asset: next === 'VALUABLE' && f.asset?.asset.kind !== 'manual' ? null : f.asset,
      price: f.asset?.priceU ? priceInput(f.asset.priceU) : '',
    }))

  // The dry run: 300 ms after typing stops, for a form that is whole.
  const sentKey =
    built.ok && tab ? JSON.stringify([built.entry, tab.version, entryId, editing]) : null
  // biome-ignore lint/correctness/useExhaustiveDependencies: sentKey holds everything the check depends on
  useEffect(() => {
    if (!open || !tab || !built.ok || sentKey === null) {
      setEffect({ state: 'idle' })
      return
    }
    setEffect({ state: 'checking' })
    let current = true
    const timer = setTimeout(async () => {
      try {
        const result =
          editing && entryId !== null
            ? await portfolioCall('activity_update', {
                id: entryId,
                version: tab.version,
                dry_run: true,
                entry: built.entry,
              })
            : await portfolioCall('activity_create', {
                id: tab.id,
                version: tab.version,
                request_id: crypto.randomUUID(),
                dry_run: true,
                entry: built.entry,
              })
        if (!current) return
        if (!result.enabled) setEffect({ state: 'refused', words: result.message })
        else setEffect({ state: 'ok', words: result.data.effect.words })
      } catch (error) {
        if (current) setEffect({ state: 'refused', words: problemOf(error).message })
      }
    }, DRY_RUN_MS)
    return () => {
      current = false
      clearTimeout(timer)
    }
  }, [open, sentKey])

  const isLoan = form.mode === 'LOAN'
  const loanReady =
    form.loanName.trim() !== '' &&
    parseRupees(amountShown) !== null &&
    inputToDay(form.date) !== null
  const canSave = isLoan ? loanReady : built.ok && effect.state !== 'refused'

  const save = async (count = false) => {
    if (!tab) return
    await send.run(async () => {
      if (isLoan) {
        const day = inputToDay(form.date)
        const paise = parseRupees(amountShown)
        if (day === null || paise === null) return
        // M7.19 V2: a loan belongs to this tab, whose net worth it lowers.
        const made = await portfolioCall('asset_create', {
          request_id: requestId.current,
          kind: 'liability',
          name: form.loanName.trim(),
          asset_class: 'other',
          first_value: { day, price_u: paise * 100 },
          tab_id: tab.id,
          version: tab.version,
        })
        if (!made.enabled)
          throw new PortfolioError({ code: 'unavailable', message: made.message, entryId: null })
      } else {
        const made = buildEntry(form, today, keepDraft && !count)
        if (!made.ok) return
        const body = made.entry
        const result =
          editing && entryId !== null
            ? await portfolioCall('activity_update', {
                id: entryId,
                version: tab.version,
                entry: body,
              })
            : await portfolioCall('activity_create', {
                id: tab.id,
                version: tab.version,
                request_id: requestId.current,
                entry: body,
              })
        if (!result.enabled)
          throw new PortfolioError({ code: 'unavailable', message: result.message, entryId: null })
        rememberAccount(
          body.kind === 'TRANSFER' ? (body.to_account_id ?? body.account_id) : body.account_id
        )
      }
      void queries.invalidateQueries({ queryKey: PORTFOLIO_KEY })
      onSaved()
    })
  }

  const m = form.mode
  const tradeLike = m === 'BUY' || m === 'SELL' || m === 'RIGHTS' || m === 'VALUABLE'
  const asked = m === 'FEE' || m === 'INTEREST'
  const problem = send.problem
    ? { message: send.problem.code === 'unanswered' ? WORDS.not_confirmed : send.problem.message }
    : null
  const title = editing ? 'Edit entry' : copying ? 'Duplicate entry' : 'Add an entry'
  const accountField = (
    label: string,
    patchKey: 'accountId' | 'toAccountId' = 'accountId',
    outside?: boolean,
    onOutside?: () => void,
    outsideLabel?: string
  ) => (
    <AccountChoice
      label={label}
      accounts={accounts}
      members={members}
      value={form[patchKey]}
      outside={outside}
      outsideLabel={outsideLabel}
      onOutside={onOutside}
      onChange={(id) =>
        set({
          [patchKey]: id,
          ...(patchKey === 'accountId' ? { fromOutside: false } : { toOutside: false }),
        })
      }
      onCreated={(account) => {
        set({ [patchKey]: account.id })
        onAccountCreated()
      }}
      disabled={send.pending}
    />
  )
  const assetField = (opts: { optional?: boolean; label?: string } = {}) => (
    <AssetSearch
      value={form.asset}
      onPick={pick}
      assets={assets}
      manualOnly={m === 'VALUABLE'}
      disabled={send.pending}
      today={today}
      {...opts}
    />
  )
  const wholeShares = !built.ok && built.problem ? built.words : null

  return (
    <BottomSheet
      open={open}
      onOpenChange={(next) => !next && onClose()}
      title={title}
      description={tab?.name}
    >
      <form
        className="flex flex-col gap-4 overflow-y-auto px-4 pb-4"
        onSubmit={(event) => {
          event.preventDefault()
          if (canSave) void save()
        }}
      >
        {entryId !== null && !ready ? (
          <p
            className="py-6 text-center text-sm text-[color:var(--text-secondary)]"
            aria-live="polite"
          >
            {entry === null
              ? 'This entry is not open any more. Close this and pick it from the list.'
              : 'Loading the entry'}
          </p>
        ) : (
          <>
            {!editing && (
              <fieldset className="flex flex-wrap gap-2">
                <legend className="sr-only">Kind of entry</legend>
                {MAIN_MODES.map((k) => (
                  <Chip key={k} on={m === k} onClick={() => changeMode(k)} disabled={send.pending}>
                    {MODE_LABELS[k]}
                  </Chip>
                ))}
                <Chip on={more || !MAIN_MODES.includes(m)} onClick={() => setMore((v) => !v)}>
                  More
                  <ChevronDown aria-hidden="true" className={cn('size-4', more && 'rotate-180')} />
                </Chip>
              </fieldset>
            )}
            {!editing && (more || !MAIN_MODES.includes(m)) && (
              <fieldset className="flex flex-wrap gap-2">
                <legend className="sr-only">More kinds</legend>
                {MORE_MODES.map((k) => (
                  <Chip key={k} on={m === k} onClick={() => changeMode(k)} disabled={send.pending}>
                    {MODE_LABELS[k]}
                  </Chip>
                ))}
              </fieldset>
            )}
            {editing && <p className="text-base font-bold">{MODE_LABELS[m]}</p>}

            {isLoan ? (
              <>
                <Box
                  label="Name of the loan"
                  value={form.loanName}
                  inputMode="text"
                  onChange={(loanName) => set({ loanName })}
                />
                <Box
                  label="Outstanding (₹)"
                  value={form.amount}
                  onChange={(amount) => set({ amount, amountTouched: true })}
                />
                <Box label="On" type="date" value={form.date} onChange={(date) => set({ date })} />
                <p className="text-xs text-[color:var(--text-tertiary)]">
                  A loan is shown in net worth and is outside every return figure.
                </p>
              </>
            ) : (
              <>
                {m !== 'FEE' && m !== 'INTEREST' && assetField()}
                {asked && assetField({ optional: true })}

                {m === 'TRANSFER' ? (
                  <>
                    {accountField(
                      'From',
                      'accountId',
                      form.fromOutside,
                      () => set({ fromOutside: true, toOutside: false, accountId: null }),
                      'From outside'
                    )}
                    {accountField(
                      'To',
                      'toAccountId',
                      form.toOutside,
                      () => set({ toOutside: true, fromOutside: false, toAccountId: null }),
                      'To outside'
                    )}
                  </>
                ) : m === 'SPLIT' ? null : (
                  accountField(m === 'OPENING' ? 'Held in' : 'Account')
                )}

                <Box
                  label={m === 'SPLIT' ? 'Ex-date' : m === 'OPENING' ? 'Date first bought' : 'Date'}
                  type="date"
                  value={form.date}
                  onChange={(date) => set({ date })}
                />
                {built.ok && built.future && (
                  <p className="flex items-start gap-2 text-sm font-medium text-[color:var(--warning-text)]">
                    <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
                    {WORDS.future_date}
                  </p>
                )}
                {keepDraft && built.ok && !built.future && (
                  <p className="flex items-start gap-2 text-sm font-medium text-[color:var(--warning-text)]">
                    <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
                    {DRAFT_WORDS}
                  </p>
                )}

                {m === 'SPLIT' && (
                  <div className="grid grid-cols-2 gap-2">
                    <Box
                      label="New shares"
                      inputMode="numeric"
                      value={form.ratioNum}
                      onChange={(ratioNum) => set({ ratioNum })}
                    />
                    <Box
                      label="for every old"
                      inputMode="numeric"
                      value={form.ratioDen}
                      onChange={(ratioDen) => set({ ratioDen })}
                    />
                  </div>
                )}

                {(tradeLike || m === 'BONUS' || m === 'TRANSFER' || m === 'OPENING') && (
                  <Box
                    label={m === 'BONUS' ? 'Shares received' : 'Quantity'}
                    value={form.qty}
                    onChange={(qty) => set({ qty })}
                    hint={wholeShares ?? undefined}
                  />
                )}

                {(tradeLike || m === 'OPENING' || (m === 'TRANSFER' && form.fromOutside)) && (
                  <Box
                    label={tradeLike ? 'Price (₹)' : 'Cost per unit (₹)'}
                    value={form.price}
                    onChange={(price) => set({ price })}
                  />
                )}
                {m === 'TRANSFER' && form.fromOutside && (
                  <Box
                    label="Date first bought (optional)"
                    type="date"
                    value={form.lotDate}
                    onChange={(lotDate) => set({ lotDate })}
                  />
                )}
                {m === 'TRANSFER' && (form.fromOutside || form.toOutside) && (
                  <Box
                    label="Value on that day (₹)"
                    value={form.value}
                    onChange={(value) => set({ value })}
                  />
                )}

                {(tradeLike || m === 'DIVIDEND' || asked) && (
                  <Box
                    label={tradeLike ? 'Amount of the trade (₹)' : 'Amount (₹)'}
                    value={amountShown}
                    onChange={(amount) => set({ amount, amountTouched: amount.trim() !== '' })}
                    hint={
                      tradeLike
                        ? 'Worked out from quantity and price until you type it.'
                        : undefined
                    }
                  />
                )}
                {tradeLike && (
                  <ChargesField
                    form={form}
                    set={set}
                    total={chargesTotal}
                    disabled={send.pending}
                  />
                )}
                {(m === 'DIVIDEND' || m === 'INTEREST') && (
                  <Box
                    label="Tax deducted, TDS (₹, optional)"
                    value={form.tds}
                    onChange={(tds) => set({ tds })}
                  />
                )}

                <label className="flex flex-col gap-1.5">
                  <span className={LABEL}>Note (optional)</span>
                  <input
                    className={FIELD}
                    maxLength={500}
                    value={form.note}
                    onChange={(e) => set({ note: e.target.value })}
                  />
                </label>

                <div aria-live="polite" className="min-h-6 text-sm">
                  {effect.state === 'checking' && (
                    <span className="text-[color:var(--text-tertiary)]">Checking</span>
                  )}
                  {effect.state === 'ok' && <span className="font-semibold">{effect.words}</span>}
                  {effect.state === 'refused' && (
                    <span className="flex items-start gap-2 font-medium text-[color:var(--down)]">
                      <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
                      {effect.words}
                    </span>
                  )}
                  {effect.state === 'idle' && !built.ok && !built.problem && (
                    <span className="text-[color:var(--text-tertiary)]">{built.words}</span>
                  )}
                </div>
              </>
            )}

            {problem && (
              <p
                role="alert"
                className="rounded-[0.875rem] border border-[color:var(--down)] bg-[color:var(--down-tint)] px-3 py-2 text-sm font-medium text-[color:var(--down)]"
              >
                {problem.message}
              </p>
            )}
            <div className={keepDraft ? 'flex flex-col gap-2' : 'flex gap-2'}>
              <button type="button" className={`${SECONDARY} flex-1`} onClick={onClose}>
                Cancel
              </button>
              <button
                type="submit"
                className={`${PRIMARY} flex-1`}
                disabled={!canSave || send.pending}
              >
                {send.pending ? 'Saving' : built.ok && built.draft ? 'Save as draft' : 'Save'}
              </button>
              {keepDraft && built.ok && !built.future && (
                <button
                  type="button"
                  className={`${SECONDARY} flex-1`}
                  disabled={!canSave || send.pending}
                  onClick={() => void save(true)}
                >
                  Save and count it
                </button>
              )}
            </div>
          </>
        )}
      </form>
    </BottomSheet>
  )
}
