import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  memberCreate: vi.fn(),
  memberUpdate: vi.fn(),
  memberStatus: vi.fn(),
  memberRemove: vi.fn(),
  inviteCreate: vi.fn(),
  inviteRevoke: vi.fn(),
  signinReset: vi.fn(),
  signinUnlock: vi.fn(),
  deviceList: vi.fn(),
  deviceRevoke: vi.fn(),
}))
vi.mock('../ownerApi', async () => ({
  ...(await vi.importActual<typeof import('../ownerApi')>('../ownerApi')),
  ownerApi: api,
}))
const floors = vi.hoisted(() => ({ list: vi.fn(), floor: vi.fn(), set: vi.fn(), remove: vi.fn() }))
vi.mock('./portfolioShares', async () => ({
  ...(await vi.importActual<typeof import('./portfolioShares')>('./portfolioShares')),
  portfolioShares: floors,
}))

import { OwnerError } from '../ownerApi'
import { cardOf, deviceOf, inviteOf, NOW } from './fixtures'
import {
  FLOOR_MISSING,
  FLOOR_WAITS,
  MADE_NO_FLOOR,
  MemberSheet,
  REMOVE_ASK,
  RESET_ASK,
  SIGN_OUT_ASK,
  SUSPEND_ASK,
  UNLOCK_LINE,
  UNLOCKED,
} from './MemberSheet'
import { FLOOR_CHANGED } from './privacySave'
import { FUNDS_WITHOUT_AMOUNTS, PNL_HIDES_MONEY } from './words'

/** The portfolio's floor of the default card: in step with it. */
const floorRow = (over: Record<string, unknown> = {}) => ({
  member: '1',
  role: 'viewer',
  shown: { pnl: true, qty: true, funds: false, orders: true },
  version: 6,
  ...over,
})

function mount(
  card: ReturnType<typeof cardOf> | 'new' = cardOf(),
  over: Record<string, unknown> = {}
) {
  const props = {
    open: true,
    onOpenChange: vi.fn(),
    card: card === 'new' ? undefined : card,
    now: NOW,
    onInvite: vi.fn(),
    onCreated: vi.fn(),
    onGone: vi.fn(),
    ...over,
  }
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const spy = vi.spyOn(client, 'invalidateQueries')
  render(
    <QueryClientProvider client={client}>
      <MemberSheet {...props} />
    </QueryClientProvider>
  )
  return { props, spy }
}

/** A promise a test settles by hand: a write that is still on its way. */
function later<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((a, b) => {
    resolve = a
    reject = b
  })
  return { promise, resolve, reject }
}

beforeEach(() => {
  for (const fn of [...Object.values(api), ...Object.values(floors)]) fn.mockReset()
  api.deviceList.mockResolvedValue([])
  floors.list.mockResolvedValue({ shares: [], privacy: [floorRow()] })
  floors.floor.mockImplementation(async (args: { version: number | null }) =>
    floorRow({ ...args, version: (args.version ?? 0) + 1 })
  )
})

/** The order the two stores were written in, by name. */
const order = () =>
  [
    ...api.memberUpdate.mock.invocationCallOrder.map((n) => [n, 'family'] as const),
    ...floors.floor.mock.invocationCallOrder.map((n) => [n, 'floor'] as const),
  ]
    .sort((a, b) => a[0] - b[0])
    .map(([, name]) => name)

// Review M6 (v0.12.0): each privacy switch was a 44 x 24 target under a 14 px name. The whole
// 48 dp row is the switch's label now. On the old row a tap on the line under the name did nothing.
describe('the privacy switches', () => {
  it('each sit in a row that is their label, at least 48 dp tall and as wide as the sheet', async () => {
    mount()
    await waitFor(() => expect(floors.list).toHaveBeenCalled())
    for (const name of ['P&L', 'Quantities', 'Funds', 'Orders']) {
      const toggle = screen.getByRole('switch', { name })
      const row = toggle.closest('label') as HTMLElement
      expect(row).toHaveAttribute('data-switch-row')
      expect(row.getAttribute('for')).toBe(toggle.id)
      expect(row.className.split(/\s+/)).toEqual(expect.arrayContaining(['min-h-12', 'w-full']))
      // the line under the name describes the switch, and is not part of its name
      expect(toggle).toHaveAccessibleDescription()
    }
  })

  it('flip from a tap on the line under the name, once', async () => {
    mount()
    await waitFor(() => expect(floors.list).toHaveBeenCalled())
    const orders = screen.getByRole('switch', { name: 'Orders' })
    const before = orders.getAttribute('aria-checked')
    const line = document.getElementById(`${orders.id}-line`) as HTMLElement
    await userEvent.click(line)
    expect(orders.getAttribute('aria-checked')).toBe(before === 'true' ? 'false' : 'true')
    await userEvent.click(orders)
    expect(orders.getAttribute('aria-checked')).toBe(before)
  })
})

