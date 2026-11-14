/**
 * What the playbook sheet edits, as plain functions a test calls: the form from a playbook, the
 * sentences for what cannot be saved, and the one request a Save sends (a create, or an update
 * carrying only what changed with the version the sheet was opened at).
 */
import { parseRupees, riskText } from './sheetForm'
import { LIMITS, type Playbook, type PlaybookCreateReq, type PlaybookUpdateReq } from './types'

export interface PlaybookForm {
  name: string
  nameAlt: string
  description: string
  /** One text per row; blank rows are dropped when saving. */
  rules: string[]
  risk: string
}

export const EMPTY_FORM: PlaybookForm = {
  name: '',
  nameAlt: '',
  description: '',
  rules: [''],
  risk: '',
}

export function formOf(playbook: Playbook | null): PlaybookForm {
  if (!playbook) return { ...EMPTY_FORM, rules: [''] }
  return {
    name: playbook.name,
    nameAlt: playbook.name_alt ?? '',
    description: playbook.description,
    rules: playbook.rules.length ? [...playbook.rules] : [''],
    risk: riskText(playbook.default_risk_paise),
  }
}

/** The rules as saved: trimmed, blank ones dropped. */
export const rulesOf = (form: PlaybookForm): string[] =>
  form.rules.map((r) => r.trim()).filter((r) => r !== '')

/** Problems with the form, each a sentence with the next step; none when it can be saved. */
export function problemsOf(form: PlaybookForm): string[] {
  const out: string[] = []
  const name = form.name.trim()
  if (name === '') out.push('Give the playbook a name.')
  if (name.length > LIMITS.playbook_name) {
    out.push(`The name can have up to ${LIMITS.playbook_name} characters. Shorten it.`)
  }
  if (form.nameAlt.trim().length > LIMITS.playbook_name) {
    out.push(`The second name can have up to ${LIMITS.playbook_name} characters. Shorten it.`)
  }
  if (rulesOf(form).length > LIMITS.playbook_rules) {
    out.push(`A playbook can have up to ${LIMITS.playbook_rules} checks. Remove some.`)
  }
  if (!parseRupees(form.risk).valid) {
    out.push(
      'Default risk is a rupee amount above zero, such as 500 or 1,250.50. Correct it or clear it.'
    )
  }
  return out
}

const sameRules = (a: readonly string[], b: readonly string[]) =>
  a.length === b.length && a.every((r, n) => r === b[n])

export function createRequest(form: PlaybookForm, requestId: string): PlaybookCreateReq {
  const alt = form.nameAlt.trim()
  return {
    name: form.name.trim(),
    ...(alt ? { name_alt: alt } : {}),
    description: form.description.trim(),
    rules: rulesOf(form),
    default_risk_paise: parseRupees(form.risk).paise,
    request_id: requestId,
  }
}

/** Only what changed, with the version the sheet was opened at; null when nothing changed. */
export function updateRequest(playbook: Playbook, form: PlaybookForm): PlaybookUpdateReq | null {
  const update: PlaybookUpdateReq = { id: playbook.id, version: playbook.version }
  let changed = false
  if (form.name.trim() !== playbook.name) {
    update.name = form.name.trim()
    changed = true
  }
  if ((form.nameAlt.trim() || null) !== playbook.name_alt) {
    update.name_alt = form.nameAlt.trim() || null
    changed = true
  }
  if (form.description.trim() !== playbook.description) {
    update.description = form.description.trim()
    changed = true
  }
  if (!sameRules(rulesOf(form), playbook.rules)) {
    update.rules = rulesOf(form)
    changed = true
  }
  const risk = parseRupees(form.risk).paise
  if (risk !== playbook.default_risk_paise) {
    update.default_risk_paise = risk
    changed = true
  }
  return changed ? update : null
}

/**
 * The owner's changes laid over a newer read of the playbook, after a conflict: a field he changed
 * keeps what he typed, every other field takes the newer value, so a Save never puts back a field
 * he did not touch over what the other screen saved.
 */
export function rebasePlaybook(
  base: PlaybookForm,
  mine: PlaybookForm,
  theirs: PlaybookForm
): PlaybookForm {
  const out: PlaybookForm = { ...theirs }
  for (const key of Object.keys(base) as Array<keyof PlaybookForm>) {
    if (JSON.stringify(mine[key]) !== JSON.stringify(base[key])) {
      Object.assign(out, { [key]: mine[key] })
    }
  }
  return out
}
