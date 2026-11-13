import { describe, expect, it } from 'vitest'
import type { GroupRow, MarketAlertsState } from '@/api/marketAlerts'
import { groupRefsFor } from './alertSync'

/** M8.14 (review M5): which list alerts a pushed change asks to follow at once. */

const group = (id: number, ref: string, status: GroupRow['status'] = 'active') =>
  ({ id, ref, status }) as GroupRow

function state(over: Partial<MarketAlertsState> = {}): MarketAlertsState {
  return {
    enabled: true,
    groups: [
      group(3, 'list:7'),
      group(4, 'list:7/section:2'),
      group(5, 'list:7/section:9', 'paused'),
      group(6, 'list:70'),
      group(8, 'flag:2'),
      group(9, 'list:12', 'stopped'),
      group(10, 'preset:nifty50'),
    ],
    groups_by_ref: {
      'list:7': [3],
      'list:7/section:2': [4],
      'list:7/section:9': [5],
      'list:70': [6],
      'flag:2': [8],
      'list:12': [9],
      'preset:nifty50': [10],
    },
    ...over,
  } as MarketAlertsState
}

describe('groupRefsFor', () => {
  it('names a list with its own alert and each of its sections that has one, once each', () => {
    expect(groupRefsFor(state(), ['list:7', 'list:7'])).toEqual([
      'list:7',
      'list:7/section:2',
      'list:7/section:9',
    ])
  })

  it('never takes another list whose id starts the same', () => {
    expect(groupRefsFor(state(), ['list:7'])).not.toContain('list:70')
    expect(groupRefsFor(state(), ['list:70'])).toEqual(['list:70'])
  })

  it('names the sections of a list that has no alert of its own', () => {
    const only = state({ groups_by_ref: { 'list:7/section:2': [4] } })
    expect(groupRefsFor(only, ['list:7'])).toEqual(['list:7/section:2'])
  })

  it('names flag lists and built-in lists that carry one, and the source and target of a move', () => {
    expect(groupRefsFor(state(), ['flag:1', 'flag:2'])).toEqual(['flag:2'])
    expect(groupRefsFor(state(), ['preset:nifty50'])).toEqual(['preset:nifty50'])
    expect(groupRefsFor(state(), ['list:70', 'list:7'])).toEqual([
      'list:70',
      'list:7',
      'list:7/section:2',
      'list:7/section:9',
    ])
  })

  it('leaves out a ref whose alerts are all stopped, a ref with none, and a change it cannot name', () => {
    expect(groupRefsFor(state(), ['list:12'])).toEqual([])
    expect(groupRefsFor(state(), ['list:99'])).toEqual([])
    expect(groupRefsFor(state(), ['lists', ''])).toEqual([])
  })

  it('asks nothing while market alerts are off or their state is not read', () => {
    expect(groupRefsFor(state({ enabled: false }), ['list:7'])).toEqual([])
    expect(groupRefsFor(undefined, ['list:7'])).toEqual([])
    expect(groupRefsFor(state({ groups_by_ref: undefined }), ['list:7'])).toEqual([])
  })
})
