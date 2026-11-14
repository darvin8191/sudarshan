import { Wallet } from 'lucide-react'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { apiClient } from '@/api/client'
import { formatRupees } from '@/sudarshan/dashboard/format'

/**
 * The order ticket's margin line (B5: M5.9): "Margin needed ₹x · available ₹y". The margin is
 * the app's own calculator (`POST /api/v1/margin`, Upstox's) for the order as it stands in the
 * ticket, read when the ticket opens and again whenever its side, quantity, price, type or
 * product changes, 400 ms after the last change and one read at a time. The funds
 * (`POST /api/v1/funds`) are read once when the ticket opens. This is not market data, so a
 * read on change is within the real-time rule.
 *
 * What could not be read says "not read", never ₹0: Upstox's funds answer with zeros outside
 * their hours (00:00 to 05:30), and an empty answer is not a balance of nothing. The line only
 * informs: it never holds the ticket's button back.
 */

/** How long after the last change the margin is read again. */
export const MARGIN_SETTLE_MS = 400

/** How long one read may take before it counts as not read. */
const READ_TIMEOUT_MS = 10_000

export interface MarginOrder {
  symbol: string
  exchange: string
  action: 'BUY' | 'SELL'
  quantity: number
  pricetype: 'MARKET' | 'LIMIT' | 'SL' | 'SL-M'
  product: string
  price: number
  triggerPrice: number
}

/** Whether the ticket holds an order the calculator can price: what it needs is filled in. */
export function priceable(order: MarginOrder): boolean {
  if (!order.symbol || !order.exchange || !(order.quantity > 0)) return false
  if ((order.pricetype === 'LIMIT' || order.pricetype === 'SL') && !(order.price > 0)) return false
  if ((order.pricetype === 'SL' || order.pricetype === 'SL-M') && !(order.triggerPrice > 0)) {
    return false
  }
  return true
}

/** A rupee figure from an answer, or null when it gives none. */
function rupees(value: unknown): number | null {
  const number = typeof value === 'string' ? Number(value) : value
  return typeof number === 'number' && Number.isFinite(number) ? number : null
}

/** The margin the order needs, or null when it could not be read. */
export async function readMargin(apiKey: string, order: MarginOrder): Promise<number | null> {
  try {
    const response = await apiClient.post(
      '/margin',
      {
        apikey: apiKey,
        positions: [
          {
            symbol: order.symbol,
            exchange: order.exchange,
            action: order.action,
            quantity: String(order.quantity),
            product: order.product,
            pricetype: order.pricetype,
            price: String(
              order.pricetype === 'LIMIT' || order.pricetype === 'SL' ? order.price : 0
            ),
            trigger_price: String(order.triggerPrice || 0),
          },
        ],
      },
      { timeout: READ_TIMEOUT_MS }
    )
    const body = response.data as { status?: unknown; data?: { total_margin_required?: unknown } }
    if (body?.status !== 'success') return null
    const needed = rupees(body.data?.total_margin_required)
    return needed !== null && needed >= 0 ? needed : null
  } catch {
    return null
  }
}

/**
 * The cash available for a new order, or null when it could not be read. An answer of all
 * zeros is Upstox's out-of-hours stand-in, and an empty one a failed read: neither is a balance.
 */
export async function readAvailable(apiKey: string): Promise<number | null> {
  try {
    const response = await apiClient.post(
      '/funds',
      { apikey: apiKey },
      { timeout: READ_TIMEOUT_MS }
    )
    const body = response.data as { status?: unknown; data?: Record<string, unknown> }
    if (body?.status !== 'success' || !body.data) return null
    const figures = [
      'availablecash',
      'collateral',
      'utiliseddebits',
      'm2mrealized',
      'm2munrealized',
    ]
      .map((name) => rupees(body.data?.[name]))
      .filter((value): value is number => value !== null)
    if (figures.length === 0 || figures.every((value) => value === 0)) return null
    return rupees(body.data.availablecash)
  } catch {
    return null
  }
}

/** One read of the margin, for the order as it stood when asked. */
interface MarginRead {
  key: string
  value: number | null
}

export function MarginLine({
  apiKey,
  order,
  sandbox,
}: {
  apiKey: string | null
  order: MarginOrder
  /** Analyze mode: the funds are the sandbox's, and the line says so. */
  sandbox: boolean
}) {
  const key = JSON.stringify(order)
  const ready = apiKey !== null && priceable(order)
  const [margin, setMargin] = useState<MarginRead | null>(null)
  const [available, setAvailable] = useState<number | null | 'reading'>('reading')

  // The order as it stands, for the read that runs after the wait.
  const latest = useRef({ apiKey, order, key })
  useLayoutEffect(() => {
    latest.current = { apiKey, order, key }
  })
  const alive = useRef(true)
  const reading = useRef(false)
  const again = useRef(false)

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  useEffect(() => {
    if (apiKey === null) return
    let current = true
    setAvailable('reading')
    void readAvailable(apiKey).then((value) => {
      if (current) setAvailable(value)
    })
    return () => {
      current = false
    }
  }, [apiKey])

  // biome-ignore lint/correctness/useExhaustiveDependencies: `key` stands for the whole order
  useEffect(() => {
    if (!ready) return
    const timer = setTimeout(async () => {
      if (reading.current) {
        again.current = true
        return
      }
      reading.current = true
      try {
        do {
          again.current = false
          const asked = latest.current
          if (asked.apiKey === null || !priceable(asked.order)) break
          const value = await readMargin(asked.apiKey, asked.order)
          if (!alive.current) return
          setMargin({ key: asked.key, value })
        } while (again.current)
      } finally {
        reading.current = false
      }
    }, MARGIN_SETTLE_MS)
    return () => clearTimeout(timer)
  }, [key, ready])

  const fresh = margin !== null && margin.key === key
  let needed: string
  let updating = false
  if (!ready) needed = '-'
  else if (fresh) needed = margin.value === null ? 'not read' : formatRupees(margin.value)
  else if (margin?.value != null) {
    needed = formatRupees(margin.value)
    updating = true
  } else needed = 'reading'
  const funds =
    available === 'reading' ? 'reading' : available === null ? 'not read' : formatRupees(available)

  return (
    <p
      data-testid="margin-line"
      className="flex items-start gap-2 rounded-lg bg-[color:var(--surface-2)] px-3 py-2 text-sm"
    >
      <Wallet aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
      <span className="min-w-0">
        <span className="text-[color:var(--text-secondary)]">Margin needed </span>
        <span className="font-semibold">{needed}</span>
        {updating && <span className="text-[color:var(--text-tertiary)]"> updating</span>}
        <span className="text-[color:var(--text-secondary)]">
          {sandbox ? ' · available in the sandbox ' : ' · available '}
        </span>
        <span className="font-semibold">{funds}</span>
      </span>
    </p>
  )
}
