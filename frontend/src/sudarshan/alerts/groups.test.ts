/** What a push does to the group cards in the cached state (M8.14). */
import { describe, expect, it } from 'vitest'
import { alertsState, NOW } from '@/test/marketAlerts'
import { applyStatePush, dropGroup, putGroup } from './stateCache'
import { groupRow, memberRow } from './testGroups'

const ENGINE = alertsState().engine

function held() {
  return alertsState({
    alerts: [memberRow(1, 'SBIN'), memberRow(2, 'TCS')],
    groups: [groupRow(), groupRow({ id: 8, name: 'Other', ref: 'list:other' })],
    groups_by_ref: { 'list:swing': [7], 'list:other': [8] },
  })
}

const push = (extra: object) => ({
  engine: ENGINE,
  alerts: [],
  removed: [],
  server_time: NOW + 1_000,
  ...extra,
})

describe('a push with group cards', () => {
  it('updates a changed card in place, with its words, without reading again', () => {
    const words =
      'Covers 180 of 195: alerts can follow at most 200 instruments and 500 Active alerts.'
    const { state, reread } = applyStatePush(
      held(),
      push({
        groups: [
          {
            id: 7,
            version: 2,
            status: 'active',
            status_reason: words,
            covered: 180,
            left_out: ['x'],
            synced_at: NOW + 500,
          },
        ],
      })
    )
    expect(reread).toBe(false)
    const card = state.groups?.find((each) => each.id === 7)
    expect(card).toMatchObject({
      status_reason: words,
      covered: 180,
      members: 180,
      left_out: ['x'],
    })
    expect(state.groups?.find((each) => each.id === 8)).toEqual(held().groups?.[1])
  })

  it('keeps the card’s "could not read" words until a later sync', () => {
    const start = alertsState({
      groups: [groupRow({ follow: 'The list could not be read just now.' })],
    })
    const brief = {
      id: 7,
      version: 3,
      status: 'paused',
      status_reason: '',
      covered: 24,
      left_out: [],
      synced_at: NOW,
    }
    const same = applyStatePush(start, push({ groups: [{ ...brief, version: 2 }] }))
    expect(same.state.groups?.[0]?.follow).toBe('The list could not be read just now.')
    expect(same.state.groups?.[0]?.status).toBe('paused')
  })

  it('removes a deleted card and reads again for one it does not hold, or a newer version', () => {
    const gone = applyStatePush(held(), push({ removed: [1, 2], removed_groups: [7] }))
    expect(gone.state.groups?.map((each) => each.id)).toEqual([8])
    expect(gone.state.alerts).toEqual([])
    expect(
      applyStatePush(
        held(),
        push({
          groups: [
            {
              id: 99,
              version: 1,
              status: 'active',
              status_reason: '',
              covered: 1,
              left_out: [],
              synced_at: null,
            },
          ],
        })
      ).reread
    ).toBe(true)
    expect(
      applyStatePush(
        held(),
        push({
          groups: [
            {
              id: 7,
              version: 5,
              status: 'active',
              status_reason: '',
              covered: 24,
              left_out: [],
              synced_at: null,
            },
          ],
        })
      ).reread
    ).toBe(true)
  })

  it('leaves a state without groups as it was', () => {
    const plain = alertsState()
    const applied = applyStatePush(
      plain,
      push({
        alerts: [
          { id: 12, version: 3, status: 'paused', status_reason: '', check: null, fired_today: 0 },
        ],
      })
    )
    expect('groups' in applied.state).toBe(false)
  })
})

describe('a group made, changed or deleted here', () => {
  it('puts the answer in place and notes its list', () => {
    const next = putGroup(alertsState(), groupRow())
    expect(next.groups).toHaveLength(1)
    expect(next.groups_by_ref).toEqual({ 'list:swing': [7] })
    expect(putGroup(next, groupRow({ version: 3, name: 'Swing 2' })).groups?.[0]?.name).toBe(
      'Swing 2'
    )
  })

  it('drops the card, its list entry and its member alerts', () => {
    const next = dropGroup(held(), 7)
    expect(next.groups?.map((each) => each.id)).toEqual([8])
    expect(next.groups_by_ref).toEqual({ 'list:other': [8] })
    expect(next.alerts).toEqual([])
  })
})
