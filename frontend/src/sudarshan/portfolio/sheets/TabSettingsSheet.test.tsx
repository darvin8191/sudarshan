import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ call: vi.fn() }))
vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  portfolioCall: api.call,
}))

import { PortfolioError } from '../api'
import { WORDS } from '../types'
import { tab } from './fixtures'
import { TabSettingsSheet } from './TabSettingsSheet'

beforeEach(() => {
  api.call.mockReset()
})

const GOLD = tab(2, 'Gold', { version: 4 })
const SIBLINGS = [tab(1, 'Stocks'), GOLD, tab(3, 'Funds')]

function show(current = GOLD, siblings = SIBLINGS) {
  const handlers = { onClose: vi.fn(), onChanged: vi.fn(), onDeleted: vi.fn() }
  render(<TabSettingsSheet open tab={current} siblings={siblings} {...handlers} />)
  return handlers
}

describe('the Tab settings sheet', () => {
  it('starts from the tab and saves only what changed, with the tab’s version', async () => {
    api.call.mockResolvedValue({ enabled: true, data: GOLD })
    const { onChanged } = show()
    const save = screen.getByRole('button', { name: 'Save changes' }) as HTMLButtonElement
    expect(save.disabled).toBe(true)
    const name = screen.getByRole('textbox', { name: /^Name/ })
    await userEvent.clear(name)
    await userEvent.type(name, 'Gold and silver')
    await userEvent.click(screen.getByRole('button', { name: 'Gem' }))
    await userEvent.click(save)
    expect(api.call).toHaveBeenCalledWith('tab_update', {
      id: 2, version: 4, name: 'Gold and silver', icon: 'gem',
    })
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
  })

  it('moves the tab earlier or later among the others, not past the ends', async () => {
    api.call.mockResolvedValue({ enabled: true, data: GOLD })
    show()
    await userEvent.click(screen.getByRole('button', { name: /Move earlier/ }))
    expect(api.call).toHaveBeenLastCalledWith('tab_update', { id: 2, version: 4, position: 1 })
    api.call.mockClear()
    await userEvent.click(screen.getByRole('button', { name: /Move later/ }))
    expect(api.call).toHaveBeenLastCalledWith('tab_update', { id: 2, version: 4, position: 3 })
  })

  it('the first tab cannot move earlier', () => {
    show(SIBLINGS[0])
    expect((screen.getByRole('button', { name: /Move earlier/ }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('a tab changed on another screen shows the server’s sentence and saves nothing', async () => {
    api.call.mockRejectedValueOnce(
      new PortfolioError({ code: 'version_conflict', message: WORDS.version_conflict, entryId: null })
    )
    const { onChanged } = show()
    await userEvent.type(screen.getByRole('textbox', { name: /Second name/ }), 'x')
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    expect((await screen.findByRole('alert')).textContent).toBe(WORDS.version_conflict)
    expect(api.call).toHaveBeenCalledTimes(1)
    expect(onChanged).not.toHaveBeenCalled()
  })

  it('a lost answer is told as not confirmed', async () => {
    api.call.mockRejectedValueOnce(
      new PortfolioError({ code: 'not_confirmed', message: WORDS.not_confirmed, entryId: null })
    )
    show()
    await userEvent.click(screen.getByRole('button', { name: /Archive this tab/ }))
    expect((await screen.findByRole('alert')).textContent).toBe(WORDS.not_confirmed)
    expect(api.call).toHaveBeenCalledTimes(1)
  })

  it('archives, sending once however often it is tapped', async () => {
    let finish: (value: unknown) => void = () => {}
    api.call.mockReturnValue(new Promise((resolve) => { finish = resolve }))
    show()
    const archive = screen.getByRole('button', { name: /Archive this tab/ })
    fireEvent.click(archive)
    fireEvent.click(archive)
    expect(api.call).toHaveBeenCalledTimes(1)
    expect(api.call).toHaveBeenCalledWith('tab_update', { id: 2, version: 4, archived: true })
    await act(async () => finish({ enabled: true, data: GOLD }))
  })

  it('offers no delete for a tab that is not archived', () => {
    show()
    expect(screen.queryByRole('button', { name: 'Delete this tab' })).toBeNull()
  })

  it('an archived tab can be restored, and deleted only with its name typed', async () => {
    const old = tab(2, 'Old gold', { archived: true })
    api.call.mockResolvedValue({ enabled: true, data: { id: 2 } })
    const { onDeleted } = show(old, [])
    expect(screen.getByRole('button', { name: /Restore this tab/ })).toBeTruthy()
    const del = screen.getByRole('button', { name: 'Delete this tab' }) as HTMLButtonElement
    expect(del.disabled).toBe(true)
    const box = screen.getByLabelText(/Type the tab’s name/)
    await userEvent.type(box, 'old gold')
    expect(del.disabled).toBe(true) // not the same letters
    await userEvent.clear(box)
    await userEvent.type(box, 'Old gold')
    expect(del.disabled).toBe(false)
    await userEvent.click(del)
    expect(api.call).toHaveBeenCalledWith('tab_delete', { id: 2, version: 4, confirm_name: 'Old gold' })
    await waitFor(() => expect(onDeleted).toHaveBeenCalledWith(2))
  })

  it('the position buttons are off for an archived tab', () => {
    show(tab(2, 'Old', { archived: true }), [])
    expect(screen.queryByRole('button', { name: /Move earlier/ })).toBeNull()
  })
})
