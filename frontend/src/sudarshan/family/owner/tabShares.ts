// SUDARSHAN M7.37: which portfolio tabs each family member is given, as the Family page shows and
// writes them. The rows live in the portfolio (`tab_share`), one per tab and viewer, where a viewer
// is one member's id ("12") or every member of a role ("role:child"). "Every member" is therefore
// one row per role: the page writes them together, and shows them as one line only while every
// role has one with the same level and end. Anything else is shown as it stands, never hidden.
// Pure functions: `now` and the members are arguments.
import type { ShareLevel, TabShare } from '@/sudarshan/portfolio/types'
import { type MemberCard, PRIVACY_DEFAULTS, type Role } from '../types'
import { ROLE_WORDS } from './words'

/** Every role a member can have: "every member" is a share for each. */
export const ROLES = Object.keys(PRIVACY_DEFAULTS) as Role[]

export const roleViewer = (role: string) => `role:${role}`

export type ShareWho = 'all' | number

export interface ShareLine {
  key: string
  /** Who the line stands for: every member, one member, or a role or member it cannot name. */
  who: ShareWho | null
  label: string
  rows: TabShare[]
  level: ShareLevel
  expiresAt: number | null
  ended: boolean
}

const MEMBER_ID = /^[0-9]{1,15}$/

/** The rows "every member" (or one member) means for a tab, as they stand. */
export function rowsFor(shares: readonly TabShare[], tabId: number, who: ShareWho): TabShare[] {
  const viewers = who === 'all' ? ROLES.map(roleViewer) : [String(who)]
  return shares.filter((s) => s.tab_id === tabId && viewers.includes(s.viewer))
}

const endedAt = (row: TabShare, now: number) => row.expires_at !== null && row.expires_at <= now

/** The lines of one tab: every member first, then roles shown apart, then members by name. */
export function linesFor(
  shares: readonly TabShare[],
  tabId: number,
  members: readonly MemberCard[],
  now: number
): ShareLine[] {
  const rows = shares.filter((s) => s.tab_id === tabId)
  const lines: ShareLine[] = []
  const roleRows = ROLES.map((role) => rows.find((r) => r.viewer === roleViewer(role)))
  const whole =
    roleRows.every((r) => r !== undefined) &&
    roleRows.every(
      (r) => r?.level === roleRows[0]?.level && r?.expires_at === roleRows[0]?.expires_at
    )
  const line = (key: string, who: ShareWho | null, label: string, picked: TabShare[]) => {
    const first = picked[0]
    lines.push({
      key,
      who,
      label,
      rows: picked,
      level: first.level,
      expiresAt: first.expires_at,
      ended: endedAt(first, now),
    })
  }
  if (whole) line('all', 'all', 'Every member', roleRows as TabShare[])
  const named = new Map(members.map((m) => [String(m.id), m]))
  const people: Array<[string, TabShare]> = []
  for (const row of rows) {
    if (row.viewer.startsWith('role:')) {
      if (whole && ROLES.some((role) => roleViewer(role) === row.viewer)) continue
      const role = row.viewer.slice(5)
      const word = ROLE_WORDS[role as Role]?.label ?? role
      line(row.viewer, null, `Members with the ${word} role`, [row])
    } else {
      const member = MEMBER_ID.test(row.viewer) ? named.get(row.viewer) : undefined
      people.push([member?.display_name ?? 'A member no longer in the family', row])
    }
  }
  people.sort((a, b) => a[0].localeCompare(b[0]))
  for (const [label, row] of people) {
    const who = MEMBER_ID.test(row.viewer) && named.has(row.viewer) ? Number(row.viewer) : null
    line(row.viewer, who, label, [row])
  }
  return lines
}

export const LEVEL_WORDS: Record<ShareLevel, { label: string; line: string }> = {
  view: { label: 'View', line: 'Sees the tab, with the figures their privacy allows.' },
  restricted: { label: 'Restricted', line: 'Sees the holdings by name only, with no figure.' },
}

// ---- the end of a share, in India's time (the owner's own; no daylight saving) ----
const IST_MS = 330 * 60_000

/** "2026-11-20T18:00", the value a date-and-time input holds, for `at` in India's time. */
export function istInputValue(at: number): string {
  return new Date(at + IST_MS).toISOString().slice(0, 16)
}

/** The moment a date-and-time input names in India's time, or null when it names none. */
export function fromIstInput(value: string): number | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::\d{2})?$/.exec(value.trim())
  if (!m) return null
  const at = Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]) - IST_MS
  return Number.isFinite(at) ? at : null
}
