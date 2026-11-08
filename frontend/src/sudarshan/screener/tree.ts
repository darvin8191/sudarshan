/**
 * The rule tree as the builder edits it, as plain functions a test calls (M9.3). A node is a
 * clause or a group (`all`, `any`, `none`); a node is addressed by its path, the indexes taken
 * from the root group. Every function returns a new tree and never changes its argument; one that
 * cannot do what it was asked (a third level too deep, a twenty-first clause) returns null, and
 * the builder shows the reason beside the disabled button.
 */
import { MAX_CLAUSES, MAX_DEPTH } from './catalog'
import type { Clause, Group, GroupKind, Node, TimeFrame } from './types'

export type Path = number[]

export const isGroup = (node: Node): node is Group => !('op' in node)

export function kindOf(group: Group): GroupKind {
  return 'all' in group ? 'all' : 'any' in group ? 'any' : 'none'
}

export function childrenOf(group: Group): Node[] {
  if ('all' in group) return group.all
  if ('any' in group) return group.any
  return group.none
}

export function groupOf(kind: GroupKind, children: Node[]): Group {
  return kind === 'all'
    ? { all: children }
    : kind === 'any'
      ? { any: children }
      : { none: children }
}

export function nodeAt(root: Group, path: Path): Node | null {
  let node: Node = root
  for (const index of path) {
    if (!isGroup(node)) return null
    const next: Node | undefined = childrenOf(node)[index]
    if (!next) return null
    node = next
  }
  return node
}

/** The tree with the node at `path` replaced by `change(node)`; null for a path that is not there. */
function replaceAt(root: Group, path: Path, change: (node: Node) => Node | null): Group | null {
  if (path.length === 0) {
    const next = change(root)
    return next && isGroup(next) ? next : null
  }
  const [head, ...rest] = path
  const kids = childrenOf(root)
  const child = kids[head]
  if (!child) return null
  if (rest.length === 0) {
    const next = change(child)
    if (!next) return null
    return groupOf(
      kindOf(root),
      kids.map((each, index) => (index === head ? next : each))
    )
  }
  if (!isGroup(child)) return null
  const inner = replaceAt(child, rest, change)
  return inner
    ? groupOf(
        kindOf(root),
        kids.map((each, index) => (index === head ? inner : each))
      )
    : null
}

/** The tree with the node at `path` taken out (a group's children go with it). */
function removeFrom(root: Group, path: Path): Group | null {
  if (path.length === 0) return null
  const parentPath = path.slice(0, -1)
  const at = path[path.length - 1]
  return replaceAt(root, parentPath, (parent) => {
    if (!isGroup(parent) || !childrenOf(parent)[at]) return null
    return groupOf(
      kindOf(parent),
      childrenOf(parent).filter((_, index) => index !== at)
    )
  })
}

/** How many levels a group holds counting itself: a group of clauses is 1. */
export function depthOf(group: Group): number {
  let deepest = 0
  for (const child of childrenOf(group))
    if (isGroup(child)) deepest = Math.max(deepest, depthOf(child))
  return deepest + 1
}

export function clausesOf(group: Group): Clause[] {
  return childrenOf(group).flatMap((child) => (isGroup(child) ? clausesOf(child) : [child]))
}

export const countClauses = (group: Group): number => clausesOf(group).length

/** Clauses that are switched on. */
export const countOn = (group: Group): number => clausesOf(group).filter((c) => c.on).length

/** The level a group at `path` sits on: the root is 1. */
export const levelOf = (path: Path): number => path.length + 1

/** Why a group at `path` cannot take another group, or null when it can. */
export function whyNoGroup(root: Group, path: Path): string | null {
  if (levelOf(path) >= MAX_DEPTH) return 'Groups can go three levels deep.'
  return countClauses(root) >= MAX_CLAUSES ? whyNoClause(root) : null
}

export function whyNoClause(root: Group): string | null {
  return countClauses(root) >= MAX_CLAUSES
    ? `A screen can have ${MAX_CLAUSES} conditions at most.`
    : null
}

/** The next clause id no clause of the tree holds: c1, c2, ... */
export function nextId(root: Group): string {
  const used = new Set(clausesOf(root).map((clause) => clause.id))
  let n = used.size + 1
  while (used.has(`c${n}`)) n += 1
  return `c${n}`
}

