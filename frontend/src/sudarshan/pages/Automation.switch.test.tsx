/**
 * The Automation page's Log and its switch (M12.1, P3): the default Stop keeps the exit alert
 * (owner decision D6), Stop everything asks again, every button sends once, the phrase is asked
 * only where the server needs it, and Live says why it is not offered.
 */
import { fireEvent, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  list: vi.fn(),
  others: vi.fn(),
  setState: vi.fn(),
  setMode: vi.fn(),
  alerts: vi.fn(),
}))
vi.mock('@/sudarshan/automation/api', async (original) => ({
  ...(await original<typeof import('@/sudarshan/automation/api')>()),
  automationApi: api,
}))
vi.mock('@/sudarshan/journal/api', () => ({
  journalCall: vi.fn(async () => ({ enabled: false, data: null, message: 'off' })),
}))
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
import { waitText } from '@/sudarshan/automation/LogSheet'
import { settle as answered, pass, renderBook, where } from '@/test/bookPages'
import { fakeClock, frames } from '@/test/frames'
import Automation from './Automation'

async function settle() {
  await answered()
  await pass(10)
}
const tap = async (name: string | RegExp) => {
  fireEvent.click(screen.getByRole('button', { name }))
  await settle()
}
const done = (over = {}) => ({
  ok: true,
  status: 'success',
  strategy: strategy(over),
  messages: {},
})

beforeEach(() => {
  fakeClock()
  frames.install()
  socket.handlers.clear()
  vi.clearAllMocks()
  api.others.mockResolvedValue({ status: 'success', others: [] })
})

