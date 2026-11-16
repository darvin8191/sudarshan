/**
 * The TRINETRA page (M13.7, U2): its own screen while the view is switched off, not set up or not
 * answering; with the view on, the header line in section 3.6's words with the age, "Real-time"
 * only while this screen is in the owner's room and the readings are fresh, one card per (module,
 * index) with "Other" and the lapsed ones below, the alerts, the positions card, the timeline, "Why"
 * and the Performance tab; a push moves the page and an older one does not. Every answer is the real
 * bridge's (bridge.answers.json).
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const webClient = vi.hoisted(() => ({ get: vi.fn(), request: vi.fn() }))
vi.mock('@/api/client', () => ({ webClient }))
const room = vi.hoisted(() => ({
  socket: null as unknown,
  handlers: new Map<string, Set<(...args: unknown[]) => void>>(),
  joins: [] as Array<(error: unknown, ack?: { ok?: unknown }) => void>,
}))
vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({ playAlertSound: () => {}, socket: room.socket }),
}))
const getPositions = vi.hoisted(() => vi.fn())
vi.mock('@/api/trading', () => ({ tradingApi: { getPositions } }))
vi.mock('@/sudarshan/access', () => ({ usePriceAccess: () => ({ state: 'ready', apiKey: 'key' }) }))
const calendar = vi.hoisted(() => ({
  isLoading: false,
  error: null,
  isMarketOpen: () => false,
  isAnyMarketOpen: () => false,
}))
vi.mock('@/hooks/useMarketStatus', () => ({ useMarketStatus: () => calendar }))

import { resetSignalsLiveForTests } from '@/sudarshan/signals/live'
import { EVENT_UPDATE, JOIN_EVENT, WORDS } from '@/sudarshan/signals/types'
import {
  ANSWERS,
  answerFromRecording,
  findRecorded,
  LATER_MS,
  NOW_MS,
  type Recorded,
  stateOf,
} from '@/test/signalsAnswers'
import Trinetra from './Trinetra'

/** The app's connection: connected, joins answered by the test. */
function connect() {
  room.handlers = new Map()
  room.joins = []
  room.socket = {
    connected: true,
    on(event: string, handler: (...args: unknown[]) => void) {
      room.handlers.set(event, (room.handlers.get(event) ?? new Set()).add(handler))
    },
    off(event: string, handler: (...args: unknown[]) => void) {
      room.handlers.get(event)?.delete(handler)
    },
    emit: vi.fn(),
    timeout: () => ({
      emit: (event: string, ack: (error: unknown, answer?: { ok?: unknown }) => void) => {
        if (event === JOIN_EVENT) room.joins.push(ack)
      },
    }),
  }
}

function push(data: unknown) {
  for (const handler of [...(room.handlers.get(EVENT_UPDATE) ?? [])]) handler(data)
}

/** The bridge answering from the recording: `state` as named, the rest by route. */
function bridge(stateName: string, more: Partial<Record<string, Recorded>> = {}) {
  answerFromRecording(webClient, (method, path, query) => {
    if (path.endsWith('/state')) return ANSWERS[stateName]
    if (path.endsWith('/timeline')) return more.timeline ?? ANSWERS.timeline
    if (path.endsWith('/performance')) return more.performance ?? ANSWERS.performance_scored
    if (path.endsWith('/settings'))
      return method === 'GET' ? ANSWERS.settings : ANSWERS.settings_saved
    return findRecorded(method, path, query)
  })
}

let where = ''
function Where() {
  where = `${useLocation().pathname}${useLocation().search}`
  return null
}

function show(path = '/trinetra') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Trinetra />
        <Where />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  resetSignalsLiveForTests()
  webClient.get.mockReset()
  webClient.request.mockReset()
  getPositions.mockReset()
  getPositions.mockResolvedValue({ status: 'success', data: [] })
  room.socket = null
})

afterEach(() => {
  vi.useRealTimers()
  resetSignalsLiveForTests()
})

