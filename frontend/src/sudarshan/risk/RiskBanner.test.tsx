/**
 * The kill switch on every page (M20.7): the banner and the chip say it in words and an icon on
 * the loss tint, never colour alone; they move with the server's announcements and read again on
 * a reconnect, and show nothing while the switch is off or cannot be read.
 */

import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { act, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

type Handler = (payload?: unknown) => void
const socket = vi.hoisted(() => {
  const handlers = new Map<string, Set<(payload?: unknown) => void>>()
  return {
    handlers,
    on: (event: string, fn: (payload?: unknown) => void) => {
      if (!handlers.has(event)) handlers.set(event, new Set())
      handlers.get(event)?.add(fn)
    },
    off: (event: string, fn: (payload?: unknown) => void) => {
      handlers.get(event)?.delete(fn)
    },
  }
})

vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({ socket, playAlertSound: () => {} }),
}))

import { useThemeStore } from '@/stores/themeStore'
import { resetKillSwitchForTests } from './killSwitch'
import { KillSwitchNote, RiskBanner, RiskChip } from './RiskBanner'

const OFF = { known: true, on: false }
const ON = {
  known: true,
  on: true,
  since: '2026-10-27T05:12:00+00:00',
  since_clock: '10:42',
  surface: 'telegram',
  running: false,
  can_resume_at: '2026-10-27T10:43:00+05:30',
}

function emit(event: string, payload?: unknown) {
  for (const fn of socket.handlers.get(event) ?? []) (fn as Handler)(payload)
}

function serve(...answers: Array<{ status: number; body?: unknown }>) {
  const fetch = vi.fn(async () => {
    const next = answers.length > 1 ? answers.shift() : answers[0]
    return {
      ok: next?.status === 200,
      status: next?.status ?? 500,
      json: async () => next?.body,
    }
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

function renderShell() {
  return render(
    <MemoryRouter>
      <RiskBanner />
      <RiskChip />
      <KillSwitchNote />
    </MemoryRouter>
  )
}

beforeEach(() => {
  resetKillSwitchForTests()
  socket.handlers.clear()
  useThemeStore.setState({ appMode: 'live' })
})
afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the kill switch banner and chip', () => {
  it('show nothing while the switch is off', async () => {
    const fetch = serve({ status: 200, body: { data: { stage: 'sandbox', kill_switch: OFF } } })
    renderShell()
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith('/sudarshan/risk/state', expect.anything())
    )
    expect(screen.queryByTestId('risk-banner')).toBeNull()
    expect(screen.queryByTestId('risk-chip')).toBeNull()
    expect(screen.queryByTestId('kill-switch-note')).toBeNull()
  })

  it('say it in words and an icon, and lead to Settings > Risk', async () => {
    serve({ status: 200, body: { data: { stage: 'on', kill_switch: ON } } })
    renderShell()
    const banner = await screen.findByTestId('risk-banner')
    expect(banner).toHaveTextContent('Kill switch on · new orders refused · exits and cancels work')
    expect(banner.querySelector('svg')).toHaveAttribute('aria-hidden', 'true')
    expect(screen.getByRole('link')).toHaveAttribute('href', '/risk')
    const chip = screen.getByTestId('risk-chip')
    expect(chip).toHaveTextContent('Kill switch on')
    expect(chip.querySelector('svg')).toHaveAttribute('aria-hidden', 'true')
    expect(screen.getByTestId('kill-switch-note')).toHaveTextContent(
      'An order that closes a position you hold still goes'
    )
  })

  it("move with the server's announcement, without another read", async () => {
    const fetch = serve({ status: 200, body: { data: { stage: 'on', kill_switch: OFF } } })
    renderShell()
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1))
    act(() => emit('sudarshan_risk', ON))
    expect(await screen.findByTestId('risk-banner')).toBeInTheDocument()
    act(() => emit('sudarshan_risk', OFF))
    await waitFor(() => expect(screen.queryByTestId('risk-banner')).toBeNull())
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('read again when the connection comes back', async () => {
    const fetch = serve(
      { status: 200, body: { data: { stage: 'on', kill_switch: OFF } } },
      { status: 200, body: { data: { stage: 'on', kill_switch: ON } } }
    )
    renderShell()
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1))
    act(() => emit('connect'))
    expect(await screen.findByTestId('risk-banner')).toBeInTheDocument()
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it('show nothing when the switch cannot be read (a viewer, or no server answer)', async () => {
    const fetch = serve({ status: 403, body: { status: 'error' } })
    renderShell()
    await waitFor(() => expect(fetch).toHaveBeenCalled())
    expect(screen.queryByTestId('risk-banner')).toBeNull()
    act(() => emit('sudarshan_risk', { on: true })) // not a state the server sends
    expect(screen.queryByTestId('risk-banner')).toBeNull()
  })
})

