// SUDARSHAN M2.8 (cross-design Y6): the order of a privacy save and what is said when part of it
// did not happen. Pure: the writes are fakes that record what reached each store.
import { describe, expect, it } from 'vitest'
import { OwnerError } from '../ownerApi'
import { CATEGORIES, type Privacy } from '../types'
import { FLOOR_CHANGED, type FloorPlan, listOf, planPrivacy, runPrivacySave } from './privacySave'

const P = (pnl: boolean, qty: boolean, funds: boolean, orders: boolean): Privacy => ({
  pnl,
  qty,
  funds,
  orders,
})
const VIEWER = P(true, true, false, true)
const floorOf = (shown: Privacy, role: string | null = 'viewer'): FloorPlan => ({ shown, role })

describe('the order of the writes', () => {
  it('hides on the floor first, then writes the family row', () => {
    const steps = planPrivacy({
      floor: floorOf(VIEWER),
      next: { privacy: P(true, true, false, false), role: 'viewer' },
      familyChanged: true,
    })
    expect(steps).toEqual([
      { kind: 'floor', why: 'hide', floor: floorOf(P(true, true, false, false)) },
      { kind: 'family' },
    ])
  })

  it('writes the family row first, then shows on the floor last', () => {
    const steps = planPrivacy({
      floor: floorOf(VIEWER),
      next: { privacy: P(true, true, true, true), role: 'viewer' },
      familyChanged: true,
    })
    expect(steps).toEqual([
      { kind: 'family' },
      { kind: 'floor', why: 'show', floor: floorOf(P(true, true, true, true)) },
    ])
  })

  it('splits an edit that hides one thing and shows another round the family write', () => {
    const steps = planPrivacy({
      floor: floorOf(VIEWER),
      next: { privacy: P(true, true, true, false), role: 'viewer' },
      familyChanged: true,
    })
    expect(steps).toEqual([
      { kind: 'floor', why: 'hide', floor: floorOf(P(true, true, false, false)) },
      { kind: 'family' },
      { kind: 'floor', why: 'show', floor: floorOf(P(true, true, true, false)) },
    ])
  })

  it('a role change goes to the floor last: the tabs shared by role follow it', () => {
    const steps = planPrivacy({
      floor: floorOf(VIEWER, 'viewer'),
      next: { privacy: VIEWER, role: 'child' },
      familyChanged: true,
    })
    expect(steps).toEqual([
      { kind: 'family' },
      { kind: 'floor', why: 'show', floor: floorOf(VIEWER, 'child') },
    ])
  })

  it('a missing floor is sent last, and alone when the family row already says it', () => {
    expect(
      planPrivacy({ floor: null, next: { privacy: VIEWER, role: 'viewer' }, familyChanged: false })
    ).toEqual([{ kind: 'floor', why: 'show', floor: floorOf(VIEWER) }])
  })

  it('nothing to do when both already agree', () => {
    expect(
      planPrivacy({
        floor: floorOf(VIEWER),
        next: { privacy: VIEWER, role: 'viewer' },
        familyChanged: false,
      })
    ).toEqual([])
  })
})

/** A fake of the two stores: what each holds, and a script of how each write ends. */
function rig(start: FloorPlan | null, endings: Array<'ok' | 'refused' | 'lost' | 'lost-done'>) {
  const state = { floor: start, family: 'old' as 'old' | 'new', writes: [] as string[] }
  let version = start ? 4 : null
  let turn = 0
  const end = <T>(apply: () => T): Promise<T> => {
    const how = endings[turn++] ?? 'ok'
    if (how === 'ok') return Promise.resolve(apply())
    if (how === 'lost-done') apply()
    return Promise.reject(
      how === 'refused'
        ? new OwnerError('conflict', 'This member was changed elsewhere.')
        : new OwnerError('not_confirmed', 'Not confirmed: open the Family page again.')
    )
  }
  return {
    state,
    start: { floor: start, version },
    writes: {
      family: () =>
        end(() => {
          state.writes.push('family')
          state.family = 'new'
          return null
        }),
      floor: (floor: FloorPlan, sent: number | null) =>
        end(() => {
          state.writes.push(`floor:${sent}`)
          state.floor = floor
          version = (version ?? 0) + 1
          return { version }
        }),
    },
  }
}

const combos: Privacy[] = []
for (let bits = 0; bits < 16; bits += 1) {
  const p = P(!!(bits & 1), !!(bits & 2), !!(bits & 4), !!(bits & 8))
  if (!p.pnl || p.qty) combos.push(p) // P&L shown needs quantities (Y24)
}

