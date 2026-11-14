/**
 * The card that stands where the indices and the P&L are while prices cannot be read (M3.4, D3,
 * decision 3): the cause in words, Reconnect or the API key page, and when auto-login runs.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

const status = vi.hoisted(() => ({ current: null as Record<string, unknown> | null }))
vi.mock('@/api/autologin', () => ({
  autologinApi: {
    status: async () => {
      if (!status.current) throw new Error('not read')
      return status.current
    },
  },
}))

import { at } from '@/test/dayFacts'
import { render, screen } from '@/test/test-utils'
import { Lapsed } from './Lapsed'

afterEach(() => {
  status.current = null
  vi.useRealTimers()
})

describe('the lapsed-login card', () => {
  it("says prices and orders need today's Upstox login, with Reconnect to the broker page", () => {
    render(<Lapsed reason="upstox-logged-out" />)
    expect(
      screen.getByRole('heading', { name: "Prices and orders need today's Upstox login" })
    ).toBeVisible()
    expect(screen.getByRole('link', { name: 'Reconnect' })).toHaveAttribute('href', '/broker')
    expect(screen.queryByRole('link', { name: /Auto login/ })).toBeNull()
  })

  it('says when auto-login runs next: today by its time, another day by its weekday', async () => {
    vi.useFakeTimers({ now: at('2026-11-17', '04:10'), toFake: ['Date'] })
    status.current = { enabled: true, running: false, next_run: '2026-11-17T08:35:00+05:30' }
    const { unmount } = render(<Lapsed reason="upstox-logged-out" />)
    expect(await screen.findByRole('link', { name: 'Auto login runs at 08:35' })).toHaveAttribute(
      'href',
      '/autologin'
    )
    unmount()

    status.current = { enabled: true, running: false, next_run: '2026-11-18T08:35:00+05:30' }
    render(<Lapsed reason="upstox-logged-out" />)
    expect(await screen.findByRole('link', { name: 'Auto login runs Wed 08:35' })).toBeVisible()
  })

  it('says auto-login is logging in now while it runs, and nothing when it is off', async () => {
    status.current = { enabled: true, running: true, next_run: null }
    const { unmount } = render(<Lapsed reason="upstox-logged-out" />)
    expect(await screen.findByRole('link', { name: 'Auto login is logging in now' })).toBeVisible()
    unmount()

    status.current = { enabled: false, running: false, next_run: '2026-11-18T08:35:00+05:30' }
    render(<Lapsed reason="upstox-logged-out" />)
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(screen.queryByRole('link', { name: /Auto login/ })).toBeNull()
  })

  it('without an API key, says to make one and links to its page', () => {
    render(<Lapsed reason="no-api-key" />)
    expect(screen.getByRole('heading', { name: 'Prices need an API key' })).toBeVisible()
    expect(screen.getByRole('link', { name: 'Create an API key' })).toHaveAttribute(
      'href',
      '/apikey'
    )
    expect(screen.queryByRole('link', { name: 'Reconnect' })).toBeNull()
  })
})
