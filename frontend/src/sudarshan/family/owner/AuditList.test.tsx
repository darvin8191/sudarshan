import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ auditList: vi.fn() }))
vi.mock('../ownerApi', async () => ({
  ...(await vi.importActual<typeof import('../ownerApi')>('../ownerApi')),
  ownerApi: api,
}))

import { OwnerError } from '../ownerApi'
import { AuditList } from './AuditList'
import { cardOf, lineOf, NOW } from './fixtures'

const members = [cardOf(), cardOf({ id: 2, display_name: 'Test Two' })]

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <AuditList members={members} now={NOW} />
    </QueryClientProvider>
  )
}

beforeEach(() => api.auditList.mockReset())

/** The lines of the record (the Kind menu has the same words as options). */
async function lines() {
  return within(await screen.findByRole('list'))
}

describe('the audit list', () => {
  it('asks for the last 200 lines first, all members and kinds', async () => {
    api.auditList.mockResolvedValue({ lines: [lineOf()], older: null })
    mount()
    await screen.findByText('Member changed')
    expect(api.auditList.mock.calls[0][0]).toEqual({
      member_id: null,
      kind: null,
      before: null,
      limit: 200,
    })
    expect(screen.getByText(/The last 200 lines, newest first/)).toBeInTheDocument()
  })

  it('shows each line as a sentence with when, who it was about and the device', async () => {
    api.auditList.mockResolvedValue({
      lines: [
        lineOf({ id: 3, kind: 'login_ok', member_id: 2, device: 'Android 15 · Chrome 130' }),
        lineOf({ id: 2, kind: 'lockout', member_id: 9, at: NOW - 86_400_000 * 2 }),
        lineOf({ id: 1, kind: 'check_failed', member_id: null }),
      ],
      older: null,
    })
    mount()
    const list = await lines()
    expect(list.getByText('Signed in')).toBeInTheDocument()
    expect(list.getByText('today 15:29 · Test Two · Android 15 · Chrome 130')).toBeInTheDocument()
    expect(list.getByText('12 Nov, 15:30 · A member no longer listed')).toBeInTheDocument()
    expect(list.getByText('Install check failed')).toBeInTheDocument()
  })

  it('filters by member and by kind, starting again from the newest line', async () => {
    api.auditList.mockResolvedValue({ lines: [lineOf()], older: null })
    mount()
    await screen.findByText('Member changed')
    await userEvent.selectOptions(screen.getByLabelText('Member'), '2')
    await waitFor(() =>
      expect(api.auditList.mock.calls.at(-1)?.[0]).toEqual({
        member_id: 2,
        kind: null,
        before: null,
        limit: 200,
      })
    )
    await userEvent.selectOptions(screen.getByLabelText('Kind'), 'login_failed')
    await waitFor(() =>
      expect(api.auditList.mock.calls.at(-1)?.[0]).toEqual({
        member_id: 2,
        kind: 'login_failed',
        before: null,
        limit: 200,
      })
    )
  })

  it('loads the older page from the cursor and adds it below, and stops offering it at the end', async () => {
    api.auditList
      .mockResolvedValueOnce({ lines: [lineOf({ id: 20, kind: 'login_ok' })], older: 20 })
      .mockResolvedValueOnce({ lines: [lineOf({ id: 10, kind: 'logout' })], older: null })
    mount()
    await lines()
    await userEvent.click(await screen.findByRole('button', { name: 'Load older' }))
    await waitFor(async () => expect((await lines()).getByText('Signed out')).toBeInTheDocument())
    expect((await lines()).getByText('Signed in')).toBeInTheDocument()
    expect(api.auditList.mock.calls[1][0]).toEqual({
      member_id: null,
      kind: null,
      before: 20,
      limit: 200,
    })
    expect(screen.queryByRole('button', { name: 'Load older' })).toBeNull()
  })

  it('asks for the older page once when Load older is tapped twice', async () => {
    let finish!: (page: unknown) => void
    api.auditList
      .mockResolvedValueOnce({ lines: [lineOf({ id: 20 })], older: 20 })
      .mockReturnValueOnce(
        new Promise((resolve) => {
          finish = resolve
        })
      )
    mount()
    const more = await screen.findByRole('button', { name: 'Load older' })
    await userEvent.dblClick(more)
    expect(api.auditList).toHaveBeenCalledTimes(2)
    finish({ lines: [], older: null })
  })

  it('says when nothing matches, and offers a retry when it cannot be read', async () => {
    api.auditList.mockResolvedValueOnce({ lines: [], older: null })
    mount()
    expect(await screen.findByText('No line matches.')).toBeInTheDocument()
    api.auditList.mockRejectedValueOnce(
      new OwnerError(
        'unavailable',
        'The family service is not answering. Nothing was changed; try again in a minute.'
      )
    )
    await userEvent.selectOptions(screen.getByLabelText('Kind'), 'login_ok')
    expect(await screen.findByText(/not answering/)).toBeInTheDocument()
    api.auditList.mockResolvedValueOnce({ lines: [lineOf({ kind: 'login_ok' })], older: null })
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect((await lines()).getByText('Signed in')).toBeInTheDocument()
  })
})
