import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ call: vi.fn() }))
vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  portfolioCall: api.call,
}))

import { createQueryClient } from '@/app/providers'
import { ACCOUNT, ASSET, entry, tab } from '../sheets/fixtures'
import { ActivityTab } from './ActivityTab'

beforeEach(() => {
  api.call.mockReset()
})

function show() {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <ActivityTab
        tab={tab(2, 'Gold', { version: 6 })}
        accounts={[ACCOUNT]}
        onAdd={() => {}}
        onImport={() => {}}
        onEdit={() => {}}
        onDuplicate={() => {}}
      />
    </QueryClientProvider>
  )
}

describe('the Activity part of a tab', () => {
  it('lists the entries with their asset names, and pages with the service’s cursor', async () => {
    api.call.mockImplementation(async (op: string, args: { before?: string }) => {
      if (op === 'tab_get') return { enabled: true, data: { assets: [ASSET] } }
      if (op === 'activities_list') {
        return args.before
          ? { enabled: true, data: { items: [entry(2, { trade_date: 20261001 })], next: null } }
          : { enabled: true, data: { items: [entry(1)], next: 'cursor-1' } }
      }
      throw new Error(op)
    })
    show()
    await screen.findByText('November 2026')
    await waitFor(() => expect(screen.getAllByText('INFOSYS').length).toBe(1))
    await userEvent.click(screen.getByRole('button', { name: 'Show older entries' }))
    await screen.findByText('October 2026')
    expect(api.call).toHaveBeenCalledWith('activities_list', { id: 2, before: 'cursor-1' })
    expect(screen.queryByRole('button', { name: 'Show older entries' })).toBeNull()
  })

  it('deletes with the tab’s version, then reads the list again', async () => {
    let listed = 0
    api.call.mockImplementation(async (op: string) => {
      if (op === 'tab_get') return { enabled: true, data: { assets: [ASSET] } }
      if (op === 'activities_list') {
        listed += 1
        return { enabled: true, data: { items: listed === 1 ? [entry(1)] : [], next: null } }
      }
      if (op === 'activity_delete') return { enabled: true, data: { id: 1, tab_version: 7 } }
      throw new Error(op)
    })
    show()
    await userEvent.click(await screen.findByRole('button', { name: /Actions for/ }))
    await userEvent.click(await screen.findByRole('menuitem', { name: 'Delete' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Delete' }))
    await screen.findByText('Nothing here yet. Add an entry or import a file.')
    expect(api.call).toHaveBeenCalledWith('activity_delete', { id: 1, version: 6 })
  })

  it('says in words when the entries cannot be read, and tries again on request', async () => {
    const { PortfolioError } = await import('../api')
    api.call.mockImplementation(async (op: string) => {
      if (op === 'tab_get') return { enabled: true, data: { assets: [] } }
      throw new PortfolioError({
        code: 'unavailable',
        message: 'The portfolio service is not answering.',
        entryId: null,
      })
    })
    show()
    await screen.findByText('The portfolio service is not answering.')
    expect(screen.getByRole('button', { name: /Try again/ })).toBeTruthy()
  })
})
