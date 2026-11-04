import { AxiosError } from 'axios'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const client = vi.hoisted(() => ({ get: vi.fn(), request: vi.fn() }))
vi.mock('@/api/client', () => ({ webClient: client }))

import { PortfolioError, portfolioCall, problemOf } from './api'
import { WORDS } from './types'

const PREFIX = '/sudarshan/portfolio/api'

beforeEach(() => {
  client.get.mockReset()
  client.request.mockReset()
})

describe('portfolioCall', () => {
  it('reads with its query from the table: lists joined, empty values left out', async () => {
    client.get.mockResolvedValue({ data: { status: 'success', enabled: true, data: { tab: 1 } } })
    const result = await portfolioCall('tab_get', {
      id: 12,
      as_of: 20261104,
      accounts: [1, 2],
      classes: [],
      held: '',
    } as never)
    expect(result).toEqual({ enabled: true, data: { tab: 1 } })
    const [url, config] = client.get.mock.calls[0]
    expect(url).toBe(`${PREFIX}/tabs/12`)
    expect(config.params).toEqual({ as_of: '20261104', accounts: '1,2' })
    expect(config.timeout).toBeGreaterThan(3000)
  })

  it('a write sends its body without the route’s number', async () => {
    client.request.mockResolvedValue({
      data: { status: 'success', enabled: true, data: { id: 5 } },
    })
    await portfolioCall('tab_update', { id: 5, version: 3, name: 'Gold' } as never)
    expect(client.request).toHaveBeenCalledWith(
      expect.objectContaining({
        url: `${PREFIX}/tabs/5`,
        method: 'PATCH',
        data: { version: 3, name: 'Gold' },
      })
    )
  })

  it('an import goes up as a file with its fields', async () => {
    client.request.mockResolvedValue({
      data: { status: 'success', enabled: true, data: { batch_id: 1 } },
    })
    await portfolioCall('import_preview', {
      id: 3,
      format: 'generic',
      filename: 'trades.csv',
      text: 'a,b\n',
      account_id: 4,
    } as never)
    const config = client.request.mock.calls[0][0]
    expect(config.url).toBe(`${PREFIX}/tabs/3/imports`)
    const form = config.data as FormData
    expect((form.get('file') as File).name).toBe('trades.csv')
    expect(form.get('format')).toBe('generic')
    expect(form.get('account_id')).toBe('4')
    expect(form.has('id')).toBe(false)
  })

  it('switched off is a result with the sentence, not an error', async () => {
    client.get.mockResolvedValue({
      data: { status: 'success', enabled: false, data: null, message: WORDS.off },
    })
    expect(await portfolioCall('state')).toEqual({ enabled: false, data: null, message: WORDS.off })
  })

  it('an error carries its code and the sentence as the server wrote it', async () => {
    const response = {
      status: 422,
      data: {
        status: 'error',
        error: { code: 'refused', message: 'Sold more than held.', entry_id: 9 },
      },
    }
    client.request.mockRejectedValue(
      new AxiosError('x', 'ERR_BAD_REQUEST', undefined, null, response as never)
    )
    const error = await portfolioCall('activity_delete', { id: 1 } as never).catch((e) => e)
    expect(error).toBeInstanceOf(PortfolioError)
    expect(error).toMatchObject({ code: 'refused', message: 'Sold more than held.', entryId: 9 })
  })

  it('no answer at all and an answer that is not the envelope are told in words', () => {
    expect(problemOf(new AxiosError('x', 'ERR_NETWORK'))).toMatchObject({ code: 'unanswered' })
    const html = new AxiosError('x', 'ERR', undefined, null, {
      status: 500,
      data: '<html>',
    } as never)
    expect(problemOf(html).message).toMatch(/could not be read/)
    for (const code of ['unanswered', 'unreadable', 'unsent'] as const) {
      const sentence = problemOf(
        code === 'unanswered'
          ? new AxiosError('x', 'ERR_NETWORK')
          : code === 'unsent'
            ? new Error('x')
            : html
      ).message
      expect(sentence).not.toMatch(/\b(HTTP|5\d\d|axios|JSON|undefined)\b/i)
    }
  })

  it('a success with no data is not taken for one', async () => {
    client.get.mockResolvedValue({ data: { status: 'success', enabled: true } })
    await expect(portfolioCall('state')).rejects.toMatchObject({ code: 'unreadable' })
  })
})
