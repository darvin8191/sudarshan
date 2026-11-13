/**
 * F8, the Flags block of All lists: the flag lists, Rename flags, Clear expired, the family render.
 * The bridge is the v2 mock.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const bridge = vi.hoisted(() => ({
  current: null as null | ReturnType<typeof import('./v2/mockBridge').createBridge>,
}))
vi.mock('@/api/client', async () => {
  const { createBridge } = await import('./v2/mockBridge')
  bridge.current = createBridge()
  return { webClient: bridge.current.client }
})

import { createQueryClient } from '@/app/providers'
import { AllListsSheet } from './AllListsSheet'
import { CLEAR_EXPIRED_ASK } from './FlagTools'
import { OPEN_GUARD } from './openGuard'
import type { FlagName, Lists, State } from './v2/types'

const b = () => bridge.current as NonNullable<typeof bridge.current>
const NAMES: FlagName[] = [
  { mark: 1, name: 'Watch' },
  { mark: 2, name: 'Breakout' },
  { mark: 3, name: 'Results' },
  { mark: 4, name: 'Long term' },
  { mark: 5, name: 'Avoid' },
]
const CONFLICT = 'Changed on another screen. The list is shown again.'

beforeEach(() => {
  b().reset()
  OPEN_GUARD.ms = 0
})

const lists = (flags: Lists['flags'] = []): Lists => ({
  version: 'a:1',
  own: [],
  presets: [],
  ranked: [],
  flags,
  favourites: [],
})
const flagEntry = (n: number, name: string, count: number) => ({
  ref: `flag:${n}`,
  kind: 'flag' as const,
  name,
  count,
  room: null,
  locked: true,
  note: null,
})
const stateWith = (flags: number): State => ({
  enabled: true,
  versions: { lists: 'a:1', favourites: 4, flags },
  flag_names: NAMES,
  favourites: [],
})

function mountSheet(readOnly = false) {
  b().on('state', () => stateWith(6))
  b().on('lists', () => lists([flagEntry(2, 'Breakout', 7), flagEntry(5, 'Avoid', 1)]))
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter>
        <AllListsSheet
          open
          onClose={() => {}}
          fed={new Set()}
          activeRef={null}
          onPick={() => {}}
          onNew={() => {}}
          newOff={null}
          readOnly={readOnly}
        />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe('the Flags block of All lists', () => {
  it('lists each flag list with its count, says it is read-only, and has the controls at 48 dp', async () => {
    mountSheet()
    const block = await screen.findByRole('region', { name: 'Flags' })
    expect(within(block).getByText('Breakout · 7')).toBeInTheDocument()
    expect(within(block).getByText('Avoid · 1')).toBeInTheDocument()
    expect(
      within(block).getByText('Flagged across your lists. Flag or unflag a symbol from its sheet.')
    ).toBeInTheDocument()
    for (const name of ['Rename flags', 'Clear expired']) {
      expect(within(block).getByRole('button', { name }).className).toContain('min-h-12')
    }
  })

  it('renames the flags: five fields of up to 24 letters, saved with the flags version', async () => {
    const user = userEvent.setup()
    b().on('flags', () => ({ version: '7', id: null, words: null }))
    mountSheet()
    await user.click(await screen.findByRole('button', { name: 'Rename flags' }))
    const fields = screen.getAllByRole('textbox')
    expect(fields).toHaveLength(5)
    for (const field of fields) expect(field).toHaveAttribute('maxlength', '24')
    await user.clear(fields[1])
    await user.type(fields[1], 'Breakout soon')
    await user.click(screen.getByRole('button', { name: 'Save names' }))
    await waitFor(() => expect(b().count('flags')).toBe(1))
    expect(b().calls.find((c) => c.op === 'flags')?.body).toEqual({
      version: 6,
      names: [
        { mark: 1, name: 'Watch' },
        { mark: 2, name: 'Breakout soon' },
        { mark: 3, name: 'Results' },
        { mark: 4, name: 'Long term' },
        { mark: 5, name: 'Avoid' },
      ],
    })
  })

  it('shows the words of a refused rename and reads again', async () => {
    const user = userEvent.setup()
    b().on('flags', () => ({ status: 'error', code: 'conflict', message: CONFLICT }))
    mountSheet()
    await user.click(await screen.findByRole('button', { name: 'Rename flags' }))
    await user.click(screen.getByRole('button', { name: 'Save names' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(CONFLICT)
    await waitFor(() => expect(b().count('state')).toBeGreaterThan(1))
  })

  it('clears expired contracts with the flags version and says how many went', async () => {
    const user = userEvent.setup()
    const calls: unknown[] = []
    let cleared = false
    b().on('expired_clear', (call) => {
      calls.push(call.body)
      cleared = true
      return { version: '7', id: null, words: 'Cleared 2 expired contracts.' }
    })
    b().on('lists', () =>
      lists([flagEntry(2, 'Breakout', cleared ? 5 : 7), flagEntry(5, 'Avoid', 1)])
    )
    mountSheet()
    b().on('lists', () =>
      lists([flagEntry(2, 'Breakout', cleared ? 5 : 7), flagEntry(5, 'Avoid', 1)])
    )
    await user.click(await screen.findByRole('button', { name: 'Clear expired' }))
    // Review L8: notes are his words, so it asks first; nothing is sent until he says so.
    expect(screen.getByText(CLEAR_EXPIRED_ASK)).toBeInTheDocument()
    expect(calls).toEqual([])
    await user.click(screen.getByRole('button', { name: 'Clear them' }))
    expect(await screen.findByText('Cleared 2 expired contracts.')).toBeInTheDocument()
    expect(calls).toEqual([{ version: 6 }])
    expect(await screen.findByText('Breakout · 5')).toBeInTheDocument()
    expect(screen.queryByText(CLEAR_EXPIRED_ASK)).toBeNull()
  })

  it('keeps everything when he says Keep them, and takes no tap on the question for half a second', async () => {
    const user = userEvent.setup()
    const calls: unknown[] = []
    b().on('expired_clear', (call) => {
      calls.push(call.body)
      return { version: '7', id: null, words: 'Cleared 2 expired contracts.' }
    })
    mountSheet()
    await user.click(await screen.findByRole('button', { name: 'Clear expired' }))
    await user.click(screen.getByRole('button', { name: 'Keep them' }))
    expect(screen.queryByText(CLEAR_EXPIRED_ASK)).toBeNull()
    expect(calls).toEqual([])
    OPEN_GUARD.ms = 500
    fireEvent.click(screen.getByRole('button', { name: 'Clear expired' }))
    fireEvent.click(screen.getByRole('button', { name: 'Clear them' })) // the tail of a double tap
    await act(async () => {})
    expect(calls).toEqual([])
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 550))
    })
    fireEvent.click(screen.getByRole('button', { name: 'Clear them' }))
    await waitFor(() => expect(calls).toEqual([{ version: 6 }]))
  })

  it('is absent in a family render', async () => {
    mountSheet(true)
    await screen.findByRole('dialog')
    await act(async () => {})
    expect(screen.queryByRole('region', { name: 'Flags' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Rename flags' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Clear expired' })).toBeNull()
  })
})
