import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

/**
 * SUDARSHAN M1.21 (T4): no pulsing or pinging mark in the terminal, the order ticket it opens or
 * the tools pages. The owner's rule is minimal motion: a state is a still mark beside its word,
 * and a mark that throbs reads as an alarm on a screen a trader keeps open all session. A
 * skeleton's shimmer belongs to the shared Skeleton component, which this does not look at.
 */
const SRC = join(process.cwd(), 'src')

/** Every page the tools registry (lib/tools.ts) opens, and the tools page itself. */
const TOOL_PAGES = [
  'Tools',
  'StrategyBuilder',
  'StrategyPortfolio',
  'PortfolioBacktester',
  'PortfolioBacktesterResults',
  'SipBacktester',
  'SipBacktesterResults',
  'PortfolioAnalyzer',
  'OptionChain',
  'IVChart',
  'OITracker',
  'OIRange',
  'MaxPain',
  'StraddleChart',
  'CustomStraddle',
  'VolSurface',
  'GEXDashboard',
  'GammaDensity',
  'IVSmile',
  'OIProfile',
  'Arbitrage',
]

const SCOPE = [
  join(SRC, 'components', 'trading'),
  join(SRC, 'lib', 'trading'),
  join(SRC, 'pages', 'Trading.tsx'),
  join(SRC, 'components', 'strategy-builder'),
  join(SRC, 'components', 'option-chain'),
  join(SRC, 'components', 'portfolio'),
  ...TOOL_PAGES.map((page) => join(SRC, 'pages', `${page}.tsx`)),
]

function sources(path: string): string[] {
  if (statSync(path).isFile()) return [path]
  return readdirSync(path, { withFileTypes: true }).flatMap((entry) => {
    const child = join(path, entry.name)
    if (entry.isDirectory()) return sources(child)
    if (!/\.(ts|tsx)$/.test(entry.name) || /\.test\.tsx?$/.test(entry.name)) return []
    return [child]
  })
}

describe('motion in the terminal and the tools', () => {
  const files = SCOPE.flatMap(sources)

  it('reads every source file in scope', () => {
    expect(files.length).toBeGreaterThan(50)
  })

  it('has no pulsing or pinging mark', () => {
    const found = files.flatMap((file) =>
      readFileSync(file, 'utf8')
        .split('\n')
        .flatMap((line, index) =>
          /\banimate-(pulse|ping)\b/.test(line)
            ? [`${file.slice(SRC.length + 1)}:${index + 1}`]
            : []
        )
    )
    expect(found).toEqual([])
  })
})
