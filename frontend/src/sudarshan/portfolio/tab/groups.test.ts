import { describe, expect, it } from 'vitest'
import { row } from './fixtures'
import { groupRows, NO_SECTOR_GROUP, NO_TAG_GROUP } from './groups'
import { listRows } from './rows'

const name = (id: number) => (id === 1 ? 'Main demat' : 'Second demat')

function listed(rows: ReturnType<typeof row>[]) {
  return listRows(
    rows,
    rows.map(() => false),
    1
  )
}
const withAsset = (r: ReturnType<typeof row>, patch: Partial<ReturnType<typeof row>['asset']>) => ({
  ...r,
  asset: { ...r.asset, ...patch },
})

describe('grouping the holdings', () => {
  it('gives no groups for none', () => {
    expect(groupRows(listed([row(1, 'A')]), 'none', 'value', 1, name)).toEqual([])
  })

  it('groups by class, the largest group first', () => {
    const rows = [
      withAsset(row(1, 'Gold', { price: 1_000_000 }), { asset_class: 'gold' }), // 100 x 10
      withAsset(row(2, 'Alpha', { price: 3_000_000 }), { asset_class: 'equity' }),
      withAsset(row(3, 'Beta', { price: 2_000_000 }), { asset_class: 'equity' }),
    ]
    const groups = groupRows(listed(rows), 'class', 'value', 1, name)
    expect(groups.map((g) => g.key)).toEqual(['equity', 'gold'])
    expect(groups[0].rows.map((r) => r.row.asset.name)).toEqual(['Alpha', 'Beta']) // sorted by value
    expect(groups[0].value_paise).toBe(500_000) // 3,000 + 2,000 rupees
  })

  it('puts a holding with no sector or no tag in a group of its own, last', () => {
    const rows = [
      withAsset(row(1, 'Big', { price: 9_000_000 }), { sector: null, tags: [] }),
      withAsset(row(2, 'Small', { price: 1_000_000 }), { sector: 'Banks', tags: ['core'] }),
    ]
    const bySector = groupRows(listed(rows), 'sector', 'value', 1, name)
    expect(bySector.map((g) => g.label)).toEqual(['Banks', NO_SECTOR_GROUP])
    const byTag = groupRows(listed(rows), 'tag', 'value', 1, name)
    expect(byTag.map((g) => g.label)).toEqual(['core', NO_TAG_GROUP])
  })

  it('lists a holding under each of its tags with all its value', () => {
    const rows = [withAsset(row(1, 'Alpha'), { tags: ['core', 'income'] })]
    const groups = groupRows(listed(rows), 'tag', 'value', 1, name)
    expect(groups.map((g) => g.label).sort()).toEqual(['core', 'income'])
    expect(groups[0].rows[0].figures.value_paise).toBe(groups[1].rows[0].figures.value_paise)
  })

  it('splits a holding held in two accounts into each account with its own lines', () => {
    const r = row(1, 'Alpha', { price: 1_000_000 })
    const [line] = r.lines
    r.lines = [
      { ...line, account_id: 1, qty_u: 10_000_000, open_qty_u: 10_000_000, cost_paise: 90_000 },
      { ...line, account_id: 2, qty_u: 5_000_000, open_qty_u: 5_000_000, cost_paise: 45_000 },
    ]
    const groups = groupRows(listed([r]), 'account', 'value', 1, name)
    expect(groups.map((g) => g.label)).toEqual(['Main demat', 'Second demat'])
    expect(groups[0].value_paise).toBe(100_000) // 10 x 100 rupees
    expect(groups[1].value_paise).toBe(50_000) // 5 x 100 rupees
  })
})
