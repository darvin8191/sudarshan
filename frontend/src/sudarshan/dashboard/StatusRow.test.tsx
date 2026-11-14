/**
 * The dashboard's status row (design 3.3, D2): the server's day facts read once and again only on
 * an event (a new day, a return after 15 s away, Try again), the chips moved at their next edge by
 * one timeout, the master contract shown only while it is not ready, and the hours sheet.
 */
import { act } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { at, serverFacts } from '@/test/dayFacts'
import { render, screen, userEvent, waitFor, within } from '@/test/test-utils'
import { type MasterContractState, StatusRow } from './StatusRow'

const READY: MasterContractState = { status: 'success' }

/** Answers the day facts with `answers` in turn (the last one again), counting each read. */
function serve(...answers: Array<Record<string, unknown> | 'fail'>) {
  const reads: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      reads.push(url)
      const answer = answers[Math.min(reads.length - 1, answers.length - 1)]
      if (answer === 'fail') return { ok: false, status: 503, json: async () => ({}) }
      return { ok: true, status: 200, json: async () => ({ status: 'success', data: answer }) }
    })
  )
  return reads
}

function setVisibility(state: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state })
  document.dispatchEvent(new Event('visibilitychange'))
}

async function pass(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

beforeEach(() => {
  setVisibility('visible')
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  setVisibility('visible')
})

describe('the status row', () => {
  it("shows the market and expiry chips; a tap opens every exchange's hours and the expiries", async () => {
    vi.useFakeTimers({ now: at('2026-11-17', '11:00'), toFake: ['Date'] })
    serve(serverFacts('2026-11-17'))
    const user = userEvent.setup()
    render(<StatusRow day="2026-11-17" contract={READY} />)

    const market = await screen.findByRole('button', { name: /Open · closes 15:30/ })
    expect(screen.getByRole('link', { name: /^Expiry today: NIFTY · stops 15:40/ })).toBeVisible()
    expect(screen.queryByText(/master contract/i)).toBeNull()
    // O9: the expiry chip opens that underlying's option chain on that expiry.
    const chip = screen.getByRole('link', { name: /^Expiry today: NIFTY/ })
    expect(chip).toHaveAttribute('href', '/options/NFO/NIFTY?expiry=2026-11-17')
    expect(chip).toHaveAccessibleName(/Open the NIFTY option chain/)

    await user.click(market)
    const sheet = await screen.findByRole('dialog', { name: 'Market hours' })
    expect(sheet).toHaveTextContent('Tue 17 Nov')
    const nse = within(sheet).getByText('NSE cash').parentElement
    expect(nse).toHaveTextContent('09:15-15:30 · pre-open from 09:00')
    expect(within(sheet).getByText('NSE F&O').parentElement).toHaveTextContent('09:15-15:40')
    expect(sheet).toHaveTextContent('Today: NIFTY · contracts stop at 15:40')
    expect(sheet).toHaveTextContent('Next open: Wed 09:15')
  })

  it("names a holiday and its occasion, with MCX's evening hours and the other exchanges closed", async () => {
    vi.useFakeTimers({ now: at('2026-11-24', '11:00'), toFake: ['Date'] })
    serve(serverFacts('2026-11-24'))
    const user = userEvent.setup()
    render(<StatusRow day="2026-11-24" contract={READY} />)

    await user.click(await screen.findByRole('button', { name: /Holiday · opens Wed 09:15/ }))
    const sheet = await screen.findByRole('dialog', { name: 'Market hours' })
    expect(sheet).toHaveTextContent('Holiday · Prakash Gurpurb Sri Guru Nanak Dev')
    expect(within(sheet).getByText('MCX').parentElement).toHaveTextContent('17:00-23:55')
    expect(within(sheet).getByText('NSE cash').parentElement).toHaveTextContent('Closed today')
    expect(sheet).toHaveTextContent('None today or on the next trading day.')
  })

  it('says the hours were not read and reads them again on Try again', async () => {
    const reads = serve('fail', serverFacts('2026-11-16'))
    const user = userEvent.setup()
    render(<StatusRow day="2026-11-16" contract={READY} />)

    expect(await screen.findByText('Market hours not read')).toBeVisible()
    await user.click(screen.getByRole('button', { name: /Try again/ }))
    await waitFor(() => expect(reads).toHaveLength(2))
    expect(screen.queryByText('Market hours not read')).toBeNull()
    expect(screen.queryByRole('button', { name: /Try again/ })).toBeNull()
  })

  it('shows the master contract only while it is not ready, each state in words', async () => {
    serve(serverFacts('2026-11-16'))
    const { rerender } = render(<StatusRow day="2026-11-16" contract={{ status: 'downloading' }} />)
    expect(screen.getByRole('link', { name: 'Downloading master contract' })).toHaveAttribute(
      'href',
      '/master-contract'
    )
    rerender(<StatusRow day="2026-11-16" contract={{ status: 'error', message: 'timed out' }} />)
    expect(screen.getByRole('link', { name: 'Master contract failed' })).toBeVisible()
    rerender(<StatusRow day="2026-11-16" contract={{ status: 'pending' }} />)
    expect(screen.queryByRole('link')).toBeNull()
    rerender(<StatusRow day="2026-11-16" contract={READY} />)
    expect(screen.queryByRole('link')).toBeNull()
  })

  it('moves the chips at their next edge with one timeout, and reads nothing on a timer', async () => {
    vi.useFakeTimers({ now: at('2026-11-16', '15:29') })
    const reads = serve(serverFacts('2026-11-16'))
    render(<StatusRow day="2026-11-16" contract={READY} />)
    await pass(0)
    expect(screen.getByRole('button', { name: /Open · closes 15:30/ })).toBeVisible()
    expect(screen.queryByRole('link', { name: /Expiry/ })).toBeNull()

    await pass(61_000) // 15:30: the cash close, and tomorrow's expiry from it
    expect(screen.getByRole('button', { name: /Cash closed · F&O to 15:40/ })).toBeVisible()
    expect(screen.getByRole('link', { name: /^Expiry tomorrow: NIFTY/ })).toBeVisible()
    await pass(10 * 60_000)
    expect(screen.getByRole('button', { name: /Closed · opens Tue 09:15/ })).toBeVisible()
    await pass(60 * 60_000)
    expect(reads).toHaveLength(1)
  })

  it('reads the facts again for a new day and when the page comes back after 15 s away', async () => {
    vi.useFakeTimers({ now: at('2026-11-16', '22:00') })
    const reads = serve(serverFacts('2026-11-16'), serverFacts('2026-11-17'))
    const { rerender } = render(<StatusRow day="2026-11-16" contract={READY} />)
    await pass(0)
    expect(reads).toHaveLength(1)

    setVisibility('hidden')
    await pass(10_000)
    setVisibility('visible')
    await pass(0)
    expect(reads).toHaveLength(1) // away 10 s: nothing read

    setVisibility('hidden')
    await pass(20_000)
    setVisibility('visible')
    await pass(0)
    expect(reads).toHaveLength(2)

    await pass(3 * 3_600_000) // past midnight in India: the page hands the row the new day
    rerender(<StatusRow day="2026-11-17" contract={READY} />)
    await pass(0)
    expect(reads).toHaveLength(3)
    expect(screen.getByRole('link', { name: /^Expiry today: NIFTY · stops 15:40/ })).toBeVisible()
  })
})
