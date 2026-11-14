import { Layers3 } from 'lucide-react'
import { Link } from 'react-router'
import { chainLinkOf } from '@/sudarshan/options/words'

/**
 * "Chain" in the phone chart's app bar (O9): the option chain of what is charted. An index opens
 * its own chain, an option its underlying's on that expiry and strike, a future its underlying's.
 * Nothing is drawn for an instrument whose chain cannot be told from its name (a share).
 */
export function ChainLink({ symbol, exchange }: { symbol: string; exchange: string }) {
  const to = chainLinkOf(symbol, exchange)
  if (!to) return null
  return (
    <Link
      to={to}
      aria-label="Option chain"
      data-chart-chain="true"
      className="inline-flex size-12 shrink-0 items-center justify-center rounded-full hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <Layers3 aria-hidden="true" className="size-5" />
    </Link>
  )
}
