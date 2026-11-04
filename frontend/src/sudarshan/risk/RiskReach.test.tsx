/**
 * What the screens say about the kill switch depends on where the guard reaches (M20.7, review
 * M1): with the guard at "sandbox" a Live page must not claim new orders are refused, and a
 * switch whose state cannot be read says so while the gate refuses every new order (LOW 4).
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({ socket: null, playAlertSound: () => {} }),
}))

import { useThemeStore } from '@/stores/themeStore'
import { actsOn, reachOf, resetKillSwitchForTests } from './killSwitch'
import { KillSwitchNote, RiskBanner, RiskChip } from './RiskBanner'

function serverSays(stage: string, killSwitch: object) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ data: { stage, kill_switch: killSwitch } }),
    }))
  )
}

function renderAll() {
  render(
    <MemoryRouter>
      <RiskBanner />
      <RiskChip />
      <KillSwitchNote />
    </MemoryRouter>
  )
}

const ON = { known: true, on: true, since_clock: '10:42' }

beforeEach(() => {
  resetKillSwitchForTests()
  useThemeStore.setState({ appMode: 'live' })
})
afterEach(() => vi.unstubAllGlobals())

describe('where the switch reaches', () => {
  it('is the book the page trades, or the sandbox only, or nowhere', () => {
    expect(actsOn('on', false)).toBe(true)
    expect(actsOn('sandbox', true)).toBe(true)
    expect(actsOn('sandbox', false)).toBe(false)
    expect(actsOn('off', true)).toBe(false)
    expect(reachOf('on', false)).toBe('here')
    expect(reachOf('sandbox', false)).toBe('sandbox')
    expect(reachOf('sandbox', true)).toBe('here')
    expect(reachOf('off', false)).toBe('none')
    expect(reachOf(undefined, false)).toBe('none')
  })
})

describe('with the guard at the sandbox stage', () => {
  it('says in Live mode that Live orders are not checked, and not that orders are refused', async () => {
    serverSays('sandbox', ON)
    renderAll()
    const banner = await screen.findByTestId('risk-banner')
    expect(banner).toHaveTextContent('for the sandbox only')
    expect(banner).toHaveTextContent('Live orders are not checked')
    expect(banner).not.toHaveTextContent('new orders refused')
    expect(screen.getByTestId('risk-chip')).toHaveTextContent('Sandbox kill switch on')
    expect(screen.getByTestId('risk-chip')).toHaveAttribute(
      'title',
      'Kill switch on for the sandbox only: Live orders are not checked'
    )
    expect(screen.getByTestId('kill-switch-note')).toHaveTextContent(
      'Kill switch on for the sandbox only: Live orders are not checked.'
    )
    expect(screen.getByTestId('kill-switch-note')).not.toHaveTextContent('is refused')
  })

  it('says new orders are refused in Analyze mode, whose orders it does refuse', async () => {
    useThemeStore.setState({ appMode: 'analyzer' })
    serverSays('sandbox', ON)
    renderAll()
    expect(await screen.findByTestId('risk-banner')).toHaveTextContent('new orders refused')
    expect(screen.getByTestId('kill-switch-note')).toHaveTextContent('a new order is refused')
    expect(screen.getByTestId('risk-chip')).toHaveTextContent('Kill switch on')
  })

  it('says order checks are off when the guard is off on the server', async () => {
    serverSays('off', ON)
    renderAll()
    expect(await screen.findByTestId('risk-banner')).toHaveTextContent('order checks are off')
    expect(screen.getByTestId('kill-switch-note')).toHaveTextContent('orders are not checked')
  })
})

describe('a switch whose state cannot be read', () => {
  it('is said on the banner, the chip and the ticket, with new orders refused meanwhile', async () => {
    serverSays('on', { known: false, on: null })
    renderAll()
    const banner = await screen.findByTestId('risk-banner')
    expect(banner).toHaveTextContent('state cannot be read')
    expect(banner).toHaveTextContent('new orders are refused until it can')
    expect(screen.getByTestId('risk-chip')).toHaveTextContent('Kill switch unread')
    expect(screen.getByTestId('kill-switch-note')).toHaveTextContent('cannot be read')
  })
})
