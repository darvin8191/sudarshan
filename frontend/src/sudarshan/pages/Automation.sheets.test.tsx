/**
 * The Automation page's sheets (M12.1, P2): New and Edit, Alert set-up with the link that is
 * shown once and never put in the address, and Check, which sends nothing.
 */
import { fireEvent, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  list: vi.fn(),
  others: vi.fn(),
  one: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
  newLink: vi.fn(),
  check: vi.fn(),
}))
vi.mock('@/sudarshan/automation/api', async (original) => ({
  ...(await original<typeof import('@/sudarshan/automation/api')>()),
  automationApi: api,
}))
vi.mock('@/sudarshan/journal/api', () => ({
  journalCall: vi.fn(async () => ({ enabled: false, data: null, message: 'off' })),
}))
vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({ playAlertSound: () => {}, socket: null }),
}))

import { listOf, strategy } from '@/sudarshan/automation/fixtures'
import { settle as answered, back, pass, renderBook, where } from '@/test/bookPages'
import { fakeClock, frames } from '@/test/frames'
import Automation from './Automation'

const TOKEN = `sdt_${'k'.repeat(43)}`
const MESSAGES = {
  strategy: '{"v": 1, "id": "{{strategy.order.id}}"}',
  indicator: '//@version=6\nindicator("My signal")',
  condition: ['{"v": 1, "position": "long"}', '{"v": 1, "position": "flat"}'],
}
const made = (over = {}) => ({
  ok: true,
  status: 'success',
  strategy: strategy({ state: 'stopped', version: 1, ...over }),
  token: TOKEN,
  link_path: `/sudarshan/tv/${TOKEN}`,
  messages: MESSAGES,
})
const copied = vi.fn(async (_text: string) => {})

async function settle() {
  await answered()
  await pass(10)
}
const tap = async (name: string | RegExp) => {
  fireEvent.click(screen.getByRole('button', { name }))
  await settle()
}

beforeEach(() => {
  fakeClock()
  frames.install()
  vi.clearAllMocks()
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText: copied },
  })
  api.others.mockResolvedValue({ status: 'success', others: [] })
  api.one.mockResolvedValue({ status: 'success', strategy: strategy(), messages: MESSAGES })
})

