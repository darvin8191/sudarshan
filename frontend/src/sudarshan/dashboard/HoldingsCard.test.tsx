import { act, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useBrokerStore } from '@/stores/brokerStore'
import { HoldingsCard } from './HoldingsCard'

const getHoldings = vi.fn()
vi.mock('@/api/trading', () => ({
  tradingApi: { getHoldings: (...args: unknown[]) => getHoldings(...args) },
}))

const MINUS = '−'

function answer(count: number, pnl = 29780.4, percent = 9.53) {
  return {
    status: 'success',
    data: {
      holdings: Array.from({ length: count }, (_, i) => ({ symbol: `S${i}`, exchange: 'NSE' })),
      statistics: {
        totalholdingvalue: 342180.4,
        totalinvvalue: 312400,
        totalprofitandloss: pnl,
        totalpnlpercentage: percent,
      },
    },
  }
}

async function show(apiKey: string | null = 'key') {
  const view = render(
    <MemoryRouter>
      <HoldingsCard apiKey={apiKey} />
    </MemoryRouter>
  )
  await act(async () => {
    await Promise.resolve()
  })
  return view
}

beforeEach(() => {
  getHoldings.mockReset()
  useBrokerStore.setState({ capabilities: null, isLoaded: false })
})

afterEach(() => {
  useBrokerStore.setState({ capabilities: null, isLoaded: false })
})

describe('HoldingsCard', () => {
  it('sums up the holdings: count, value, invested and overall P&L with sign and arrow', async () => {
    getHoldings.mockResolvedValue(answer(12))
    await show()

    const card = screen.getByRole('region', { name: /Holdings/ })
    expect(card).toHaveTextContent('12')
    expect(screen.getByText('₹3,42,180')).toBeInTheDocument()
    expect(screen.getByText('Invested ₹3,12,400')).toBeInTheDocument()
    expect(screen.getByText('+₹29,780')).toHaveClass('text-[color:var(--up)]')
    expect(screen.getByText('+9.53%')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'See all' })).toHaveAttribute('href', '/holdings')
  })

  it('shows a loss on the holdings with a true minus', async () => {
    getHoldings.mockResolvedValue(answer(3, -5120.6, -1.64))
    await show()

    expect(screen.getByText(`${MINUS}₹5,121`)).toHaveClass('text-[color:var(--down)]')
    expect(screen.getByText(`${MINUS}1.64%`)).toBeInTheDocument()
  })

  it('stays out of the way when there are no holdings', async () => {
    getHoldings.mockResolvedValue(answer(0))
    const { container } = await show()

    expect(container).toBeEmptyDOMElement()
  })

  it('stays out of the way when the call fails', async () => {
    getHoldings.mockRejectedValue(new Error('network'))
    const { container } = await show()

    expect(container).toBeEmptyDOMElement()
  })

  it('does not ask a broker that has no holdings, or without an API key', async () => {
    useBrokerStore.setState({
      capabilities: { broker_type: 'crypto' } as never,
      isLoaded: true,
    })
    const crypto = await show()
    expect(getHoldings).not.toHaveBeenCalled()
    crypto.unmount()

    useBrokerStore.setState({ capabilities: null, isLoaded: false })
    await show(null)
    expect(getHoldings).not.toHaveBeenCalled()
  })
})
