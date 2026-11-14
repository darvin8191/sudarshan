// SUDARSHAN M14.5, JD5: a playbook in a sheet, to create or change: its name, a second-language
// name, what it is, up to 12 checks (the checklist a trade offers), and the default risk. Save
// sends once; a conflict is said in one sentence and nothing is saved. A playbook is archived, not
// deleted: its trades keep it, and it can be brought back.
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Archive, ArchiveRestore, LoaderCircle, Plus, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { journalData, problemOf, WORDS } from './api'
import { JOURNAL_KEY } from './keys'
import { Problem, UnsavedQuestion } from './parts'
import {
  createRequest,
  formOf,
  type PlaybookForm,
  problemsOf,
  rebasePlaybook,
  updateRequest,
} from './playbookForm'
import { LIMITS, type Playbook } from './types'

const LABEL = 'text-[0.8125rem] font-semibold text-[color:var(--text-secondary)]'
const FIELD =
  'min-h-12 w-full rounded-[0.875rem] border border-input bg-card px-3 text-[0.9375rem] outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50'

const sameForm = (a: PlaybookForm, b: PlaybookForm) => JSON.stringify(a) === JSON.stringify(b)

function Editor({
  playbook,
  asking,
  onDone,
  onReopen,
  onDirty,
  onKeep,
}: {
  playbook: Playbook | null
  /** Closing asked while the form holds changes: the question stands in its place. */
  asking: boolean
  onDone: () => void
  onReopen: () => void
  onDirty: (dirty: boolean) => void
  onKeep: () => void
}) {
  const queries = useQueryClient()
  const [form, setForm] = useState<PlaybookForm>(() => formOf(playbook))
  // The playbook as the form started from it: a newer read under changes being typed does not
  // replace them (Save then meets the conflict); "Open it again" takes the newer one under them.
  const [base, setBase] = useState<Playbook | null>(playbook)
  const [adopt, setAdopt] = useState(false)
  const [moved, setMoved] = useState(false)
  const [saving, setSaving] = useState(false)
  const [problem, setProblem] = useState<{ message: string; conflict: boolean } | null>(null)
  const sending = useRef(false)
  const requestId = useRef(crypto.randomUUID())
  const set = <K extends keyof PlaybookForm>(key: K, value: PlaybookForm[K]) =>
    setForm((now) => ({ ...now, [key]: value }))

  const problems = problemsOf(form)
  const update = useMemo(() => (base ? updateRequest(base, form) : null), [base, form])
  const fresh = playbook === null
  const dirty = fresh ? !sameForm(form, formOf(null)) : update !== null
  if (playbook && base && playbook.version !== base.version && (!dirty || adopt)) {
    setBase(playbook)
    if (dirty) {
      setForm(rebasePlaybook(formOf(base), form, formOf(playbook)))
      setMoved(true)
      setAdopt(false)
      setProblem(null)
    } else setForm(formOf(playbook))
  }
  const told = useRef(onDirty)
  told.current = onDirty
  useEffect(() => {
    told.current(dirty)
  }, [dirty])
  const canSave = !saving && problems.length === 0 && (fresh || update !== null)

  const send = async (run: () => Promise<unknown>) => {
    // One Save, one request, however it is tapped.
    if (sending.current) return
    sending.current = true
    setSaving(true)
    setProblem(null)
    try {
      await run()
      await queries.invalidateQueries({ queryKey: JOURNAL_KEY })
      onDone()
    } catch (error) {
      const found = problemOf(error)
      const conflict = found.code === 'conflict'
      setProblem({ message: conflict ? WORDS.playbookConflict : found.message, conflict })
      onKeep()
    } finally {
      sending.current = false
      setSaving(false)
    }
  }

  const save = () => {
    if (!canSave) {
      onKeep() // what to correct is said under the form
      return
    }
    void send(() =>
      playbook
        ? journalData('playbook_update', update as NonNullable<typeof update>)
        : journalData('playbook_create', createRequest(form, requestId.current))
    )
  }

  const archive = (archived: boolean) => {
    if (!playbook || saving) return
    void send(() =>
      journalData('playbook_update', { id: playbook.id, version: playbook.version, archived })
    )
  }

  const rules = form.rules
  return (
    <>
      {asking && (
        <UnsavedQuestion
          title={
            fresh
              ? 'This new playbook is not saved.'
              : 'Your changes to this playbook are not saved.'
          }
          canSave={fresh || update !== null}
          saving={saving}
          onSave={save}
          onDiscard={onDone}
          onKeep={onKeep}
        />
      )}
      <form
        hidden={asking}
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault()
          save()
        }}
      >
        {moved && (
          <output className="block text-sm font-medium text-[color:var(--text-secondary)]">
            This playbook was changed on another screen. What you typed is kept, and Save puts it in
            place of that change.
          </output>
        )}
        <label className="flex flex-col gap-1">
          <span className={LABEL}>Name (up to {LIMITS.playbook_name} characters)</span>
          <input
            type="text"
            value={form.name}
            maxLength={LIMITS.playbook_name + 20}
            onChange={(e) => set('name', e.target.value)}
            className={FIELD}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className={LABEL}>Second-language name (optional)</span>
          <input
            type="text"
            value={form.nameAlt}
            maxLength={LIMITS.playbook_name + 20}
            onChange={(e) => set('nameAlt', e.target.value)}
            className={FIELD}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className={LABEL}>What it is</span>
          <textarea
            value={form.description}
            rows={3}
            onChange={(e) => set('description', e.target.value)}
            className={`${FIELD} py-2`}
          />
        </label>

        <section aria-label="Checks" className="flex flex-col gap-2">
          <h3 className="text-[0.9375rem] font-bold">
            Checks before entering ({rules.filter((r) => r.trim() !== '').length} of{' '}
            {LIMITS.playbook_rules})
          </h3>
          <ul className="flex flex-col gap-2">
            {rules.map((rule, n) => (
              <li key={n} className="flex items-center gap-2">
                <input
                  type="text"
                  aria-label={`Check ${n + 1}`}
                  value={rule}
                  onChange={(e) =>
                    set(
                      'rules',
                      rules.map((r, i) => (i === n ? e.target.value : r))
                    )
                  }
                  className={FIELD}
                />
                <button
                  type="button"
                  aria-label={`Remove check ${n + 1}`}
                  onClick={() => {
                    const left = rules.filter((_, i) => i !== n)
                    set('rules', left.length ? left : [''])
                  }}
                  className="inline-flex size-12 shrink-0 items-center justify-center rounded-[0.875rem] border border-input bg-card hover:bg-accent"
                >
                  <X aria-hidden="true" className="size-4" />
                </button>
              </li>
            ))}
          </ul>
          <button
            type="button"
            disabled={rules.length >= LIMITS.playbook_rules}
            onClick={() => set('rules', [...rules, ''])}
            className="inline-flex min-h-12 items-center justify-center gap-1.5 rounded-[0.875rem] border border-input bg-card px-3.5 text-[0.9375rem] font-semibold hover:bg-accent disabled:opacity-60"
          >
            <Plus aria-hidden="true" className="size-4" />
            Add a check
          </button>
        </section>

        <label className="flex flex-col gap-1">
          <span className={LABEL}>Default risk in rupees (optional)</span>
          <input
            type="text"
            inputMode="decimal"
            value={form.risk}
            placeholder="For example 500"
            onChange={(e) => set('risk', e.target.value)}
            className={FIELD}
          />
        </label>

        {problems.map((text) => (
          <Problem key={text}>{text}</Problem>
        ))}
        {problem && (
          <div className="flex flex-col gap-2">
            <Problem>{problem.message}</Problem>
            {problem.conflict && (
              <button
                type="button"
                onClick={() => {
                  setAdopt(true)
                  onReopen()
                }}
                className="inline-flex min-h-12 items-center justify-center rounded-[0.875rem] border border-input bg-card px-4 text-[0.9375rem] font-semibold hover:bg-accent"
              >
                Open it again
              </button>
            )}
          </div>
        )}

        <button
          type="submit"
          disabled={!canSave}
          className="inline-flex min-h-12 items-center justify-center gap-2 rounded-[0.875rem] bg-[color:var(--brand)] px-4 text-[0.9375rem] font-bold text-white outline-none hover:bg-[color:var(--brand-hover)] focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-60"
        >
          {saving && <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />}
          {saving
            ? 'Saving'
            : fresh
              ? 'Create playbook'
              : update === null
                ? 'No changes to save'
                : 'Save'}
        </button>
        {playbook && (
          <button
            type="button"
            disabled={saving}
            onClick={() => archive(!playbook.archived)}
            className="inline-flex min-h-12 items-center justify-center gap-2 rounded-[0.875rem] border border-input bg-card px-4 text-[0.9375rem] font-semibold hover:bg-accent disabled:opacity-60"
          >
            {playbook.archived ? (
              <ArchiveRestore aria-hidden="true" className="size-4" />
            ) : (
              <Archive aria-hidden="true" className="size-4" />
            )}
            {playbook.archived ? 'Bring it back' : 'Archive this playbook'}
          </button>
        )}
        {playbook && !playbook.archived && (
          <p className="-mt-2 text-xs text-[color:var(--text-tertiary)]">
            Archiving hides it from new trades. Trades that used it keep it.
          </p>
        )}
      </form>
    </>
  )
}