describe('the Automation sheets', () => {
  it('makes a strategy, then shows its link once, to copy, and never in the address', async () => {
    api.list.mockResolvedValue(listOf([]))
    api.create.mockResolvedValue(made())
    renderBook('/automation', <Automation />)
    await settle()
    await tap('New strategy')
    expect(where()).toBe('/automation?sheet=new')
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: ' ORB ' } })
    api.list.mockResolvedValue(listOf([strategy({ state: 'stopped' })])) // the read after it
    await tap('Create strategy')
    expect(api.create).toHaveBeenCalledWith({
      name: 'ORB',
      kind: 'future',
      chart: null,
      product: 'MIS',
      direction: 'both',
      max_age_s: 30,
      max_alerts_day: 20,
      exchange: 'NFO',
      underlying: 'NIFTY',
      expiry_rule: 'month',
      lots: 1,
    })
    expect(where()).toBe('/automation?sheet=setup&id=1')
    const link = `${window.location.origin}/sudarshan/tv/${TOKEN}`
    expect(screen.getByTestId('auto-link')).toHaveTextContent(link)
    await tap('Copy the link')
    expect(copied).toHaveBeenCalledWith(link)
    expect(screen.getByRole('button', { name: 'Copied' })).toBeInTheDocument()
    await tap('Copy the message')
    expect(copied).toHaveBeenLastCalledWith(MESSAGES.strategy)
    fireEvent.click(screen.getByRole('tab', { name: 'Condition' }))
    expect(screen.getByRole('button', { name: 'Copy the flat message' })).toBeInTheDocument()
    expect(api.one).not.toHaveBeenCalled()
    // Closed and opened again, the link is gone: only its last letters are said.
    back()
    await settle()
    expect(where()).toBe('/automation')
    await tap('Alert set-up')
    expect(screen.queryByTestId('auto-link')).toBeNull()
    expect(screen.getByText(/link ends in Ab3x/)).toBeInTheDocument()
    expect(document.body.textContent).not.toContain(TOKEN)
  })

  it("shows the server's sentence when a strategy is refused, and keeps the form", async () => {
    api.list.mockResolvedValue(listOf([]))
    api.create.mockResolvedValue({
      ok: false,
      message: 'Lots must be a whole number from 1 to 100.',
    })
    renderBook('/automation', <Automation />)
    await settle()
    await tap('New strategy')
    fireEvent.change(screen.getByLabelText('Lots'), { target: { value: 'two' } })
    await tap('Create strategy')
    expect(api.create.mock.calls[0][0].lots).toBeNull()
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Lots must be a whole number from 1 to 100.'
    )
    expect(screen.getByRole('dialog', { name: 'New strategy' })).toBeInTheDocument()
  })

  it('makes an option strategy: nearest expiry and ATM unless chosen, sized in lots', async () => {
    api.list.mockResolvedValue(listOf([]))
    api.create.mockResolvedValue(made())
    renderBook('/automation', <Automation />)
    await settle()
    await tap('New strategy')
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'OPT' } })
    fireEvent.click(screen.getByRole('tab', { name: 'An option' }))
    expect(screen.getByText(/Options are only bought/)).toBeInTheDocument()
    expect(screen.getByLabelText('Contract')).toHaveValue('nearest')
    fireEvent.change(screen.getByLabelText('Strike'), { target: { value: 'OTM2' } })
    expect(screen.queryByRole('option', { name: 'Delivery (CNC)' })).toBeNull()
    await tap('Create strategy')
    expect(api.create).toHaveBeenCalledWith({
      name: 'OPT',
      kind: 'option',
      chart: null,
      product: 'MIS',
      direction: 'both',
      max_age_s: 30,
      max_alerts_day: 20,
      exchange: 'NFO',
      underlying: 'NIFTY',
      expiry_rule: 'nearest',
      strike_offset: 'OTM2',
      lots: 1,
    })
  })

  it('makes a scaled strategy: a multiplier and a cap in place of the fixed size', async () => {
    api.list.mockResolvedValue(listOf([]))
    api.create.mockResolvedValue(made())
    renderBook('/automation', <Automation />)
    await settle()
    await tap('New strategy')
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'SCL' } })
    fireEvent.change(screen.getByLabelText('Size'), { target: { value: 'scaled' } })
    expect(screen.queryByLabelText('Lots')).toBeNull()
    fireEvent.change(screen.getByLabelText('Multiplier'), { target: { value: '65' } })
    fireEvent.change(screen.getByLabelText('Cap, lots'), { target: { value: '2' } })
    expect(screen.getByText(/or nothing is sent/)).toBeInTheDocument()
    // an option strategy takes a fixed size: the choice goes away and lots come back
    fireEvent.click(screen.getByRole('tab', { name: 'An option' }))
    expect(screen.queryByLabelText('Size')).toBeNull()
    expect(screen.getByLabelText('Lots')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: 'A future' }))
    await tap('Create strategy')
    const sent = api.create.mock.calls[0][0]
    expect(sent).toMatchObject({ size_mode: 'scaled', multiplier: 65, max_lots: 2 })
    expect(sent).not.toHaveProperty('lots')
  })

  it('sizes a share chart in shares and offers Delivery, a future chart in lots', async () => {
    api.list.mockResolvedValue(listOf([]))
    renderBook('/automation', <Automation />)
    await settle()
    await tap('New strategy')
    fireEvent.click(screen.getByRole('tab', { name: 'Its chart' }))
    fireEvent.change(screen.getByLabelText('TradingView chart'), {
      target: { value: 'nse:reliance' },
    })
    expect(screen.getByLabelText('Shares')).toBeInTheDocument()
    expect(within(screen.getByLabelText('Product')).getByText('Delivery (CNC)')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('TradingView chart'), {
      target: { value: 'NSE:NIFTY1!' },
    })
    expect(screen.getByLabelText('Lots')).toBeInTheDocument()
  })

  it('lets only the name and limits of a strategy holding a position change, and sends what changed', async () => {
    const held = strategy({ held_symbol: 'NIFTY24NOV26FUT', held_side: 'long' })
    api.list.mockResolvedValue(listOf([held]))
    api.update.mockResolvedValue({
      ok: true,
      status: 'success',
      strategy: held,
      messages: MESSAGES,
    })
    renderBook('/automation', <Automation />)
    await settle()
    await tap('Edit')
    expect(screen.getByLabelText('Underlying')).toBeDisabled()
    expect(screen.getByText(/only its name and limits can change/)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Most alerts a day'), { target: { value: '12' } })
    await tap('Save')
    expect(api.update).toHaveBeenCalledWith(1, 3, { max_alerts_day: 12 })
    expect(where()).toBe('/automation')
  })

  it('asks before making a new link, sends once, and checks a pasted alert without an order', async () => {
    api.list.mockResolvedValue(listOf([strategy()]))
    api.newLink.mockResolvedValue(made({ state: 'active', version: 4 }))
    api.check.mockResolvedValue({
      ok: true,
      status: 'success',
      verdict: 'sent',
      sentence: 'Would send an order to the Sandbox to be long 65 NIFTY 24 Nov FUT.',
      order: { symbol: 'NIFTY24NOV26FUT', exchange: 'NFO', action: 'BUY', quantity: '65' },
    })
    renderBook('/automation', <Automation />)
    await settle()
    await tap('Alert set-up')
    expect(api.one).toHaveBeenCalledWith(1)
    await tap('New link')
    expect(api.newLink).not.toHaveBeenCalled()
    expect(screen.getByText(/The old link stops working at once\./)).toBeInTheDocument()
    const send = screen.getByRole('button', { name: 'Make a new link' })
    fireEvent.click(send)
    fireEvent.click(send)
    await settle()
    expect(api.newLink).toHaveBeenCalledTimes(1)
    expect(api.newLink).toHaveBeenCalledWith(1, 3)
    expect(screen.getByTestId('auto-link')).toHaveTextContent(TOKEN)
    fireEvent.change(screen.getByLabelText('Alert text to check'), { target: { value: '{"v":1}' } })
    await tap('Check')
    expect(api.check).toHaveBeenCalledWith(1, '{"v":1}')
    const answer = screen.getByTestId('auto-check')
    expect(answer).toHaveAttribute('data-verdict', 'sent')
    expect(answer).toHaveTextContent('Would send an order to the Sandbox to be long 65')
    expect(within(answer).getByText('NIFTY24NOV26FUT')).toBeInTheDocument()
  })
})
