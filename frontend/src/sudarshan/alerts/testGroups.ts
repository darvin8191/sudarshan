/** List alerts as the service answers them (M8.14), for the tests. Every value is made up. */
import type { GroupRow } from '@/api/marketAlerts'
import { alertRow, NOW } from '@/test/marketAlerts'

export function groupRow(patch: Partial<GroupRow> = {}): GroupRow {
  return {
    id: 7,
    version: 2,
    ref: 'list:swing',
    name: 'Swing',
    kind: 'move',
    params: { base: 'close', direction: 'up', percent: '2' },
    frequency: 'per_candle',
    candle_min: 0,
    cooldown_s: 0,
    ends_at: null,
    status: 'active',
    status_reason: '',
    delivery: 'first_then_digest',
    digest_min: 5,
    note: '',
    covered: 24,
    left_out: [],
    synced_at: NOW,
    created_at: NOW - 86_400_000,
    updated_at: NOW - 86_400_000,
    members: 24,
    words: {
      title: 'Swing list · Up 2% on the day',
      condition: 'Up 2% on the day',
      meta: 'Once per candle',
    },
    follow: '',
    ...patch,
  }
}

/** A member alert of a group. */
export function memberRow(id: number, symbol: string, group = 7) {
  return alertRow({
    id,
    symbol,
    group_id: group,
    kind: 'move',
    params: { base: 'close', direction: 'up', percent: '2' },
    words: { condition: 'Up 2% on the day', meta: 'Once per candle', checks_at: null },
  })
}
