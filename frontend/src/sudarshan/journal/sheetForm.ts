/**
 * What the trade sheet edits, as plain functions a test calls: the risk typed in rupees, the R it
 * gives, the checklist as the notes store it, and the one update a Save sends (only what changed).
 */
import { roundDiv } from '@/sudarshan/portfolio/money'
import { strategyOf } from './live'
import {
  LIMITS,
  type Mood,
  PAISE_PER_RUPEE,
  type Trip,
  type TripNote,
  type TripUpdateReq,
} from './types'

/** The six words of a mood, in the order the sheet offers them. */
export const MOODS: ReadonlyArray<{ id: Mood; label: string }> = [
  { id: 'planned', label: 'Planned' },
  { id: 'calm', label: 'Calm' },
  { id: 'rushed', label: 'Rushed' },
  { id: 'fomo', label: 'FOMO' },
  { id: 'revenge', label: 'Revenge' },
  { id: 'tired', label: 'Tired' },
]

/** The risk the owner typed, in paise: whole rupees or up to two places, above zero; else null. */
export function parseRupees(text: string): { paise: number | null; valid: boolean } {
  const cleaned = text.replace(/[₹,\s]/g, '')
  if (cleaned === '') return { paise: null, valid: true }
  const match = /^(\d{1,12})(?:\.(\d{1,2}))?$/.exec(cleaned)
  if (!match) return { paise: null, valid: false }
  const paise = Number(match[1]) * PAISE_PER_RUPEE + Number((match[2] ?? '').padEnd(2, '0') || 0)
  return paise > 0 ? { paise, valid: true } : { paise: null, valid: false }
}

/** Paise as the text the risk field starts from: "500" or "1250.50". */
export function riskText(paise: number | null): string {
  if (paise === null) return ''
  const rupees = Math.floor(paise / PAISE_PER_RUPEE)
  const rest = paise % PAISE_PER_RUPEE
  return rest === 0 ? String(rupees) : `${rupees}.${String(rest).padStart(2, '0')}`
}

/** net / risk as "+1.63" or "−0.65" (hundredths, half away from zero); null with no risk or net. */
export function rText(net: number | null, risk: number | null): string | null {
  if (net === null || risk === null || risk <= 0) return null
  const hundredths = Number(roundDiv(BigInt(net) * 100n, BigInt(risk)))
  const magnitude = Math.abs(hundredths)
  const body = `${Math.floor(magnitude / 100)}.${String(magnitude % 100).padStart(2, '0')}`
  return hundredths > 0 ? `+${body}` : hundredths < 0 ? `−${body}` : body
}

export interface ChecklistItem {
  rule: string
  yes: boolean
}

/** The notes store the checklist as text: the rules and their answers as JSON. */
export function parseChecklist(text: string): ChecklistItem[] {
  if (!text.trim()) return []
  try {
    const raw: unknown = JSON.parse(text)
    if (!Array.isArray(raw)) return []
    return raw.flatMap((item) => {
      if (!item || typeof item !== 'object') return []
      const { rule, yes } = item as { rule?: unknown; yes?: unknown }
      return typeof rule === 'string' && rule ? [{ rule, yes: yes === true }] : []
    })
  } catch {
    return []
  }
}

export const serializeChecklist = (items: readonly ChecklistItem[]): string =>
  items.length === 0 ? '' : JSON.stringify(items.map(({ rule, yes }) => ({ rule, yes })))

/** The playbook's rules with the answers already given to them, kept by rule. */
export function checklistFor(
  rules: readonly string[],
  saved: readonly ChecklistItem[]
): ChecklistItem[] {
  const answers = new Map(saved.map((item) => [item.rule, item.yes]))
  return rules.map((rule) => ({ rule, yes: answers.get(rule) ?? false }))
}

export interface SheetForm {
  strategy: string
  setupId: number | null
  risk: string
  checklist: ChecklistItem[]
  mistakes: number[]
  mood: Mood | null
  notes: string
}

export function formOf(trip: Trip, note: TripNote | null | undefined): SheetForm {
  return {
    strategy: trip.strategy_override ?? '',
    setupId: trip.setup_id,
    risk: riskText(trip.risk_paise),
    checklist: parseChecklist(note?.checklist ?? ''),
    mistakes: [...(note?.mistakes ?? [])].sort((a, b) => a - b),
    mood: note?.mood ?? null,
    notes: note?.notes ?? '',
  }
}

const sameList = (a: readonly number[], b: readonly number[]) =>
  a.length === b.length && a.every((v, n) => v === b[n])

/** Problems with the form, each a sentence with the next step; none when it can be saved. */
export function problemsOf(form: SheetForm): string[] {
  const out: string[] = []
  if (form.strategy.trim().length > LIMITS.strategy_name) {
    out.push(`The strategy name can have up to ${LIMITS.strategy_name} characters. Shorten it.`)
  }
  if (!parseRupees(form.risk).valid) {
    out.push('Risk is a rupee amount above zero, such as 500 or 1,250.50. Correct it or clear it.')
  }
  if (form.notes.length > LIMITS.notes) {
    out.push(
      `Notes can have up to ${LIMITS.notes.toLocaleString('en-IN')} characters. Shorten them.`
    )
  }
  return out
}

/**
 * The update a Save sends: only what the owner changed, with the version the sheet was opened at.
 * Null when nothing changed. An emptied strategy clears the owner's own label; the recorded one
 * shows again.
 */
export function buildUpdate(trip: Trip, start: SheetForm, form: SheetForm): TripUpdateReq | null {
  const update: TripUpdateReq = { id: trip.id, version: trip.version }
  let changed = false
  const strategy = form.strategy.trim()
  if (strategy !== start.strategy.trim()) {
    update.strategy_override = strategy === '' ? null : strategy
    changed = true
  }
  if (form.setupId !== start.setupId) {
    update.setup_id = form.setupId
    changed = true
  }
  const risk = parseRupees(form.risk).paise
  if (risk !== trip.risk_paise) {
    update.risk_paise = risk
    changed = true
  }
  if (form.notes !== start.notes) {
    update.notes = form.notes
    changed = true
  }
  const mistakes = [...form.mistakes].sort((a, b) => a - b)
  if (!sameList(mistakes, start.mistakes)) {
    update.mistakes = mistakes
    changed = true
  }
  if (serializeChecklist(form.checklist) !== serializeChecklist(start.checklist)) {
    update.checklist = serializeChecklist(form.checklist)
    changed = true
  }
  if (form.mood !== start.mood) {
    update.mood = form.mood
    changed = true
  }
  return changed ? update : null
}

/** The last `n` strategies the owner used, newest first, each once: what the sheet offers. */
export function recentStrategies(trips: readonly Trip[], n = 8): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const trip of [...trips].sort((a, b) => b.opened_ms - a.opened_ms)) {
    const name = strategyOf(trip)
    if (!name || seen.has(name)) continue
    seen.add(name)
    out.push(name)
    if (out.length === n) break
  }
  return out
}
