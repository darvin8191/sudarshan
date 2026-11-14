/**
 * The money views of phase P7 on the Portfolio page (F0): beside the tabs only while the service
 * says their switch is on; `?view=tax` opens the Tax view and leaves no tab selected; a view
 * whose switch is off falls back to the tabs; a tab chip leaves the money view.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ call: vi.fn() }))
vi.mock('@/sudarshan/portfolio/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/sudarshan/portfolio/api')>()),
  portfolioCall: api.call,
}))

import { createQueryClient } from '@/app/providers'
import { PortfolioError } from '@/sudarshan/portfolio/api'
import { view } from '@/sudarshan/portfolio/tab/fixtures'
import { TAX_OFF } from '@/sudarshan/portfolio/tax/TaxView'
import Portfolio from './Portfolio'

const UPSTOX = {
  id: 1,
  name: 'Upstox',
  name_alt: null,
  kind: 'upstox',
  icon: 'landmark',
  colour: 'brand',
  position: 0,
  pinned: true,
  archived: false,
  system: true,
  manual_old_days: 30,
  version: 1,
}
const GOLD = {
  ...UPSTOX,
  id: 2,
  name: 'Gold',
  kind: 'gold',
  icon: 'gem',
  system: false,
  position: 1,
}

function state(switches?: Record<string, boolean>) {
  return { tabs: [UPSTOX, GOLD], accounts: [], members: [], ...(switches ? { switches } : {}) }
}

function serve(answer: object) {
  api.call.mockImplementation(async (op: string) => {
    if (op === 'state') return { enabled: true, data: answer }
    if (op === 'tab_get') return { enabled: true, data: view([]) }
    if (op === 'upstox_book') {
      return {
        enabled: true,
        data: { holdings: { state: 'ok', read_at: 1, note: null, data: [] } },
      }
    }
    if (op === 'tax_get')
      throw new PortfolioError({ code: 'not_ready', message: '', entryId: null })
    throw new Error(`unexpected op ${op}`)
  })
}

let where = ''
function Where() {
  where = useLocation().search
  return null
}

function show(url = '/portfolio') {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={[url]}>
        <Portfolio />
        <Where />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  api.call.mockReset()
  where = ''
})

describe('the money views beside the tabs', () => {
  it('none is offered while the service names no switch, or names them off', async () => {
    for (const answer of [state(), state({ tax: false, insights: false, goals: false })]) {
      serve(answer)
      const { unmount } = show()
      await screen.findByRole('tab', { name: /Upstox/ })
      expect(screen.queryByRole('tablist', { name: 'Money views' })).toBeNull()
      unmount()
    }
  })

  it('each switch that is on brings its chips', async () => {
    serve(state({ tax: true, insights: true, goals: false }))
    show()
    const chips = await screen.findByRole('tablist', { name: 'Money views' })
    expect([...chips.querySelectorAll('[role="tab"]')].map((c) => c.textContent)).toEqual([
      'Tax',
      'Risk',
      'Calendar',
    ])
  })

  it('a chip opens its view in the address, its screen in place of the tab, and no tab selected', async () => {
    serve(state({ tax: true }))
    show('/portfolio?tab=2&view=overview')
    await userEvent.click(await screen.findByRole('tab', { name: 'Tax' }))
    await waitFor(() => expect(where).toBe('?view=tax'))
    expect(await screen.findByText(TAX_OFF)).toBeTruthy()
    expect(screen.getByRole('tab', { name: 'Tax' }).getAttribute('aria-selected')).toBe('true')
    for (const name of [/Upstox/, 'Gold']) {
      expect(screen.getByRole('tab', { name }).getAttribute('aria-selected')).toBe('false')
    }
    expect(screen.queryByRole('button', { name: 'Add' })).toBeNull() // no tab's actions here
  })

  it('a tab chip leaves the money view', async () => {
    serve(state({ tax: true }))
    show('/portfolio?view=tax')
    await screen.findByText(TAX_OFF)
    await userEvent.click(screen.getByRole('tab', { name: 'Gold' }))
    await waitFor(() => expect(where).toBe('?tab=2'))
    expect(screen.queryByText(TAX_OFF)).toBeNull()
  })

  it('a view whose switch is off falls back to the tabs, even from its address', async () => {
    serve(state({ tax: false, insights: true }))
    show('/portfolio?view=tax')
    expect(await screen.findByRole('tab', { name: 'Risk' })).toBeTruthy()
    expect(screen.queryByText(TAX_OFF)).toBeNull()
    expect(screen.getByRole('tab', { name: /Upstox/ }).getAttribute('aria-selected')).toBe('true')
  })
})
