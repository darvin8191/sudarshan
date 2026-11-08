import { useCallback, useMemo } from 'react'
import { ClauseCard, type Side } from './ClauseCard'
import { GroupCard } from './GroupCard'
import { type NodeMenu, NodeMenuSheet } from './NodeMenuSheet'
import { OperandSheet } from './OperandSheet'
import { OperatorSheet, withOperator } from './OperatorSheet'
import {
  addClause,
  addGroup,
  childrenOf,
  duplicate,
  isGroup,
  kindOf,
  move,
  nodeAt,
  type Path,
  pathOfClause,
  remove,
  setGroupKind,
  updateClause,
  whyNoClause,
  whyNoGroup,
} from './tree'
import type { Clause, Group, GroupKind, TimeFrame } from './types'
import type { Address } from './useScreenerAddress'

/** The sheet reference of a group: `g` for the top one, `g.0.2` for a nested one. */
export const groupRef = (path: Path): string => ['g', ...path].join('.')

export function pathOfRef(ref: string | null): Path | null {
  if (!ref || !ref.startsWith('g')) return null
  const parts = ref.split('.').slice(1)
  const path = parts.map(Number)
  return path.every((index) => Number.isInteger(index) && index >= 0) ? path : null
}

/**
 * The rule builder (design 5.2): one condition per card, written as a sentence of chips, groups as
 * cards holding cards, three levels at most. The tree is the screen's `where`; every change is
 * handed to `onChange` as a new tree. What is open over it (a condition's menu, its operator
 * sheet, a group's menu) lives in the address, so the phone's Back closes it first.
 */
export function RuleBuilder({
  where,
  onChange,
  runTf,
  problems,
  address,
  openSheet,
  closeSheet,
}: {
  where: Group
  onChange: (next: Group) => void
  runTf: TimeFrame
  /** The service's words for a clause, by its id. */
  problems: ReadonlyMap<string, string>
  address: Address
  openSheet: (sheet: string, extra?: Record<string, string>) => void
  closeSheet: () => void
}) {
  const apply = useCallback(
    (next: Group | null) => {
      if (next) onChange(next)
    },
    [onChange]
  )

  const clausePath = address.clause ? pathOfClause(where, address.clause) : null
  const clause = clausePath ? (nodeAt(where, clausePath) as Clause | null) : null
  const groupPath = address.sheet === 'group' ? pathOfRef(address.clause) : null

  const menu = useMemo((): NodeMenu | null => {
    const path = address.sheet === 'clause' ? clausePath : groupPath
    if (!path || path.length === 0) return null
    const node = nodeAt(where, path)
    if (!node) return null
    const parent = nodeAt(where, path.slice(0, -1))
    const siblings = parent && isGroup(parent) ? childrenOf(parent).length : 1
    const at = path[path.length - 1]
    return {
      what: isGroup(node) ? 'group' : 'condition',
      kind: isGroup(node) ? kindOf(node) : undefined,
      canUp: at > 0,
      canDown: at < siblings - 1,
      noDuplicate: duplicate(where, path)
        ? null
        : (whyNoClause(where) ?? 'This would go past three levels.'),
    }
  }, [address.sheet, clausePath, groupPath, where])
  const menuPath = address.sheet === 'clause' ? clausePath : groupPath

  const renderGroup = (group: Group, path: Path): React.ReactNode => {
    const kids = childrenOf(group)
    return (
      <GroupCard
        key={path.join('.') || 'root'}
        kind={kindOf(group)}
        level={path.length + 1}
        empty={kids.length === 0}
        onMenu={path.length > 0 ? () => openSheet('group', { c: groupRef(path) }) : undefined}
        onAddClause={() => apply(addClause(where, path, runTf))}
        onAddGroup={() => apply(addGroup(where, path, 'any', runTf))}
        whyNoClause={whyNoClause(where)}
        whyNoGroup={whyNoGroup(where, path)}
      >
        {kids.map((kid, index) => {
          const here = [...path, index]
          if (isGroup(kid)) return renderGroup(kid, here)
          return (
            <ClauseCard
              key={kid.id}
              clause={kid}
              problem={problems.get(kid.id) ?? null}
              onToggle={(on) => apply(updateClause(where, here, (c) => ({ ...c, on })))}
              onOperator={() => openSheet('operator', { c: kid.id })}
              onOperand={(side) => openSheet('operand', { c: kid.id, side })}
              onNumber={(side: Side, value) =>
                apply(updateClause(where, here, (c) => ({ ...c, [side]: { num: value } })))
              }
              onMenu={() => openSheet('clause', { c: kid.id })}
            />
          )
        })}
      </GroupCard>
    )
  }

  const act = (change: (path: Path) => Group | null, after: 'close' | 'stay' = 'stay') => {
    if (!menuPath) return
    const next = change(menuPath)
    if (next) apply(next)
    if (after === 'close' && next) closeSheet()
  }

  return (
    <div className="flex flex-col gap-2">
      {renderGroup(where, [])}
      {address.sheet === 'operand' && clause && address.side && (
        <OperandSheet
          key={`${clause.id}:${address.side}`}
          operand={(address.side === 'left' ? clause.left : clause.right) ?? { num: '0' }}
          runTf={runTf}
          onApply={(next) => {
            if (clausePath) {
              apply(
                updateClause(where, clausePath, (c) => ({ ...c, [address.side as Side]: next }))
              )
            }
            closeSheet()
          }}
          onClose={closeSheet}
        />
      )}
      <OperatorSheet
        clause={address.sheet === 'operator' ? clause : null}
        onChange={(next) => clausePath && apply(updateClause(where, clausePath, () => next))}
        onClose={closeSheet}
      />
      <NodeMenuSheet
        menu={address.sheet === 'clause' || address.sheet === 'group' ? menu : null}
        onClose={closeSheet}
        onUp={() => act((path) => move(where, path, -1), groupPath ? 'close' : 'stay')}
        onDown={() => act((path) => move(where, path, 1), groupPath ? 'close' : 'stay')}
        onDuplicate={() => act((path) => duplicate(where, path), 'close')}
        onDelete={() => act((path) => remove(where, path), 'close')}
        onKind={(kind: GroupKind) => groupPath && apply(setGroupKind(where, groupPath, kind))}
      />
    </div>
  )
}

export { withOperator }
