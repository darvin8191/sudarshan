/**
 * M13.7 (U4): the timeline, newest first, 50 a page, "Load older" with the service's cursor, each
 * row opening Why. Pages are the real service's (bridge.answers.json: its first page of three and
 * the page before it).
 */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const webClient = vi.hoisted(() => ({ get: vi.fn(), request: vi.fn() }))
vi.mock('@/api/client', () => ({ webClient }))

import { ANSWERS, answerFromRecording, NOW_MS, refusal } from '@/test/signalsAnswers'
import { renderSignals } from '@/test/signalsRender'
import { Timeline } from './Timeline'
import { TIMELINE, WORDS } from './types'

beforeEach(() => {
  webClient.get.mockReset()
})

describe('the timeline', () => {
  it('reads 50 at a time, newest first, and the older page with the cursor the service gave', async () => {
    answerFromRecording(webClient, (_method, _path, query) =>
      query.before ? ANSWERS.timeline_older : ANSWERS.timeline_first
    )
    const onWhy = vi.fn()
    renderSignals(<Timeline now={NOW_MS} onWhy={onWhy} />)
    const list = await screen.findByRole('list', { name: 'Changes, newest first' })
    expect(webClient.get.mock.calls[0][1].params).toEqual({ limit: String(TIMELINE.page) })
    const rows = within(list).getAllByRole('listitem')
    expect(rows.map((row) => row.getAttribute('data-change'))).toEqual(['8', '7', '6'])
    expect(rows[0]).toHaveTextContent('09:04 · Other · NIFTY · First reading')

    await userEvent.click(screen.getByRole('button', { name: 'Load older' }))
    await waitFor(() => expect(within(list).getAllByRole('listitem')).toHaveLength(6))
    const next = ANSWERS.timeline_first.answer.data as { next_before: number }
    expect(webClient.get.mock.calls[1][1].params).toMatchObject({
      before: String(next.next_before),
    })
    const change = within(list).getByText('FII distribution').closest('li') as HTMLElement
    expect(change).toHaveTextContent('DRISHTI · NIFTY · Changed')
    expect(change).toHaveTextContent('FII accumulationtoFII distributionbearish0.71')
    expect(screen.getByRole('button', { name: 'Load older' })).toBeInTheDocument() // more remain

    await userEvent.click(within(change).getByRole('button'))
    expect(onWhy).toHaveBeenCalledWith(expect.objectContaining({ id: 4, kind: 'state' }))
  })

  it('a row older than 30 minutes keeps its arrow and word, without colour', async () => {
    answerFromRecording(webClient, () => ANSWERS.timeline)
    renderSignals(<Timeline now={NOW_MS + 31 * 60_000} onWhy={() => {}} />)
    const list = await screen.findByRole('list', { name: 'Changes, newest first' })
    for (const mark of list.querySelectorAll('[data-bias]')) {
      expect(mark.className).toContain('--text-secondary')
    }
    expect(screen.queryByRole('button', { name: 'Load older' })).toBeNull()
  })

  it('says when nothing has changed yet, and a refusal in its own sentence', async () => {
    webClient.get.mockResolvedValueOnce({
      data: { status: 'success', enabled: true, data: { changes: [], next_before: null } },
      request: {},
    })
    const empty = renderSignals(<Timeline now={NOW_MS} onWhy={() => {}} />)
    expect(
      await screen.findByText(
        'No changes yet. The timeline begins with the first reading TRINETRA publishes.'
      )
    ).toBeInTheDocument()
    empty.unmount()
    // A refusal that reading again cannot help is said at once (one the reader may recover from
    // is read twice more first).
    webClient.get.mockRejectedValue(refusal(403, ANSWERS.forbidden.answer))
    renderSignals(<Timeline now={NOW_MS} onWhy={() => {}} />)
    expect(await screen.findByRole('alert')).toHaveTextContent(WORDS.forbidden)
    expect(screen.getByRole('button', { name: 'Try again' })).toHaveClass('min-h-12')
  })
})
