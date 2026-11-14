// SUDARSHAN M12.1 (P5): on upstream's TradingView page, which stays for one-off line alerts and
// puts the API key in its message. Strategies belong on the Automation page.
import { ChevronRight, Workflow } from 'lucide-react'
import { Link } from 'react-router'
import { AUTOMATION_PAGE } from './types'

export function TradingViewNotice() {
  return (
    <Link
      to={AUTOMATION_PAGE}
      data-testid="tv-automation-notice"
      className="mb-8 flex min-h-12 items-center gap-3 rounded-xl border border-[color:var(--brand)] bg-[color:var(--brand-tint)] px-4 py-3 text-[0.9375rem] font-semibold text-[color:var(--brand)]"
    >
      <Workflow aria-hidden="true" className="size-5 shrink-0" />
      <span className="min-w-0 flex-1">
        For a TradingView strategy, use Automation: its alert message carries no API key, each
        strategy has its own switch, and it runs in the Sandbox first.
      </span>
      <ChevronRight aria-hidden="true" className="size-5 shrink-0" />
    </Link>
  )
}
