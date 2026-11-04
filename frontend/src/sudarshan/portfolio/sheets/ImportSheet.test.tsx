/**
 * The Import sheet (M7.22): the format, the 1.2 MB limit, the preview in words with the problem
 * rows first, the commit with the version the preview saw, "Remove this import", and the export.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ call: vi.fn() }))
vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  portfolioCall: api.call,
}))

import { createQueryClient } from '@/app/providers'
import { PortfolioError } from '../api'
import { IMPORT_MAX_FILE_BYTES, type ImportPreview, WORDS } from '../types'
import { tab } from './fixtures'
import { ImportSheet } from './ImportSheet'

const GOLD = tab(2, 'Gold', { version: 4 })

const PREVIEW: ImportPreview = {
  batch_id: 31,
  format: 'generic',
  filename: 'trades.csv',
  rows_total: 6,
  rows_new: 3,
  rows_dup: 1,
  rows_bad: 2,
  words: '3 new, 1 already in this tab, 2 with problems',
  rows: [
    {
      row: 7,
      status: 'bad',
      words: "Row 7: TATAMTR is not in today's instrument list. Check the symbol.",
      entry: null,
    },
    { row: 9, status: 'bad', words: 'Row 9: The date 31-02-2026 is not a date.', entry: null },
    {
      row: 3,
      status: 'dup',
      words: 'Row 3 is already in this tab (imported 28 Oct).',
      entry: null,
    },
    { row: 2, status: 'new', words: null, entry: null },
  ],
  truncated: false,
  tab_version: 4,
  expires_at: 0,
}

function show(accounts: Parameters<typeof ImportSheet>[0]['accounts'] = []) {
  const handlers = { onClose: vi.fn(), onChanged: vi.fn() }
  render(
    <QueryClientProvider client={createQueryClient()}>
      <ImportSheet open tab={GOLD} accounts={accounts} {...handlers} />
    </QueryClientProvider>
  )
  return handlers
}

const file = (name = 'trades.csv', body = 'date,action\n') =>
  new File([body], name, { type: 'text/csv' })

async function upload(f: File = file()) {
  await userEvent.upload(screen.getByLabelText('Choose a file'), f)
}

beforeEach(() => {
  api.call.mockReset()
})

describe('the Import sheet', () => {
  it('sends the file with its format and the tab, then shows the preview in words', async () => {
    api.call.mockResolvedValue({ enabled: true, data: PREVIEW })
    show()
    await userEvent.click(screen.getByRole('radio', { name: /Zerodha tradebook/ }))
    await upload(file('tradebook.csv', 'symbol,isin\nINFY,x\n'))
    await screen.findByText('3 new, 1 already in this tab, 2 with problems')
    expect(api.call).toHaveBeenCalledWith('import_preview', {
      id: 2,
      format: 'zerodha',
      filename: 'tradebook.csv',
      text: 'symbol,isin\nINFY,x\n',
    })
    const rows = within(screen.getByRole('list', { name: 'Rows' })).getAllByRole('listitem')
    expect(rows[0].textContent).toContain("Row 7: TATAMTR is not in today's instrument list.")
    expect(rows[0].textContent).toContain('Problem') // told by a word as well as the mark
    expect(rows[2].textContent).toContain('Row 3 is already in this tab (imported 28 Oct).')
    expect(screen.getByRole('list', { name: 'Counts' }).textContent).toContain('With problems: 2')
  })

  it('refuses a file over 1.2 MB without sending it', async () => {
    show()
    const big = file('big.csv')
    Object.defineProperty(big, 'size', { value: IMPORT_MAX_FILE_BYTES + 1 })
    await upload(big)
    expect((await screen.findByRole('alert')).textContent).toBe(WORDS.import_too_big)
    expect(api.call).not.toHaveBeenCalled()
  })

  it('commits, says how many were added, and Remove this import sends the new tab version', async () => {
    const { onChanged } = show()
    api.call
      .mockResolvedValueOnce({ enabled: true, data: PREVIEW })
      .mockResolvedValueOnce({ enabled: true, data: { batch_id: 31, added: 3, tab_version: 5 } })
      .mockResolvedValueOnce({
        enabled: true,
        data: { batch_id: 31, status: 'undone', removed: 3, tab_version: 6 },
      })
    await upload()
    await userEvent.click(await screen.findByRole('button', { name: 'Add 3 entries' }))
    expect(await screen.findByText('3 entries were added.')).toBeTruthy()
    expect(api.call).toHaveBeenNthCalledWith(2, 'import_commit', { id: 31, version: 4 })
    expect(onChanged).toHaveBeenCalledTimes(1)
    await userEvent.click(screen.getByRole('button', { name: 'Remove this import' }))
    expect(api.call).toHaveBeenNthCalledWith(3, 'import_discard', { id: 31, version: 5 })
    expect(await screen.findByText(/3 entries were removed/)).toBeTruthy()
    expect(onChanged).toHaveBeenCalledTimes(2)
  })

  it('shows the service sentence when the tab changed since the preview', async () => {
    api.call.mockResolvedValueOnce({ enabled: true, data: PREVIEW }).mockRejectedValueOnce(
      new PortfolioError({
        code: 'version_conflict',
        message: WORDS.version_conflict,
        entryId: null,
      })
    )
    show()
    await upload()
    await userEvent.click(await screen.findByRole('button', { name: 'Add 3 entries' }))
    expect((await screen.findByRole('alert')).textContent).toBe(WORDS.version_conflict)
  })

  it('names the entry that stands in the way of removing an import', async () => {
    const words = 'This import cannot be removed: the sale on 12 Oct (entry 44) uses these shares.'
    api.call
      .mockResolvedValueOnce({ enabled: true, data: PREVIEW })
      .mockResolvedValueOnce({ enabled: true, data: { batch_id: 31, added: 3, tab_version: 5 } })
      .mockRejectedValueOnce(new PortfolioError({ code: 'refused', message: words, entryId: 44 }))
    show()
    await upload()
    await userEvent.click(await screen.findByRole('button', { name: 'Add 3 entries' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Remove this import' }))
    expect((await screen.findByRole('alert')).textContent).toBe(words)
    expect(screen.getByText('3 entries were added.')).toBeTruthy() // still added
  })

  it('does not offer to add a preview with nothing new', async () => {
    api.call.mockResolvedValue({
      enabled: true,
      data: {
        ...PREVIEW,
        rows_new: 0,
        rows_dup: 4,
        rows_bad: 0,
        words: '0 new, 4 already in this tab',
      },
    })
    show()
    await upload()
    const add = (await screen.findByRole('button', {
      name: 'Nothing new to add',
    })) as HTMLButtonElement
    expect(add.disabled).toBe(true)
  })

  it('downloads the entries with the service file name', async () => {
    const click = vi.fn()
    const created: string[] = []
    URL.createObjectURL = vi.fn(() => 'blob:x')
    URL.revokeObjectURL = vi.fn()
    const real = document.createElement.bind(document)
    vi.spyOn(document, 'createElement').mockImplementation((tag: string) => {
      const el = real(tag)
      if (tag === 'a') {
        el.click = click
        Object.defineProperty(el, 'download', {
          set: (v: string) => created.push(v),
          get: () => created[0],
        })
      }
      return el
    })
    api.call.mockResolvedValue({
      enabled: true,
      data: { filename: 'SUDARSHAN_Portfolio_2_2026-11-04.csv', text: 'date,action\n' },
    })
    show()
    await userEvent.click(screen.getByRole('button', { name: /Download entries/ }))
    await waitFor(() => expect(click).toHaveBeenCalled())
    expect(api.call).toHaveBeenCalledWith('export_csv', { id: 2 })
    expect(created).toEqual(['SUDARSHAN_Portfolio_2_2026-11-04.csv'])
    vi.restoreAllMocks()
  })
})
