/** What the trade sheet edits, with the figures typed by hand (nothing here is computed by the code under test). */
import { describe, expect, it } from 'vitest'
import { closedTrip, trip } from './fixtures'
import {
  buildUpdate,
  changedFields,
  checklistFor,
  formOf,
  parseChecklist,
  parseRupees,
  problemsOf,
  rebase,
  recentStrategies,
  riskText,
  rText,
  type SheetForm,
  serializeChecklist,
} from './sheetForm'

describe('the risk field', () => {
  it.each([
    ['500', 50000, true],
    ['1,250.50', 125050, true],
    ['₹ 75.5', 7550, true],
    ['0.01', 1, true],
    ['', null, true],
    ['0', null, false],
    ['-5', null, false],
    ['12.345', null, false],
    ['five hundred', null, false],
    ['1e3', null, false],
  ])('%j is %j paise (valid %j)', (text, paise, valid) => {
    expect(parseRupees(text)).toEqual({ paise, valid })
  })

  it('writes paise back as the field shows them', () => {
    expect(riskText(50000)).toBe('500')
    expect(riskText(125050)).toBe('1250.50')
    expect(riskText(105)).toBe('1.05')
    expect(riskText(null)).toBe('')
  })
})

describe('R as typed (vector M1: risk 500.00 each)', () => {
  it.each([
    [81250, '+1.63'], // 1.625 rounds away from zero
    [-32500, '−0.65'],
    [26000, '+0.52'],
    [0, '0.00'],
    [65000, '+1.30'],
    [-100000, '−2.00'],
  ])('net %i paise is R %s', (net, text) => {
    expect(rText(net, 50000)).toBe(text)
  })

  it('has no R without a risk or a net', () => {
    expect(rText(1000, null)).toBeNull()
    expect(rText(null, 5000)).toBeNull()
    expect(rText(1000, 0)).toBeNull()
  })
})

describe('the checklist the notes store', () => {
  it('round trips, and reads anything else as empty', () => {
    const items = [
      { rule: 'Waited for the retest', yes: true },
      { rule: 'Stop placed', yes: false },
    ]
    expect(parseChecklist(serializeChecklist(items))).toEqual(items)
    expect(serializeChecklist([])).toBe('')
    expect(parseChecklist('')).toEqual([])
    expect(parseChecklist('not json')).toEqual([])
    expect(parseChecklist('{"a":1}')).toEqual([])
    expect(parseChecklist('[1,{"rule":"","yes":true},{"rule":"A","yes":"y"}]')).toEqual([
      { rule: 'A', yes: false },
    ])
  })

  it('takes a playbook’s rules with the answers already given to the same words', () => {
    expect(
      checklistFor(
        ['A', 'B', 'C'],
        [
          { rule: 'B', yes: true },
          { rule: 'Gone', yes: true },
        ]
      )
    ).toEqual([
      { rule: 'A', yes: false },
      { rule: 'B', yes: true },
      { rule: 'C', yes: false },
    ])
  })
})

describe('what Save sends', () => {
  const t = closedTrip({
    id: 7,
    net_paise: 81250,
    close_day: 20261113,
    version: 3,
    strategy: 'ORB',
  })
  const note = {
    round_trip_id: 7,
    notes: 'First note',
    mistakes: [2, 1],
    checklist: '',
    mood: 'calm' as const,
    version: 3,
  }
  const start = formOf(t, note)

  it('is nothing when nothing changed', () => {
    expect(buildUpdate(t, start, { ...start })).toBeNull()
    expect(buildUpdate(t, start, { ...start, mistakes: [1, 2] })).toBeNull()
  })

  it('carries only what changed, with the version the sheet was opened at', () => {
    expect(buildUpdate(t, start, { ...start, notes: 'Second note' })).toEqual({
      id: 7,
      version: 3,
      notes: 'Second note',
    })
    expect(
      buildUpdate(t, start, {
        ...start,
        strategy: ' Gap fade ',
        risk: '500',
        mood: null,
        setupId: 4,
      })
    ).toEqual({
      id: 7,
      version: 3,
      strategy_override: 'Gap fade',
      risk_paise: 50000,
      mood: null,
      setup_id: 4,
    })
  })

  it('an emptied strategy clears the owner’s own label', () => {
    const own = formOf({ ...t, strategy_override: 'Mine' }, note)
    expect(
      buildUpdate({ ...t, strategy_override: 'Mine' }, own, { ...own, strategy: '  ' })
    ).toEqual({
      id: 7,
      version: 3,
      strategy_override: null,
    })
  })

  it('names a problem with its next step, and sends nothing a problem stands in front of', () => {
    expect(problemsOf(start)).toEqual([])
    const long = problemsOf({ ...start, strategy: 'x'.repeat(41) })
    expect(long).toEqual(['The strategy name can have up to 40 characters. Shorten it.'])
    expect(problemsOf({ ...start, risk: '12.345' })[0]).toMatch(/rupee amount above zero/)
    expect(problemsOf({ ...start, notes: 'n'.repeat(4001) })[0]).toMatch(/up to 4,000 characters/)
  })
})

describe('the strategies offered', () => {
  it('are the last 8 used, newest first, each once, the owner’s label over the recorded', () => {
    const list = [
      ...Array.from({ length: 10 }, (_, n) =>
        trip({ id: n + 1, strategy: `S${n}`, opened_ms: 1000 + n })
      ),
      trip({ id: 50, strategy: 'S9', opened_ms: 500 }),
      trip({ id: 51, strategy: 'Recorded', strategy_override: 'Mine', opened_ms: 5000 }),
      trip({ id: 52, strategy: '', opened_ms: 6000 }),
    ]
    expect(recentStrategies(list)).toEqual(['Mine', 'S9', 'S8', 'S7', 'S6', 'S5', 'S4', 'S3'])
  })
})

describe('rebase (review M1)', () => {
  const base: SheetForm = {
    strategy: 'ORB',
    setupId: null,
    risk: '500',
    checklist: [],
    mistakes: [1],
    mood: 'calm',
    notes: 'First words.',
  }

  it('keeps what was typed where the other screen changed nothing, and takes its other changes', () => {
    const mine = { ...base, notes: 'First words. More.', risk: '750' }
    const theirs = { ...base, mood: 'tired' as const, mistakes: [1, 2] }
    const out = rebase(base, mine, theirs)
    expect(out.form).toEqual({ ...theirs, notes: 'First words. More.', risk: '750' })
    expect(out.typedNotes).toBeNull()
    expect(out.overlap).toEqual([])
  })

  it('shows the other screen’s value where both changed, names it, and hands back typed notes', () => {
    const mine = { ...base, notes: 'Mine.', mood: 'rushed' as const, strategy: 'Gap' }
    const theirs = { ...base, notes: 'Theirs.', mood: 'tired' as const, strategy: ' Gap ' }
    const out = rebase(base, mine, theirs)
    expect(out.form.notes).toBe('Theirs.')
    expect(out.form.mood).toBe('tired')
    expect(out.typedNotes).toBe('Mine.')
    // the same change on both screens is no overlap
    expect(out.overlap).toEqual(['the mood'])
    expect(changedFields(base, mine)).toEqual(['strategy', 'mood', 'notes'])
  })
})
