import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ call: vi.fn() }))
vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  portfolioCall: api.call,
}))

import { OPEN_GUARD } from '@/sudarshan/watchlist/openGuard'
import { PortfolioError } from '../api'
import { MAX_TABS, WORDS } from '../types'
import { tab } from './fixtures'
import { NewTabSheet } from './NewTabSheet'

beforeEach(() => {
  api.call.mockReset()
})

function show(props: Partial<React.ComponentProps<typeof NewTabSheet>> = {}) {
  const onClose = vi.fn()
  const onCreated = vi.fn()
  render(<NewTabSheet open onClose={onClose} tabCount={2} onCreated={onCreated} {...props} />)
  return { onClose, onCreated }
}

const typeName = (name: string) =>
  userEvent.type(screen.getByRole('textbox', { name: /^Name/ }), name)

describe('the New tab sheet', () => {
  it('holds a name to 40 letters and says how many are used', async () => {
    show()
    const input = screen.getByRole('textbox', { name: /^Name/ }) as HTMLInputElement
    expect(input.maxLength).toBe(40)
    await typeName('Gold')
    expect(screen.getByText('4 of 40 letters')).toBeTruthy()
  })

  it('cannot be saved without a name', () => {
    show()
    expect((screen.getByRole('button', { name: 'Create tab' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('sends the name, second name, kind, icon, colour and pin with a request id', async () => {
    api.call.mockResolvedValue({ enabled: true, data: tab(9, 'Gold') })
    const { onCreated } = show()
    await typeName('Gold')
    await userEvent.type(screen.getByRole('textbox', { name: /Second name/ }), 'સોનું')
    await userEvent.click(screen.getByRole('button', { name: 'Gold and silver' }))
    await userEvent.click(screen.getByRole('button', { name: 'Gem' }))
    await userEvent.click(screen.getByRole('button', { name: 'Gold' }))
    await userEvent.click(screen.getByRole('checkbox'))
    await userEvent.click(screen.getByRole('button', { name: 'Create tab' }))
    expect(api.call).toHaveBeenCalledTimes(1)
    const [op, args] = api.call.mock.calls[0]
    expect(op).toBe('tab_create')
    expect(args).toMatchObject({
      name: 'Gold', name_alt: 'સોનું', kind: 'gold', icon: 'gem', colour: 'gold', pinned: true,
    })
    expect(args.request_id).toMatch(/^[0-9a-f-]{36}$/)
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(expect.objectContaining({ id: 9 })))
  })

  it('tells the chosen kind, icon and colour by more than colour', async () => {
    show()
    await userEvent.click(screen.getByRole('button', { name: 'Crypto' }))
    expect(screen.getByRole('button', { name: 'Crypto' }).getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByRole('button', { name: 'Crypto' }).className).toMatch(/border-2/)
    expect(screen.getByRole('button', { name: 'Stocks and ETFs' }).getAttribute('aria-pressed')).toBe('false')
    for (const label of ['Blue', 'Gold', 'Sky', 'Grey']) {
      expect(screen.getByRole('button', { name: label })).toBeTruthy() // named, not just coloured
    }
  })

  it('sends once however often Create is tapped while the request is out', async () => {
    let finish: (value: unknown) => void = () => {}
    api.call.mockReturnValue(new Promise((resolve) => { finish = resolve }))
    show()
    await typeName('Gold')
    const create = screen.getByRole('button', { name: 'Create tab' })
    fireEvent.click(create)
    fireEvent.click(create)
    fireEvent.click(create)
    expect(api.call).toHaveBeenCalledTimes(1)
    await act(async () => finish({ enabled: true, data: tab(9, 'Gold') }))
  })

  it('a lost answer is told as not confirmed, and a second tap resends the same request id', async () => {
    api.call.mockRejectedValueOnce(
      new PortfolioError({ code: 'not_confirmed', message: WORDS.not_confirmed, entryId: null })
    )
    api.call.mockResolvedValueOnce({ enabled: true, data: tab(9, 'Gold') })
    const { onCreated } = show()
    await typeName('Gold')
    await userEvent.click(screen.getByRole('button', { name: 'Create tab' }))
    expect((await screen.findByRole('alert')).textContent).toBe(WORDS.not_confirmed)
    expect(screen.queryByText(/failed/i)).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Create tab' }))
    await waitFor(() => expect(onCreated).toHaveBeenCalled())
    expect(api.call.mock.calls[1][1].request_id).toBe(api.call.mock.calls[0][1].request_id)
  })

  it('at 50 tabs it says so and sends nothing', async () => {
    show({ tabCount: MAX_TABS })
    expect(screen.getByText(WORDS.tab_limit)).toBeTruthy()
    await typeName('Gold')
    const create = screen.getByRole('button', { name: 'Create tab' }) as HTMLButtonElement
    expect(create.disabled).toBe(true)
    fireEvent.submit(create.closest('form') as HTMLFormElement)
    expect(api.call).not.toHaveBeenCalled()
  })

  it('Cancel closes the sheet', async () => {
    const { onClose } = show()
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})

describe('the 500 ms tap guard', () => {
  beforeEach(() => {
    OPEN_GUARD.ms = 500
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
  })
  afterEach(() => {
    vi.useRealTimers()
    OPEN_GUARD.ms = 0
  })

  it('a tap on Cancel in the first half second does nothing; after it, it closes', () => {
    const { onClose } = show()
    const cancel = screen.getByRole('button', { name: 'Cancel' })
    const tap = () => {
      fireEvent.pointerDown(cancel)
      fireEvent.mouseDown(cancel)
      fireEvent.pointerUp(cancel)
      fireEvent.mouseUp(cancel)
      fireEvent.click(cancel)
    }
    tap()
    expect(onClose).not.toHaveBeenCalled()
    act(() => vi.advanceTimersByTime(500))
    tap()
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
