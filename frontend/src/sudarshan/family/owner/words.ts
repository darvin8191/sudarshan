// SUDARSHAN P6 D7a: the Family page's words and small rules, as pure functions (nothing here reads
// a clock or the network: `now` is always an argument, so a test moves it).
import {
  type AuditKind,
  CATEGORIES,
  type Category,
  type MemberCard,
  type Privacy,
  type Role,
} from '../types'

const IST = 'Asia/Kolkata'
const clock = new Intl.DateTimeFormat('en-GB', {
  timeZone: IST,
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})
const dayKey = new Intl.DateTimeFormat('en-CA', { timeZone: IST })
const dayName = new Intl.DateTimeFormat('en-GB', { timeZone: IST, day: 'numeric', month: 'short' })

/** "18:40" in India's time. */
export function clockOf(at: number): string {
  return clock.format(new Date(at))
}

/** "today 14:02", or "9 Nov, 14:02" for another day. */
export function whenOf(at: number, now: number): string {
  const same = dayKey.format(new Date(at)) === dayKey.format(new Date(now))
  return same ? `today ${clockOf(at)}` : `${dayName.format(new Date(at))}, ${clockOf(at)}`
}

/** The label of each privacy category, in the owner's words. */
export const CATEGORY_LABEL: Record<Category, { name: string; shown: string; hidden: string }> = {
  pnl: { name: 'P&L', shown: 'P&L shown', hidden: 'P&L hidden' },
  qty: { name: 'Quantities', shown: 'quantities shown', hidden: 'quantities hidden' },
  funds: { name: 'Funds', shown: 'funds shown', hidden: 'funds hidden' },
  orders: { name: 'Orders', shown: 'orders shown', hidden: 'orders hidden' },
}

/** "P&L shown · quantities shown · funds hidden · orders shown" */
export function privacyLine(privacy: Privacy): string {
  return CATEGORIES.map((c) =>
    privacy[c] ? CATEGORY_LABEL[c].shown : CATEGORY_LABEL[c].hidden
  ).join(' · ')
}

/** Cross-design Y24: P&L is worked out from quantities, so P&L is offered only with them. */
export const PNL_NEEDS_QTY = 'Live P&L is worked out from quantities'

/**
 * Review H1: beside a quantity, any rupee figure gives the P&L away, so hiding P&L hides them all
 * (the `cost` list of privacy_fields.json). Said under the switches while P&L is hidden.
 */
export const PNL_HIDES_MONEY =
  'Hidden, and every rupee figure with it: cost, value, the day’s change'
export const FUNDS_WITHOUT_AMOUNTS = 'Shown without amounts while P&L is hidden'

/** The privacy as the page sends it: with quantities off, P&L goes off with them. */
export function settled(privacy: Privacy): Privacy {
  return privacy.qty ? privacy : { ...privacy, pnl: false }
}

export const ROLE_WORDS: Record<Role, { label: string; line: string }> = {
  viewer: {
    label: 'Viewer',
    line: 'Sees positions, holdings and shared lists; figures can be hidden one by one below.',
  },
  child: {
    label: 'Child',
    line: 'Symbols and direction only, quiet outside market hours.',
  },
}

export type StatusKind = 'active' | 'invited' | 'locked' | 'suspended' | 'removed'

export interface StatusChip {
  kind: StatusKind
  label: string
}

/** The status chip of a card at `now`: a lock or an invite that has ended is not shown as one. */
export function statusOf(card: MemberCard, now: number): StatusChip {
  if (card.status === 'removed') return { kind: 'removed', label: 'Removed' }
  if (card.status === 'suspended') return { kind: 'suspended', label: 'Suspended' }
  if (card.status === 'invited') {
    const end = card.invite_expires_at
    return end !== null && end > now
      ? { kind: 'invited', label: `Invited · expires ${clockOf(end)}` }
      : { kind: 'invited', label: 'Invited · link expired' }
  }
  if (card.locked_until !== null && card.locked_until > now) {
    return { kind: 'locked', label: `Locked until ${clockOf(card.locked_until)}` }
  }
  return { kind: 'active', label: 'Active' }
}

/**
 * Review L8: when the open invite's link was first opened (it hands out the code app's key). A
 * link opened before the member says they opened it is the owner's cue to cancel it.
 */
export function openedLine(card: MemberCard): string | null {
  if (card.invite_expires_at === null || card.invite_opened_at === null) return null
  return `Link opened at ${clockOf(card.invite_opened_at)}`
}

export function devicesLine(count: number): string {
  if (count === 0) return 'No devices'
  return count === 1 ? '1 device' : `${count} devices`
}

export function lastSeenLine(at: number | null, now: number): string {
  return at === null ? 'Not seen yet' : `Last seen ${whenOf(at, now)}`
}

/** What each kind of audit line says, for the owner. */
export const AUDIT_WORDS: Record<AuditKind, string> = {
  login_ok: 'Signed in',
  login_failed: 'Sign-in failed',
  lockout: 'Sign-in locked after wrong attempts',
  logout: 'Signed out',
  logout_all: 'Signed out everywhere',
  session_revoked: 'Device signed out',
  session_expired: 'Sign-in expired',
  invite_created: 'Invite made',
  invite_opened: 'Invite link opened',
  invite_used: 'Invite used',
  invite_revoked: 'Invite cancelled',
  member_created: 'Member added',
  member_updated: 'Member changed',
  member_suspended: 'Member suspended',
  member_resumed: 'Member resumed',
  member_removed: 'Member removed',
  privacy_changed: 'Privacy changed',
  signin_reset: 'Sign-in reset',
  signin_unlocked: 'Sign-in unlocked',
  share_set: 'Sharing set',
  share_removed: 'Sharing removed',
  device_renamed: 'Device renamed',
  password_changed: 'Password changed',
  totp_reset: 'Code app reset',
  push_subscribed: 'Notifications turned on for a phone',
  push_removed: 'Notifications turned off for a phone',
  telegram_linked: 'Telegram linked',
  telegram_unlinked: 'Telegram unlinked',
  bot_command: 'Bot command used',
  view: 'Screen opened',
  digest_sent: 'Daily summary sent',
  check_failed: 'Install check failed',
}

export const SENT_OK = 'Done.'
