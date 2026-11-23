import { useEffect, useState } from 'react'
import { useTextSize } from '@/sudarshan/displaySize'
import { columnsFor, type FigureCell, specOf } from './figures'

/** Grey for a figure that has stopped arriving, as the price beside it. */
const STALE = 'text-[color:var(--stale)]'
const LABEL = 'text-[color:var(--text-tertiary)]'
const VALUE = 'text-[color:var(--text-primary)]'

/** The size of a rem now (Larger text makes it 18 px). */
function remPx(): number {
  if (typeof document === 'undefined') return 16
  const size = Number.parseFloat(getComputedStyle(document.documentElement).fontSize)
  return size > 0 ? size : 16
}

/**
 * M8.3 GA6.2: how many columns of figures the open list's width holds, measured on the list itself
 * (a phone upright gives one, on its side three), again whenever it is resized or the text size
 * changes. One observer for the list, released with it.
 */
export function useFigureColumns(): {
  ref: (node: HTMLElement | null) => void
  columns: number
  node: HTMLElement | null
  /** Changes with the list's width and the text size: what fits is counted again. */
  size: string
} {
  const text = useTextSize()
  const [node, setNode] = useState<HTMLElement | null>(null)
  const [width, setWidth] = useState(0)
  // biome-ignore lint/correctness/useExhaustiveDependencies: `text` changes the rem; the list is measured again
  useEffect(() => {
    if (!node) return
    const measure = () => setWidth(node.getBoundingClientRect().width)
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(measure)
    observer.observe(node)
    return () => observer.disconnect()
  }, [node, text])
  const rem = remPx()
  return { ref: setNode, columns: columnsFor(width, rem), node, size: `${width}/${rem}` }
}

/** How many columns of figures the rows draw (one that does not fit is not drawn): the fewest and the most. */
export interface Drawn {
  least: number
  most: number
}

/** Counted on the rows as laid out; null where nothing is laid out yet (or no row has figures). */
export function drawnColumns(list: HTMLElement | null): Drawn | null {
  const blocks = list ? [...list.querySelectorAll<HTMLElement>('[data-figures]')] : []
  if (blocks.length === 0 || blocks[0].clientHeight === 0) return null
  const counts = blocks.map(
    (block) =>
      [...block.querySelectorAll<HTMLElement>('[data-figure-column]')].filter(
        (column) => column.offsetTop < block.clientHeight
      ).length
  )
  return { least: Math.min(...counts), most: Math.max(...counts) }
}

/**
 * A row's figures, in columns of two (the first over the second), between the name and the price.
 * A column that does not fit whole wraps below the first line, which is all that is drawn, so a
 * number is never cut (the zero-width first item lets even the first column go). Label in ink-3,
 * value in ink, no colour; grey with the row when it is stale or not followed. A figure that does
 * not apply to the row is blank, and said so to a screen reader.
 */
export function FigureCells({ cells, grey }: { cells: readonly FigureCell[]; grey: boolean }) {
  if (cells.length === 0) return null
  const columns: FigureCell[][] = []
  for (let i = 0; i < cells.length; i += 2) columns.push(cells.slice(i, i + 2))
  return (
    <span
      data-figures="true"
      className="relative -ml-3 flex h-8 min-w-0 shrink flex-wrap content-start justify-end overflow-hidden"
    >
      {/* As tall as a column, so a column that wraps lands below the first line, out of sight. */}
      <span aria-hidden="true" className="h-8 w-0 shrink-0" />
      {columns.map((column) => (
        <span key={column[0].id} data-figure-column="true" className="ml-3 flex flex-col items-end">
          {column.map((cell) => (
            <span
              key={cell.id}
              data-figure={cell.id}
              data-state={cell.state}
              className="flex h-4 items-baseline gap-1 text-xs leading-4 font-semibold whitespace-nowrap tabular-nums"
            >
              <span className="sr-only">{cell.speech}</span>
              {cell.state !== 'none' && (
                <>
                  <span aria-hidden="true" className={grey ? STALE : LABEL}>
                    {specOf(cell.id).label}
                  </span>
                  <span aria-hidden="true" className={grey ? STALE : VALUE}>
                    {cell.text ?? '—'}
                  </span>
                </>
              )}
            </span>
          ))}
        </span>
      ))}
    </span>
  )
}
