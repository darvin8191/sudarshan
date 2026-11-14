/**
 * A trade's changes that are not saved, kept in this tab's session storage (review H2), so leaving
 * the page by Back, a reload, or the phone stopping the tab in the background does not lose them.
 * Written while the sheet holds changes, cleared on Save and on Discard, kept a week at most.
 * Storage that cannot be used (a private tab) simply keeps nothing.
 */
import type { SheetForm } from './sheetForm'

const PREFIX = 'sd-journal-draft:'
export const DRAFT_KEEP_MS = 7 * 24 * 60 * 60 * 1000

export interface StoredDraft {
  /** The trade's version the changes were made on. */
  version: number
  /** The form as it was read at that version, and as the owner changed it. */
  base: SheetForm
  form: SheetForm
  /** Notes typed here that another screen's notes replaced in the field. */
  typedNotes: string | null
  at: number
}

const isForm = (value: unknown): value is SheetForm => {
  if (!value || typeof value !== 'object') return false
  const f = value as Record<string, unknown>
  return (
    typeof f.strategy === 'string' &&
    (f.setupId === null || typeof f.setupId === 'number') &&
    typeof f.risk === 'string' &&
    Array.isArray(f.checklist) &&
    Array.isArray(f.mistakes) &&
    (f.mood === null || typeof f.mood === 'string') &&
    typeof f.notes === 'string'
  )
}

function storage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.sessionStorage
  } catch {
    return null
  }
}

export function readDraft(id: number, now = Date.now()): StoredDraft | null {
  const store = storage()
  try {
    const raw = store?.getItem(`${PREFIX}${id}`)
    if (!raw) return null
    const draft = JSON.parse(raw) as Partial<StoredDraft>
    if (
      typeof draft.version !== 'number' ||
      typeof draft.at !== 'number' ||
      now - draft.at > DRAFT_KEEP_MS ||
      !isForm(draft.base) ||
      !isForm(draft.form)
    ) {
      store?.removeItem(`${PREFIX}${id}`)
      return null
    }
    return {
      version: draft.version,
      base: draft.base,
      form: draft.form,
      typedNotes: typeof draft.typedNotes === 'string' ? draft.typedNotes : null,
      at: draft.at,
    }
  } catch {
    return null
  }
}

export function writeDraft(id: number, draft: Omit<StoredDraft, 'at'>, now = Date.now()): void {
  try {
    storage()?.setItem(`${PREFIX}${id}`, JSON.stringify({ ...draft, at: now }))
  } catch {
    // Full or refused: the sheet still asks before it closes; only a stopped tab could lose it.
  }
}

export function clearDraft(id: number): void {
  try {
    storage()?.removeItem(`${PREFIX}${id}`)
  } catch {
    // nothing kept, nothing to clear
  }
}