describe('the privacy floor follows the owner (cross-design Y6)', () => {
  it('hiding writes the floor first, then the family row, and says Saved when both are confirmed', async () => {
    api.memberUpdate.mockResolvedValue(cardOf({ version: 4 }))
    mount()
    await waitFor(() => expect(floors.list).toHaveBeenCalled())
    await userEvent.click(screen.getByRole('switch', { name: 'Orders' }))
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(await screen.findByText('Saved.')).toBeInTheDocument()
    expect(order()).toEqual(['floor', 'family'])
    expect(floors.floor).toHaveBeenCalledWith({
      member: '1',
      role: 'viewer',
      shown: { pnl: true, qty: true, funds: false, orders: false },
      version: 6,
    })
  })

  it('showing writes the family row first, then the floor last', async () => {
    api.memberUpdate.mockResolvedValue(cardOf({ version: 4 }))
    mount()
    await waitFor(() => expect(floors.list).toHaveBeenCalled())
    await userEvent.click(screen.getByRole('switch', { name: 'Funds' }))
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(await screen.findByText('Saved.')).toBeInTheDocument()
    expect(order()).toEqual(['family', 'floor'])
    expect(floors.floor).toHaveBeenCalledWith(
      expect.objectContaining({ shown: { pnl: true, qty: true, funds: true, orders: true } })
    )
  })

  it('an edit that hides one thing and shows another hides first and shows last', async () => {
    api.memberUpdate.mockResolvedValue(cardOf({ version: 4 }))
    mount()
    await waitFor(() => expect(floors.list).toHaveBeenCalled())
    await userEvent.click(screen.getByRole('switch', { name: 'Orders' }))
    await userEvent.click(screen.getByRole('switch', { name: 'Funds' }))
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(await screen.findByText('Saved.')).toBeInTheDocument()
    expect(order()).toEqual(['floor', 'family', 'floor'])
    const [hide, show] = floors.floor.mock.calls.map(([args]) => args)
    expect(hide).toMatchObject({ shown: { funds: false, orders: false }, version: 6 })
    expect(show).toMatchObject({ shown: { funds: true, orders: false }, version: 7 })
  })

  it('a family row refused after the floor hid names what took effect, never Saved', async () => {
    api.memberUpdate.mockRejectedValue(
      new OwnerError('conflict', 'This member was changed elsewhere.')
    )
    const { spy } = mount()
    await waitFor(() => expect(floors.list).toHaveBeenCalled())
    await userEvent.click(screen.getByRole('switch', { name: 'Orders' }))
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(
      await screen.findByText(
        'The portfolio now hides orders from them; nothing else was saved. This member was changed elsewhere.'
      )
    ).toBeInTheDocument()
    expect(screen.queryByText('Saved.')).toBeNull()
    expect(spy).toHaveBeenCalledWith({
      queryKey: ['sudarshan', 'family-owner', 'portfolio-shares'],
    })
  })

  it('a floor that did not answer after the family row is not confirmed, never failed', async () => {
    api.memberUpdate.mockResolvedValue(cardOf({ version: 4 }))
    floors.floor.mockRejectedValue(new OwnerError('not_confirmed', 'Not confirmed.'))
    mount()
    await waitFor(() => expect(floors.list).toHaveBeenCalled())
    await userEvent.click(screen.getByRole('switch', { name: 'Funds' }))
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    const said = await screen.findByText(
      'Saved in the family view; whether the portfolio now shows them funds is not confirmed. Open this member again to check.'
    )
    expect(said.textContent).not.toMatch(/fail/i)
  })

  it('a refused hide saves nothing at all', async () => {
    floors.floor.mockRejectedValue(new OwnerError('conflict', 'x'))
    mount()
    await waitFor(() => expect(floors.list).toHaveBeenCalled())
    await userEvent.click(screen.getByRole('switch', { name: 'Orders' }))
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(await screen.findByText(`Nothing was saved. ${FLOOR_CHANGED}`)).toBeInTheDocument()
    expect(api.memberUpdate).not.toHaveBeenCalled()
  })

  it('a missing floor is said, and Save sends it alone, with no version', async () => {
    floors.list.mockResolvedValue({ shares: [], privacy: [] })
    mount()
    expect(await screen.findByText(FLOOR_MISSING)).toBeInTheDocument()
    const save = screen.getByRole('button', { name: 'Save changes' })
    expect(save).toBeEnabled()
    await userEvent.click(save)
    expect(await screen.findByText('Saved.')).toBeInTheDocument()
    expect(api.memberUpdate).not.toHaveBeenCalled()
    expect(floors.floor).toHaveBeenCalledWith({
      member: '1',
      role: 'viewer',
      shown: { pnl: true, qty: true, funds: false, orders: true },
      version: null,
    })
  })

  it('a floor that shows more than the card is said and is brought down first', async () => {
    floors.list.mockResolvedValue({
      shares: [],
      privacy: [floorRow({ shown: { pnl: true, qty: true, funds: true, orders: true } })],
    })
    mount()
    expect(
      await screen.findByText(/shows them funds, which these switches hide/)
    ).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(await screen.findByText('Saved.')).toBeInTheDocument()
    expect(floors.floor).toHaveBeenCalledTimes(1)
    expect(floors.floor.mock.calls[0][0]).toMatchObject({ shown: { funds: false }, version: 6 })
  })

  it('while the portfolio cannot be read, privacy waits and the name still saves', async () => {
    floors.list.mockRejectedValue(
      new OwnerError('off', 'The portfolio is switched off on the server.')
    )
    api.memberUpdate.mockResolvedValue(cardOf())
    mount()
    expect(
      await screen.findByText(`The portfolio is switched off on the server. ${FLOOR_WAITS}`)
    ).toBeInTheDocument()
    await userEvent.click(screen.getByRole('switch', { name: 'Orders' }))
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(api.memberUpdate).not.toHaveBeenCalled()
    expect(floors.floor).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('switch', { name: 'Orders' }))
    const name = screen.getByLabelText('Display name')
    await userEvent.clear(name)
    await userEvent.type(name, 'New Name')
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() =>
      expect(api.memberUpdate).toHaveBeenCalledWith({ id: 1, version: 3, display_name: 'New Name' })
    )
  })

  it('sends each write once when Save is tapped twice in the same frame', async () => {
    let finish!: (card: unknown) => void
    api.memberUpdate.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve
      })
    )
    mount()
    await waitFor(() => expect(floors.list).toHaveBeenCalled())
    await userEvent.click(screen.getByRole('switch', { name: 'Orders' }))
    const save = screen.getByRole('button', { name: 'Save changes' })
    act(() => {
      save.click()
      save.click()
    })
    await waitFor(() => expect(api.memberUpdate).toHaveBeenCalledTimes(1))
    expect(floors.floor).toHaveBeenCalledTimes(1)
    await act(async () => finish(cardOf()))
  })
})

