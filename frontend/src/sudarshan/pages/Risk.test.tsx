/**
 * The Risk page (M20.7): its kill switch sheets live in the address so the phone's Back closes
 * one before it leaves the page, and nothing polls (rule 7): the status is read once and again
 * when the server announces a change.
 */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  status: vi.fn(),
  audit: vi.fn(),
  preview: vi.fn(),
  turnOn: vi.fn(),
  resume: vi.fn(),
  saveSettings: vi.fn(),
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

const SETTINGS = {
  daily_loss_limit: 4000,
  band_on: false,
  band_opt_pct: 20,
  band_opt_min_rs: 2,
  band_fut_pct: 1,
  band_cash_pct: 5,
  band_both_sides: false,
  size_on: false,
  max_lots_fo: 2,
  max_qty_cash: 1000,
  burst_on: false,
  burst_n: 10,
  burst_window_s: 60,
  revoke_mcp: true,
  phrase_set: true,
  updated_at: '2026-11-14T04:00:00+00:00',
  updated_by: 'owner',
}

describe('a wrong confirm phrase for the daily limit', () => {
  it("is in History at once, as the kill switch's is, and the box is emptied", async () => {
    // Backlog 2b (Analyzer session): the server wrote the line, but the page read History again
    // only after a save went through, so the owner never saw it.
    const refused = 'A wrong confirm phrase was refused when asked to change the daily loss limit.'
    api.status.mockResolvedValue({ ...status({ known: true, on: false }), settings: SETTINGS })
    api.saveSettings
      .mockResolvedValueOnce({
        code: 400,
        ok: false,
        message: 'Loosening a limit needs your confirm phrase. Nothing was saved.',
        settings: SETTINGS,
        needsPhrase: true,
      })
      .mockResolvedValueOnce({
        code: 400,
        ok: false,
        message: 'The confirm phrase does not match. Nothing was changed.',
        settings: SETTINGS,
        needsPhrase: true,
      })
    renderBook('/risk', <Risk />)
    await settle()
    await pass(100)
    expect(screen.getByText(/^Nothing yet\./)).toBeInTheDocument()

    const card = within(screen.getByRole('region', { name: 'Daily loss limit' }))
    fireEvent.change(card.getByLabelText('Limit for the day'), { target: { value: '9000' } })
    fireEvent.click(card.getByRole('button', { name: 'Save the daily limit' }))
    const box = await card.findByLabelText('Confirm phrase')
    api.audit.mockResolvedValue([
      {
        at: new Date().toISOString(),
        last_at: new Date().toISOString(),
        kind: 'phrase_refused',
        pipe: null,
        surface: 'app',
        count: 1,
        summary: refused,
      },
    ])
    fireEvent.change(box, { target: { value: 'hunter2' } })
    fireEvent.click(card.getByRole('button', { name: 'Confirm with your phrase' }))

    expect(await screen.findByText(refused)).toBeInTheDocument()
    expect(api.saveSettings).toHaveBeenLastCalledWith({ daily_loss_limit: 9000 }, 'hunter2')
    expect(card.getByLabelText('Confirm phrase')).toHaveValue('')
    expect(card.getByText('The confirm phrase does not match. Nothing was changed.')).toBeVisible()
  })
})

describe('a tab in the background', () => {
  // Backlog 2e (Analyzer session): a background tab's screen did not move until it was focused
  // (a hidden tab runs no animation frame). Nothing here runs one after the tab is hidden.
  function sandboxDay(lock: object | null = null) {
    return {
      figure: -4100,
      approximate: false,
      read_at: new Date().toISOString(),
      in_session: true,
      opens_at: null,
      reached: lock !== null,
      lock,
      unchecked: false,
    }
  }
  const OFF = { ...status({ known: true, on: false }), daily: { sandbox: sandboxDay() } }
  const ON_AND_LOCKED = {
    ...status({ known: true, on: true, since: new Date().toISOString(), surface: 'telegram' }),
    daily: {
      sandbox: sandboxDay({ at: new Date().toISOString(), figure: -4100, limit: 4000 }),
    },
  }
  const killSwitch = () => within(screen.getByRole('region', { name: 'Kill switch' }))
  const locked = () =>
    screen.queryByText(/^Locked at \d\d:\d\d: new orders refused until tomorrow$/)

  function setVisibility(state: 'visible' | 'hidden') {
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state })
    // As a browser fires it: at the document, bubbling up to the window.
    act(() => {
      document.dispatchEvent(new Event('visibilitychange', { bubbles: true }))
    })
  }

  /** Let a read land and React draw it, with no animation frame run. */
  async function landWithoutAFrame() {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50)
    })
  }

  async function openThenHide() {
    api.status.mockResolvedValue(OFF)
    renderBook('/risk', <Risk />)
    await settle()
    await pass(100)
    expect(killSwitch().getByRole('button', { name: 'Turn kill switch on' })).toBeInTheDocument()
    expect(locked()).toBeNull()
    setVisibility('hidden')
  }

  afterEach(() => {
    Reflect.deleteProperty(document, 'visibilityState')
  })

  it('draws a change announced while it is hidden, without waiting for a frame', async () => {
    await openThenHide()
    api.status.mockResolvedValue(ON_AND_LOCKED)
    act(() => socket.handlers.get('sudarshan_risk')?.())
    await landWithoutAFrame()
    expect(killSwitch().getByRole('button', { name: 'Resume trading' })).toBeInTheDocument()
    expect(locked()).toBeInTheDocument()
  })

  it('reads again the moment it is seen, so a change it missed while hidden shows at once', async () => {
    await openThenHide()
    // Turned on from Telegram while the phone slept: the announcement reached nobody.
    api.status.mockResolvedValue(ON_AND_LOCKED)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000) // well inside the app's one-minute freshness
    })
    expect(api.status).toHaveBeenCalledTimes(1)
    setVisibility('visible')
    await landWithoutAFrame()
    expect(api.status).toHaveBeenCalledTimes(2)
    expect(killSwitch().getByRole('button', { name: 'Resume trading' })).toBeInTheDocument()
    expect(locked()).toBeInTheDocument()
  })

  it('reads again when the live connection comes back after a drop', async () => {
    api.status.mockResolvedValue(OFF)
    renderBook('/risk', <Risk />)
    await settle()
    await pass(100)
    api.status.mockResolvedValue(ON_AND_LOCKED)
    act(() => socket.handlers.get('connect')?.())
    await landWithoutAFrame()
    expect(killSwitch().getByRole('button', { name: 'Resume trading' })).toBeInTheDocument()
    expect(locked()).toBeInTheDocument()
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