describe("the daily loss limit's lock", () => {
  const LIVE_LOCKED = { live: { locked: true }, sandbox: { locked: false } }

  it('is said in words and a lock, for the book the page trades', async () => {
    serve({ status: 200, body: { data: { stage: 'on', kill_switch: OFF, daily: LIVE_LOCKED } } })
    renderShell()
    const banner = await screen.findByTestId('risk-banner')
    expect(banner).toHaveTextContent(
      'Daily loss limit reached · new orders refused until tomorrow · exits and cancels work'
    )
    expect(banner.querySelector('a')).toHaveClass('sd-risk-banner--day')
    expect(banner.querySelector('svg')).toHaveAttribute('aria-hidden', 'true')
    expect(screen.getByRole('link')).toHaveAttribute('href', '/risk')
    expect(screen.getByTestId('risk-chip')).toHaveTextContent('Day locked')
    expect(screen.getByTestId('kill-switch-note')).toHaveTextContent(
      'Daily loss limit reached: a new order is refused until tomorrow. An order that closes a position you hold still goes.'
    )
  })

  it("in Analyze mode is the sandbox's lock, not Live's", async () => {
    useThemeStore.setState({ appMode: 'analyzer' })
    const fetch = serve(
      { status: 200, body: { data: { stage: 'on', kill_switch: OFF, daily: LIVE_LOCKED } } },
      {
        status: 200,
        body: {
          data: { stage: 'on', kill_switch: OFF, daily: { sandbox: { locked: true } } },
        },
      }
    )
    renderShell()
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1))
    expect(screen.queryByTestId('risk-banner')).toBeNull()
    act(() => emit('sudarshan_risk_day', { pipe: 'sandbox', locked: true }))
    expect(await screen.findByTestId('risk-banner')).toHaveTextContent('Daily loss limit reached')
    expect(fetch).toHaveBeenCalledTimes(2) // the day locking is read again, then shown
  })

  it('gives way to the kill switch when both hold', async () => {
    serve({ status: 200, body: { data: { stage: 'on', kill_switch: ON, daily: LIVE_LOCKED } } })
    renderShell()
    expect(await screen.findByTestId('risk-banner')).toHaveTextContent('Kill switch on')
    expect(screen.getByTestId('risk-chip')).toHaveTextContent('Kill switch on')
    act(() => emit('sudarshan_risk', OFF)) // resumed: the day's lock still holds
    await waitFor(() =>
      expect(screen.getByTestId('risk-banner')).toHaveTextContent('Daily loss limit reached')
    )
  })
})

describe('the D look', () => {
  const RISK = join(process.cwd(), 'src', 'sudarshan', 'risk')

  it('has no dark styles in the risk layer', () => {
    for (const file of [
      'RiskBanner.tsx',
      'TurnOnSheet.tsx',
      'ResumeSheet.tsx',
      'parts.tsx',
      'DailyCard.tsx',
    ]) {
      expect(readFileSync(join(RISK, file), 'utf8')).not.toMatch(/\bdark:/)
    }
    const page = readFileSync(join(process.cwd(), 'src', 'sudarshan', 'pages', 'Risk.tsx'), 'utf8')
    expect(page).not.toMatch(/\bdark:/)
  })

  it('draws the banner and the chip from the loss tokens', () => {
    const css = readFileSync(join(process.cwd(), 'src', 'sudarshan', 'theme.css'), 'utf8')
    const banner = css.slice(
      css.indexOf('.sd-risk-banner {'),
      css.indexOf('}', css.indexOf('.sd-risk-banner {'))
    )
    expect(banner).toContain('background: var(--down-tint)')
    expect(banner).toContain('color: var(--down)')
    expect(banner).toContain('min-height: 3rem') // a 48dp target on a phone
  })

  it("draws the day's lock from the attention tint with its own ink", () => {
    const css = readFileSync(join(process.cwd(), 'src', 'sudarshan', 'theme.css'), 'utf8')
    const start = css.indexOf('.sd-risk-banner--day,')
    const day = css.slice(start, css.indexOf('}', start))
    expect(day).toContain('background: var(--gold-tint)')
    expect(day).toContain('color: var(--warning-text)')
  })
})