describe('a half-done edit can only leave the member seeing less, never more', () => {
  it.each([
    'refused',
    'lost',
    'lost-done',
  ] as const)('for every old and new choice and every step ending %s', async (ending) => {
    for (const before of combos)
      for (const after of combos) {
        const plan = planPrivacy({
          floor: floorOf(before),
          next: { privacy: after, role: 'viewer' },
          familyChanged: true,
        })
        for (let stop = 0; stop < plan.length; stop += 1) {
          const endings = plan.map((_, i) => (i < stop ? 'ok' : i === stop ? ending : 'ok'))
          const r = rig(floorOf(before), endings)
          await expect(runPrivacySave(plan, r.writes, r.start)).rejects.toBeInstanceOf(OwnerError)
          const floor = (r.state.floor as FloorPlan).shown
          const familyConfirmed = plan.slice(0, stop).some((s) => s.kind === 'family')
          const first = plan[0]
          const hid = first.kind === 'floor' && first.why === 'hide'
          const hideTookEffect = hid && (stop > 0 || ending === 'lost-done')
          const where = JSON.stringify({ before, after, stop, ending })
          for (const c of CATEGORIES) {
            // never more than before, unless it is the new choice, written last, after the
            // family service confirmed its own write
            const intended = after[c] && familyConfirmed && ending === 'lost-done'
            if (floor[c]) expect(before[c] || intended, `${c} shown: ${where}`).toBe(true)
            // and what the edit hides is hidden as soon as its first write took effect
            if (hideTookEffect && before[c] && !after[c])
              expect(floor[c], `${c} hidden: ${where}`).toBe(false)
          }
          // and nothing after the step that did not end well was sent
          expect(r.state.writes.length).toBeLessThanOrEqual(stop + (ending === 'lost-done' ? 1 : 0))
        }
      }
  })

  it('every step confirmed leaves both stores at the new choice', async () => {
    const after = P(true, true, true, false)
    const plan = planPrivacy({
      floor: floorOf(VIEWER),
      next: { privacy: after, role: 'viewer' },
      familyChanged: true,
    })
    const r = rig(floorOf(VIEWER), [])
    await runPrivacySave(plan, r.writes, r.start)
    expect(r.state).toEqual({
      floor: floorOf(after),
      family: 'new',
      writes: ['floor:4', 'family', 'floor:5'],
    })
  })
})

describe('what is said', () => {
  const run = async (
    before: Privacy | null,
    after: Privacy,
    endings: Array<'ok' | 'refused' | 'lost' | 'lost-done'>,
    familyChanged = true
  ) => {
    const start = before ? floorOf(before) : null
    const plan = planPrivacy({
      floor: start,
      next: { privacy: after, role: 'viewer' },
      familyChanged,
    })
    const r = rig(start, endings)
    try {
      await runPrivacySave(plan, r.writes, r.start)
      return { said: 'Saved.', code: null as string | null, writes: r.state.writes }
    } catch (error) {
      const problem = error as OwnerError
      return { said: problem.message, code: problem.code, writes: r.state.writes }
    }
  }

  it('a refused hide: nothing was saved, and the family row was not written', async () => {
    const out = await run(VIEWER, P(true, true, false, false), ['refused'])
    expect(out.said).toBe(`Nothing was saved. ${FLOOR_CHANGED}`)
    expect(out.writes).toEqual([])
  })

  it('a hide not confirmed says what may have happened and sends nothing else', async () => {
    const out = await run(VIEWER, P(true, true, false, false), ['lost'])
    expect(out.said).toBe(
      'Not confirmed: the portfolio may now hide orders from them, and nothing else was saved. Open this member again to check.'
    )
    expect(out.code).toBe('not_confirmed')
    expect(out.writes).toEqual([])
  })

  it('the family row refused after the hide names what took effect, then the reason', async () => {
    const out = await run(VIEWER, P(false, true, false, false), ['ok', 'refused'])
    expect(out.said).toBe(
      'The portfolio now hides P&L and orders from them; nothing else was saved. This member was changed elsewhere.'
    )
  })

  it('the family row refused with nothing before it is the family service sentence as it is', async () => {
    const out = await run(VIEWER, P(true, true, true, true), ['refused'])
    expect(out.said).toBe('This member was changed elsewhere.')
  })

  it('a show refused after the family row: saved there, still hidden in the portfolio', async () => {
    const out = await run(VIEWER, P(true, true, true, true), ['ok', 'refused'])
    expect(out.said).toBe(
      `Saved in the family view, but the portfolio still hides funds from them. ${FLOOR_CHANGED}`
    )
  })

  it('a show not confirmed is not confirmed, never failed', async () => {
    const out = await run(VIEWER, P(true, true, true, true), ['ok', 'lost'])
    expect(out.said).toBe(
      'Saved in the family view; whether the portfolio now shows them funds is not confirmed. Open this member again to check.'
    )
    expect(out.said).not.toMatch(/fail/i)
    expect(out.code).toBe('not_confirmed')
  })

  it('a floor that only catches up says so without the family view', async () => {
    const out = await run(null, VIEWER, ['refused'], false)
    expect(out.said).toBe(
      `The portfolio still hides P&L, quantities and orders from them. ${FLOOR_CHANGED}`
    )
  })

  it('says "Saved." only when every write was confirmed', async () => {
    expect((await run(VIEWER, P(true, true, true, false), [])).said).toBe('Saved.')
  })

  it('lists categories as a person would', () => {
    expect(listOf(['pnl'])).toBe('P&L')
    expect(listOf(['pnl', 'funds'])).toBe('P&L and funds')
    expect(listOf(['pnl', 'qty', 'funds'])).toBe('P&L, quantities and funds')
  })
})
