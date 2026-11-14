/**
 * What the phone chart downloads (design 2.5): the engine's base tier with the
 * page, the indicator, drawing and transform tiers only when first used, and
 * the trade, widget and profile tiers never. A static import of any of them
 * puts its bytes in the first download of every phone chart, so each is held
 * here to a dynamic `import()`, in every non-test file of the chart folder and
 * in the terminal modules those files import.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const SRC = resolve(__dirname, '../..')
const CHART = resolve(__dirname)

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) return name === 'testing' ? [] : sources(path)
    return /\.(ts|tsx)$/.test(name) && !/\.test\.(ts|tsx)$/.test(name) ? [path] : []
  })
}

/** Module names a file imports statically (import ... from, or a bare import). */
function staticImports(text: string): string[] {
  const found: string[] = []
  for (const match of text.matchAll(
    /^\s*import\s+(?!type\b)(?:[^'"]*?\s+from\s+)?['"]([^'"]+)['"]/gm
  ))
    found.push(match[1])
  return found
}

const LATER = /^openalgo-charts\/(indicators|draw|transform)$/
const NEVER = /^openalgo-charts\/(trade|widget|profile|workspace)$/
/** Terminal modules that load a later tier statically themselves. */
const HEAVY = ['@/lib/trading/chartTypes', '@/lib/trading/terminal']

describe('the tiers the phone chart loads', () => {
  const files = sources(CHART)
  const terminalModules = [
    ...new Set(
      files.flatMap((file) =>
        staticImports(readFileSync(file, 'utf8')).filter((name) =>
          name.startsWith('@/lib/trading/')
        )
      )
    ),
  ]

  it("finds the chart folder's files", () => {
    expect(files.some((file) => file.endsWith('phoneChart.ts'))).toBe(true)
  })

  it.each(
    files.map((file) => [file.slice(CHART.length + 1), file])
  )('%s loads no tier but the base before it is used', (_name, file) => {
    const names = staticImports(readFileSync(file, 'utf8'))
    expect(names.filter((name) => LATER.test(name) || NEVER.test(name))).toEqual([])
    expect(names.filter((name) => HEAVY.includes(name))).toEqual([])
  })

  it('imports no terminal module that loads a tier itself', () => {
    for (const name of terminalModules) {
      const base = join(SRC, name.slice(2))
      const file = [`${base}.ts`, `${base}.tsx`].find((path) => {
        try {
          return statSync(path).isFile()
        } catch {
          return false
        }
      })
      if (!file) continue
      const names = staticImports(readFileSync(file, 'utf8'))
      expect(
        names.filter((n) => LATER.test(n) || NEVER.test(n)),
        name
      ).toEqual([])
    }
  })
})
