/**
 * The market alerts client (M10.4): History's reads carry React Query's abort signal to the
 * request, so a read no longer wanted (a page left, a newer read) stops on the wire too.
 */
import { describe, expect, it, vi } from 'vitest'

const web = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('@/api/client', () => ({ webClient: web }))

import { marketAlertsApi } from './marketAlerts'

describe('History reads', () => {
  it('send their page, size and alert, and React Query’s signal with them', async () => {
    web.get.mockResolvedValue({ data: { status: 'success', data: { events: [], more: false } } })
    const signal = new AbortController().signal

    await marketAlertsApi.events({ before: 344, limit: 50, alert: 12 }, signal)

    const [path, config] = web.get.mock.calls[0]
    expect(path).toBe('/sudarshan/alerts/api/events')
    expect(config.params).toEqual({ before: 344, limit: 50, alert: 12 })
    expect(config.signal).toBe(signal)
  })
})
