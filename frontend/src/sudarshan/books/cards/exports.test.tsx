/**
 * Export on the phone books and trades grouped by order (B3: M6.1-M6.4). Export asks the app's
 * own export route for the book's CSV and saves it, and never follows the route's redirect to
 * the logout page (its answer to a failed broker read): anything but a CSV is said in words and
 * leaves the session alone. Holdings have no route, so their file is made from the cards. The
 * trade book groups fills by order: count, quantity, average weighted by quantity, each fill.
 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Holding, Trade } from '@/types/trading'

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('@/utils/toast', () => ({ showToast: toast }))

import { holdingsCsv } from './HoldingCards'
import { ExportIcon, exportBook, saveCsv } from './parts'
import { groupTrades } from './TradeGroups'
import type { PricedRow } from './useFramePrices'

// jsdom has no object URLs, so the download path gets stand-ins.
const urlStatics = URL as unknown as Record<string, unknown>
const made: Blob[] = []
let clicked: HTMLAnchorElement[] = []

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
  vi.setSystemTime(new Date('2026-11-14T19:00:00Z')) // 00:30 on the 15th in India
  made.length = 0
  clicked = []
  urlStatics.createObjectURL = vi.fn((blob: Blob) => {
    made.push(blob)
    return 'blob:csv'
  })
  urlStatics.revokeObjectURL = vi.fn()
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
    this: HTMLAnchorElement
  ) {
    clicked.push(this)
  })
  toast.success.mockReset()
  toast.error.mockReset()
})

afterEach(() => {
  delete urlStatics.createObjectURL
  delete urlStatics.revokeObjectURL
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

/** A fetch answer, as much of one as exportBook reads. */
function answer(status: number, type: string | null, body = '', kind = 'basic') {
  return {
    ok: status >= 200 && status < 300,
    status,
    type: kind,
    headers: { get: (name: string) => (name.toLowerCase() === 'content-type' ? type : null) },
    text: async () => body,
  }
}

describe('saving a CSV', () => {
  it('names the file by the day in India and keeps its address until the download has begun', () => {
    expect(saveCsv('a,b\n1,2', 'orderbook')).toBe('orderbook_2026-11-15.csv')
    expect(made[0].type).toBe('text/csv;charset=utf-8')
    expect(clicked[0].download).toBe('orderbook_2026-11-15.csv')
    expect(clicked[0].href).toBe('blob:csv')
    vi.advanceTimersByTime(59_000)
    expect(URL.revokeObjectURL).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1_000)
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:csv')
  })
})

describe('a book from its export route', () => {
  it('asks the route without following a redirect, and saves the CSV it answers', async () => {
    const fetch = vi.fn(async () => answer(200, 'text/csv; charset=utf-8', 'Symbol\nSBIN'))
    vi.stubGlobal('fetch', fetch)
    expect(await exportBook('/orderbook/export', 'orderbook')).toBe('orderbook_2026-11-15.csv')
    expect(fetch).toHaveBeenCalledWith(
      '/orderbook/export',
      expect.objectContaining({ redirect: 'manual', credentials: 'same-origin' })
    )
    expect(made).toHaveLength(1)
  })

  it('saves nothing for the logout redirect, a page that is not a CSV, an error or no answer', async () => {
    const answers = [
      answer(0, null, '', 'opaqueredirect'),
      answer(200, 'text/html; charset=utf-8', '<html>Login</html>'),
      answer(500, 'text/html', 'Error exporting orderbook'),
    ]
    for (const each of answers) {
      vi.stubGlobal(
        'fetch',
        vi.fn(async () => each)
      )
      expect(await exportBook('/tradebook/export', 'tradebook')).toBeNull()
    }
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => Promise.reject(new TypeError('Failed to fetch')))
    )
    expect(await exportBook('/positions/export', 'positions')).toBeNull()
    expect(made).toHaveLength(0)
  })

  it('says what was saved, or in words that it could not be', async () => {
    vi.useRealTimers()
    const tap = userEvent.setup()
    const run = vi.fn<() => string | null>(() => 'positions_2026-11-15.csv')
    render(<ExportIcon what="positions" run={run} />)
    await tap.click(screen.getByRole('button', { name: 'Export positions' }))
    expect(toast.success).toHaveBeenCalledWith('Downloaded positions_2026-11-15.csv', 'clipboard')
    run.mockReturnValue(null)
    await tap.click(screen.getByRole('button', { name: 'Export positions' }))
    expect(toast.error).toHaveBeenCalledWith(
      'The positions could not be exported just now. Try again.',
      'system'
    )
    // One that broke making the file says the same, and can be tapped again.
    run.mockImplementation(() => {
      throw new Error('no object URLs here')
    })
    await tap.click(screen.getByRole('button', { name: 'Export positions' }))
    expect(toast.error).toHaveBeenCalledTimes(2)
    expect(screen.getByRole('button', { name: 'Export positions' })).toBeEnabled()
  })
})

