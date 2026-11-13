/**
 * A list alert's card (M8.14): its one line and the service's words as sent, its member alerts
 * hidden until it is expanded, and Pause, Resume, Edit and Delete for the whole group, each sent
 * once with the group's version; Delete asks first and says how many alerts go, and takes no tap
 * for half a second after it opens.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ changeGroup: vi.fn(), removeGroup: vi.fn(), state: vi.fn() }))
vi.mock('@/api/marketAlerts', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/marketAlerts')>()),
  marketAlertsApi: api,
}))
const toasts = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}))
vi.mock('@/utils/toast', () => ({ toast: toasts, showToast: toasts }))

import { AlertsError, type GroupRow } from '@/api/marketAlerts'
import { createQueryClient } from '@/app/providers'
import { OPEN_GUARD } from '@/sudarshan/watchlist/openGuard'
import { alertsState, NOW } from '@/test/marketAlerts'
import { GroupCard, groupLine } from './GroupCard'
import { resetGroupActionsForTests } from './groupActions'
import { STATE_KEY } from './stateCache'
import { groupRow, memberRow } from './testGroups'

function renderCard(group: GroupRow, props: { expanded?: boolean } = {}) {
  const client = createQueryClient()
  client.setQueryData(STATE_KEY, alertsState({ groups: [group] }))
  const onEdit = vi.fn()
  const onToggle = vi.fn()
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <GroupCard
          group={group}
          members={[memberRow(1, 'SBIN'), memberRow(2, 'TCS')]}
          expanded={props.expanded ?? false}
          onToggle={onToggle}
          onEdit={onEdit}
          renderMember={(row) => <p data-member={row.id}>{row.symbol}</p>}
        />
      </MemoryRouter>
    </QueryClientProvider>
  )
  return { client, onEdit, onToggle, card: screen.getByRole('region') }
}

beforeEach(() => {
  resetGroupActionsForTests()
  for (const fn of [...Object.values(api), ...Object.values(toasts)]) fn.mockReset()
})

describe('the card', () => {
  it('draws its line and the service’s words as sent', () => {
    const words = [
      'The watchlist feature is switched off, so this alert keeps its last 24 symbols.',
      'Covers 180 of 195: alerts can follow at most 200 instruments and 500 Active alerts.',
      'ATM and strike rows change contract during the day and are left out: 2.',
    ]
    const group = groupRow({
      status_reason: words[0],
      follow: words[1],
      left_out: [words[2] as string],
    })
    const { card } = renderCard(group)
    expect(
      within(card).getByText('Swing · % move 2% · 24 symbols · Active · follows the list')
    ).toBeInTheDocument()
    expect(within(card).getByText('Up 2% on the day')).toBeInTheDocument()
    for (const line of words) expect(within(card).getByText(line)).toBeInTheDocument()
  })

  it('says Stopped and no longer follows when the list was deleted', () => {
    const group = groupRow({
      status: 'stopped',
      status_reason: 'The list Swing was deleted, so this alert stopped.',
    })
    expect(groupLine(group)).toBe('Swing · % move 2% · 24 symbols · Stopped')
    const { card } = renderCard(group)
    expect(
      within(card).getByText('The list Swing was deleted, so this alert stopped.')
    ).toBeInTheDocument()
    expect(within(card).queryByRole('button', { name: 'Pause' })).toBeNull()
    expect(within(card).queryByRole('button', { name: 'Resume' })).toBeNull()
    expect(within(card).getByRole('button', { name: 'Delete' })).toBeInTheDocument()
  })

  it('says other kinds in short words', () => {
    expect(groupLine(groupRow({ kind: 'day_break', params: { side: 'low' }, members: 1 }))).toBe(
      'Swing · Day low break · 1 symbol · Active · follows the list'
    )
    expect(
      groupLine(groupRow({ kind: 'vwap', params: { direction: 'above' }, members: 1234 }))
    ).toContain('VWAP cross · 1,234 symbols')
  })

  it('hides its member alerts until it is expanded', async () => {
    const closed = renderCard(groupRow())
    expect(closed.card.querySelector('[data-member]')).toBeNull()
    await userEvent
      .setup()
      .click(within(closed.card).getByRole('button', { name: /Show the alerts \(2\)/ }))
    expect(closed.onToggle).toHaveBeenCalledWith(7)
  })

  it('shows them, one per alert, when expanded', () => {
    const { card } = renderCard(groupRow(), { expanded: true })
    expect([...card.querySelectorAll('[data-member]')].map((each) => each.textContent)).toEqual([
      'SBIN',
      'TCS',
    ])
    expect(within(card).getByRole('button', { name: 'Hide the alerts' })).toHaveAttribute(
      'aria-expanded',
      'true'
    )
  })
})

describe('what the buttons do', () => {
  it('pauses once with the version, and takes the answer in place', async () => {
    let answer: (value: unknown) => void = () => {}
    api.changeGroup.mockReturnValue(
      new Promise((resolve) => {
        answer = resolve
      })
    )
    const user = userEvent.setup()
    const { card, client } = renderCard(groupRow())
    const pause = within(card).getByRole('button', { name: 'Pause' })
    await user.click(pause)
    await user.click(pause)
    expect(api.changeGroup).toHaveBeenCalledTimes(1)
    answer({ group: groupRow({ version: 3, status: 'paused' }), fit: '' })
    expect(api.changeGroup).toHaveBeenCalledWith(7, { version: 2, action: 'pause' })
    await waitFor(() =>
      expect(client.getQueryData<{ groups: GroupRow[] }>(STATE_KEY)?.groups[0]?.status).toBe(
        'paused'
      )
    )
  })

  it('resumes a paused one and shows the fit sentence as it came', async () => {
    const fit =
      'This alert needs 24 instruments and 24 alerts. In use now: 1 of 200 instruments and 1 of 500 Active alerts. It fits.'
    api.changeGroup.mockResolvedValue({ group: groupRow({ version: 3 }), fit })
    const user = userEvent.setup()
    const { card } = renderCard(groupRow({ status: 'paused' }))
    await user.click(within(card).getByRole('button', { name: 'Resume' }))
    expect(api.changeGroup).toHaveBeenCalledWith(7, { version: 2, action: 'resume' })
    await waitFor(() => expect(toasts.info).toHaveBeenCalledWith(fit))
  })

  it('opens the form from Edit', async () => {
    const { card, onEdit } = renderCard(groupRow())
    await userEvent.setup().click(within(card).getByRole('button', { name: 'Edit' }))
    expect(onEdit).toHaveBeenCalledWith(7)
  })

  it('shows a stale version in the service’s words and reads again', async () => {
    const words = 'This alert was changed on another screen. Your alerts are read again.'
    api.changeGroup.mockRejectedValue(
      new AlertsError({ code: 'conflict', message: words, field: null })
    )
    api.state.mockResolvedValue(alertsState())
    const user = userEvent.setup()
    const { card, client } = renderCard(groupRow())
    const spy = vi.spyOn(client, 'invalidateQueries')
    await user.click(within(card).getByRole('button', { name: 'Pause' }))
    await waitFor(() => expect(toasts.error).toHaveBeenCalledWith(words))
    expect(spy).toHaveBeenCalledWith({ queryKey: STATE_KEY })
    expect(within(card).getByRole('button', { name: 'Pause' })).toBeEnabled()
  })

  it('asks before deleting, names how many alerts go, sends once and drops the card', async () => {
    api.removeGroup.mockResolvedValue({ deleted: true, alerts: 24 })
    const user = userEvent.setup()
    const { card, client } = renderCard(groupRow())
    await user.click(within(card).getByRole('button', { name: 'Delete' }))
    expect(api.removeGroup).not.toHaveBeenCalled()
    const dialog = screen.getByRole('dialog')
    expect(
      within(dialog).getByText('Delete the Swing list alert and its 24 alerts?')
    ).toBeInTheDocument()
    expect(within(dialog).getByText('Their history stays.')).toBeInTheDocument()
    const confirm = within(dialog).getByRole('button', { name: 'Delete' })
    await user.click(confirm)
    await user.click(confirm)
    expect(api.removeGroup).toHaveBeenCalledTimes(1)
    expect(api.removeGroup).toHaveBeenCalledWith(7)
    await waitFor(() =>
      expect(client.getQueryData<{ groups: GroupRow[] }>(STATE_KEY)?.groups).toEqual([])
    )
    expect(toasts.success).toHaveBeenCalledWith('List alert deleted. Its history stays.')
  })

  it('cancelling the question sends nothing', async () => {
    const user = userEvent.setup()
    const { card } = renderCard(groupRow())
    await user.click(within(card).getByRole('button', { name: 'Delete' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }))
    expect(api.removeGroup).not.toHaveBeenCalled()
  })

  it('takes no tap on the delete question for half a second after it opens', async () => {
    OPEN_GUARD.ms = 500
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(NOW)
    try {
      api.removeGroup.mockResolvedValue({ deleted: true, alerts: 24 })
      const user = userEvent.setup()
      const { card } = renderCard(groupRow())
      vi.setSystemTime(NOW + 2_000)
      await user.click(within(card).getByRole('button', { name: 'Delete' }))
      const dialog = screen.getByRole('dialog')
      await user.click(within(dialog).getByRole('button', { name: 'Delete' }))
      expect(api.removeGroup).not.toHaveBeenCalled()
      vi.setSystemTime(NOW + 2_700)
      await waitFor(() =>
        expect(within(dialog).getByRole('button', { name: 'Delete' })).toBeEnabled()
      )
      await user.click(within(dialog).getByRole('button', { name: 'Delete' }))
      await waitFor(() => expect(api.removeGroup).toHaveBeenCalledTimes(1))
    } finally {
      vi.useRealTimers()
      OPEN_GUARD.ms = 0
    }
  })

  it('holds the buttons while a change has no answer, and says so', async () => {
    api.changeGroup.mockRejectedValue(
      new AlertsError({ code: 'unanswered', message: 'No answer.', field: null })
    )
    api.state.mockResolvedValue(alertsState())
    const user = userEvent.setup()
    const { card } = renderCard(groupRow())
    await user.click(within(card).getByRole('button', { name: 'Pause' }))
    await waitFor(() => expect(toasts.warning).toHaveBeenCalled())
    expect(within(card).getByRole('button', { name: 'Pause' })).toBeDisabled()
    expect(within(card).getByRole('button', { name: 'Edit' })).toBeDisabled()
  })
})