/** A new condition: the close compared with a number, on the screen's own candles. */
export function freshClause(root: Group, tf: TimeFrame): Clause {
  return { id: nextId(root), on: true, left: { field: 'close', tf }, op: 'gt', right: { num: '0' } }
}

export function addClause(root: Group, path: Path, tf: TimeFrame): Group | null {
  if (whyNoClause(root)) return null
  return replaceAt(root, path, (node) =>
    isGroup(node) ? groupOf(kindOf(node), [...childrenOf(node), freshClause(root, tf)]) : null
  )
}

export function addGroup(root: Group, path: Path, kind: GroupKind, tf: TimeFrame): Group | null {
  if (whyNoGroup(root, path)) return null
  return replaceAt(root, path, (node) =>
    isGroup(node)
      ? groupOf(kindOf(node), [...childrenOf(node), groupOf(kind, [freshClause(root, tf)])])
      : null
  )
}

export function updateClause(
  root: Group,
  path: Path,
  change: (clause: Clause) => Clause
): Group | null {
  return replaceAt(root, path, (node) => (isGroup(node) ? null : change(node)))
}

export function setGroupKind(root: Group, path: Path, kind: GroupKind): Group | null {
  return replaceAt(root, path, (node) => (isGroup(node) ? groupOf(kind, childrenOf(node)) : null))
}

export function remove(root: Group, path: Path): Group | null {
  return removeFrom(root, path)
}

/** A copy of the node right after it, its clauses under new ids. */
export function duplicate(root: Group, path: Path): Group | null {
  const node = nodeAt(root, path)
  if (!node || path.length === 0) return null
  const size = countClauses(isGroup(node) ? node : groupOf('all', [node]))
  if (countClauses(root) + size > MAX_CLAUSES) return null
  let taken = root
  const renew = (each: Node): Node => {
    if (isGroup(each)) return groupOf(kindOf(each), childrenOf(each).map(renew))
    const copy = { ...each, id: nextId(taken) }
    taken = groupOf(kindOf(taken), [...childrenOf(taken), copy])
    return copy
  }
  const copy = renew(node)
  const parentPath = path.slice(0, -1)
  const at = path[path.length - 1]
  return replaceAt(root, parentPath, (parent) => {
    if (!isGroup(parent)) return null
    const kids = [...childrenOf(parent)]
    kids.splice(at + 1, 0, copy)
    return groupOf(kindOf(parent), kids)
  })
}

/** Move a node one place up (-1) or down (1) among its siblings; null at either end. */
export function move(root: Group, path: Path, by: -1 | 1): Group | null {
  if (path.length === 0) return null
  const parentPath = path.slice(0, -1)
  const at = path[path.length - 1]
  return replaceAt(root, parentPath, (parent) => {
    if (!isGroup(parent)) return null
    const kids = [...childrenOf(parent)]
    const to = at + by
    if (to < 0 || to >= kids.length) return null
    ;[kids[at], kids[to]] = [kids[to], kids[at]]
    return groupOf(kindOf(parent), kids)
  })
}

/** Where a clause with this id sits, or null. */
export function pathOfClause(root: Group, id: string, from: Path = []): Path | null {
  const group = nodeAt(root, from)
  if (!group || !isGroup(group)) return null
  const kids = childrenOf(group)
  for (let index = 0; index < kids.length; index += 1) {
    const kid = kids[index]
    if (!isGroup(kid)) {
      if (kid.id === id) return [...from, index]
    } else {
      const inner = pathOfClause(root, id, [...from, index])
      if (inner) return inner
    }
  }
  return null
}

/** The distinct library indicators a tree reads, for the cap of 12 (catalog.py counts the same). */
export function instanceKeys(group: Group, isLibrary: (ind: string) => boolean): Set<string> {
  const keys = new Set<string>()
  const visit = (operand: unknown): void => {
    if (!operand || typeof operand !== 'object') return
    const item = operand as Record<string, unknown>
    if (typeof item.ind === 'string' && isLibrary(item.ind)) {
      keys.add(JSON.stringify([item.ind, item.p ?? {}, item.out ?? null, item.tf ?? null]))
    }
    if (Array.isArray(item.args) && 'math' in item) for (const arg of item.args) visit(arg)
  }
  for (const clause of clausesOf(group)) {
    visit(clause.left)
    visit(clause.right)
  }
  return keys
}
