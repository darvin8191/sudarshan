/**
 * Phase P7's api functions (F0): each sends its op the way the contract's table says, the switches
 * are read from the service's own answer (anything it does not say is off), and a downloaded file
 * is handed to the phone as CSV text or as a PDF made from its base64.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const client = vi.hoisted(() => ({ get: vi.fn(), request: vi.fn() }))
vi.mock('@/api/client', () => ({ webClient: client }))

import {
  createGoal,
  deleteTaxInput,
  getCalendar,
  getRisk,
  getTax,
  getTaxCsv,
  listGoals,
  saveDownload,
  saveTaxInput,
  switchesOf,
} from './api'
import type { PortfolioState } from './types'

const PREFIX = '/sudarshan/portfolio/api'
const OK = { data: { status: 'success', enabled: true, data: { ok: 1 } } }

beforeEach(() => {
  client.get.mockReset().mockResolvedValue(OK)
  client.request.mockReset().mockResolvedValue(OK)
})

describe('the switches', () => {
  it('are off unless the service says on', () => {
    expect(switchesOf(undefined)).toEqual({ tax: false, insights: false, goals: false })
    const state = { switches: { tax: true } } as unknown as PortfolioState
    expect(switchesOf(state)).toEqual({ tax: true, insights: false, goals: false })
    const odd = { switches: { tax: 'yes', goals: 1 } } as unknown as PortfolioState
    expect(switchesOf(odd)).toEqual({ tax: false, insights: false, goals: false })
  })
})

describe('the ops of the phase', () => {
  it('reads send their query from the table', async () => {
    await getTax({ fy: 2026, member: 3 })
    expect(client.get.mock.calls[0][0]).toBe(`${PREFIX}/tax`)
    expect(client.get.mock.calls[0][1].params).toEqual({ fy: '2026', member: '3' })
    await getCalendar({ from: 20261101, to: 20270430 })
    expect(client.get.mock.calls[1][0]).toBe(`${PREFIX}/calendar`)
    expect(client.get.mock.calls[1][1].params).toEqual({ from: '20261101', to: '20270430' })
    await getRisk()
    expect(client.get.mock.calls[2][0]).toBe(`${PREFIX}/risk`)
    expect(client.get.mock.calls[2][1].params).toEqual({})
    await listGoals()
    expect(client.get.mock.calls[3][0]).toBe(`${PREFIX}/goals`)
    await getTaxCsv({ fy: 2025, schedule: 'capital_gains' })
    expect(client.get.mock.calls[4][1].params).toEqual({ fy: '2025', schedule: 'capital_gains' })
  })

  it('writes send their body, a dry run included, and the route names its target', async () => {
    await saveTaxInput({
      kind: 'fmv',
      row: { isin: 'INE000A01011', fmv_u: 3_500_000 },
      dry_run: true,
    })
    expect(client.request).toHaveBeenLastCalledWith(
      expect.objectContaining({
        url: `${PREFIX}/tax/inputs`,
        method: 'POST',
        data: { kind: 'fmv', row: { isin: 'INE000A01011', fmv_u: 3_500_000 }, dry_run: true },
      })
    )
    await deleteTaxInput({ id: 7, kind: 'carry', version: 2 })
    expect(client.request).toHaveBeenLastCalledWith(
      expect.objectContaining({
        url: `${PREFIX}/tax/inputs/7`,
        method: 'DELETE',
        data: { kind: 'carry', version: 2 },
      })
    )
    await createGoal({ request_id: 'r-1', name: 'House', target_paise: 1, target_day: 20310331 })
    expect(client.request).toHaveBeenLastCalledWith(
      expect.objectContaining({ url: `${PREFIX}/goals`, method: 'POST' })
    )
  })

  it('an op not built yet comes back as the contract says, in words', async () => {
    client.get.mockRejectedValueOnce(
      Object.assign(new Error('x'), {
        isAxiosError: true,
        response: {
          status: 501,
          data: {
            status: 'error',
            error: { code: 'not_ready', message: 'This part of the portfolio is not ready yet.' },
          },
        },
      })
    )
    await expect(getTax()).rejects.toMatchObject({ code: 'not_ready' })
  })
})

/** A blob's text (jsdom's Blob has no text()). */
function read(blob: Blob): Promise<string> {
  return new Promise((resolve) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.readAsText(blob)
  })
}

describe('saving a downloaded file', () => {
  const made: Blob[] = []
  beforeEach(() => {
    made.length = 0
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL: (blob: Blob) => {
        made.push(blob)
        return 'blob:file'
      },
      revokeObjectURL: () => {},
    })
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('CSV goes as UTF-8 text under its ASCII name', async () => {
    const clicked = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    saveDownload({ filename: 'SUDARSHAN_Tax_2026.csv', text: 'a,b\n1,2\n' })
    expect(made[0].type).toBe('text/csv;charset=utf-8')
    expect(await read(made[0])).toBe('a,b\n1,2\n')
    expect(clicked).toHaveBeenCalledTimes(1)
    clicked.mockRestore()
  })

  it('a PDF is made from its base64', async () => {
    const clicked = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    saveDownload({ filename: 'x.pdf', base64: btoa('%PDF-1.4') })
    expect(made[0].type).toBe('application/pdf')
    expect(await read(made[0])).toBe('%PDF-1.4')
    clicked.mockRestore()
  })
})
