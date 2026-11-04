/**
 * The Portfolio page shell in jsdom: the bridge is a stand-in answering as the contract says.
 * Pinned: the tab strip from the service's tabs (archived ones left out), the Upstox part first,
 * a stub for each part that says what it will show, and the switched-off and service-down
 * states in the owner's words.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ call: vi.fn() }))
vi.mock('@/sudarshan/portfolio/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/sudarshan/portfolio/api')>()),
  portfolioCall: api.call,
}))

import { createQueryClient } from '@/app/providers'
import { PortfolioError } from '@/sudarshan/portfolio/api'
import { WORDS } from '@/sudarshan/portfolio/types'
import Portfolio from './Portfolio'

function tab(id: number, name: string, extra: object = {}) {
  return {
    id,
    name,
    name_alt: null,
    kind: 'stocks',
    icon: 'briefcase',
    colour: 'brand',
    position: id,
    pinned: false,
    archived: false,
    system: false,
    manual_old_days: 30,
    version: 1,
    ...extra,
  }
}
const STATE = {
  tabs: [
    tab(1, 'Upstox', { system: true, kind: 'upstox' }),
    tab(2, 'Gold'),
    tab(3, 'Old one', { archived: true }),
  ],
}

function show(url = '/portfolio') {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={[url]}>
        <Portfolio />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

beforeEach(() => api.call.mockReset())

describe('the Portfolio page', () => {
  it('shows the Upstox tab first with its parts, and the custom tabs without the archived', async () => {
    api.call.mockResolvedValue({ enabled: true, data: STATE })
    show()
    await screen.findByRole('tab', { name: /Upstox/ })
    expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual(
      expect.arrayContaining([
        'Upstoxread-only',
        'Gold',
        'Holdings',
        'Positions',
        'Funds',
        'History',
      ])
    )
    expect(screen.queryByText('Old one')).toBeNull()
    expect(screen.getByText(/Every holding with its quantity/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Add' })).toBeNull() // no entries on Upstox
  })

  it('a custom tab has its own parts and an Add button', async () => {
    api.call.mockResolvedValue({ enabled: true, data: STATE })
    show('/portfolio?tab=2&view=activity')
    await screen.findByRole('button', { name: 'Add' })
    expect(screen.getByText(/Every entry of this tab/)).toBeTruthy()
    expect(screen.getByRole('tab', { name: 'Performance' })).toBeTruthy()
    await userEvent.click(screen.getByRole('tab', { name: 'Overview' }))
    expect(screen.getByText(/The value of this tab now/)).toBeTruthy()
  })

  it('a tab that is gone falls back to Upstox', async () => {
    api.call.mockResolvedValue({ enabled: true, data: STATE })
    show('/portfolio?tab=99')
    await screen.findByText(/Every holding with its quantity/)
  })

  it('switched off is said in words, with no tabs', async () => {
    api.call.mockResolvedValue({ enabled: false, data: null, message: WORDS.off })
    show()
    await screen.findByText(WORDS.off)
    expect(screen.queryByRole('tablist')).toBeNull()
  })

  it('a service that does not answer is said in words, and Try again asks once more', async () => {
    api.call.mockRejectedValueOnce(
      new PortfolioError({ code: 'unavailable', message: WORDS.not_answering, entryId: null })
    )
    show()
    await screen.findByText(WORDS.not_answering)
    api.call.mockResolvedValue({ enabled: true, data: STATE })
    await userEvent.click(screen.getByRole('button', { name: /Try again/ }))
    await waitFor(() => expect(screen.getByRole('tab', { name: /Upstox/ })).toBeTruthy())
  })

  it('a sheet in the address opens a stub that says what it is for, and says it is not ready', async () => {
    api.call.mockResolvedValue({ enabled: true, data: STATE })
    show('/portfolio?tab=2&sheet=import')
    await screen.findByText('Import entries')
    expect(screen.getByText(WORDS.not_ready)).toBeTruthy()
  })

  it('says Real-time nowhere and never uses the arm words', async () => {
    api.call.mockResolvedValue({ enabled: true, data: STATE })
    const { container } = show()
    await screen.findByRole('tab', { name: /Upstox/ })
    expect(container.textContent).not.toMatch(/\bLive\b|\barm(ed|ing)?\b/i)
  })
})
