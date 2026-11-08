import { describe, expect, it } from 'vitest'
import { MAX_CLAUSES } from './catalog'
import {
  addClause,
  addGroup,
  childrenOf,
  clausesOf,
  countClauses,
  depthOf,
  duplicate,
  groupOf,
  kindOf,
  move,
  nodeAt,
  pathOfClause,
  remove,
  setGroupKind,
  updateClause,
  whyNoClause,
  whyNoGroup,
} from './tree'
import type { Clause, Group } from './types'

const clause = (id: string, over: Partial<Clause> = {}): Clause => ({
  id,
  on: true,
  left: { field: 'close', tf: 15 },
  op: 'gt',
  right: { num: '1' },
  ...over,
})

const two: Group = groupOf('all', [clause('c1'), clause('c2')])

describe('the rule tree', () => {
  it('adds a condition with an id no other holds, and never changes its argument', () => {
    const before = JSON.stringify(two)
    const next = addClause(two, [], 15)
    expect(JSON.stringify(two)).toBe(before)
    expect(clausesOf(next as Group).map((c) => c.id)).toEqual(['c1', 'c2', 'c3'])
  })

  it('refuses a third level of groups and says why', () => {
    const second = addGroup(two, [], 'any', 15) as Group
    expect(depthOf(second)).toBe(2)
    const third = addGroup(second, [2], 'none', 15) as Group
    expect(depthOf(third)).toBe(3)
    expect(addGroup(third, [2, 1], 'any', 15)).toBeNull()
    expect(whyNoGroup(third, [2, 1])).toBe('Groups can go three levels deep.')
    expect(whyNoGroup(third, [2])).toBeNull()
  })

  it('refuses a condition past twenty, and a duplicate that would go past it', () => {
    let tree: Group = groupOf('all', [clause('c1')])
    for (let i = 1; i < MAX_CLAUSES; i += 1) tree = addClause(tree, [], 15) as Group
    expect(countClauses(tree)).toBe(MAX_CLAUSES)
    expect(addClause(tree, [], 15)).toBeNull()
    expect(whyNoClause(tree)).toBe('A screen can hold 20 conditions at most. Delete some.')
    expect(duplicate(tree, [0])).toBeNull()
  })

  it('duplicates a group with its conditions under new ids, right after it', () => {
    const tree = addGroup(two, [], 'any', 15) as Group
    const copy = duplicate(tree, [2]) as Group
    expect(childrenOf(copy)).toHaveLength(4)
    const ids = clausesOf(copy).map((c) => c.id)
    expect(new Set(ids).size).toBe(ids.length)
  })

  it('moves a node among its siblings and stops at either end', () => {
    expect(clausesOf(move(two, [1], -1) as Group).map((c) => c.id)).toEqual(['c2', 'c1'])
    expect(move(two, [0], -1)).toBeNull()
    expect(move(two, [1], 1)).toBeNull()
  })

  it('removes a node and finds a clause by its id', () => {
    expect(clausesOf(remove(two, [0]) as Group).map((c) => c.id)).toEqual(['c2'])
    expect(pathOfClause(addGroup(two, [], 'any', 15) as Group, 'c3')).toEqual([2, 0])
    expect(pathOfClause(two, 'zz')).toBeNull()
    expect(remove(two, [])).toBeNull()
  })

  it('changes a group kind keeping what is in it, and a clause in place', () => {
    const any = setGroupKind(two, [], 'none') as Group
    expect(kindOf(any)).toBe('none')
    expect(childrenOf(any)).toHaveLength(2)
    const off = updateClause(two, [1], (c) => ({ ...c, on: false })) as Group
    expect((nodeAt(off, [1]) as Clause).on).toBe(false)
    expect((nodeAt(off, [0]) as Clause).on).toBe(true)
  })
})
