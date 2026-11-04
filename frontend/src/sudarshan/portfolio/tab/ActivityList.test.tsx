import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { PortfolioError } from '../api'
import { ACCOUNT, ASSET, entry } from '../sheets/fixtures'
import { WORDS } from '../types'
import { ActivityList, groupByMonth } from './ActivityList'

function show(items = [entry(1)], extra: Partial<React.ComponentProps<typeof ActivityList>> = {}) {
  const handlers = {
    onMore: vi.fn(),
    onAdd: vi.fn(),
    onImport: vi.fn(),
    onEdit: vi.fn(),
    onDuplicate: vi.fn(),
    onDelete: vi.fn().mockResolvedValue(undefined),
  }
  render(
    <ActivityList
      items={items}
      assets={[ASSET]}
      accounts={[ACCOUNT]}
      hasMore={false}
      loadingMore={false}
      {...handlers}
      {...extra}
    />
  )
  return handlers
}

describe('grouping', () => {
  it('keeps the service’s order (newest first) and starts a group at each new month', () => {
    const groups = groupByMonth([
      entry(1, { trade_date: 20261104 }),
      entry(2, { trade_date: 20261030 }),
      entry(3, { trade_date: 20260915 }),
      entry(4, { trade_date: 20261101 }),
    ])
    expect(groups.map((g) => [g.label, g.rows.map((r) => r.id)])).toEqual([
      ['November 2026', [1]],
      ['October 2026', [2]],
      ['September 2026', [3]],
      ['November 2026', [4]],
    ])
  })
})

