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
import { OPEN_GUARD } from '@/sudarshan/watchlist/openGuard'
import { alertRow, NOW } from '@/test/marketAlerts'
import { AlertSheet, type SheetLive } from './AlertSheet'
import { resetAlertActionsForTests } from './actions'

const LIVE: SheetLive = {
  line: undefined,
  followed: false,
  feed: { connected: true, fallback: false, epoch: 1 },
  now: NOW,
  marketOpen: true,
}

function renderSheet(
  row: AlertRow,
  group: { id: number; name: string } | null = null,
  onOpenGroup: (id: number) => void = () => {}
) {
  const client = createQueryClient()
  const tree = (shown: AlertRow) => (
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AlertSheet
          row={shown}
          live={LIVE}
          onClose={() => {}}
          onEdit={() => {}}
          group={group}
          onOpenGroup={onOpenGroup}
        />
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

describe('a member of a list alert (A7)', () => {
  const member = () => alertRow({ group_id: 7, note: 'kept', fired_today: 1 })

  it('says which list alert it is part of and hides Edit, Pause, Resume and Delete', () => {
    renderSheet(member(), { id: 7, name: 'Swing' })
    expect(screen.getByText('Part of the list alert Swing')).toBeInTheDocument()
    for (const name of ['Edit', 'Pause', 'Resume', 'Restart', 'Delete'])
      expect(screen.queryByRole('button', { name })).toBeNull()
    // The rest of the sheet stays: its note, its chart and its history.
    expect(screen.getByText('kept')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Chart' })).toBeInTheDocument()
    expect(api.events).toHaveBeenCalled()
  })

  it('opens the list alert from its button, by the group id', async () => {
    const open = vi.fn()
    const user = userEvent.setup()
    renderSheet(member(), { id: 7, name: 'Swing' }, open)
    vi.setSystemTime(NOW + 2_000) // past the half second a sheet takes no tap
    await user.click(screen.getByRole('button', { name: 'Open the list alert' }))
    expect(open).toHaveBeenCalledWith(7)
  })

  it('leaves a sheet of an alert with no group as it was', () => {
    renderSheet(alertRow())
    expect(screen.queryByText(/Part of/)).toBeNull()
    expect(screen.queryByRole('button', { name: 'Open the list alert' })).toBeNull()
    for (const name of ['Edit', 'Pause', 'Delete'])
      expect(screen.getByRole('button', { name })).toBeInTheDocument()
  })
})