/** The playbook sheet: a new one when `playbook` is null and `missing` is false. */
export function PlaybookSheet({
  playbook,
  missing,
  loading,
  failed = null,
  open,
  onClose,
  onReopen,
  onDirty,
  asked = 0,
}: {
  playbook: Playbook | null
  /** The address names a playbook the list does not have. */
  missing: boolean
  loading: boolean
  /** The sentence for a list that could not be read. */
  failed?: string | null
  open: boolean
  onClose: () => void
  onReopen: () => void
  /** Told whenever the form starts or stops holding changes that are not saved. */
  onDirty?: (dirty: boolean) => void
  /** Moved on by the page when Back took the sheet's entry while it held changes: ask first. */
  asked?: number
}) {
  // Review H2: Close, a tap outside, Escape or Back with changes not saved asks first.
  const [dirty, setDirty] = useState(false)
  const [asking, setAsking] = useState(false)
  const [seenAsked, setSeenAsked] = useState(asked)
  if (asked !== seenAsked) {
    setSeenAsked(asked)
    setAsking(true)
  }
  const told = useRef(onDirty)
  told.current = onDirty
  const finish = () => {
    told.current?.(false)
    onClose()
  }
  return (
    <BottomSheet
      open={open}
      onOpenChange={(next) => {
        if (next) return
        if (asking) setAsking(false)
        else if (dirty) setAsking(true)
        else finish()
      }}
      step={asking ? 'ask' : 'form'}
      title={playbook ? playbook.name : missing ? 'Playbook' : 'New playbook'}
      description={playbook?.archived ? 'Archived' : undefined}
    >
      {loading ? (
        <p className="flex items-center gap-2 py-6 text-base font-bold" aria-live="polite">
          <LoaderCircle aria-hidden="true" className="size-5 animate-spin" />
          Loading the playbook
        </p>
      ) : failed ? (
        <Problem>{failed}</Problem>
      ) : missing ? (
        <Problem>This playbook is not there any more. Close this and pick another.</Problem>
      ) : (
        <Editor
          key={playbook ? playbook.id : 'new'}
          playbook={playbook}
          asking={asking}
          onDone={finish}
          onReopen={onReopen}
          onDirty={(next) => {
            setDirty(next)
            told.current?.(next)
          }}
          onKeep={() => setAsking(false)}
        />
      )}
    </BottomSheet>
  )
}

/** The sheet for the playbook the address names (or a new one), from the list the page holds. */
export function PlaybookSheetHost({
  playbookId,
  onClose,
  onDirty,
  asked,
}: {
  playbookId: number | null
  onClose: () => void
  onDirty?: (dirty: boolean) => void
  asked?: number
}) {
  const list = useQuery({
    queryKey: [...JOURNAL_KEY, 'playbooks'],
    queryFn: async () => journalData('playbooks_list'),
    retry: false,
    staleTime: 0,
    refetchOnWindowFocus: false,
  })
  const playbook =
    playbookId === null ? null : (list.data?.find((p) => p.id === playbookId) ?? null)
  const loading = playbookId !== null && list.isPending
  const failed = playbookId !== null && list.isError && !list.data
  return (
    <PlaybookSheet
      open
      playbook={playbook}
      loading={loading}
      missing={playbookId !== null && !loading && playbook === null}
      failed={failed ? problemOf(list.error).message : null}
      onClose={onClose}
      onReopen={() => void list.refetch()}
      onDirty={onDirty}
      asked={asked}
    />
  )
}
