/**
 * Pause, Resume, Restart and Delete as sent (M10.3): one change at a time for an alert, however
 * often it is asked for, and a refusal read again like a conflict, its sentence the server's.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ change: vi.fn(), remove: vi.fn() }))
vi.mock('@/api/marketAlerts', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/marketAlerts')>()),
  marketAlertsApi: api,
}))
const toasts = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}))
vi.mock('@/utils/toast', () => ({ toast: toasts }))

import { type AlertRow, AlertsError } from '@/api/marketAlerts'
import { createQueryClient } from '@/app/providers'
import { alertRow } from '@/test/marketAlerts'
import { resetAlertActionsForTests, sendChange } from './actions'
import { STATE_KEY } from './stateCache'

beforeEach(() => {
  resetAlertActionsForTests()
  for (const fn of [...Object.values(api), ...Object.values(toasts)]) fn.mockReset()
})

afterEach(() => {
  resetAlertActionsForTests()
})

describe('a change for one alert', () => {
  it('is sent once however often it is asked for, whatever else is asked meanwhile', async () => {
    let answer: (row: AlertRow) => void = () => {}
    api.change.mockReturnValue(
      new Promise<AlertRow>((resolve) => {
        answer = resolve
      })
    )
    const client = createQueryClient()
    const row = alertRow()

    const first = sendChange(client, row, 'pause')
    void sendChange(client, row, 'pause')
    void sendChange(client, row, 'delete')
    expect(api.change).toHaveBeenCalledTimes(1)
    expect(api.remove).not.toHaveBeenCalled()

    answer(alertRow({ version: 4, status: 'paused' }))
    await first
    await sendChange(client, alertRow({ version: 4, status: 'paused' }), 'resume')
    expect(api.change).toHaveBeenCalledTimes(2)
  })

  it('reads the alerts again after a refusal, and shows its sentence as it came', async () => {
    api.change.mockRejectedValue(
      new AlertsError({ code: 'refused', message: 'This alert is already paused.', field: null })
    )
    const client = createQueryClient()
    const invalidate = vi.spyOn(client, 'invalidateQueries')

    await sendChange(client, alertRow(), 'pause')
    expect(toasts.error).toHaveBeenCalledWith('This alert is already paused.')
    expect(invalidate).toHaveBeenCalledWith({ queryKey: STATE_KEY })
  })
})