describe('the TRINETRA page, switched off or not answering', () => {
  it('while reading: says so', () => {
    webClient.get.mockReturnValue(new Promise(() => {}))
    show()
    expect(screen.getByRole('heading', { level: 1, name: 'TRINETRA' })).toBeInTheDocument()
    expect(screen.getByTestId('trinetra-notice')).toHaveTextContent("Reading TRINETRA's state")
  })

  it("switched off: its own screen, in the service's sentence, with what turns it on", async () => {
    bridge('off')
    show()
    const title = await screen.findByText('The TRINETRA view is switched off.')
    const notice = title.closest('[data-testid="trinetra-notice"]') as HTMLElement
    expect(notice).toHaveTextContent("the README's TRINETRA section has the steps")
    expect(notice.querySelector('.lucide-power')).not.toBeNull()
    expect(screen.queryByRole('tablist')).toBeNull()
  })

  it('not set up: the sentence that says what to run', async () => {
    bridge('not_set_up')
    show()
    const title = await screen.findByText(WORDS.not_set_up)
    expect(
      title.closest('[data-testid="trinetra-notice"]')?.querySelector('.lucide-unplug')
    ).not.toBeNull()
  })

  it('not answering: the sentence, and Try again reads again', { timeout: 15_000 }, async () => {
    bridge('not_answering')
    show()
    // Read twice more first (a reader that may be back), then said.
    const title = await screen.findByText(WORDS.not_answering, {}, { timeout: 8_000 })
    const notice = title.closest('[data-testid="trinetra-notice"]') as HTMLElement
    const reads = webClient.get.mock.calls.length
    bridge('state')
    await userEvent.click(within(notice).getByRole('button', { name: 'Try again' }))
    expect(await screen.findByTestId('trinetra-freshness')).toBeInTheDocument()
    expect(webClient.get.mock.calls.length).toBeGreaterThan(reads)
  })
})

