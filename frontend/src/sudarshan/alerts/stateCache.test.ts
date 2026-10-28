/**
 * The Alerts page's cached state and History (M10.3, M10.4, M10.7), changed by pushes and by the
 * owner's own changes: an out-of-order push never undoes a newer row, the server's answer takes
 * a row's place only when it is as new, and a settled delivery never undoes a later one.
 */
import { describe, expect, it } from 'vitest'
import type { AlertEvent, AlertRow, MarketAlertsState } from '@/api/marketAlerts'
import { applyDeliveries, applyStatePush, dropRow, mergeFirstPage, putRow } from './stateCache'

const row = (patch: Partial<AlertRow> = {}): AlertRow =>
  ({
    id: 12,
    version: 3,
    status: 'active',
    status_reason: '',
    check: null,
    fired_today: 0,
    words: { condition: 'Crosses above ₹100.00', meta: 'Once', checks_at: null },
    ...patch,
  }) as AlertRow

const state = (alerts: AlertRow[]): MarketAlertsState =>
  ({
    enabled: true,
    engine: { code: 'ok', line: 'Watching · Real-time', real_time: true },
    alerts,
  }) as MarketAlertsState

const engine = { code: 'ok' as const, line: 'Watching · Real-time', real_time: true }

describe('the cached state', () => {
  it('ignores a push older than the row it holds', () => {
    const { state: next, reread } = applyStatePush(state([row({ version: 5 })]), {
      engine,
      alerts: [
        { id: 12, version: 4, status: 'paused', status_reason: '', check: null, fired_today: 0 },
      ],
      removed: [],
      server_time: 1,
    })
    expect(next.alerts[0]).toMatchObject({ version: 5, status: 'active' })
    expect(reread).toBe(false)
  })

  it('takes the server’s answer for a row only when it is as new as the one it holds', () => {
    const held = state([row({ version: 5, status: 'paused' })])
    expect(putRow(held, row({ version: 6, status: 'active' })).alerts[0].status).toBe('active')
    expect(putRow(held, row({ version: 4, status: 'stopped' })).alerts[0].status).toBe('paused')
    expect(putRow(held, row({ id: 13 })).alerts.map((each) => each.id)).toEqual([12, 13])
  })

  it('drops a deleted row', () => {
    expect(dropRow(state([row(), row({ id: 13 })]), 12).alerts.map((each) => each.id)).toEqual([13])
  })
})

describe('the cached History', () => {
  const event = (deliveries: AlertEvent['deliveries']) => ({ id: 7, deliveries }) as AlertEvent

  it('keeps a later delivery over an earlier one that arrives after it', () => {
    const later = {
      status: 'not_sent' as const,
      reason: 'Telegram did not confirm it after 3 tries',
      attempts: 3,
      updated_at: 20,
    }
    const earlier = { status: 'pending' as const, reason: '', attempts: 1, updated_at: 10 }
    const page = { events: [event({ telegram: later })], more: false }

    const kept = applyDeliveries(page, new Map([[7, { telegram: earlier }]]))
    expect(kept).toBe(page)
  })

  it('replaces what every page of the History tab holds, and leaves the same object when nothing changed', () => {
    const sent = {
      status: 'sent' as const,
      reason: 'Shown on 1 open screen',
      attempts: 1,
      updated_at: 30,
    }
    const data = { pages: [{ events: [event({})], more: true }], pageParams: [null] }

    const next = applyDeliveries(data, new Map([[7, { app: sent }]]))
    expect(next.pages[0].events[0].deliveries.app).toEqual(sent)
    expect(applyDeliveries(next, new Map([[7, { app: sent }]]))).toBe(next)
    const later = { ...sent, reason: 'Shown on 2 open screens' }
    const replaced = applyDeliveries(next, new Map([[7, { app: later }]]))
    expect(replaced.pages[0].events[0].deliveries.app).toEqual(later)
    expect(applyDeliveries(data, new Map([[8, { app: sent }]]))).toBe(data)
  })
})

describe('History’s newest page read again', () => {
  const event = (id: number, deliveries: AlertEvent['deliveries'] = {}) =>
    ({ id, deliveries }) as AlertEvent
  const ids = (data: { pages: Array<{ events: AlertEvent[] }> } | null) =>
    data?.pages.map((page) => page.events.map((each) => each.id)) ?? null

  it('takes the fresh copies and what is newer, keeping the older pages as they are', () => {
    const held = {
      pages: [
        { events: [event(5), event(4)], more: true },
        { events: [event(3)], more: false },
      ],
      pageParams: [null, 4],
    }
    const sent = { status: 'sent' as const, reason: '', attempts: 1, updated_at: 9 }
    const merged = mergeFirstPage(held, {
      events: [event(7), event(6), event(5, { app: sent })],
      more: true,
    })

    expect(ids(merged)).toEqual([[7, 6, 5, 4], [3]])
    expect(merged?.pages[0].events[2].deliveries.app).toEqual(sent)
    expect(merged?.pages[1]).toBe(held.pages[1])
    expect(merged?.pageParams).toEqual([null, 4])
  })

  it('takes out of the older pages what the fresh page now holds', () => {
    const held = {
      pages: [
        { events: [event(5)], more: true },
        { events: [event(4), event(3)], more: false },
      ],
      pageParams: [null, 5],
    }
    expect(
      ids(mergeFirstPage(held, { events: [event(6), event(5), event(4)], more: true }))
    ).toEqual([[6, 5, 4], [3]])
  })

  it('asks for the whole list when more fired since than one page holds', () => {
    const held = { pages: [{ events: [event(5)], more: false }], pageParams: [null] }
    expect(mergeFirstPage(held, { events: [event(60), event(11)], more: true })).toBeNull()
  })
})
