/**
 * The kill switch is said in both shells and in the account controls (M20.7): the banner in
 * Layout (above the navbar) and in FullWidthLayout (above /trading and the other full-screen
 * pages), and the chip in AccountControls, which the navbar and the /trading toolbar share.
 */
import { render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useAuthStore } from '@/stores/authStore'
import { useThemeStore } from '@/stores/themeStore'

vi.mock('@/components/socket/SocketProvider', () => ({
  SocketProvider: ({ children }: { children: ReactNode }) => children,
  useSocketContext: () => ({ socket: null, playAlertSound: () => {} }),
}))
vi.mock('@/hooks/useProfileMenuItems', () => ({ useProfileMenuItems: () => [] }))

import { noteKillSwitch, resetKillSwitchForTests } from '@/sudarshan/risk/killSwitch'
import { AccountControls } from './AccountControls'
import { FullWidthLayout } from './FullWidthLayout'
import { Layout } from './Layout'

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

const ON = { known: true, on: true, since_clock: '10:42' }

beforeEach(() => {
  resetKillSwitchForTests()
  useThemeStore.setState({ appMode: 'live' })
  useAuthStore.setState({
    isAuthenticated: true,
    user: { username: 'owner', broker: 'upstox' } as never,
  })
})
afterEach(() => {
  vi.unstubAllGlobals()
  useAuthStore.setState({ user: null, isAuthenticated: false })
})

function inShell(shell: ReactNode) {
  return render(
    <MemoryRouter initialEntries={['/page']}>
      <Routes>
        <Route element={shell}>
          <Route path="/page" element={<p>Page</p>} />
        </Route>
      </Routes>
    </MemoryRouter>
  )
}

describe('the kill switch in the shells', () => {
  it.each([
    ['Layout', <Layout key="a" />],
    ['FullWidthLayout', <FullWidthLayout key="b" />],
  ])('is a banner above the page in %s', async (_name, shell) => {
    serverSays('on', ON)
    inShell(shell)
    expect(await screen.findByTestId('risk-banner')).toHaveTextContent('new orders refused')
    expect(screen.getByText('Page')).toBeInTheDocument()
  })

  it.each([
    ['Layout', <Layout key="a" />],
    ['FullWidthLayout', <FullWidthLayout key="b" />],
  ])('is no banner in %s while it is off', async (_name, shell) => {
    serverSays('on', { known: true, on: false })
    inShell(shell)
    await screen.findByText('Page')
    await vi.waitFor(() => expect(fetch).toHaveBeenCalled())
    expect(screen.queryByTestId('risk-banner')).toBeNull()
  })

  it('is a chip in the account controls, which the shell keeps in step', async () => {
    noteKillSwitch(ON, 'on')
    render(
      <MemoryRouter>
        <AccountControls variant="toolbar" />
      </MemoryRouter>
    )
    expect(await screen.findByTestId('risk-chip')).toHaveTextContent('Kill switch on')
  })
})
