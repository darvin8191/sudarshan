// SUDARSHAN M2.8 (cross-design Y6): a member's privacy is kept twice. The family service keeps the
// owner's choice; the portfolio keeps a floor of its own, and the stricter of the two wins, so a
// break-in at the family process cannot widen what a member sees. Saving privacy on the Family
// page writes both in one action, in an order that lets a half-done edit leave the member seeing
// less and never more:
//
//   1. what the edit hides goes to the floor first (the floor keeps showing a category only where
//      the new choice shows it too), before anything else is written;
//   2. then the family service's row;
//   3. then what the edit shows (and the member's role, which decides the tabs shared by role)
//      goes to the floor last, only once the family service confirmed its own write.
//
// Each step runs only when it changes something, and the save stops at the first step that is
// refused or not confirmed. "Saved" is said only when every step is confirmed; otherwise one
// sentence names what took effect, then the reason. Nothing here touches the network itself: the
// writes are handed in, so a test drives every order and every failure.
import { OwnerError } from '../ownerApi'
import { CATEGORIES, type Category, type Privacy, type Role } from '../types'

/** What the portfolio holds for a member: the categories it shows, and their role. */
export interface FloorPlan {
  shown: Privacy
  role: string | null
}

export type Step = { kind: 'floor'; floor: FloorPlan; why: 'hide' | 'show' } | { kind: 'family' }

const NONE: Privacy = { pnl: false, qty: false, funds: false, orders: false }

/** The reason a floor write was refused because the row moved on another screen. */
export const FLOOR_CHANGED =
  'The portfolio’s copy of their privacy was changed on another screen; the page now shows it as it is.'
const CHECK = 'Open this member again to check.'

const sameFloor = (a: FloorPlan, b: FloorPlan) =>
  a.role === b.role && CATEGORIES.every((c) => a.shown[c] === b.shown[c])

/**
 * The steps of one save. `floor` is the portfolio's row now (null: it has none, which shows
 * nothing); `next` is what the owner chose; `familyChanged` says whether the family service's own
 * row needs a write at all (a floor that only catches up with it needs none).
 */
export function planPrivacy({
  floor,
  next,
  familyChanged,
}: {
  floor: FloorPlan | null
  next: { privacy: Privacy; role: Role }
  familyChanged: boolean
}): Step[] {
  const now: FloorPlan = floor ?? { shown: NONE, role: null }
  const target: FloorPlan = { shown: { ...next.privacy }, role: next.role }
  const shown = { ...NONE }
  for (const c of CATEGORIES) shown[c] = now.shown[c] && target.shown[c]
  const narrowed: FloorPlan = { shown, role: now.role }
  const steps: Step[] = []
  if (!sameFloor(narrowed, now)) steps.push({ kind: 'floor', floor: narrowed, why: 'hide' })
  if (familyChanged) steps.push({ kind: 'family' })
  if (!sameFloor(target, narrowed)) steps.push({ kind: 'floor', floor: target, why: 'show' })
  return steps
}

const WORD: Record<Category, string> = {
  pnl: 'P&L',
  qty: 'quantities',
  funds: 'funds',
  orders: 'orders',
}

/** "P&L", "P&L and funds", "P&L, quantities and funds". */
export function listOf(categories: readonly Category[]): string {
  const words = categories.map((c) => WORD[c])
  if (words.length <= 1) return words.join('')
  return `${words.slice(0, -1).join(', ')} and ${words[words.length - 1]}`
}

/** The categories `from` shows and `to` does not. */
const lost = (from: Privacy, to: Privacy) => CATEGORIES.filter((c) => from[c] && !to[c])

export interface PrivacyWrites {
  family: () => Promise<unknown>
  floor: (floor: FloorPlan, version: number | null) => Promise<{ version: number }>
}

/**
 * Run the steps in order. Resolves when every one was confirmed; otherwise throws an `OwnerError`
 * whose sentence names what took effect (code `not_confirmed` when the last write may have
 * happened, the refusal's own code otherwise).
 */
export async function runPrivacySave(
  steps: readonly Step[],
  writes: PrivacyWrites,
  start: { floor: FloorPlan | null; version: number | null }
): Promise<void> {
  let floorNow: FloorPlan = start.floor ?? { shown: NONE, role: null }
  let version = start.version
  let hidden: Category[] | null = null
  let familySaved = false
  for (const step of steps) {
    try {
      if (step.kind === 'family') {
        await writes.family()
        familySaved = true
        continue
      }
      const saved = await writes.floor(step.floor, version)
      version = saved.version
      if (step.why === 'hide') hidden = lost(floorNow.shown, step.floor.shown)
      floorNow = step.floor
    } catch (caught) {
      const error =
        caught instanceof OwnerError ? caught : new OwnerError('failed', 'That was not saved.')
      if (step.kind === 'family' && hidden === null) throw error // nothing took effect before it
      throw new OwnerError(
        error.unconfirmed ? 'not_confirmed' : error.code,
        sentenceFor(step, error, { hidden: hidden ?? [], familySaved, floorNow })
      )
    }
  }
}

function sentenceFor(
  step: Step,
  error: OwnerError,
  done: { hidden: Category[]; familySaved: boolean; floorNow: FloorPlan }
): string {
  const unsure = error.unconfirmed
  if (step.kind === 'family') {
    const took = `The portfolio now hides ${done.hidden.length ? listOf(done.hidden) : 'more'} from them`
    return unsure
      ? `${took}; whether the rest was saved is not confirmed. ${CHECK}`
      : `${took}; nothing else was saved. ${error.message}`
  }
  const reason = error.code === 'conflict' ? FLOOR_CHANGED : error.message
  if (step.why === 'hide') {
    const what = lost(done.floorNow.shown, step.floor.shown)
    return unsure
      ? `Not confirmed: the portfolio may now hide ${what.length ? listOf(what) : 'more'} from them, and nothing else was saved. ${CHECK}`
      : `Nothing was saved. ${reason}`
  }
  const shows = lost(step.floor.shown, done.floorNow.shown)
  const named = shows.length ? listOf(shows) : null
  const lead = done.familySaved ? 'Saved in the family view' : null
  if (unsure) {
    const what = named
      ? `whether the portfolio now shows them ${named} is not confirmed`
      : 'whether the portfolio took the change is not confirmed'
    return lead ? `${lead}; ${what}. ${CHECK}` : `${capital(what)}. ${CHECK}`
  }
  const missing = named
    ? `the portfolio still hides ${named} from them`
    : 'the portfolio did not take the change'
  return lead ? `${lead}, but ${missing}. ${reason}` : `${capital(missing)}. ${reason}`
}

const capital = (text: string) => text.charAt(0).toUpperCase() + text.slice(1)
