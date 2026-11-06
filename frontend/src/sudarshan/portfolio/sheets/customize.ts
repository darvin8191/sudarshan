// SUDARSHAN M7.25: the pure logic of the Customize sheet (design 4.9): an ordered list with a
// switch on each item, moved one place up or down (never dragged), and the layout it makes.
import {
  type Group,
  HOLDING_COLUMNS,
  type HoldingColumn,
  type LayoutSpec,
  type Sort,
  WIDGETS,
  type Widget,
} from '../types'

export interface Item<T extends string> {
  id: T
  on: boolean
}

export const WIDGET_LABELS: Record<Widget, string> = {
  summary: 'Summary',
  today: 'Today',
  holdings: 'Holdings',
  movers: 'Top movers',
  allocation: 'Allocation',
  history: 'Value and money put in',
  performance: 'Return',
  income: 'Income',
  notes: 'Notes on prices',
}
export const COLUMN_LABELS: Record<HoldingColumn, string> = {
  name: 'Name',
  quantity: 'Quantity',
  avg_cost: 'Average cost',
  price: 'Price',
  today: 'Today',
  value: 'Value',
  gain: 'Gain',
  weight: 'Weight',
}
export const SORT_LABELS: Record<Sort, string> = {
  value: 'Value',
  today_pct: 'Today %',
  gain_pct: 'Gain %',
  weight: 'Weight',
  name: 'Name',
}
export const GROUP_LABELS: Record<Group, string> = {
  none: 'No grouping',
  account: 'Account',
  class: 'Class',
  sector: 'Sector',
  tag: 'Tag',
}

/** Every item: the ones on in their order, then the ones off in the list's own order. */
export function ordered<T extends string>(all: readonly T[], on: readonly T[]): Item<T>[] {
  const first = on.filter((id) => all.includes(id))
  return [
    ...first.map((id) => ({ id, on: true })),
    ...all.filter((id) => !first.includes(id)).map((id) => ({ id, on: false })),
  ]
}

/** One place earlier (-1) or later (1); the ends stay where they are. */
export function moved<T extends string>(
  items: readonly Item<T>[],
  at: number,
  by: -1 | 1
): Item<T>[] {
  const to = at + by
  if (to < 0 || to >= items.length) return [...items]
  const next = [...items]
  const [item] = next.splice(at, 1)
  if (item) next.splice(to, 0, item)
  return next
}

export const chosen = <T extends string>(items: readonly Item<T>[]): T[] =>
  items.filter((i) => i.on).map((i) => i.id)

/**
 * The widgets the form saves: the ones the screen draws as the form has them, then any the screen
 * does not draw yet exactly as the layout had them, so a part that arrives later finds its choice.
 */
export function widgetsOf(
  items: readonly Item<Widget>[],
  before: readonly Widget[],
  drawn: readonly Widget[]
): Widget[] {
  return [...chosen(items), ...before.filter((w) => !drawn.includes(w))]
}

export interface CustomForm {
  widgets: Item<Widget>[]
  columns: Item<HoldingColumn>[]
  sort: Sort
  group: Group
}

export function formOf(spec: LayoutSpec, drawn: readonly Widget[]): CustomForm {
  return {
    widgets: ordered(
      WIDGETS.filter((w) => drawn.includes(w)),
      spec.widgets
    ),
    columns: ordered(HOLDING_COLUMNS, spec.columns),
    sort: spec.sort,
    group: spec.group,
  }
}

export function specOf(form: CustomForm, before: LayoutSpec, drawn: readonly Widget[]): LayoutSpec {
  return {
    v: before.v,
    widgets: widgetsOf(form.widgets, before.widgets, drawn),
    columns: chosen(form.columns),
    sort: form.sort,
    group: form.group,
  }
}

export function sameSpec(a: LayoutSpec, b: LayoutSpec): boolean {
  const same = (x: readonly string[], y: readonly string[]) =>
    x.length === y.length && x.every((v, n) => v === y[n])
  return (
    same(a.widgets, b.widgets) &&
    same(a.columns, b.columns) &&
    a.sort === b.sort &&
    a.group === b.group
  )
}
