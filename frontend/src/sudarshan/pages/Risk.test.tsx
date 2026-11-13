/**
 * The Risk page (M20.7): its kill switch sheets live in the address so the phone's Back closes
 * one before it leaves the page, and nothing polls (rule 7): the status is read once and again
 * when the server announces a change.
 */
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  status: vi.fn(),
  audit: vi.fn(),
  preview: vi.fn(),
  turnOn: vi.fn(),
  resume: vi.fn(),
}))
vi.mock('@/api/risk', () => ({ riskApi: api }))
vi.mock('@/api/trading', () => ({
  tradingApi: {
    getPositions: vi.fn(async () => ({ data: [] })),
    getOrders: vi.fn(async () => ({ data: {} })),
  },
}))
const socket = vi.hoisted(() => ({ handlers: new Map<string, () => void>() }))
vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({
    playAlertSound: () => {},
    socket: {
      on: (event: string, handler: () => void) => socket.handlers.set(event, handler),
      off: (event: string) => socket.handlers.delete(event),
    },
  }),
}))
vi.mock('@/utils/toast', () => ({
  showToast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))

import { resetKillSwitchForTests } from '@/sudarshan/risk/killSwitch'
import { back, pass, renderBook, settle, where } from '@/test/bookPages'
import { fakeClock, frames } from '@/test/frames'
import Risk, { serverOffset } from './Risk'

vi.setConfig({ testTimeout: 15_000 })

function status(killSwitch: object, stage = 'on') {
  return {
    stage,
    kill_switch: killSwitch,
    settings: null,
    gate: { orders: 0, p50_ms: null, p95_ms: null, max_ms: null, last_problem: null },
    upstox_kill_switch: null,
    broker: 'upstox',
    server_time: new Date().toISOString(),
  }
}

beforeEach(() => {
  fakeClock()
  frames.install()
  resetKillSwitchForTests()
  socket.handlers.clear()
  vi.clearAllMocks()
  api.audit.mockResolvedValue([])
  api.preview.mockResolvedValue({ counts: null, message: 'counted' })
})

describe('the Risk page', () => {
  it('does not read the status on a timer while an activation is running', async () => {
    api.status.mockResolvedValue(status({ known: true, on: true, running: true, since: null }))
    renderBook('/risk', <Risk />)
    await settle()
    expect(api.status).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(30_000)
    expect(api.status).toHaveBeenCalledTimes(1)
    // The server announcing that the report was saved is what reads it again.
    socket.handlers.get('sudarshan_risk')?.()
    await waitFor(() => expect(api.status).toHaveBeenCalledTimes(2))
  })

  it("closes the turn-on sheet with the phone's Back before it leaves the page", async () => {
    api.status.mockResolvedValue(status({ known: true, on: false }))
    renderBook('/risk', <Risk />)
    await settle()
    await pass(100)
    const tap = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    await tap.click(screen.getByRole('button', { name: 'Turn kill switch on' }))
    expect(screen.getByRole('dialog', { name: 'Turn kill switch on' })).toBeInTheDocument()
    expect(where()).toBe('/risk?act=killswitch')
    back()
    await waitFor(() => expect(where()).toBe('/risk'))
    expect(screen.queryByRole('dialog', { name: 'Turn kill switch on' })).toBeNull()
  })

  it("closes the resume sheet with the phone's Back before it leaves the page", async () => {
    api.status.mockResolvedValue(
      status({ known: true, on: true, since: new Date().toISOString(), can_resume_at: null })
    )
    renderBook('/risk', <Risk />)
    await settle()
    await pass(100)
    const tap = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    await tap.click(screen.getByRole('button', { name: 'Resume trading' }))
    expect(screen.getByRole('dialog', { name: 'Resume trading' })).toBeInTheDocument()
    expect(where()).toBe('/risk?act=resume')
    back()
    await waitFor(() => expect(where()).toBe('/risk'))
    expect(screen.queryByRole('dialog', { name: 'Resume trading' })).toBeNull()
  })

  it('says in History where each line was done, from the app or from Telegram', async () => {
    api.status.mockResolvedValue(status({ known: true, on: false }))
    api.audit.mockResolvedValue([
      {
        at: '2026-11-09T05:10:00.000Z',
        last_at: '2026-11-09T05:10:00.000Z',
        kind: 'ks_on',
        pipe: null,
        surface: 'telegram',
        count: 1,
        summary: 'Kill switch on.',
      },
      {
        at: '2026-11-09T05:20:00.000Z',
        last_at: '2026-11-09T05:20:00.000Z',
        kind: 'ks_off',
        pipe: null,
        surface: 'app',
        count: 1,
        summary: 'Resumed.',
      },
      {
        at: '2026-11-09T05:30:00.000Z',
        last_at: '2026-11-09T05:30:00.000Z',
        kind: 'refused',
        pipe: 'sandbox',
        surface: '/api/v1/placeorder',
        count: 1,
        summary: 'Refused.',
      },
    ])
    renderBook('/risk', <Risk />)
    await settle()
    await pass(100)
    expect(screen.getByText(/^9 Nov, 10:40:00 IST, from Telegram$/)).toBeInTheDocument()
    expect(screen.getByText(/^9 Nov, 10:50:00 IST, from the app$/)).toBeInTheDocument()
    // An order's route is not a surface the owner used.
    expect(screen.getByText(/^9 Nov, 11:00:00 IST$/)).toBeInTheDocument()
  })

  it('words the switch for the sandbox only when the guard covers nothing else', async () => {
    api.status.mockResolvedValue(status({ known: true, on: false }, 'sandbox'))
    renderBook('/risk', <Risk />)
    await settle()
    await pass(100)
    expect(
      screen.getByText(/Live orders are not checked and Upstox is not touched/)
    ).toBeInTheDocument()
  })
})

describe('while it is being turned on in the sandbox stage', () => {
  it('does not say Upstox is being touched', async () => {
    api.status.mockResolvedValue(status({ known: true, on: true, running: true }, 'sandbox'))
    renderBook('/risk', <Risk />)
    await settle()
    await pass(100)
    expect(
      screen.getByText("Being turned on: cancelling the sandbox's orders.")
    ).toBeInTheDocument()
  })
})

describe('the server clock', () => {
  it("is the difference between the server's time and when the status arrived", () => {
    expect(serverOffset('2026-11-04T10:00:30.000Z', Date.parse('2026-11-04T10:00:00.000Z'))).toBe(
      30_000
    )
    expect(serverOffset(undefined, 1000)).toBe(0)
    expect(serverOffset('not a time', 1000)).toBe(0)
  })
})
