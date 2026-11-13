/** The Journal shell: its states in words, its parts, and the Sandbox switch that never mixes modes. */
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const call = vi.hoisted(() => vi.fn())
vi.mock('@/sudarshan/journal/api', async (original) => ({
  ...(await original<typeof import('@/sudarshan/journal/api')>()),
  journalCall: call,
}))

import { JournalError } from '@/sudarshan/journal/api'
import { renderBook, where } from '@/test/bookPages'
import Journal from './Journal'

const ON = { enabled: true, data: { enabled: true, fills_today: 0, open_trips: 0 } }

beforeEach(() => call.mockReset())

describe('Journal', () => {
  it('says the journal is switched off, in the server’s words', async () => {
    call.mockResolvedValue({
      enabled: false,
      data: null,
      message: 'The journal is switched off on the server.',
    })
    renderBook('/journal', <Journal />)
    expect(await screen.findByText('The journal is switched off on the server.')).toBeTruthy()
    expect(screen.queryByRole('tablist')).toBeNull()
  })

  it('says why it could not read, with the next step, and tries again on request', async () => {
    const user = userEvent.setup()
    call.mockRejectedValueOnce(
      new JournalError({
        code: 'unavailable',
        message:
          'The journal service is not answering. Your trades are safe; try again in a minute.',
      })
    )
    renderBook('/journal', <Journal />)
    expect(await screen.findByText(/Your trades are safe; try again in a minute/)).toBeTruthy()
    call.mockResolvedValue(ON)
    await user.click(screen.getByRole('button', { name: /Try again/ }))
    expect(await screen.findByRole('tablist', { name: 'Journal parts' })).toBeTruthy()
  })

  it('shows the three parts and keeps the one shown in the address', async () => {
    const user = userEvent.setup()
    call.mockResolvedValue(ON)
    renderBook('/journal', <Journal />)
    await screen.findByRole('tab', { name: 'Trades' })
    expect(screen.getByRole('tab', { name: 'Trades' }).getAttribute('aria-selected')).toBe('true')
    await user.click(screen.getByRole('tab', { name: 'Playbooks' }))
    expect(where()).toBe('/journal?view=playbooks')
    expect(screen.getByRole('tab', { name: 'Playbooks' }).getAttribute('aria-selected')).toBe(
      'true'
    )
  })

  it('the Sandbox chip switches the mode, with its word, and back', async () => {
    const user = userEvent.setup()
    call.mockResolvedValue(ON)
    renderBook('/journal', <Journal />)
    const chip = await screen.findByRole('switch', { name: 'Sandbox' })
    expect(chip.getAttribute('aria-checked')).toBe('false')
    await user.click(chip)
    await waitFor(() => expect(where()).toBe('/journal?mode=sandbox'))
    const on = screen.getByRole('switch', { name: 'Sandbox trades' })
    expect(on.getAttribute('aria-checked')).toBe('true')
    await user.click(on)
    await waitFor(() => expect(where()).toBe('/journal'))
  })
})