describe('the switch of a strategy', () => {
  it('stops new entries by default, once, and says the exit alert still closes the position', async () => {
    api.list.mockResolvedValue(
      listOf([strategy({ held_symbol: 'NIFTY24NOV26FUT', held_side: 'long' })])
    )
    api.setState.mockResolvedValue(done({ state: 'exits_only', version: 4 }))
    renderBook('/automation', <Automation />)
    await settle()
    await tap('Stop')
    expect(where()).toBe('/automation?sheet=switch&id=1')
    const sheet = within(screen.getByRole('dialog', { name: 'Stop ORB' }))
    expect(
      sheet.getByText(/Its exit alert still closes the position it holds, NIFTY 24 Nov FUT\./)
    ).toBeInTheDocument()
    const stop = sheet.getByRole('button', { name: 'Stop new entries' })
    api.list.mockResolvedValue(listOf([strategy({ state: 'exits_only', version: 4 })]))
    fireEvent.click(stop)
    fireEvent.click(stop)
    await settle()
    expect(api.setState).toHaveBeenCalledTimes(1)
    expect(api.setState).toHaveBeenCalledWith(1, 3, 'exits_only')
    expect(where()).toBe('/automation')
    expect(screen.getByTestId('auto-state')).toHaveTextContent('Stopped for new entries')
  })

  it('asks again before it stops everything, and names the position nothing will close', async () => {
    api.list.mockResolvedValue(
      listOf([strategy({ held_symbol: 'NIFTY24NOV26FUT', held_side: 'long' })])
    )
    api.setState.mockResolvedValue(done({ state: 'stopped' }))
    renderBook('/automation', <Automation />)
    await settle()
    await tap('Stop')
    await tap('Stop everything, exits too')
    expect(api.setState).not.toHaveBeenCalled()
    expect(screen.getByText(/ORB holds NIFTY 24 Nov FUT: nothing will close it\./)).toBeVisible()
    await tap('Stop everything')
    expect(api.setState).toHaveBeenCalledWith(1, 3, 'stopped')
  })

  it('starts a Sandbox strategy without a phrase, and shows a refusal as its sentence', async () => {
    api.list.mockResolvedValue(listOf([strategy({ state: 'stopped' })]))
    api.setState.mockResolvedValue({
      ok: false,
      message: 'ORB was not started: Swing already trades the same contract and product.',
    })
    renderBook('/automation', <Automation />)
    await settle()
    await tap('Start')
    const sheet = within(screen.getByRole('dialog', { name: 'Start ORB' }))
    expect(sheet.queryByLabelText('Confirm phrase')).toBeNull()
    fireEvent.click(sheet.getByRole('button', { name: 'Start' }))
    await settle()
    expect(api.setState).toHaveBeenCalledWith(1, 3, 'active', undefined)
    expect(screen.getByRole('alert')).toHaveTextContent('Swing already trades the same contract')
    expect(api.list).toHaveBeenCalledTimes(2) // read again: another screen may have changed it
  })

  it('does not offer Live while the server does not allow it, and says why', async () => {
    api.list.mockResolvedValue(listOf([strategy()]))
    renderBook('/automation', <Automation />)
    await settle()
    await tap('Stop')
    expect(screen.getByTestId('auto-live-blocked')).toHaveTextContent(
      'Automation is in Sandbox only. Live comes in a later release. ORB stays in the Sandbox.'
    )
    expect(screen.getByRole('button', { name: 'Set to Live' })).toBeDisabled()
    expect(screen.queryByLabelText('Confirm phrase')).toBeNull()
  })

  it('asks the phrase for Live where it is allowed, and shows the refusal of a wrong one', async () => {
    api.list.mockResolvedValue(
      listOf([strategy()], { stage: 'on', live_allowed: true, live_blocked_why: null })
    )
    api.setMode.mockResolvedValue({
      ok: false,
      message: 'That is not your confirm phrase. Nothing was changed.',
    })
    renderBook('/automation', <Automation />)
    await settle()
    await tap('Stop')
    expect(screen.getByRole('button', { name: 'Set to Live' })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Confirm phrase'), { target: { value: 'nope' } })
    await tap('Set to Live')
    expect(api.setMode).toHaveBeenCalledWith(1, 3, 'live', 'nope')
    expect(screen.getByRole('alert')).toHaveTextContent('That is not your confirm phrase.')
    expect(screen.getByLabelText('Confirm phrase')).toHaveValue('')
    expect(within(screen.getByTestId('auto-card-1')).getByTestId('auto-mode')).toHaveTextContent(
      'Sandbox'
    )
  })
})

describe('the Log of a strategy', () => {
  it('pages back through the alerts, and reads again when one arrives', async () => {
    api.list.mockResolvedValue(listOf([strategy()]))
    api.alerts.mockImplementation(async (_id: number, before?: number) =>
      before
        ? {
            status: 'success',
            alerts: [alertLine({ id: 5, verdict: 'late', sentence: 'ORB: late.' })],
            more: false,
          }
        : { status: 'success', alerts: [alertLine({ id: 7 })], more: true }
    )
    renderBook('/automation', <Automation />)
    await settle()
    await tap('Log')
    const sheet = within(screen.getByRole('dialog', { name: 'Log: ORB' }))
    expect(sheet.getByText(/order sent to be long 65/)).toBeInTheDocument()
    expect(
      sheet.getByText('Sandbox · TradingView took 1.2 s · SUDARSHAN answered in 310 ms')
    ).toBeInTheDocument()
    await tap('Earlier alerts')
    expect(api.alerts).toHaveBeenLastCalledWith(1, 7)
    expect(sheet.getByText('ORB: late.').closest('[data-verdict]')).toHaveAttribute(
      'data-verdict',
      'late'
    )
    expect(screen.queryByRole('button', { name: 'Earlier alerts' })).toBeNull()
    const reads = api.alerts.mock.calls.length
    socket.handlers.get('sudarshan_automation')?.({
      kind: 'alert',
      strategy_id: 1,
      name: 'ORB',
      verdict: 'sent',
      sentence: 'ORB: exit sent.',
      at_ms: Date.now(),
    })
    await settle()
    expect(api.alerts.mock.calls.length).toBeGreaterThan(reads)
  })

  it('says a wait as a trader does', () => {
    expect(waitText(310)).toBe('310 ms')
    expect(waitText(1200)).toBe('1.2 s')
    expect(waitText(130_000)).toBe('2 min 10 s')
  })
})
