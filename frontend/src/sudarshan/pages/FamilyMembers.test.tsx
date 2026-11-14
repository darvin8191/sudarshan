import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  state: vi.fn(),
  shareList: vi.fn(),
  auditList: vi.fn(),
  memberList: vi.fn(),
  memberCreate: vi.fn(),
  inviteCreate: vi.fn(),
  deviceList: vi.fn(),
}))
vi.mock('@/sudarshan/family/ownerApi', async () => ({
  ...(await vi.importActual<typeof import('@/sudarshan/family/ownerApi')>(
    '@/sudarshan/family/ownerApi'
  )),
  ownerApi: api,
}))

import { cardOf, inviteOf } from '@/sudarshan/family/owner/fixtures'
import { OwnerError } from '@/sudarshan/family/ownerApi'
import FamilyMembers from './FamilyMembers'

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <FamilyMembers />
    </QueryClientProvider>
  )
}

const START = Date.UTC(2026, 10, 14, 10, 0, 0)

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(START)
  for (const fn of Object.values(api)) fn.mockReset()
  api.deviceList.mockResolvedValue([])
  api.state.mockResolvedValue({
    enabled: true,
    data: { enabled: true, message: null, store_version: 5, members: 1, sessions: 1, sockets: 0 },
  })
  api.shareList.mockResolvedValue([])
  api.auditList.mockResolvedValue({ lines: [], older: null })
})
afterEach(() => {
  vi.useRealTimers()
})

describe('the Family page', () => {
  it('shows a card for each member and the Add member button', async () => {
    api.memberList.mockResolvedValue([
      cardOf(),
      cardOf({ id: 2, display_name: 'Test Two', role: 'child' }),
    ])
    mount()
    expect(await screen.findByText('Test One')).toBeInTheDocument()
    expect(screen.getByText('Test Two')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add member' })).toBeEnabled()
  })

  it('says so when there is no member yet', async () => {
    api.memberList.mockResolvedValue([])
    mount()
    expect(await screen.findByText(/No family member yet/)).toBeInTheDocument()
  })

  it('does not list a removed member', async () => {
    api.memberList.mockResolvedValue([
      cardOf(),
      cardOf({ id: 2, display_name: 'Gone One', status: 'removed' }),
    ])
    mount()
    await screen.findByText('Test One')
    expect(screen.queryByText('Gone One')).toBeNull()
  })

  it('stops offering Add member at the limit, and says why', async () => {
    api.memberList.mockResolvedValue(
      [1, 2, 3, 4].map((id) => cardOf({ id, display_name: `Test ${id}` }))
    )
    mount()
    await screen.findByText('Test 4')
    expect(screen.getByRole('button', { name: 'Add member' })).toBeDisabled()
    expect(screen.getByText(/holds up to 4 members/)).toBeInTheDocument()
  })

  it('says the family view is switched off, in the service words, and offers nothing', async () => {
    api.memberList.mockRejectedValue(
      new OwnerError('off', 'The family view is switched off on the server.')
    )
    mount()
    expect(
      await screen.findByText('The family view is switched off on the server.')
    ).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Add member' })).toBeNull()
  })

  it('shows a problem in words with a way to try again', async () => {
    api.memberList.mockRejectedValueOnce(
      new OwnerError(
        'unavailable',
        'The family service is not answering. Nothing was changed; try again in a minute.'
      )
    )
    api.memberList.mockResolvedValueOnce([cardOf()])
    mount()
    expect(await screen.findByText(/not answering/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('Test One')).toBeInTheDocument()
  })

  it('reads the members again every 30 seconds while it is open', async () => {
    api.memberList.mockResolvedValue([cardOf()])
    mount()
    await screen.findByText('Test One')
    expect(api.memberList).toHaveBeenCalledTimes(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000)
    })
    expect(api.memberList).toHaveBeenCalledTimes(2)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000)
    })
    expect(api.memberList).toHaveBeenCalledTimes(3)
  })

  it('moves a status chip on when the clock passes the time it names', async () => {
    api.memberList.mockResolvedValue([
      cardOf({ status: 'invited', handle: null, invite_expires_at: START + 60_000 }),
    ])
    mount()
    expect(await screen.findByText('Invited · expires 15:31')).toBeInTheDocument()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(65_000)
    })
    expect(screen.getByText('Invited · link expired')).toBeInTheDocument()
  })

  it('opens a member, and shows the invite link once the invite is made', async () => {
    api.memberList.mockResolvedValue([
      cardOf({ status: 'invited', handle: null, invite_expires_at: START + 600_000 }),
    ])
    api.inviteCreate.mockResolvedValue(inviteOf())
    mount()
    await userEvent.click(await screen.findByRole('button', { name: /Test One/ }))
    await userEvent.click(await screen.findByRole('button', { name: 'Invite' }))
    expect(await screen.findByTestId('invite-link')).toHaveTextContent(
      '/family/join/test-code-0001'
    )
    // The member sheet gives way to the link: one sheet at a time.
    expect(screen.queryByRole('button', { name: 'Save changes' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(screen.queryByTestId('invite-link')).toBeNull())
    expect(document.body.textContent).not.toContain('test-code-0001')
  })

  it('adds a member and opens that member sheet', async () => {
    api.memberList.mockResolvedValue([])
    api.memberCreate.mockResolvedValue(
      cardOf({
        id: 5,
        display_name: 'Test Five',
        handle: null,
        status: 'invited',
        invite_expires_at: null,
      })
    )
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Add member' }))
    await userEvent.type(screen.getByLabelText('Display name'), 'Test Five')
    api.memberList.mockResolvedValue([
      cardOf({
        id: 5,
        display_name: 'Test Five',
        handle: null,
        status: 'invited',
        invite_expires_at: null,
      }),
    ])
    await userEvent.click(screen.getByRole('button', { name: 'Add member', hidden: false }))
    expect(await screen.findByRole('button', { name: 'Invite' })).toBeInTheDocument()
  })

  it('has Members, Shares, Audit and Service, each read only when it is opened', async () => {
    api.memberList.mockResolvedValue([cardOf()])
    mount()
    await screen.findByText('Test One')
    expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual([
      'Members',
      'Shares',
      'Audit',
      'Service',
    ])
    expect(api.auditList).not.toHaveBeenCalled()
    expect(api.shareList).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('tab', { name: 'Audit' }))
    await waitFor(() => expect(api.auditList).toHaveBeenCalledTimes(1))
    await userEvent.click(screen.getByRole('tab', { name: 'Service' }))
    expect(await screen.findByText('Store version 5')).toBeInTheDocument()
  })

  it('shows the service state with the message when the family view is off', async () => {
    api.memberList.mockRejectedValue(
      new OwnerError('off', 'The family view is switched off on the server.')
    )
    api.state.mockResolvedValue({
      enabled: true,
      data: {
        enabled: false,
        message: 'The family view is switched off on the server.',
        store_version: null,
        members: 0,
        sessions: 0,
        sockets: 0,
      },
    })
    mount()
    expect(await screen.findByText('Off')).toBeInTheDocument()
    expect(screen.queryByRole('tab')).toBeNull()
  })
})
