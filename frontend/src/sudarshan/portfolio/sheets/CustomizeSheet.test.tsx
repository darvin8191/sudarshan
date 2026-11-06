/**
 * The Customize sheet (M7.25): switches and up and down buttons, sort and group, the layout saved
 * with its version, saved views by name, and the reset. The sentences of a refusal and of a lost
 * answer are shown as they are.
 */
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ call: vi.fn() }))
vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  portfolioCall: api.call,
}))

import { PortfolioError } from '../api'
import { view } from '../tab/fixtures'
import { type SavedView, WORDS } from '../types'
import { CustomizeSheet } from './CustomizeSheet'
import { tab } from './fixtures'

const DRAWN = ['summary', 'today', 'holdings', 'movers'] as const
const BASE = view([]).layout
const LAYOUT = {
  ...BASE,
  version: 3,
  spec: {
    ...BASE.spec,
    widgets: ['summary', 'holdings', 'movers'] as typeof BASE.spec.widgets,
    columns: ['name', 'value'] as typeof BASE.spec.columns,
  },
}
const FILTERS = { accounts: [1], classes: [], tags: [], held: null, as_of: null }
const SAVED: SavedView = {
  id: 9,
  tab_id: 2,
  name: 'Long term',
  spec: { filters: FILTERS, sort: 'gain_pct', group: 'class', columns: ['name'] },
  is_default: false,
  version: 2,
}

const used = vi.fn()
const changed = vi.fn()
const closed = vi.fn()

function show(views: SavedView[] = []) {
  return render(
    <CustomizeSheet
      open
      onClose={closed}
      tab={tab(2, 'Gold')}
      layout={LAYOUT}
      views={views}
      filters={FILTERS}
      drawn={DRAWN}
      appliedViewId={null}
      onUseView={used}
      onChanged={changed}
    />
  )
}

beforeEach(() => {
  api.call.mockReset()
  used.mockReset()
  changed.mockReset()
  closed.mockReset()
})

describe('Customize', () => {
  it('lists the sections the screen draws, the ones on first, with a switch and arrows', () => {
    show()
    const list = screen.getByRole('list', { name: 'Sections of the page' })
    expect(
      within(list)
        .getAllByRole('listitem')
        .map((li) => li.textContent?.split(/On|Off/)[0])
    ).toEqual(['Summary', 'Holdings', 'Top movers', 'Today'])
    expect(within(list).getByRole('switch', { name: 'Today' }).getAttribute('aria-checked')).toBe(
      'false'
    )
    expect(
      within(list).getByRole('button', { name: 'Move Summary up' }).hasAttribute('disabled')
    ).toBe(true)
  })

  it('saves the layout with its version after a change of order, a switch, a sort and a group', async () => {
    api.call.mockResolvedValue({ enabled: true, data: {} })
    show()
    const sections = screen.getByRole('list', { name: 'Sections of the page' })
    await userEvent.click(within(sections).getByRole('button', { name: 'Move Holdings up' }))
    await userEvent.click(within(sections).getByRole('switch', { name: 'Today' }))
    await userEvent.click(screen.getByRole('button', { name: 'Name', exact: true }))
    await userEvent.click(screen.getByRole('button', { name: 'Class' }))
    await userEvent.click(screen.getByRole('button', { name: 'Save layout' }))
    await waitFor(() => expect(changed).toHaveBeenCalled())
    expect(api.call).toHaveBeenCalledWith('layout_set', {
      id: 2,
      version: 3,
      spec: {
        v: 1,
        widgets: ['holdings', 'summary', 'movers', 'today'],
        columns: ['name', 'value'],
        sort: 'name',
        group: 'class',
      },
    })
    expect(closed).toHaveBeenCalled()
  })

  it('keeps the last column on', () => {
    show()
    const columns = screen.getByRole('list', { name: 'Columns of the holdings' })
    // Two columns are on: turning one off leaves a last one, which cannot be turned off.
    expect(within(columns).getByRole('switch', { name: 'Name' }).hasAttribute('disabled')).toBe(
      false
    )
  })

  it('has nothing to save until something changes', () => {
    show()
    expect(screen.getByRole('button', { name: 'Save layout' }).hasAttribute('disabled')).toBe(true)
  })

  it('shows the sentence of a layout changed on another screen, and saves nothing', async () => {
    api.call.mockRejectedValue(
      new PortfolioError({
        code: 'version_conflict',
        message: WORDS.version_conflict,
        entryId: null,
      })
    )
    show()
    await userEvent.click(screen.getByRole('button', { name: 'Class' }))
    await userEvent.click(screen.getByRole('button', { name: 'Save layout' }))
    expect((await screen.findByRole('alert')).textContent).toBe(WORDS.version_conflict)
    expect(closed).not.toHaveBeenCalled()
  })

  it('says Not confirmed when the answer was lost', async () => {
    api.call.mockRejectedValue(
      new PortfolioError({ code: 'unanswered', message: 'x', entryId: null })
    )
    show()
    await userEvent.click(
      screen.getByRole('button', { name: 'Reset to the defaults of this kind of tab' })
    )
    expect((await screen.findByRole('alert')).textContent).toBe(WORDS.not_confirmed)
  })

  it('resets to the defaults of this kind of tab with a null layout', async () => {
    api.call.mockResolvedValue({ enabled: true, data: {} })
    show()
    await userEvent.click(
      screen.getByRole('button', { name: 'Reset to the defaults of this kind of tab' })
    )
    await waitFor(() => expect(closed).toHaveBeenCalled())
    expect(api.call).toHaveBeenCalledWith('layout_set', { id: 2, version: 3, spec: null })
  })

  it('saves a view by name with the filters on screen and the sort, group and columns above', async () => {
    api.call.mockResolvedValue({ enabled: true, data: SAVED })
    show()
    await userEvent.type(screen.getByLabelText('Name for a new view'), ' Long term ')
    await userEvent.click(screen.getByLabelText('Open this tab with it'))
    await userEvent.click(screen.getByRole('button', { name: 'Save this view' }))
    await waitFor(() => expect(changed).toHaveBeenCalled())
    const [op, args] = api.call.mock.calls[0]
    expect(op).toBe('view_create')
    expect(args).toMatchObject({
      id: 2,
      name: 'Long term',
      is_default: true,
      spec: { filters: FILTERS, sort: 'value', group: 'none', columns: ['name', 'value'] },
    })
    expect(typeof args.request_id).toBe('string')
  })

  it('uses a saved view, makes it the default and deletes it after a second tap', async () => {
    api.call.mockResolvedValue({ enabled: true, data: SAVED })
    show([SAVED])
    await userEvent.click(screen.getByRole('button', { name: 'Open Long term first' }))
    await waitFor(() =>
      expect(api.call).toHaveBeenCalledWith('view_update', { id: 9, version: 2, is_default: true })
    )
    await userEvent.click(screen.getByRole('button', { name: 'Delete Long term' }))
    expect(api.call).not.toHaveBeenCalledWith('view_delete', expect.anything())
    await userEvent.click(screen.getByRole('button', { name: 'Delete Long term for good' }))
    await waitFor(() => expect(api.call).toHaveBeenCalledWith('view_delete', { id: 9, version: 2 }))
    await userEvent.click(screen.getByRole('button', { name: 'Use Long term' }))
    expect(used).toHaveBeenCalledWith(SAVED)
    expect(closed).toHaveBeenCalled()
  })
})
