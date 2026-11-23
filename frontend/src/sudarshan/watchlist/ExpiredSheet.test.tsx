/** M8.2 TD1: the sheet of an expired contract asks before it removes, and removes once. */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ExpiredSheet } from './ExpiredSheet'

const ITEM = { id: 13, symbol: 'NIFTY03NOV2622700PE', exchange: 'NFO', position: 2 }

describe('the sheet of an expired contract (M8.2 TD1)', () => {
  it('sends the removal once however it is tapped, and Keep it sends nothing', () => {
    const onRemove = vi.fn()
    const onClose = vi.fn()
    render(
      <ExpiredSheet
        item={ITEM}
        day="2026-11-03"
        listName="INDEX"
        onClose={onClose}
        onRemove={onRemove}
      />
    )
    const sheet = within(screen.getByRole('dialog', { name: 'NIFTY 22700 PE' }))
    expect(sheet.getByText('Expired on 3 Nov 2026')).toBeInTheDocument()
    fireEvent.click(sheet.getByRole('button', { name: 'Keep it' }))
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(onRemove).not.toHaveBeenCalled()
    const remove = sheet.getByRole('button', { name: 'Remove' })
    fireEvent.click(remove)
    fireEvent.click(remove)
    expect(onRemove).toHaveBeenCalledTimes(1)
    expect(onRemove).toHaveBeenCalledWith(ITEM)
  })

  it('only says when it expired on a list that cannot be changed', () => {
    render(<ExpiredSheet item={ITEM} day="2026-11-03" listName="NIFTY 50" onClose={() => {}} />)
    const sheet = within(screen.getByRole('dialog'))
    expect(sheet.getByText('It is no longer quoted or traded.')).toBeInTheDocument()
    expect(sheet.queryByRole('button', { name: 'Remove' })).toBeNull()
    expect(sheet.queryByRole('button', { name: 'Keep it' })).toBeNull()
  })
})
