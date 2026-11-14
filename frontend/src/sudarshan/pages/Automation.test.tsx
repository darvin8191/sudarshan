/**
 * The Automation page (M12.1): a card per strategy with its state and mode in words, what it
 * holds, its day and its last alert; nothing polls, and the server's event changes the card.
 */
import { screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ list: vi.fn(), others: vi.fn() }))
vi.mock('@/sudarshan/automation/api', async (original) => ({
  ...(await original<typeof import('@/sudarshan/automation/api')>()),
  automationApi: api,
}))
const journal = vi.hoisted(() => ({ call: vi.fn() }))
vi.mock('@/sudarshan/journal/api', () => ({ journalCall: journal.call }))
const socket = vi.hoisted(() => ({ handlers: new Map<string, (event?: unknown) => void>() }))
vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({
    playAlertSound: () => {},
    socket: {
      on: (event: string, handler: () => void) => socket.handlers.set(event, handler),
      off: (event: string) => socket.handlers.delete(event),
    },
  }),
}))

import { alertLine, listOf, strategy } from '@/sudarshan/automation/fixtures'
import { settle as answered, pass, renderBook } from '@/test/bookPages'
import { fakeClock, frames } from '@/test/frames'
import Automation from './Automation'

/** Let the answers arrive and the page draw them (the query client tells React on a timer). */
async function settle() {
  await answered()
  await pass(10)
}

beforeEach(() => {
  fakeClock()
  frames.install()
  socket.handlers.clear()
  vi.clearAllMocks()
  api.others.mockResolvedValue({ status: 'success', others: [] })
  journal.call.mockResolvedValue({ enabled: false, data: null, message: 'off' })
})

