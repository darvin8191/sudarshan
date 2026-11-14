import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { describe, expect, it } from 'vitest'
import { ChainLink } from './ChainLink'

const show = (symbol: string, exchange: string) =>
  render(
    <MemoryRouter>
      <ChainLink symbol={symbol} exchange={exchange} />
    </MemoryRouter>
  )

describe('the chart app bar’s option chain link (O9)', () => {
  it('opens the chain of a charted option on its expiry and strike', () => {
    show('NIFTY17NOV2625900CE', 'NFO')
    expect(screen.getByRole('link', { name: 'Option chain' })).toHaveAttribute(
      'href',
      '/options/NFO/NIFTY?expiry=2026-11-17&strike=25900'
    )
  })

  it('opens an index’s own chain', () => {
    show('NIFTY', 'NSE_INDEX')
    expect(screen.getByRole('link', { name: 'Option chain' })).toHaveAttribute(
      'href',
      '/options/NSE_INDEX/NIFTY'
    )
  })

  it('is a 48 dp target', () => {
    show('SENSEX', 'BSE_INDEX')
    expect(screen.getByRole('link', { name: 'Option chain' }).className).toContain('size-12')
  })

  it('is not drawn for a share, whose chain its name cannot tell', () => {
    show('RELIANCE', 'NSE')
    expect(screen.queryByRole('link')).toBeNull()
  })
})
