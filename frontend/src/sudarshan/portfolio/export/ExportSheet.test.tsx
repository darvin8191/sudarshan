/**
 * The Export sheet (P7 F9), against lane X's contract: the five views and two formats, the file
 * handed to the phone, only the entries as CSV while the switch is off, the note that the whole
 * tab is exported, and the service's sentence when a file could not be made.
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ call: vi.fn(), pdf: vi.fn(), save: vi.fn() }))
vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  portfolioCall: api.call,
  getExportPdf: api.pdf,
  saveDownload: api.save,
}))
vi.mock('@/sudarshan/watchlist/openGuard', () => ({
  useOpenGuard: () => ({ settling: false, handlers: {} }),
}))

import { PortfolioError } from '../api'
import type { TabInfo } from '../types'
import { choices, EXPORTS_OFF, ExportSheet, WHOLE_TAB } from './ExportSheet'

const TAB = { id: 4, name: 'Long term', version: 1 } as TabInfo
const CSV = { filename: 'SUDARSHAN_Portfolio_Long-term_income_2026-11-14.csv', text: 'a' }
const PDF = { filename: 'SUDARSHAN_Portfolio_Long-term_holdings_2026-11-14.pdf', base64: 'QQ==' }

beforeEach(() => {
  api.call.mockReset().mockResolvedValue({ enabled: true, data: CSV })
  api.pdf.mockReset().mockResolvedValue({ enabled: true, data: PDF })
  api.save.mockReset()
})

describe('what is offered', () => {
  it('everything while the switch is on, the entries as CSV alone while it is off', () => {
    expect(choices(true)).toEqual({
      views: ['holdings', 'activity', 'performance', 'allocation', 'income'],
      formats: ['csv', 'pdf'],
    })
    expect(choices(false)).toEqual({ views: ['activity'], formats: ['csv'] })
  })
})

describe('the sheet', () => {
  it('downloads the view and format chosen, hands the file to the phone and closes', async () => {
    const close = vi.fn()
    render(<ExportSheet open onClose={close} tab={TAB} on />)
    expect(screen.getByText(WHOLE_TAB)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Entries' })).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Income' }))
    await userEvent.click(screen.getByRole('button', { name: 'Download Income as CSV' }))
    await waitFor(() => expect(api.save).toHaveBeenCalledWith(CSV))
    expect(api.call).toHaveBeenCalledWith('export_csv', { id: 4, view: 'income' })
    expect(close).toHaveBeenCalled()
    for (const button of screen.getAllByRole('button')) {
      if (button.textContent !== 'Close') expect(button.className).toContain('min-h-12')
    }
  })

  it('asks for a PDF through its own op', async () => {
    render(<ExportSheet open onClose={() => {}} tab={TAB} on />)
    await userEvent.click(screen.getByRole('button', { name: 'PDF' }))
    expect(screen.getByRole('button', { name: 'PDF' }).getAttribute('aria-pressed')).toBe('true')
    await userEvent.click(screen.getByRole('button', { name: 'Download Holdings as PDF' }))
    await waitFor(() => expect(api.save).toHaveBeenCalledWith(PDF))
    expect(api.pdf).toHaveBeenCalledWith({ id: 4, view: 'holdings' })
    expect(api.call).not.toHaveBeenCalled()
  })

  it('while the switch is off offers the entries as CSV only, and says why', async () => {
    render(<ExportSheet open onClose={() => {}} tab={TAB} on={false} />)
    expect(screen.getByText(EXPORTS_OFF)).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Holdings' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'PDF' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Download Entries as CSV' }))
    await waitFor(() =>
      expect(api.call).toHaveBeenCalledWith('export_csv', { id: 4, view: 'activity' })
    )
  })

  it('stays open with the service’s sentence when the file could not be made', async () => {
    const close = vi.fn()
    api.pdf.mockRejectedValue(
      new PortfolioError({
        code: 'unavailable',
        message: 'The PDF could not be made. Download the CSV instead.',
        entryId: null,
      })
    )
    render(<ExportSheet open onClose={close} tab={TAB} on />)
    await userEvent.click(screen.getByRole('button', { name: 'PDF' }))
    await userEvent.click(screen.getByRole('button', { name: 'Download Holdings as PDF' }))
    expect((await screen.findByRole('alert')).textContent).toMatch(/Download the CSV instead/)
    expect(close).not.toHaveBeenCalled()
    expect(api.save).not.toHaveBeenCalled()
  })

  it('starts on Holdings once the switch is known to be on, even when the sheet was drawn before', () => {
    const { rerender } = render(<ExportSheet open onClose={() => {}} tab={TAB} on={false} />)
    rerender(<ExportSheet open onClose={() => {}} tab={TAB} on />)
    expect(screen.getByRole('button', { name: 'Holdings' }).getAttribute('aria-pressed')).toBe(
      'true'
    )
  })

  it('draws nothing without a tab', () => {
    render(<ExportSheet open onClose={() => {}} tab={null} on />)
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})
