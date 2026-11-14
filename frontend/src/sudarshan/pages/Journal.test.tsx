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
import { PART_FAILED } from '@/sudarshan/journal/PartBoundary'

const boom = vi.hoisted(() => ({ on: false }))
vi.mock('@/sudarshan/journal/TripsList', () => ({
  TripsList: ({ mode }: { mode: string }) => {
    if (boom.on) throw new Error('Cannot read properties of undefined')
    return <p data-testid="trips-of">{mode}</p>
  },
}))
vi.mock('@/utils/errorReporter', () => ({ reportClientError: vi.fn() }))

vi.mock('@/sudarshan/journal/TripSheet', () => ({
  TripSheet: ({
    tradeId,
    onClose,
    onDirty,
    asked,
  }: {
    tradeId: number
    onClose: () => void
    onDirty?: (dirty: boolean) => void
    asked?: number
  }) => (
    <div>
      <button type="button" onClick={onClose}>
        {`Sheet of trade ${tradeId}`}
      </button>
      <button type="button" onClick={() => onDirty?.(true)}>
        Type a note
      </button>
      <p data-testid="asked">{asked}</p>
    </div>
  ),
}))

import { back, renderBook, where } from '@/test/bookPages'
import Journal from './Journal'

const ON = { enabled: true, data: { enabled: true, fills_today: 0, open_trips: 0 } }

beforeEach(() => {
  call.mockReset()
  boom.on = false
})

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
    expect(screen.getByTestId('trips-of').textContent).toBe('sandbox')
    const on = screen.getByRole('switch', { name: 'Sandbox trades' })
    expect(on.getAttribute('aria-checked')).toBe('true')
    await user.click(on)
    await waitFor(() => expect(where()).toBe('/journal'))
  })

  it('lets Back close a sheet that holds nothing typed', async () => {
    call.mockResolvedValue(ON)
    // the sheet was opened from the list: its entry is the page's own
    renderBook('/journal', <Journal />, [
      '/dashboard',
      '/journal',
      { pathname: '/journal', search: '?trade=5', state: { sdOverlay: true } },
    ])
    await screen.findByRole('button', { name: 'Sheet of trade 5' })
    expect(screen.getByTestId('asked').textContent).toBe('0')
    // with nothing typed, Back closes it
    back()
    await waitFor(() => expect(where()).toBe('/journal'))
    expect(screen.queryByRole('button', { name: /Sheet of trade/ })).toBeNull()
  })

  it('asks instead of closing when Back meets a sheet holding changes', async () => {
    const user = userEvent.setup()
    call.mockResolvedValue(ON)
    renderBook('/journal', <Journal />, [
      '/dashboard',
      '/journal',
      { pathname: '/journal', search: '?trade=5', state: { sdOverlay: true } },
    ])
    await user.click(await screen.findByRole('button', { name: 'Type a note' }))
    back()
    await waitFor(() => expect(screen.getByTestId('asked').textContent).toBe('1'))
    expect(screen.getByRole('button', { name: 'Sheet of trade 5' })).toBeTruthy()
    await waitFor(() => expect(where()).toBe('/journal?trade=5'))
    // the answer closes it, and Back from the page then leaves the sheet's entry behind
    await user.click(screen.getByRole('button', { name: 'Sheet of trade 5' }))
    await waitFor(() => expect(where()).toBe('/journal'))
    expect(screen.queryByRole('button', { name: /Sheet of trade/ })).toBeNull()
  })

  it('says a part that cannot be drawn in words, keeps the page, and tries again (review M6)', async () => {
    const user = userEvent.setup()
    const quiet = vi.spyOn(console, 'error').mockImplementation(() => {})
    try {
      boom.on = true
      call.mockResolvedValue(ON)
      renderBook('/journal', <Journal />)
      expect(await screen.findByText(PART_FAILED)).toBeTruthy()
      expect(screen.getByRole('heading', { name: 'Journal' })).toBeTruthy()
      expect(screen.getByRole('tablist', { name: 'Journal parts' })).toBeTruthy()
      expect(screen.queryByText(/Cannot read properties/)).toBeNull()
      boom.on = false
      await user.click(screen.getByRole('button', { name: 'Try again' }))
      expect(await screen.findByTestId('trips-of')).toBeTruthy()
    } finally {
      quiet.mockRestore()
    }
  })

  it('opens a trade’s sheet from the address, and closing it leaves the page where it was', async () => {
    const user = userEvent.setup()
    call.mockResolvedValue(ON)
    renderBook('/journal?trade=5', <Journal />, ['/journal?trade=5'])
    await user.click(await screen.findByRole('button', { name: 'Sheet of trade 5' }))
    await waitFor(() => expect(where()).toBe('/journal'))
    expect(screen.queryByRole('button', { name: /Sheet of trade/ })).toBeNull()
  })
})