describe('the activity list', () => {
  it('shows months as headings, newest first', () => {
    show([
      entry(1, { trade_date: 20261104 }),
      entry(2, { trade_date: 20261001 }),
      entry(3, { trade_date: 20260915 }),
    ])
    expect(screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)).toEqual([
      'November 2026',
      'October 2026',
      'September 2026',
    ])
    expect(screen.getAllByTestId('activity-row')).toHaveLength(3)
  })

  it('a row has its date, a kind chip with icon and word, asset, quantity x price, amount and account', () => {
    show([entry(1, { charges_paise: 2000 })])
    const row = screen.getByTestId('activity-row')
    expect(within(row).getByText('4 Nov 2026')).toBeTruthy()
    const chip = within(row).getByText('Buy')
    expect(chip.parentElement?.querySelector('svg')).not.toBeNull() // the icon beside the word
    expect(within(row).getByText('INFOSYS')).toBeTruthy()
    expect(within(row).getByText('10 × ₹1,428.25')).toBeTruthy()
    expect(within(row).getByText('₹14,282.50')).toBeTruthy()
    expect(within(row).getByText('Charges ₹20.00')).toBeTruthy()
    expect(within(row).getByText('Main demat')).toBeTruthy()
  })

  it('every kind has its own word, and a sale is not a buy by colour alone', () => {
    show([
      entry(1, { kind: 'SELL' }),
      entry(2, { kind: 'DIVIDEND', qty_u: null, price_u: null, amount_paise: 50_000 }),
      entry(3, {
        kind: 'FEE',
        asset_id: null,
        qty_u: null,
        price_u: null,
        amount_paise: 11800,
        note: 'Annual charge',
      }),
      entry(4, {
        kind: 'SPLIT',
        qty_u: null,
        price_u: null,
        amount_paise: null,
        ratio_num: 5,
        ratio_den: 1,
      }),
    ])
    for (const word of ['Sell', 'Dividend', 'Fee', 'Split'])
      expect(screen.getByText(word)).toBeTruthy()
    expect(screen.getByText('Annual charge')).toBeTruthy()
    expect(screen.getByText('5 new for every 1 held')).toBeTruthy()
  })

  it('a figure that is negative carries its minus', () => {
    show([
      entry(1, { kind: 'FEE', asset_id: null, qty_u: null, price_u: null, amount_paise: -50_000 }),
    ])
    expect(screen.getByText('−₹500.00')).toBeTruthy()
  })

  it('a draft says so in a word', () => {
    show([entry(1, { status: 'draft' })])
    expect(screen.getByText('Draft')).toBeTruthy()
  })

  it('a transfer names both accounts', () => {
    show([entry(1, { kind: 'TRANSFER', to_account_id: 1, account_id: null })])
    expect(screen.getByText('Outside to Main demat')).toBeTruthy()
  })

  it('the row menu is 48 dp and offers Edit and Duplicate for that entry', async () => {
    const h = show([entry(5)])
    const trigger = screen.getByRole('button', { name: /Actions for the Buy of 4 Nov 2026/ })
    expect(trigger.className).toMatch(/size-12/)
    await userEvent.click(trigger)
    await userEvent.click(await screen.findByRole('menuitem', { name: 'Edit' }))
    expect(h.onEdit).toHaveBeenCalledWith(expect.objectContaining({ id: 5 }))
    await userEvent.click(trigger)
    await userEvent.click(await screen.findByRole('menuitem', { name: 'Duplicate' }))
    expect(h.onDuplicate).toHaveBeenCalledWith(expect.objectContaining({ id: 5 }))
  })

  it('Delete asks first and sends nothing until it is confirmed', async () => {
    const h = show([entry(5)])
    await userEvent.click(screen.getByRole('button', { name: /Actions for/ }))
    await userEvent.click(await screen.findByRole('menuitem', { name: 'Delete' }))
    expect(await screen.findByText('Delete this entry?')).toBeTruthy()
    expect(h.onDelete).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Keep it' }))
    expect(h.onDelete).not.toHaveBeenCalled()
  })

  it('a confirmed delete is sent once, however often it is tapped, and closes the question', async () => {
    let finish: () => void = () => {}
    const onDelete = vi.fn().mockReturnValue(
      new Promise<void>((resolve) => {
        finish = resolve
      })
    )
    show([entry(5)], { onDelete })
    await userEvent.click(screen.getByRole('button', { name: /Actions for/ }))
    await userEvent.click(await screen.findByRole('menuitem', { name: 'Delete' }))
    const del = await screen.findByRole('button', { name: 'Delete' })
    fireEvent.click(del)
    fireEvent.click(del)
    fireEvent.click(del)
    expect(onDelete).toHaveBeenCalledTimes(1)
    expect(onDelete).toHaveBeenCalledWith(expect.objectContaining({ id: 5 }))
    await act(async () => finish())
    await waitFor(() => expect(screen.queryByText('Delete this entry?')).toBeNull())
  })

  it('a refused delete keeps the question open with the server’s sentence', async () => {
    const onDelete = vi.fn().mockRejectedValueOnce(
      new PortfolioError({
        code: 'version_conflict',
        message: WORDS.version_conflict,
        entryId: null,
      })
    )
    show([entry(5)], { onDelete })
    await userEvent.click(screen.getByRole('button', { name: /Actions for/ }))
    await userEvent.click(await screen.findByRole('menuitem', { name: 'Delete' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Delete' }))
    expect((await screen.findByRole('alert')).textContent).toBe(WORDS.version_conflict)
    expect(screen.getByText('Delete this entry?')).toBeTruthy()
  })

  it('shows older entries on request', async () => {
    const h = show([entry(1)], { hasMore: true })
    await userEvent.click(screen.getByRole('button', { name: 'Show older entries' }))
    expect(h.onMore).toHaveBeenCalledTimes(1)
  })
})

describe('an empty tab', () => {
  it('says so and offers both ways in', async () => {
    const h = show([])
    expect(screen.getByText('Nothing here yet. Add an entry or import a file.')).toBeTruthy()
    const add = screen.getByRole('button', { name: 'Add an entry' })
    const file = screen.getByRole('button', { name: 'Import a file' })
    expect(add.className).toMatch(/min-h-12/)
    expect(file.className).toMatch(/min-h-12/)
    await userEvent.click(add)
    await userEvent.click(file)
    expect(h.onAdd).toHaveBeenCalledTimes(1)
    expect(h.onImport).toHaveBeenCalledTimes(1)
  })
})
