// SUDARSHAN P7 F8 (M7.34): the form of a goal (design p7 9.4; owner decision 13): its name and
// second-language name, the target, the date, the tabs it counts or an amount typed instead, and
// the owner's assumed return (10% a year unless he sets one). A kept goal is changed from the
// version read and can be archived; the service's sentence shows when a value is refused.
import { useEffect, useRef, useState } from 'react'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { createGoal, updateGoal } from '../api'
import { Box } from '../sheets/EntryFields'
import { dayToInput, inputToDay, parseRupees, rupeesInput, scaledText } from '../sheets/parse'
import { LABEL, PRIMARY, ProblemLine, SECONDARY } from '../sheets/TabFields'
import { useSend } from '../sheets/useSend'
import type { Goal, GoalsList, TabInfo } from '../types'

export const DEFAULT_RATE = '10'

export interface GoalForm {
  name: string
  name_alt: string
  target: string
  day: string
  rate: string
  tabs: number[]
  fixed: string
}

export function formOf(goal: Goal | null): GoalForm {
  if (goal === null) {
    return { name: '', name_alt: '', target: '', day: '', rate: DEFAULT_RATE, tabs: [], fixed: '' }
  }
  return {
    name: goal.name,
    name_alt: goal.name_alt ?? '',
    target: rupeesInput(goal.target_paise),
    day: dayToInput(goal.target_day),
    rate: scaledText(goal.rate_bp, 100),
    tabs: goal.tab_ids,
    fixed: goal.fixed_paise === null ? '' : rupeesInput(goal.fixed_paise),
  }
}

/** The form as the service takes it: tabs to count, or the typed amount, never both. */
export function fieldsOf(form: GoalForm) {
  const typed = form.tabs.length === 0 && form.fixed.trim() !== ''
  return {
    name: form.name.trim(),
    name_alt: form.name_alt.trim() || null,
    target_paise: parseRupees(form.target) as number,
    target_day: inputToDay(form.day) as number,
    rate_bp: parseRupees(form.rate) as number,
    tab_ids: typed ? [] : form.tabs,
    fixed_paise: typed ? parseRupees(form.fixed) : null,
  }
}

export function GoalSheet({
  open,
  goal,
  tabs,
  onClose,
  onSaved,
}: {
  open: boolean
  /** The goal to change; null for a new one. */
  goal: Goal | null
  tabs: readonly TabInfo[]
  onClose: () => void
  onSaved: (list: GoalsList) => void
}) {
  const [form, setForm] = useState<GoalForm>(formOf(null))
  const send = useSend()
  const { clear } = send
  const requestId = useRef('')
  useEffect(() => {
    if (!open) return
    setForm(formOf(goal))
    requestId.current = crypto.randomUUID()
    clear()
  }, [open, goal, clear])
  const set = (name: keyof GoalForm) => (value: string) =>
    setForm((was) => ({ ...was, [name]: value }))
  const toggle = (id: number) =>
    setForm((was) => ({
      ...was,
      tabs: was.tabs.includes(id) ? was.tabs.filter((t) => t !== id) : [...was.tabs, id],
    }))
  const run = async (change: () => Promise<{ enabled: boolean; data: GoalsList | null }>) => {
    let list: GoalsList | null = null
    const done = await send.run(async () => {
      list = (await change()).data
    })
    if (done && list) onSaved(list)
  }
  const save = () =>
    run(() =>
      goal === null
        ? createGoal({ ...fieldsOf(form), request_id: requestId.current })
        : updateGoal({ ...fieldsOf(form), id: goal.id, version: goal.version })
    )
  const archive = (archived: boolean) => {
    if (goal) void run(() => updateGoal({ id: goal.id, version: goal.version, archived }))
  }
  return (
    <BottomSheet
      open={open}
      onOpenChange={(next) => !next && onClose()}
      title={goal === null ? 'New goal' : 'Change goal'}
      description="A target, a date and what counts towards it"
    >
      <div className="flex flex-col gap-3">
        <Box label="Name" value={form.name} onChange={set('name')} inputMode="text" />
        <Box
          label="Name in Gujarati or Hindi (optional)"
          value={form.name_alt}
          onChange={set('name_alt')}
          inputMode="text"
        />
        <Box label="Target, in rupees" value={form.target} onChange={set('target')} />
        <Box label="Reach it by" type="date" value={form.day} onChange={set('day')} />
        <fieldset className="flex min-w-0 flex-col gap-1">
          <legend className={LABEL}>Tabs to count</legend>
          {tabs.length === 0 && (
            <p className="text-sm text-[color:var(--text-secondary)]">
              You have no tab of your own yet. Type the amount saved so far instead.
            </p>
          )}
          {tabs.map((tab) => (
            <label key={tab.id} className="flex min-h-12 items-center gap-3 text-[0.9375rem]">
              <input
                type="checkbox"
                className="size-5 accent-[color:var(--brand)]"
                checked={form.tabs.includes(tab.id)}
                onChange={() => toggle(tab.id)}
              />
              {tab.name}
            </label>
          ))}
        </fieldset>
        {form.tabs.length === 0 && (
          <Box
            label="Or the amount saved so far, in rupees"
            value={form.fixed}
            onChange={set('fixed')}
            hint="Used only when no tab is chosen. It does not move with prices."
          />
        )}
        <Box
          label="Assumed return, percent a year"
          value={form.rate}
          onChange={set('rate')}
          hint="Your own assumption, not a forecast."
        />
        <ProblemLine problem={send.problem} />
        <button
          type="button"
          className={PRIMARY}
          disabled={send.pending}
          onClick={() => void save()}
        >
          {send.pending ? 'Saving' : goal === null ? 'Add goal' : 'Save'}
        </button>
        {goal !== null && (
          <button
            type="button"
            className={SECONDARY}
            disabled={send.pending}
            onClick={() => archive(!goal.archived)}
          >
            {goal.archived ? 'Bring this goal back' : 'Archive this goal'}
          </button>
        )}
      </div>
    </BottomSheet>
  )
}