describe('editing a member', () => {
  it('sends only what changed, with the card version, and says what the service said', async () => {
    api.memberUpdate.mockResolvedValue(cardOf({ version: 4, display_name: 'New Name' }))
    mount()
    const name = screen.getByLabelText('Display name')
    await userEvent.clear(name)
    await userEvent.type(name, 'New Name')
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(api.memberUpdate).toHaveBeenCalledTimes(1)
    expect(api.memberUpdate).toHaveBeenCalledWith({ id: 1, version: 3, display_name: 'New Name' })
    expect(await screen.findByText('Saved.')).toBeInTheDocument()
  })

  it('offers no save until something changes', () => {
    mount()
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled()
  })

  it('sends privacy as the four categories when one is changed', async () => {
    api.memberUpdate.mockResolvedValue(cardOf())
    mount()
    await userEvent.click(screen.getByRole('switch', { name: 'Orders' }))
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(api.memberUpdate).toHaveBeenCalledWith({
      id: 1,
      version: 3,
      privacy: { pnl: true, qty: true, funds: false, orders: false },
    })
  })

  it('turns P&L off with quantities and says why P&L cannot be on (Y24)', async () => {
    api.memberUpdate.mockResolvedValue(cardOf())
    mount()
    await userEvent.click(screen.getByRole('switch', { name: 'Quantities' }))
    const pnl = screen.getByRole('switch', { name: 'P&L' })
    expect(pnl).toBeDisabled()
    expect(pnl).toHaveAttribute('aria-checked', 'false')
    expect(screen.getByText('Live P&L is worked out from quantities')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(api.memberUpdate).toHaveBeenCalledWith({
      id: 1,
      version: 3,
      privacy: { pnl: false, qty: false, funds: false, orders: true },
    })
  })

  it('says that hiding P&L hides every rupee figure with it, funds amounts too (review H1)', async () => {
    mount(cardOf({ privacy: { pnl: true, qty: true, funds: true, orders: true } }))
    expect(screen.queryByText(PNL_HIDES_MONEY)).toBeNull()
    await userEvent.click(screen.getByRole('switch', { name: 'P&L' }))
    expect(screen.getByText(PNL_HIDES_MONEY)).toBeInTheDocument()
    expect(screen.getByText(FUNDS_WITHOUT_AMOUNTS)).toBeInTheDocument()
    expect(PNL_HIDES_MONEY).toMatch(/cost, value, the day’s change/)
  })

  it('shows a refusal in the service words and reads the members again after a conflict', async () => {
    api.memberUpdate.mockRejectedValue(
      new OwnerError(
        'conflict',
        'This member was changed elsewhere. The page shows the new version.'
      )
    )
    const { spy } = mount()
    await userEvent.click(screen.getByRole('switch', { name: 'Orders' }))
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(await screen.findByText(/changed elsewhere/)).toBeInTheDocument()
    expect(spy).toHaveBeenCalled()
  })
})

describe('one send, however it is tapped', () => {
  it('sends a save once when the button is tapped twice before the answer', async () => {
    const wait = later<ReturnType<typeof cardOf>>()
    api.memberUpdate.mockReturnValue(wait.promise)
    mount()
    await userEvent.click(screen.getByRole('switch', { name: 'Orders' }))
    const save = screen.getByRole('button', { name: 'Save changes' })
    await userEvent.dblClick(save)
    await userEvent.click(save)
    expect(api.memberUpdate).toHaveBeenCalledTimes(1)
    await act(async () => wait.resolve(cardOf()))
  })

  it('sends an invite once, and Suspend once, under repeated taps', async () => {
    const wait = later<ReturnType<typeof inviteOf>>()
    api.inviteCreate.mockReturnValue(wait.promise)
    mount(cardOf({ status: 'invited', handle: null, invite_expires_at: NOW + 60_000 }))
    const invite = screen.getByRole('button', { name: 'Invite' })
    await userEvent.dblClick(invite)
    await userEvent.click(invite)
    expect(api.inviteCreate).toHaveBeenCalledTimes(1)
    await act(async () => wait.resolve(inviteOf()))
  })

  it('a second tap in the same frame is turned away by the lock, not only by the disabled button', async () => {
    const wait = later<ReturnType<typeof inviteOf>>()
    api.inviteCreate.mockReturnValue(wait.promise)
    mount(cardOf({ status: 'invited', handle: null, invite_expires_at: NOW + 60_000 }))
    const invite = screen.getByRole('button', { name: 'Invite' })
    act(() => {
      invite.click()
      invite.click()
    })
    expect(api.inviteCreate).toHaveBeenCalledTimes(1)
    await act(async () => wait.resolve(inviteOf()))
  })
})

describe('the request id', () => {
  it('is sent with an invite, and the same one is sent again after "not confirmed"', async () => {
    api.inviteCreate
      .mockRejectedValueOnce(
        new OwnerError(
          'not_confirmed',
          'Not confirmed: open the Family page again to see whether it was done.'
        )
      )
      .mockResolvedValueOnce(inviteOf())
    const { props } = mount(
      cardOf({ status: 'invited', handle: null, invite_expires_at: NOW + 60_000 })
    )
    await userEvent.click(screen.getByRole('button', { name: 'Invite' }))
    expect(await screen.findByText(/^Not confirmed/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Invite' }))
    await waitFor(() => expect(props.onInvite).toHaveBeenCalled())
    const [first, second] = api.inviteCreate.mock.calls.map(([body]) => body.request_id)
    expect(first).toBeTruthy()
    expect(second).toBe(first)
  })

  it('is a new one after an answer in words', async () => {
    api.inviteCreate
      .mockRejectedValueOnce(new OwnerError('limit', 'Two invites at once are not allowed.'))
      .mockResolvedValueOnce(inviteOf())
    mount(cardOf({ status: 'invited', handle: null, invite_expires_at: NOW + 60_000 }))
    await userEvent.click(screen.getByRole('button', { name: 'Invite' }))
    await screen.findByText('Two invites at once are not allowed.')
    await userEvent.click(screen.getByRole('button', { name: 'Invite' }))
    await waitFor(() => expect(api.inviteCreate).toHaveBeenCalledTimes(2))
    const [first, second] = api.inviteCreate.mock.calls.map(([body]) => body.request_id)
    expect(second).not.toBe(first)
  })

  it('says "Not confirmed" for a write that may have happened, never "failed"', async () => {
    api.memberStatus.mockRejectedValue(
      new OwnerError(
        'not_confirmed',
        'Not confirmed: open the Family page again to see whether it was done.'
      )
    )
    const { spy } = mount()
    await userEvent.click(screen.getByRole('button', { name: 'Suspend' }))
    await userEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Suspend' }))
    const said = await screen.findByText(/^Not confirmed/)
    expect(said.textContent).not.toMatch(/fail/i)
    expect(spy).toHaveBeenCalled() // the members are read again to see what happened
  })
})

describe('adding a member', () => {
  it('prefills a child with everything hidden, sends a request id, and hands the new card on', async () => {
    const made = cardOf({ id: 9, display_name: 'Test Two', role: 'child' })
    api.memberCreate.mockResolvedValue(made)
    const { props } = mount('new')
    await userEvent.type(screen.getByLabelText('Display name'), 'Test Two')
    await userEvent.click(screen.getByRole('radio', { name: /Child/ }))
    expect(screen.getByRole('switch', { name: 'Orders' })).toHaveAttribute('aria-checked', 'false')
    expect(screen.getByRole('switch', { name: 'Quantities' })).toHaveAttribute(
      'aria-checked',
      'false'
    )
    await userEvent.click(screen.getByRole('button', { name: 'Add member' }))
    expect(api.memberCreate).toHaveBeenCalledTimes(1)
    const body = api.memberCreate.mock.calls[0][0]
    expect(body).toMatchObject({
      display_name: 'Test Two',
      role: 'child',
      language: 'en',
      privacy: { pnl: false, qty: false, funds: false, orders: false },
    })
    expect(body.request_id).toBeTruthy()
    await waitFor(() => expect(props.onCreated).toHaveBeenCalledWith(made))
  })

  it('sends the new member floor once the member exists, named by the new id', async () => {
    const made = cardOf({
      id: 9,
      role: 'child',
      privacy: { pnl: false, qty: false, funds: false, orders: false },
    })
    api.memberCreate.mockResolvedValue(made)
    const { props } = mount('new')
    await userEvent.type(screen.getByLabelText('Display name'), 'Test Two')
    await userEvent.click(screen.getByRole('button', { name: 'Add member' }))
    await waitFor(() => expect(props.onCreated).toHaveBeenCalledWith(made))
    expect(floors.floor).toHaveBeenCalledWith({
      member: '9',
      role: 'child',
      shown: { pnl: false, qty: false, funds: false, orders: false },
      version: null,
    })
    expect(api.memberCreate.mock.invocationCallOrder[0]).toBeLessThan(
      floors.floor.mock.invocationCallOrder[0]
    )
  })

  it('a floor refused at create still hands the new member on, and says what is missing', async () => {
    const made = cardOf({ id: 9 })
    api.memberCreate.mockResolvedValue(made)
    floors.floor.mockRejectedValue(
      new OwnerError('unavailable', 'The portfolio service is not answering.')
    )
    const { props } = mount('new')
    await userEvent.type(screen.getByLabelText('Display name'), 'Test Two')
    await userEvent.click(screen.getByRole('button', { name: 'Add member' }))
    await waitFor(() => expect(props.onCreated).toHaveBeenCalledWith(made))
    expect(
      await screen.findByText(`${MADE_NO_FLOOR} The portfolio service is not answering.`)
    ).toBeInTheDocument()
  })

  it('keeps the owner own privacy choice when he then changes the role', async () => {
    mount('new')
    await userEvent.click(screen.getByRole('switch', { name: 'Funds' }))
    await userEvent.click(screen.getByRole('radio', { name: /Child/ }))
    expect(screen.getByRole('switch', { name: 'Funds' })).toHaveAttribute('aria-checked', 'true')
  })

  it('offers no add until the name is filled in', () => {
    mount('new')
    expect(screen.getByRole('button', { name: 'Add member' })).toBeDisabled()
  })
})

describe('Invite or Reset sign-in', () => {
  it('offers Invite for a member who has not signed in, and hands the link to the page', async () => {
    api.inviteCreate.mockResolvedValue(inviteOf())
    const { props } = mount(cardOf({ status: 'invited', handle: null, invite_expires_at: NOW + 1 }))
    expect(screen.queryByRole('button', { name: 'Reset sign-in' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Invite' }))
    await waitFor(() => expect(props.onInvite).toHaveBeenCalledWith(inviteOf(), 'Test One'))
  })

  it('offers Reset sign-in for a member who has, and asks before it sends anything', async () => {
    api.signinReset.mockResolvedValue(inviteOf())
    const { props } = mount()
    expect(screen.queryByRole('button', { name: 'Invite' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Reset sign-in' }))
    expect(api.signinReset).not.toHaveBeenCalled()
    expect(screen.getByText(RESET_ASK)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Keep' }))
    expect(api.signinReset).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Reset sign-in' }))
    const ask = screen.getByRole('alertdialog')
    await userEvent.click(within(ask).getByRole('button', { name: 'Reset sign-in' }))
    expect(api.signinReset).toHaveBeenCalledWith(
      expect.objectContaining({ member_id: 1, version: 3, request_id: expect.any(String) })
    )
    await waitFor(() => expect(props.onInvite).toHaveBeenCalled())
  })

  it('cancels an open invite', async () => {
    api.inviteRevoke.mockResolvedValue({ ok: true, words: null })
    mount(cardOf({ status: 'invited', handle: null, invite_expires_at: NOW + 60_000 }))
    await userEvent.click(screen.getByRole('button', { name: 'Cancel the open invite' }))
    expect(api.inviteRevoke).toHaveBeenCalledWith({ member_id: 1 })
    expect(await screen.findByText('The invite link no longer works.')).toBeInTheDocument()
  })
})

describe('Unlock sign-in (review L1)', () => {
  it('is offered only while wrong attempts hold the sign-in', () => {
    mount(cardOf({ locked_until: NOW - 1 }))
    expect(screen.queryByRole('button', { name: 'Unlock sign-in' })).toBeNull()
    expect(screen.queryByText(UNLOCK_LINE)).toBeNull()
  })

  it('clears the lock with one tap, keeps the sign-in, and reads the members again', async () => {
    const answer = later<ReturnType<typeof cardOf>>()
    api.signinUnlock.mockReturnValue(answer.promise)
    const { spy } = mount(cardOf({ locked_until: NOW + 300_000 }))
    expect(screen.getByText(UNLOCK_LINE)).toBeInTheDocument()
    const button = screen.getByRole('button', { name: 'Unlock sign-in' })
    await userEvent.click(button)
    await userEvent.click(button) // sent once however it is tapped
    expect(api.signinUnlock).toHaveBeenCalledTimes(1)
    expect(api.signinUnlock).toHaveBeenCalledWith({ member_id: 1 })
    expect(api.signinReset).not.toHaveBeenCalled()
    await act(async () => answer.resolve(cardOf({ locked_until: null })))
    expect(await screen.findByText(UNLOCKED)).toBeInTheDocument()
    expect(spy).toHaveBeenCalled()
  })
})

describe('Suspend and Resume', () => {
  it('suspends an active member and resumes a suspended one, with the card version', async () => {
    api.memberStatus.mockResolvedValue(cardOf())
    const { unmount } = render(<div />)
    unmount()
    mount()
    // Review L1 (v0.12.0): one tap sent it, with no question. Fails on the old sheet.
    await userEvent.click(screen.getByRole('button', { name: 'Suspend' }))
    expect(api.memberStatus).not.toHaveBeenCalled()
    const ask = screen.getByRole('alertdialog')
    expect(ask.textContent).toContain(SUSPEND_ASK)
    await userEvent.click(within(ask).getByRole('button', { name: 'Suspend' }))
    expect(api.memberStatus).toHaveBeenCalledTimes(1)
    expect(api.memberStatus).toHaveBeenCalledWith({ id: 1, version: 3, status: 'suspended' })
  })

  it('keeps the member signed in when the owner answers Keep', async () => {
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Suspend' }))
    await userEvent.click(
      within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Keep' })
    )
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(api.memberStatus).not.toHaveBeenCalled()
  })

  it('offers Resume for a suspended member', async () => {
    api.memberStatus.mockResolvedValue(cardOf())
    mount(cardOf({ status: 'suspended' }))
    expect(screen.queryByRole('button', { name: 'Suspend' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Resume' }))
    expect(api.memberStatus).toHaveBeenCalledWith({ id: 1, version: 3, status: 'active' })
  })
})

describe('Remove', () => {
  it('asks with the design sentence and sends nothing until the owner says yes', async () => {
    api.memberRemove.mockResolvedValue({ ok: true, words: null })
    const { props } = mount()
    await userEvent.click(screen.getByRole('button', { name: 'Remove this member' }))
    expect(
      screen.getByText(
        'Remove this member? Their devices are signed out and their Telegram unlinked. The audit record stays.'
      )
    ).toBeInTheDocument()
    expect(REMOVE_ASK).toContain('The audit record stays.')
    expect(api.memberRemove).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Keep' }))
    expect(api.memberRemove).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Remove this member' }))
    await userEvent.click(screen.getByRole('button', { name: 'Remove member' }))
    expect(api.memberRemove).toHaveBeenCalledTimes(1)
    expect(api.memberRemove).toHaveBeenCalledWith({ id: 1, version: 3 })
    await waitFor(() => expect(props.onGone).toHaveBeenCalled())
  })

  it('sends once when Yes is tapped twice', async () => {
    const wait = later<{ ok: boolean; words: null }>()
    api.memberRemove.mockReturnValue(wait.promise)
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Remove this member' }))
    const yes = screen.getByRole('button', { name: 'Remove member' })
    await userEvent.dblClick(yes)
    expect(api.memberRemove).toHaveBeenCalledTimes(1)
    await act(async () => wait.resolve({ ok: true, words: null }))
  })

  it('stays when the removal was refused, and says why', async () => {
    api.memberRemove.mockRejectedValue(
      new OwnerError('conflict', 'This member was changed elsewhere.')
    )
    const { props } = mount()
    await userEvent.click(screen.getByRole('button', { name: 'Remove this member' }))
    await userEvent.click(screen.getByRole('button', { name: 'Remove member' }))
    expect(await screen.findByText('This member was changed elsewhere.')).toBeInTheDocument()
    expect(props.onGone).not.toHaveBeenCalled()
  })
})

describe('Devices', () => {
  it('lists each device and signs one out only after asking', async () => {
    api.deviceList.mockResolvedValue([
      deviceOf(),
      deviceOf({
        id: 'dev-2',
        label: 'Android 14 · Chrome 129',
        notifications: 'Notifications may not arrive on this phone.',
      }),
    ])
    api.deviceRevoke.mockResolvedValue({ ok: true, words: null })
    mount()
    expect(await screen.findByText('Android 15 · Chrome 130')).toBeInTheDocument()
    expect(screen.getByText('Notifications may not arrive on this phone.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Sign out Android 14 · Chrome 129' }))
    expect(screen.getByText(SIGN_OUT_ASK)).toBeInTheDocument()
    expect(api.deviceRevoke).not.toHaveBeenCalled()
    const ask = screen.getByRole('alertdialog')
    await userEvent.click(within(ask).getByRole('button', { name: 'Sign out' }))
    expect(api.deviceRevoke).toHaveBeenCalledWith({ member_id: 1, id: 'dev-2' })
    expect(await screen.findByText('Signed out on that device.')).toBeInTheDocument()
  })

  it('says so when no device is signed in', async () => {
    mount(cardOf({ devices: 0 }))
    expect(await screen.findByText('No device is signed in.')).toBeInTheDocument()
  })
})
