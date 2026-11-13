/** What the playbook sheet saves, typed by hand. */
import { describe, expect, it } from 'vitest'
import {
  createRequest,
  EMPTY_FORM,
  formOf,
  problemsOf,
  rulesOf,
  updateRequest,
} from './playbookForm'
import type { Playbook } from './types'

const OPENING: Playbook = {
  id: 4,
  name: 'Opening range',
  name_alt: null,
  description: 'First 15 minutes.',
  rules: ['Waited for the retest', 'Stop placed'],
  default_risk_paise: 50000,
  archived: false,
  version: 2,
}

describe('the form', () => {
  it('starts empty with one row for a check, and from a playbook with its own', () => {
    expect(formOf(null)).toEqual(EMPTY_FORM)
    expect(formOf(null).rules).not.toBe(EMPTY_FORM.rules)
    expect(formOf(OPENING)).toEqual({
      name: 'Opening range',
      nameAlt: '',
      description: 'First 15 minutes.',
      rules: ['Waited for the retest', 'Stop placed'],
      risk: '500',
    })
    expect(formOf({ ...OPENING, rules: [] }).rules).toEqual([''])
  })

  it('drops blank checks and trims the rest', () => {
    expect(rulesOf({ ...EMPTY_FORM, rules: [' A ', '', '  ', 'B'] })).toEqual(['A', 'B'])
  })
})

describe('what cannot be saved is a sentence with the next step', () => {
  const ok = { ...EMPTY_FORM, name: 'Gap fade' }
  it('needs a name, of 40 characters at most', () => {
    expect(problemsOf(ok)).toEqual([])
    expect(problemsOf(EMPTY_FORM)).toEqual(['Give the playbook a name.'])
    expect(problemsOf({ ...ok, name: 'x'.repeat(41) })).toEqual([
      'The name can have up to 40 characters. Shorten it.',
    ])
    expect(problemsOf({ ...ok, nameAlt: 'x'.repeat(41) })[0]).toMatch(/second name/)
  })

  it('holds 12 checks and no more', () => {
    expect(problemsOf({ ...ok, rules: Array.from({ length: 12 }, (_, n) => `R${n}`) })).toEqual([])
    expect(problemsOf({ ...ok, rules: Array.from({ length: 13 }, (_, n) => `R${n}`) })).toEqual([
      'A playbook can have up to 12 checks. Remove some.',
    ])
    expect(
      problemsOf({ ...ok, rules: [...Array.from({ length: 12 }, (_, n) => `R${n}`), '  '] })
    ).toEqual([])
  })

  it('takes a default risk as a rupee amount above zero', () => {
    expect(problemsOf({ ...ok, risk: '1,250.50' })).toEqual([])
    expect(problemsOf({ ...ok, risk: '0' })[0]).toMatch(/rupee amount above zero/)
    expect(problemsOf({ ...ok, risk: 'lots' })[0]).toMatch(/Correct it or clear it/)
  })
})

describe('what Save sends', () => {
  it('a create carries the form, trimmed, with its request id', () => {
    expect(
      createRequest(
        {
          name: ' Gap fade ',
          nameAlt: '',
          description: ' Fade the gap. ',
          rules: ['A', ''],
          risk: '500',
        },
        'req-1'
      )
    ).toEqual({
      name: 'Gap fade',
      description: 'Fade the gap.',
      rules: ['A'],
      default_risk_paise: 50000,
      request_id: 'req-1',
    })
    expect(createRequest({ ...EMPTY_FORM, name: 'X', nameAlt: 'ऍक्स' }, 'r').name_alt).toBe('ऍक्स')
    expect(createRequest({ ...EMPTY_FORM, name: 'X' }, 'r').default_risk_paise).toBeNull()
  })

  it('an update is nothing when nothing changed, however the rows are padded', () => {
    expect(updateRequest(OPENING, formOf(OPENING))).toBeNull()
    expect(updateRequest(OPENING, { ...formOf(OPENING), rules: [...OPENING.rules, ''] })).toBeNull()
  })

  it('an update carries only what changed, with the version it was opened at', () => {
    expect(updateRequest(OPENING, { ...formOf(OPENING), name: 'ORB' })).toEqual({
      id: 4,
      version: 2,
      name: 'ORB',
    })
    expect(
      updateRequest(OPENING, {
        ...formOf(OPENING),
        nameAlt: 'ओआरबी',
        rules: ['Stop placed'],
        risk: '',
      })
    ).toEqual({
      id: 4,
      version: 2,
      name_alt: 'ओआरबी',
      rules: ['Stop placed'],
      default_risk_paise: null,
    })
  })

  it('clearing the second name sends null, not an empty word', () => {
    const withAlt = { ...OPENING, name_alt: 'ओआरबी' }
    expect(updateRequest(withAlt, { ...formOf(withAlt), nameAlt: '' })).toEqual({
      id: 4,
      version: 2,
      name_alt: null,
    })
  })
})