describe('holdings as a CSV', () => {
  it('takes the wide page’s columns at the prices shown, figures plain, text made safe', () => {
    const row = (item: Partial<Holding>, ltp: number | null, pnl: number | null) =>
      ({
        item: item as Holding,
        ltp,
        pnl,
        pnlPercent: pnl === null ? null : 5.6789,
      }) as PricedRow<Holding>
    const text = holdingsCsv([
      row(
        {
          symbol: 'RELIANCE',
          exchange: 'NSE',
          product: 'CNC',
          quantity: 40,
          average_price: 1374.3,
        },
        1452.35,
        3122.0001
      ),
      row(
        { symbol: '=HYPERLINK', exchange: 'NSE', product: 'CNC', quantity: 1, average_price: 10 },
        null,
        null
      ),
    ])
    expect(text.split('\n')).toEqual([
      'Symbol,Exchange,Quantity,Avg Price,LTP,Product,P&L,P&L %',
      'RELIANCE,NSE,40,1374.3,1452.35,CNC,3122,5.68',
      "'=HYPERLINK,NSE,1,10,,CNC,,",
    ])
  })
})

describe('trades grouped by order', () => {
  const fill = (orderid: string, quantity: number, price: number, time: string): Trade => ({
    symbol: 'NIFTY27OCT2625900CE',
    exchange: 'NFO',
    action: 'BUY',
    quantity,
    average_price: price,
    trade_value: quantity * price,
    product: 'MIS',
    orderid,
    timestamp: `2026-10-20 ${time}`,
  })

  it('totals each order’s fills, averages them by quantity, and puts the latest order first', () => {
    const groups = groupTrades([
      fill('7', 65, 140, '10:00:00'),
      fill('9', 30, 150, '10:41:50'),
      fill('9', 65, 142, '10:41:49'),
      fill('9', 35, 143, '10:41:51'),
    ])
    expect(groups.map((group) => group.key)).toEqual(['order:9', 'order:7'])
    const [several, one] = groups
    expect(several.fills.map((each) => each.timestamp.slice(11))).toEqual([
      '10:41:49',
      '10:41:50',
      '10:41:51',
    ])
    expect(several.quantity).toBe(130)
    expect(several.average).toBeCloseTo((65 * 142 + 30 * 150 + 35 * 143) / 130, 6)
    expect(several.value).toBe(65 * 142 + 30 * 150 + 35 * 143)
    expect(one.fills).toHaveLength(1)
    expect(one.average).toBe(140)
  })

  it('keeps a fill with no order id on its own, and says nothing it cannot add up', () => {
    const odd = { ...fill('', 10, 100, '09:30:00'), quantity: null as unknown as number }
    const groups = groupTrades([odd, fill('', 5, 101, '09:31:00')])
    expect(groups).toHaveLength(2)
    expect(groups[1].quantity).toBeNull()
    expect(groups[1].average).toBeNull()
  })
})