describe('the TRINETRA page, on', () => {
  it('at 09:05: the header with its age and Real-time once in the room, the cards in order', async () => {
    vi.useFakeTimers({ now: NOW_MS, toFake: ['Date'] })
    connect()
    bridge('state')
    show()
    const header = await screen.findByTestId('trinetra-freshness')
    expect(header).toHaveTextContent('TRINETRA · updated 09:04:40 · under a minute old')
    expect(header.querySelector('[data-updates]')).toBeNull() // not in the room yet
    await waitFor(() => expect(room.joins).toHaveLength(1))
    act(() => room.joins[0](null, { ok: true }))
    expect(
      screen.getByTestId('trinetra-freshness').querySelector('[data-updates]')
    ).toHaveTextContent('Real-time')

    const cards = within(screen.getByTestId('trinetra-cards')).getAllByRole('article')
    expect(cards.map((card) => card.getAttribute('aria-label'))).toEqual([
      'DRISHTI, NIFTY',
      'DRISHTI, BANKNIFTY',
      'PRABHAT, NIFTY',
      'PRABHAT, BANKNIFTY',
      'SANDHI end-of-day, NIFTY',
      'SANDHI intraday, BANKNIFTY',
    ])
    const other = screen.getByRole('region', { name: 'Other' })
    expect(within(other).getByRole('article', { name: 'Other, NIFTY' })).toHaveTextContent(
      'Breadth weak'
    )
    expect(screen.queryByRole('region', { name: 'Lapsed readings' })).toBeNull()
    const alerts = screen
      .getByRole('heading', { name: "TRINETRA's alerts" })
      .closest('section') as HTMLElement
    expect(alerts).toHaveTextContent('Watch')
    expect(alerts).toHaveTextContent('NIFTY regime turned to FII distribution')
    expect(screen.getByTestId('trinetra-positions')).toBeInTheDocument()
    expect(await screen.findByRole('list', { name: 'Changes, newest first' })).toBeInTheDocument()
    // Nothing of TRINETRA's own: no state names, run, sequence or codes.
    const text = document.body.textContent ?? ''
    for (const inner of ['DISTRIBUTION', 'NEUTRAL_RANGE', 'invented-run', 'doc_seq', 'gap_bias']) {
      expect(text).not.toContain(inner)
    }
    expect(text).not.toMatch(/\bLive\b/)
  })

  it('at 09:20: stale in words, no Real-time though in the room, the lapsed gap bias below', async () => {
    vi.useFakeTimers({ now: LATER_MS, toFake: ['Date'] })
    connect()
    bridge('state_stale')
    show()
    const header = await screen.findByTestId('trinetra-freshness')
    expect(header).toHaveTextContent(
      'TRINETRA has not published since 09:04. This is its last reading, 15 minutes old.'
    )
    await waitFor(() => expect(room.joins).toHaveLength(1))
    act(() => room.joins[0](null, { ok: true }))
    expect(screen.getByTestId('trinetra-freshness').querySelector('[data-updates]')).toBeNull()
    const lapsed = screen.getByRole('region', { name: 'Lapsed readings' })
    const gap = within(lapsed).getByRole('article', { name: 'PRABHAT, NIFTY' })
    expect(gap).toHaveTextContent('Lapsed 09:15')
  })

  it('the room refused or the connection down: reconnecting, said under the header', async () => {
    vi.useFakeTimers({ now: NOW_MS, toFake: ['Date'] })
    connect()
    bridge('state')
    show()
    await waitFor(() => expect(room.joins).toHaveLength(1))
    act(() => room.joins[0](null, { ok: false, message: WORDS.forbidden }))
    expect(screen.getByText(/Reconnecting: a new reading shows here/)).toBeInTheDocument()
  })

  it('a push moves the page; an older push does not', async () => {
    vi.useFakeTimers({ now: NOW_MS, toFake: ['Date'] })
    connect()
    bridge('state')
    show()
    await screen.findByTestId('trinetra-cards')
    const newer = stateOf()
    newer.now_ms += 1_000
    newer.readings[0].label = 'FII distribution, deepening'
    act(() => push({ cause: 'document', state: newer }))
    await waitFor(() =>
      expect(screen.getByRole('article', { name: 'DRISHTI, NIFTY' })).toHaveTextContent(
        'FII distribution, deepening'
      )
    )
    const older = stateOf()
    older.now_ms -= 60_000
    older.readings[0].label = 'An older label'
    await act(async () => {
      push({ cause: 'document', state: older })
      await new Promise((resolve) => setTimeout(resolve, 20))
    })
    expect(screen.getByRole('article', { name: 'DRISHTI, NIFTY' })).toHaveTextContent(
      'FII distribution, deepening'
    )
  })

  it('Why opens from a card, in the saved language', async () => {
    vi.useFakeTimers({ now: NOW_MS, toFake: ['Date'] })
    bridge('state')
    show()
    await screen.findByTestId('trinetra-cards')
    await userEvent.click(screen.getByRole('button', { name: 'Why: DRISHTI, NIFTY' }))
    const sheet = await screen.findByRole('dialog', { name: 'DRISHTI · NIFTY' })
    await waitFor(() =>
      expect(within(sheet).getByRole('radio', { name: 'Hinglish' })).toBeChecked()
    )
    expect(within(sheet).getByTestId('why-text')).toHaveTextContent(
      'teen sessions se index futures'
    )
  })

  it('the Performance tab, kept in the address', async () => {
    vi.useFakeTimers({ now: NOW_MS, toFake: ['Date'] })
    bridge('state')
    show()
    await screen.findByTestId('trinetra-cards')
    await userEvent.click(screen.getByRole('tab', { name: 'Performance' }))
    expect(where).toBe('/trinetra?view=performance')
    expect(await screen.findByRole('region', { name: 'DRISHTI' })).toHaveTextContent(
      '32 cases · 56.25% moved the way it said'
    )
    expect(screen.queryByTestId('trinetra-cards')).toBeNull()
    await userEvent.click(screen.getByRole('tab', { name: 'Readings' }))
    expect(where).toBe('/trinetra')
  })
})
