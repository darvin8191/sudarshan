/**
 * An alert's sheet (M10.3) when the server changes the alert while it is open: a Once alert that
 * fires, one paused after 50 fires. Its middle button then means something else under the
 * finger, so the row takes no tap for the half second a sheet does when it opens.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ change: vi.fn(), remove: vi.fn(), events: vi.fn() }))
vi.mock('@/api/marketAlerts', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/marketAlerts')>()),
  marketAlertsApi: api,
}))
vi.mock('@/utils/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}))

import type { AlertRow } from '@/api/marketAlerts'
import { createQueryClient } from '@/app/providers'
import { alertRow, NOW } from '@/test/marketAlerts'
import { OPEN_GUARD } from '@/sudarshan/watchlist/openGuard'
import { resetAlertActionsForTests } from './actions'
import { AlertSheet, type SheetLive } from './AlertSheet'

const LIVE: SheetLive = {
  line: undefined,
  followed: false,
  feed: { connected: true, fallback: false, epoch: 1 },
  now: NOW,
  marketOpen: true,
}

function renderSheet(row: AlertRow) {
  const client = createQueryClient()
  const tree = (shown: AlertRow) => (
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AlertSheet row={shown} live={LIVE} onClose={() => {}} onEdit={() => {}} />
      </MemoryRouter>
    </QueryClientProvider>
  )
  const view = render(tree(row))
  return { rerender: (shown: AlertRow) => view.rerender(tree(shown)) }
}

beforeEach(() => {
  resetAlertActionsForTests()
  for (const fn of Object.values(api)) fn.mockReset()
  api.events.mockResolvedValue({ events: [], more: false })
  // The guard reads the clock: hold it still, so a slow machine cannot end the half second early.
  OPEN_GUARD.ms = 500
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(NOW)
})

afterEach(() => {
  vi.useRealTimers()
})

describe('a status changed by the server while the sheet is open', () => {
  it('takes no tap for half a second, then does what its button now says', async () => {
    api.change.mockResolvedValue(alertRow({ version: 4, status: 'active' }))
    const user = userEvent.setup()
    const { rerender } = renderSheet(alertRow({ status: 'active' }))
    vi.setSystemTime(NOW + 2_000)

    // The push for a Once alert that fired: the same version, the engine's own stop.
    rerender(alertRow({ status: 'stopped', status_reason: 'Fired once at 10:42 on 28 Oct' }))
    const restart = screen.getByRole('button', { name: 'Restart' })
    await user.click(restart)
    expect(api.change).not.toHaveBeenCalled()

    vi.setSystemTime(NOW + 2_600)
    await user.click(restart)
    expect(api.change).toHaveBeenCalledWith(12, { version: 3, action: 'restart' })
  })

  it('leaves the buttons alone while the status stays', async () => {
    api.change.mockResolvedValue(alertRow({ version: 4, status: 'paused' }))
    const user = userEvent.setup()
    const { rerender } = renderSheet(alertRow({ status: 'active' }))
    vi.setSystemTime(NOW + 2_000)

    rerender(alertRow({ status: 'active', fired_today: 2 }))
    await user.click(screen.getByRole('button', { name: 'Pause' }))
    expect(api.change).toHaveBeenCalledWith(12, { version: 3, action: 'pause' })
  })
})
