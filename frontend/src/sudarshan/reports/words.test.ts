import { describe, expect, it } from 'vitest'
import type { Report } from '../journal/types'
import {
  ALERTS_OFF,
  checkWords,
  deliveryOf,
  downloadName,
  emptyWords,
  hasCsv,
  periodWords,
  SWITCHES,
  timeOf,
} from './words'

const report = (over: Partial<Report> = {}): Report => ({
  id: 1,
  kind: 'eod',
  period_key: '20261109',
  built_ms: 1_762_683_900_000,
  status: 'sent',
  text: 'x',
  file_name: 'x.pdf',
  file_mime: 'application/pdf',
  file_sha256: null,
  reconciled_at: null,
  version: 1,
  ...over,
})

describe('delivery in words', () => {
  it('says Sent, Text only and Not sent with the reason', () => {
    expect(deliveryOf(report(), true)).toMatchObject({ tone: 'sent', word: 'Sent' })
    expect(deliveryOf(report({ status: 'text_only' }), true)).toMatchObject({
      tone: 'text',
      word: 'Text only',
    })
    expect(
      deliveryOf({ ...report({ status: 'not_sent' }), reason: 'Telegram did not answer' }, true)
        .word
    ).toBe('Not sent: Telegram did not answer')
  })

  it('says the alerts service is off when the settings say so (Y38)', () => {
    expect(deliveryOf(report({ status: 'not_sent' }), false).word).toBe(`Not sent: ${ALERTS_OFF}`)
    expect(deliveryOf(report({ status: 'ready' }), false).word).toBe(
      'Not sent: the alerts service is switched off on the server'
    )
  })
})

describe('names and periods', () => {
  it('names the download in ASCII from the kind and the period', () => {
    expect(downloadName(report())).toBe('SUDARSHAN_Day_report_20261109.pdf')
    expect(downloadName(report({ kind: 'fy', period_key: 'FY2627' }))).toBe(
      'SUDARSHAN_Tax_pack_FY2627.pdf'
    )
    expect(
      downloadName(report({ kind: 'monthly', period_key: '202610', file_name: 'a.csv' }))
    ).toBe('SUDARSHAN_Monthly_statement_202610.csv')
    const statement = report({ kind: 'monthly', period_key: '202610' })
    expect(downloadName(statement, 'csv')).toBe('SUDARSHAN_Monthly_statement_202610.csv')
    expect(downloadName(statement)).toBe('SUDARSHAN_Monthly_statement_202610.pdf')
  })

  it('offers a CSV for the monthly statement only', () => {
    expect(hasCsv(report({ kind: 'monthly' }))).toBe(true)
    for (const kind of ['eod', 'brief', 'weekly', 'fy'] as const) {
      expect(hasCsv(report({ kind }))).toBe(false)
    }
  })

  it('words each key', () => {
    expect(periodWords('20261109')).toBe('Mon 9 Nov 2026')
    expect(periodWords('2026W45')).toBe('Week 45, 2026')
    expect(periodWords('202610')).toBe('October 2026')
    expect(periodWords('FY2627')).toBe('FY 2026-27')
    expect(periodWords('odd')).toBe('odd')
  })
})

describe('the times, as the server says them', () => {
  const times = { brief: '08:45', eod: '15:50', check: '16:05', weekly: 'Sunday 18:00' }

  it('takes each time from the server and none from anywhere else', () => {
    expect(timeOf(times, 'eod')).toBe('15:50')
    expect(timeOf(times, 'monthly')).toBeNull()
    expect(timeOf({ eod: '  ' }, 'eod')).toBeNull()
    expect(timeOf(undefined, 'eod')).toBeNull()
    expect(timeOf(null, 'brief')).toBeNull()
    expect(SWITCHES.map((row) => row.at)).toEqual(['eod', 'brief', 'weekly', 'monthly', 'fy'])
    expect(JSON.stringify(SWITCHES)).not.toMatch(/\d\d:\d\d/)
  })

  it('says the close check and the empty archive with the server’s times, or that it has not said', () => {
    expect(checkWords(times)).toBe(
      "Close check at 16:05: the day's fills are read again and the journal corrected."
    )
    expect(checkWords({})).toBeNull()
    expect(emptyWords(times)).toContain('The Day report is built at 15:50 on each trading day.')
    expect(emptyWords(undefined)).toContain('the server has not said the time yet.')
    expect(emptyWords(undefined)).not.toMatch(/\d\d:\d\d/)
  })
})
