import { describe, expect, it } from 'vitest'
import { screenerState, screenRow } from '@/test/screener'
import { applyRun, applyState, dropRow, putRow } from './stateCache'
import type { RunPush, StatePush } from './types'

const counts = { matches: 9, entered: 2, exited: 0, missing: 0, not_checked: 0 }
const push = (over: Partial<RunPush> = {}): RunPush => ({
  screen_id: 1,
  run_id: 11,
  run_key: 'k',
  at: 1,
  candle: '',
  counts,
  entered: [],
  exited: [],
  marks: [],
  ...over,
})

describe('a run push', () => {
  it('moves the matches and adds the entered to today', () => {
    const next = applyRun(screenerState(), push())
    expect(next?.screens[0]).toMatchObject({ matches: 9, entered_today: 5, last_run_id: 11 })
    expect(next?.last_run_id).toBe(11)
  })
  it('is ignored when the card already holds that run or a newer one', () => {
    const state = screenerState()
    expect(applyRun(state, push({ run_id: 10 }))).toBe(state)
  })
  it('asks for a read when the screen is not held', () => {
    expect(applyRun(screenerState(), push({ screen_id: 5 }))).toBeNull()
  })
})

describe('a state push', () => {
  const statePush = (over: Partial<StatePush> = {}): StatePush => ({
    engine: { state: 'closed', line: 'Market closed', real_time: false },
    screens: [],
    removed: [],
    ...over,
  })
  it('takes the engine line and the changed fields of the same version', () => {
    const next = applyState(
      screenerState(),
      statePush({
        screens: [
          {
            id: 1,
            version: 1,
            status: 'paused',
            status_reason: null,
            next_check: null,
            matches: 7,
            entered_today: 3,
          },
        ],
      })
    )
    expect(next?.engine.state).toBe('closed')
    expect(next?.screens[0]).toMatchObject({ status: 'paused', next_check: null })
  })
  it('asks for a read for a newer version, an unknown screen or a lost push', () => {
    const part = {
      id: 1,
      version: 2,
      status: 'active' as const,
      status_reason: null,
      next_check: null,
      matches: 0,
      entered_today: 0,
    }
    expect(applyState(screenerState(), statePush({ screens: [part] }))).toBeNull()
    expect(
      applyState(screenerState(), statePush({ screens: [{ ...part, id: 8, version: 1 }] }))
    ).toBeNull()
    expect(applyState(screenerState(), statePush({ resync: true }))).toBeNull()
  })
  it('drops a removed screen', () => {
    expect(
      applyState(screenerState([screenRow(), screenRow({ id: 2 })]), statePush({ removed: [2] }))
        ?.screens
    ).toHaveLength(1)
  })
})

describe('the owner own changes', () => {
  it('put a row in its place or at the end, and drop one', () => {
    const state = screenerState()
    expect(putRow(state, screenRow({ name: 'Renamed' })).screens[0].name).toBe('Renamed')
    expect(putRow(state, screenRow({ id: 2 })).screens).toHaveLength(2)
    expect(dropRow(state, 1).screens).toHaveLength(0)
  })
})
