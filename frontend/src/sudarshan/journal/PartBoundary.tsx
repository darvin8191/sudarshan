// SUDARSHAN review M6 (v0.11.0): one part of the Journal or Reports page that fails to draw (an
// answer missing a field, say, while the service and the page are of different releases for a
// moment during a deploy) shows a sentence and Try again in its place, and the rest of the app
// stays. What failed is reported as the app's own boundary reports it, and never shown.
import { RefreshCw, RotateCw, TriangleAlert } from 'lucide-react'
import { Component, type ErrorInfo, type ReactNode } from 'react'
import { reportClientError } from '@/utils/errorReporter'

export const PART_FAILED =
  'This part of the page could not be shown. Try again, or reload the page.'

const BUTTON =
  'inline-flex min-h-12 items-center justify-center gap-2 rounded-[0.875rem] border border-input bg-card px-4 text-[0.9375rem] font-semibold outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50'

interface Props {
  children: ReactNode
  /** Asked before the part is drawn again: read its answers afresh. */
  onRetry?: () => void
}

export class PartBoundary extends Component<Props, { failed: boolean }> {
  state = { failed: false }

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    reportClientError({
      message: error.message || 'Render error',
      stack: error.stack,
      component_stack: info.componentStack || undefined,
    })
  }

  render(): ReactNode {
    if (!this.state.failed) return this.props.children
    return (
      <div
        role="alert"
        className="rounded-[1.125rem] border bg-card px-4 py-6 text-center shadow-[var(--shadow-card)]"
      >
        <TriangleAlert aria-hidden="true" className="mx-auto mb-2 size-6" />
        <p className="text-base font-bold">{PART_FAILED}</p>
        <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
          <button
            type="button"
            className={BUTTON}
            onClick={() => {
              this.props.onRetry?.()
              this.setState({ failed: false })
            }}
          >
            <RefreshCw aria-hidden="true" className="size-4" />
            Try again
          </button>
          <button type="button" className={BUTTON} onClick={() => window.location.reload()}>
            <RotateCw aria-hidden="true" className="size-4" />
            Reload the page
          </button>
        </div>
      </div>
    )
  }
}
