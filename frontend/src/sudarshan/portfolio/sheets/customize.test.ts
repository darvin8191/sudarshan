import { describe, expect, it } from 'vitest'
import type { LayoutSpec, Widget } from '../types'
import { chosen, formOf, moved, ordered, sameSpec, specOf } from './customize'

const SPEC: LayoutSpec = {
  v: 1,
  widgets: ['holdings', 'summary', 'notes'],
  columns: ['name', 'value'],
  sort: 'value',
  group: 'none',
}
const DRAWN: Widget[] = ['summary', 'today', 'holdings']

describe('the Customize form', () => {
  it('lists the ones on in their order, then the ones off', () => {
    expect(ordered(['a', 'b', 'c'], ['c', 'a'])).toEqual([
      { id: 'c', on: true },
      { id: 'a', on: true },
      { id: 'b', on: false },
    ])
  })

  it('moves one place and stays put at the ends', () => {
    const items = ordered(['a', 'b', 'c'], ['a', 'b', 'c'])
    expect(chosen(moved(items, 2, -1))).toEqual(['a', 'c', 'b'])
    expect(chosen(moved(items, 0, -1))).toEqual(['a', 'b', 'c'])
    expect(chosen(moved(items, 2, 1))).toEqual(['a', 'b', 'c'])
  })

  it('keeps a section the screen cannot draw yet where the layout had it', () => {
    const form = formOf(SPEC, DRAWN)
    expect(form.widgets.map((w) => w.id)).not.toContain('notes')
    expect(specOf(form, SPEC, DRAWN).widgets).toEqual(['holdings', 'summary', 'notes'])
  })

  it('knows when nothing changed', () => {
    const form = formOf(SPEC, DRAWN)
    expect(sameSpec(specOf(form, SPEC, DRAWN), SPEC)).toBe(true)
    expect(sameSpec(specOf({ ...form, group: 'class' }, SPEC, DRAWN), SPEC)).toBe(false)
  })
})
