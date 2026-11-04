/** The filters: each is told in words with a way to take it off; as of a date cannot be future. */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import type { Account, Asset, Filters } from '../types'
import { countFilters, FilterBar, NO_FILTERS } from './Filters'

const ACCOUNTS = [
  { id: 1, name: 'Demat', system: false },
  { id: 2, name: 'Bank', system: false },
] as Account[]
const ASSETS = [
  { id: 1, asset_class: 'equity', tags: ['core'] },
  { id: 2, asset_class: 'gold', tags: [] },
] as unknown as Asset[]

function Harness({ onChange }: { onChange: (f: Filters) => void }) {
  const [filters, set] = useState<Filters>(NO_FILTERS)
  return (
    <FilterBar
      filters={filters}
      onChange={(f) => {
        set(f)
        onChange(f)
      }}
      accounts={ACCOUNTS}
      assets={ASSETS}
      today={20261104}
    />
  )
}

describe('the filter bar', () => {
  it('counts the filters in force and offers each choice from the tab’s own assets', async () => {
    const onChange = vi.fn()
    render(<Harness onChange={onChange} />)
    await userEvent.click(screen.getByRole('button', { name: /Filters/ }))
    expect(screen.getByRole('button', { name: 'Demat' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Gold and silver' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'core' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Crypto' })).toBeNull() // not in this tab
    await userEvent.click(screen.getByRole('button', { name: 'Gold and silver' }))
    await userEvent.click(screen.getByRole('button', { name: 'Held over a year' }))
    expect(onChange).toHaveBeenLastCalledWith({ ...NO_FILTERS, classes: ['gold'], held: 'over' })
    expect(screen.getByRole('button', { name: /Filters · 2/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Remove filter: Gold and silver' })).toBeTruthy()
  })

  it('removes one filter, or all of them', async () => {
    render(<Harness onChange={() => {}} />)
    await userEvent.click(screen.getByRole('button', { name: /Filters/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Bank' }))
    await userEvent.click(screen.getByRole('button', { name: 'Remove filter: Bank' }))
    expect(screen.queryByRole('button', { name: /Remove filter/ })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Held under a year' }))
    await userEvent.click(screen.getByRole('button', { name: 'Clear filters' }))
    expect(screen.queryByRole('button', { name: 'Clear filters' })).toBeNull()
  })

  it('as of a date is a day, said in words, and never after today', async () => {
    const onChange = vi.fn()
    render(<Harness onChange={onChange} />)
    await userEvent.click(screen.getByRole('button', { name: /Filters/ }))
    const date = screen.getByLabelText('As of a date') as HTMLInputElement
    expect(date.max).toBe('2026-11-04')
    await userEvent.type(date, '2026-09-12')
    expect(onChange).toHaveBeenLastCalledWith({ ...NO_FILTERS, as_of: 20260912 })
    expect(screen.getByRole('button', { name: 'Remove filter: As of 12 Sep 2026' })).toBeTruthy()
    expect(countFilters({ ...NO_FILTERS, as_of: 20260912 })).toBe(1)
  })
})
