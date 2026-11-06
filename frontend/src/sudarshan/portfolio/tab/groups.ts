// SUDARSHAN M7.25: the holdings list in groups (design 4.9): by account, class, sector or tag, as
// the tab's layout says. Pure, free of React. A holding held in two accounts is in the group of
// each with that account's lines only, so a group's value is what that account holds. A holding
// with several tags is in the group of each tag with all its value (as the tag bars of the
// allocation do), so tag groups can add up to more than the tab. A holding with no sector or no
// tag is in a group of its own, named in words.
import { ASSET_CLASS_LABELS, type Group, type Sort } from '../types'
import { type ListedRow, listRows, sortRows } from './rows'

export const NO_SECTOR_GROUP = 'No sector set'
export const NO_TAG_GROUP = 'No tag'

export interface HoldingGroup {
  key: string
  label: string
  rows: ListedRow[]
  value_paise: number
  unrealised_paise: number
}

/** What the tag groups are said to do, for the screen to print under the list. */
export const TAG_GROUPS_NOTE =
  'A holding with several tags is listed under each of them, so these groups can add up to more than the tab.'

type Place = { key: string; label: string }

function placesOf(
  r: ListedRow,
  group: Exclude<Group, 'none'>,
  accountName: (id: number) => string
): Place[] {
  const asset = r.row.asset
  switch (group) {
    case 'class':
      return [{ key: asset.asset_class, label: ASSET_CLASS_LABELS[asset.asset_class] }]
    case 'sector':
      return [
        asset.sector
          ? { key: asset.sector, label: asset.sector }
          : { key: '', label: NO_SECTOR_GROUP },
      ]
    case 'tag':
      return asset.tags.length > 0
        ? asset.tags.map((t) => ({ key: t, label: t }))
        : [{ key: '', label: NO_TAG_GROUP }]
    case 'account': {
      const ids = [...new Set(r.row.lines.map((l) => l.account_id))]
      return ids.map((id) => ({ key: String(id), label: accountName(id) }))
    }
  }
}

/**
 * The listed rows split into groups, the largest group first and the group of "nothing set" last,
 * each group's rows in the layout's sort. `none` gives no groups.
 */
export function groupRows(
  list: readonly ListedRow[],
  group: Group,
  sort: Sort,
  totalValuePaise: number,
  accountName: (id: number) => string
): HoldingGroup[] {
  if (group === 'none') return []
  const found = new Map<string, HoldingGroup>()
  for (const r of list) {
    const places = placesOf(r, group, accountName)
    for (const place of places) {
      let row = r
      if (group === 'account' && places.length > 1) {
        const lines = r.row.lines.filter((l) => String(l.account_id) === place.key)
        const [part] = listRows([{ ...r.row, lines }], [r.moved], totalValuePaise)
        if (!part) continue
        row = part
      }
      const at = found.get(place.key) ?? {
        key: place.key,
        label: place.label,
        rows: [],
        value_paise: 0,
        unrealised_paise: 0,
      }
      at.rows.push(row)
      at.value_paise += row.figures.value_paise
      at.unrealised_paise += row.figures.unrealised_paise
      found.set(place.key, at)
    }
  }
  const nothing = group === 'sector' || group === 'tag' ? '' : null
  return [...found.values()]
    .map((g) => ({ ...g, rows: sortRows(g.rows, sort) }))
    .sort((a, b) => {
      if (a.key === nothing) return 1
      if (b.key === nothing) return -1
      return b.value_paise - a.value_paise || a.label.localeCompare(b.label)
    })
}
