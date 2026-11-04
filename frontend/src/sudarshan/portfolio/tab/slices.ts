/**
 * What a tap on an allocation bar does to the holdings (design 4.8), and the filters as the
 * service takes them. Class, account and tag are filters the service applies; a member is its
 * accounts; sector and market cap have no filter in the service, so the screen narrows the rows
 * itself (a "slice").
 */
import type {
  Account,
  AllocationBy,
  AllocationRow,
  AssetClass,
  Filters,
  HoldingRow,
  TabGetArgs,
} from '../types'

export interface Slice {
  by: 'sector' | 'mcap'
  key: string
}

export interface Narrowing {
  filters: Filters
  slice: Slice | null
}

/** Only what is set: an empty list is no filter, so it is not sent. */
export function argsOf(f: Filters): Omit<TabGetArgs, 'id'> {
  return {
    ...(f.accounts.length > 0 ? { accounts: f.accounts } : {}),
    ...(f.classes.length > 0 ? { classes: f.classes } : {}),
    ...(f.tags.length > 0 ? { tags: f.tags } : {}),
    ...(f.held ? { held: f.held } : {}),
    ...(f.as_of ? { as_of: f.as_of } : {}),
  }
}

const same = (a: readonly number[], b: readonly number[]) =>
  a.length === b.length && a.every((v) => b.includes(v))

function membersAccounts(key: string, accounts: readonly Account[]): number[] {
  return accounts.filter((a) => a.member_id === Number(key)).map((a) => a.id)
}

export function isActive(
  by: AllocationBy,
  row: AllocationRow,
  now: Narrowing,
  accounts: readonly Account[]
): boolean {
  switch (by) {
    case 'class':
      return now.filters.classes.includes(row.key as AssetClass)
    case 'account':
      return now.filters.accounts.includes(Number(row.key))
    case 'tag':
      return now.filters.tags.includes(row.key)
    case 'member': {
      const ids = membersAccounts(row.key, accounts)
      return ids.length > 0 && same(now.filters.accounts, ids)
    }
    case 'sector':
    case 'mcap':
      return now.slice?.by === by && now.slice.key === row.key
  }
}

/** The narrowing after a tap on `row`: the slice is taken on, or off if it was on. */
export function pick(
  by: AllocationBy,
  row: AllocationRow,
  now: Narrowing,
  accounts: readonly Account[]
): Narrowing {
  const on = isActive(by, row, now, accounts)
  const f = now.filters
  switch (by) {
    case 'class':
      return { ...now, filters: { ...f, classes: on ? [] : [row.key as AssetClass] } }
    case 'account':
      return { ...now, filters: { ...f, accounts: on ? [] : [Number(row.key)] } }
    case 'tag':
      return { ...now, filters: { ...f, tags: on ? [] : [row.key] } }
    case 'member':
      return { ...now, filters: { ...f, accounts: on ? [] : membersAccounts(row.key, accounts) } }
    case 'sector':
    case 'mcap':
      return { ...now, slice: on ? null : { by, key: row.key } }
  }
}

/** The rows a slice keeps. */
export function sliced<T extends Pick<HoldingRow, 'asset'>>(
  rows: readonly T[],
  slice: Slice | null
): T[] {
  if (!slice) return [...rows]
  return rows.filter((r) => (slice.by === 'sector' ? r.asset.sector : r.asset.mcap) === slice.key)
}
