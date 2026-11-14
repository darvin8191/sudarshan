// Synthetic answers for the Family page's tests. Nothing here is a real member: the handles are
// invented and the names are the platform's own words.
import type { AuditLine, Device, Invite, MemberCard, Share } from '../types'

export const NOW = Date.UTC(2026, 10, 14, 10, 0, 0) // 15:30 in India

export function cardOf(over: Partial<MemberCard> = {}): MemberCard {
  return {
    id: 1,
    handle: 'test_one',
    display_name: 'Test One',
    role: 'viewer',
    status: 'active',
    privacy: { pnl: true, qty: true, funds: false, orders: true },
    language: 'en',
    devices: 2,
    last_seen_at: NOW - 3_600_000,
    invite_expires_at: null,
    locked_until: null,
    telegram_linked: true,
    created_at: NOW - 86_400_000 * 9,
    version: 3,
    ...over,
  }
}

export function deviceOf(over: Partial<Device> = {}): Device {
  return {
    id: 'dev-1',
    label: 'Android 15 · Chrome 130',
    created_at: NOW - 86_400_000 * 2,
    last_seen_at: NOW - 600_000,
    place: null,
    current: false,
    notifications: null,
    ...over,
  }
}

export function inviteOf(over: Partial<Invite> = {}): Invite {
  return {
    member_id: 1,
    code: 'test-code-0001',
    path: '/family/join/test-code-0001',
    expires_at: NOW + 7_200_000,
    ...over,
  }
}

export function shareOf(over: Partial<Share> = {}): Share {
  return {
    id: 1,
    kind: 'watchlist',
    ref: '7',
    member_id: 1,
    role: null,
    level: 'view',
    expires_at: null,
    created_at: NOW - 1000,
    version: 1,
    ...over,
  }
}

export function lineOf(over: Partial<AuditLine> = {}): AuditLine {
  return {
    id: 1,
    at: NOW - 60_000,
    actor: 'owner',
    member_id: 1,
    kind: 'member_updated',
    device: null,
    place: null,
    detail: null,
    ...over,
  }
}
