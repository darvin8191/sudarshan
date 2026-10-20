/**
 * The product an order ticket opens with, remembered per device and per
 * segment (M8.2). The owner trades intraday F&O: a ticket that opens on NRML
 * has to be switched to MIS every time, and forgetting it means no automatic
 * square-off. Once he picks a product for a segment, every ticket for that
 * segment opens with it on this phone.
 *
 * Kept in this browser's storage: it is how this device opens a ticket, not
 * something the account holds. Storage can be missing or blocked (a private
 * window, cleared site data); then the choice lasts for the visit and the
 * ticket falls back to its own default. Which products a segment takes stays
 * the ticket's business (`PlaceOrderDialog` checks it against the exchange);
 * this only offers the ones it accepts.
 */
export type Product = 'MIS' | 'NRML' | 'CNC'

export type Segment = 'fno' | 'cash' | 'mcx' | 'cds'

const SEGMENT: Record<string, Segment> = {
  NFO: 'fno',
  BFO: 'fno',
  NSE: 'cash',
  BSE: 'cash',
  MCX: 'mcx',
  CDS: 'cds',
  BCD: 'cds',
}

/** The products an order ticket takes on each segment, the ticket's default first. */
const PRODUCTS: Record<Segment, readonly Product[]> = {
  fno: ['NRML', 'MIS'],
  cash: ['CNC', 'MIS'],
  mcx: ['NRML', 'MIS'],
  cds: ['NRML', 'MIS'],
}

/** The segment an exchange's orders belong to, or null for one that takes no orders here (an index). */
export function segmentOf(exchange: string): Segment | null {
  return SEGMENT[exchange.toUpperCase()] ?? null
}

/** The products a ticket for this exchange can open with, the ticket's own default first. */
export function productsFor(exchange: string): readonly Product[] {
  const segment = segmentOf(exchange)
  return segment ? PRODUCTS[segment] : []
}

export const STORAGE_KEY = 'sudarshan-ticket-product'

/** For a visit whose storage is blocked. */
let memory: Partial<Record<Segment, Product>> = {}

function stored(): Partial<Record<Segment, Product>> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    const parsed = raw ? (JSON.parse(raw) as unknown) : {}
    return parsed && typeof parsed === 'object' ? (parsed as Partial<Record<Segment, Product>>) : {}
  } catch {
    return memory
  }
}

/**
 * The product last picked for this exchange's segment on this device, or
 * undefined until one is picked, so the ticket keeps its own default.
 */
export function readProduct(exchange: string): Product | undefined {
  const segment = segmentOf(exchange)
  if (!segment) return undefined
  const product = stored()[segment]
  return product && PRODUCTS[segment].includes(product) ? product : undefined
}

/** Remember a pick for this exchange's segment. A product the segment does not take is ignored. */
export function rememberProduct(exchange: string, product: Product): void {
  const segment = segmentOf(exchange)
  if (!segment || !PRODUCTS[segment].includes(product)) return
  const next = { ...stored(), [segment]: product }
  memory = next
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
  } catch {
    // Storage refused: the pick lasts for the visit.
  }
}

/** What a product means to a trader, for the label beside it. */
export const PRODUCT_MEANING: Record<Product, string> = {
  MIS: 'Intraday, squared off by the broker',
  NRML: 'Carry forward',
  CNC: 'Delivery',
}