describe('the Automation page', () => {
  it('says so when there is no strategy, and where automation may send', async () => {
    api.list.mockResolvedValue(listOf([]))
    renderBook('/automation', <Automation />)
    await settle()
    expect(screen.getByTestId('auto-empty')).toHaveTextContent('No strategy yet.')
    expect(screen.getByTestId('auto-stage')).toHaveTextContent('Automation is in Sandbox only.')
    expect(screen.getByText('Nothing else here can send an order by itself.')).toBeInTheDocument()
  })

  it('shows a strategy with its state and mode in words, its position, day and last alert', async () => {
    journal.call.mockImplementation(async (_op: string, args: { strategy: string }) =>
      args.strategy === 'ORB'
        ? { enabled: true, data: { metrics: { trips: 2, net_paise: 124000, charges_paise: 9640 } } }
        : { enabled: false, data: null, message: 'off' }
    )
    api.list.mockResolvedValue(
      listOf([
        strategy({
          held_symbol: 'NIFTY24NOV26FUT',
          held_side: 'long',
          alerts_today: 3,
          last_alert: alertLine(),
        }),
        strategy({ id: 2, name: 'Swing', state: 'stopped', last_alert: null }),
      ])
    )
    renderBook('/automation', <Automation />)
    await settle()
    const card = within(screen.getByTestId('auto-card-1'))
    expect(card.getByTestId('auto-state')).toHaveTextContent('Active')
    expect(card.getByTestId('auto-mode')).toHaveTextContent('Sandbox')
    expect(card.getByText('Long NIFTY 24 Nov FUT in the Sandbox')).toBeInTheDocument()
    expect(card.getByText(/3 of 20 alerts · 2 trades closed ·/)).toHaveTextContent(
      '+₹1,240.00 after est. charges ₹96.40'
    )
    expect(card.getByText(/order sent to be long 65 NIFTY 24 Nov FUT\./)).toBeInTheDocument()
    expect(journal.call).toHaveBeenCalledWith('analytics', {
      mode: 'sandbox',
      strategy: 'ORB',
      period: 'today',
    })
    const stopped = within(screen.getByTestId('auto-card-2'))
    expect(stopped.getByTestId('auto-state')).toHaveTextContent('Stopped')
    expect(stopped.getByText('Holds nothing')).toBeInTheDocument()
    expect(stopped.getByText('No alert has arrived yet')).toBeInTheDocument()
    expect(stopped.getByText(/Journal is off/)).toBeInTheDocument()
  })

  it('never reads on a timer, and shows an alert the moment the server announces it', async () => {
    api.list.mockResolvedValue(listOf([strategy()]))
    renderBook('/automation', <Automation />)
    await settle()
    await pass(120_000)
    expect(api.list).toHaveBeenCalledTimes(1)
    api.list.mockReturnValue(new Promise(() => {})) // the read it sets off has not answered yet
    socket.handlers.get('sudarshan_automation')?.({
      kind: 'alert',
      strategy_id: 1,
      name: 'ORB',
      verdict: 'late',
      sentence: 'ORB: a buy alert arrived 2 min 10 s late and was not sent.',
      at_ms: Date.now(),
    })
    await settle()
    const line = screen.getByText(/arrived 2 min 10 s late and was not sent\./)
    expect(line.closest('[data-verdict]')).toHaveAttribute('data-verdict', 'late')
    expect(api.list).toHaveBeenCalledTimes(2)
  })

  it('moves the switch on the card when the server says a strategy was stopped', async () => {
    api.list.mockResolvedValue(listOf([strategy()]))
    renderBook('/automation', <Automation />)
    await settle()
    api.list.mockReturnValue(new Promise(() => {}))
    socket.handlers.get('sudarshan_automation')?.({
      kind: 'strategy',
      strategy_id: 1,
      name: 'ORB',
      state: 'exits_only',
      mode: 'sandbox',
      at_ms: Date.now(),
    })
    await settle()
    expect(screen.getByTestId('auto-state')).toHaveTextContent('Stopped for new entries')
  })

  it('lists the other automation on this server, each a link to its own page', async () => {
    api.list.mockResolvedValue(listOf([], { notes: ['The proxy setting needs a check.'] }))
    api.others.mockResolvedValue({
      status: 'success',
      others: [{ kind: 'flow', name: 'Breakout', on: true, detail: 'webhook on', path: '/flow' }],
    })
    renderBook('/automation', <Automation />)
    await settle()
    expect(screen.getByRole('alert')).toHaveTextContent('The proxy setting needs a check.')
    expect(screen.getByRole('link', { name: /Breakout/ })).toHaveAttribute('href', '/flow')
    await waitFor(() => expect(screen.getByText('On · webhook on')).toBeInTheDocument())
  })

  it('says when an Active strategy has had no alert for three trading days, and not before (P4)', async () => {
    api.list.mockResolvedValue(
      listOf([
        strategy({ id: 1, name: 'Quiet', quiet_days: 3 }),
        strategy({ id: 2, name: 'Fresh', quiet_days: 2 }),
        strategy({ id: 3, name: 'Off', state: 'stopped', quiet_days: 9 }),
      ])
    )
    renderBook('/automation', <Automation />)
    await settle()
    const quiet = screen.getAllByTestId('auto-quiet')
    expect(quiet).toHaveLength(1)
    expect(screen.getByTestId('auto-card-1')).toContainElement(quiet[0])
    expect(quiet[0]).toHaveTextContent('No alert for 3 trading days.')
    expect(quiet[0].querySelector('svg')).not.toBeNull() // never colour alone
  })

  it('lists a Python script among the other automation, as a link to its page (P4)', async () => {
    api.list.mockResolvedValue(listOf([]))
    api.others.mockResolvedValue({
      status: 'success',
      others: [
        { kind: 'python', name: 'ORB script', on: true, detail: 'running', path: '/python' },
      ],
    })
    renderBook('/automation', <Automation />)
    await settle()
    const link = screen.getByRole('link', { name: /ORB script/ })
    expect(link).toHaveAttribute('href', '/python')
    expect(link).toHaveTextContent('On · running')
    expect(
      screen.getByText('The kill switch on the Risk page stops new orders from all of these.')
    ).toBeInTheDocument()
  })
})
