/**
 * What a market-data frame carries besides the price.
 *
 * The chart engine's stream client reduces a depth frame to the book, the
 * last price and a time, which is all a ladder needs. A live bar needs more:
 * the day's cumulative volume, so the forming candle's volume can move with
 * every tick, plus the last traded quantity, open interest and the previous
 * close where the feed sends them. This reads those fields off the same frames
 * the engine reads, through the socket factory the client already accepts, so
 * there is still one socket and one subscription per chart.
 *
 * Every field is optional and absence is kept distinct from zero: a feed that
 * does not send the day's volume leaves it out, and the live bar then falls
 * back to history for it rather than drawing a volume of nothing.
 */
import type { SocketLike } from 'openalgo-charts'

/** The extra fields of one frame, each present only when the frame carried it. */
export interface FrameExtras {
  /** The day's cumulative traded quantity. */
  dayVolume?: number
  /** The quantity of the last trade. */
  ltq?: number
  oi?: number
  /** The previous session's close. */
  prevClose?: number
  /** The exchange's time of the last trade, in UTC seconds. */
  tradeTime?: number
}

/** A number, or a numeric string, that is finite. */
function numeric(value: unknown): number | undefined {
  if (typeof value === 'number') return Number.isFinite(value) ? value : undefined
  if (typeof value === 'string' && value.trim() !== '') {
    const n = Number(value)
    return Number.isFinite(n) ? n : undefined
  }
  return undefined
}

function count(value: unknown): number | undefined {
  const n = numeric(value)
  return n !== undefined && n >= 0 ? n : undefined
}

function positive(value: unknown): number | undefined {
  const n = numeric(value)
  return n !== undefined && n > 0 ? n : undefined
}

/** Epoch seconds from seconds or milliseconds. */
function seconds(value: unknown): number | undefined {
  const n = positive(value)
  if (n === undefined) return undefined
  return n > 1e12 ? Math.floor(n / 1000) : Math.floor(n)
}

/** The `SYMBOL.EXCHANGE` topic some proxies put on the envelope instead. */
function topicParts(topic: unknown): { symbol: string; exchange: string } | null {
  if (typeof topic !== 'string') return null
  const parts = topic.split('.')
  if (parts.length !== 2 || !parts[0] || !parts[1]) return null
  return { symbol: parts[0], exchange: parts[1] }
}

/**
 * Read one inbound frame. Returns the instrument and its extras for a market
 * data frame, and null for anything else (acknowledgements, order updates,
 * pings, malformed text). Never throws.
 */
export function readFrameExtras(
  raw: unknown
): { symbol: string; exchange: string; extras: FrameExtras } | null {
  let message: unknown = raw
  if (typeof raw === 'string') {
    try {
      message = JSON.parse(raw)
    } catch {
      return null
    }
  }
  if (!message || typeof message !== 'object') return null
  const envelope = message as Record<string, unknown>
  const type = typeof envelope.type === 'string' ? envelope.type : ''
  if (type && type !== 'market_data') return null
  const data = (
    envelope.data && typeof envelope.data === 'object' ? envelope.data : envelope
  ) as Record<string, unknown>
  const topic = topicParts(envelope.topic)
  const symbol = String(data.symbol || envelope.symbol || topic?.symbol || '')
  const exchange = String(data.exchange || envelope.exchange || topic?.exchange || '')
  if (!symbol || !exchange) return null
  const extras: FrameExtras = {}
  const dayVolume = count(data.volume)
  if (dayVolume !== undefined) extras.dayVolume = dayVolume
  const ltq = count(data.last_trade_quantity ?? data.ltq)
  if (ltq !== undefined) extras.ltq = ltq
  const oi = count(data.oi)
  if (oi !== undefined) extras.oi = oi
  const prevClose = positive(data.prev_close)
  if (prevClose !== undefined) extras.prevClose = prevClose
  const tradeTime = seconds(data.last_trade_time)
  if (tradeTime !== undefined) extras.tradeTime = tradeTime
  return { symbol, exchange, extras }
}

/**
 * Hands the stream client a socket that reads each frame on its way past.
 *
 * The client calls the socket's message handler synchronously, and its tick
 * and depth callbacks run inside that call, so `get()` from a callback returns
 * the extras of the very frame that callback is reporting.
 */
export class FrameExtrasTap {
  private readonly latest = new Map<string, FrameExtras>()

  /** The latest extras for an instrument, or undefined before any frame for it. */
  get(symbol: string, exchange: string): FrameExtras | undefined {
    return this.latest.get(`${exchange}:${symbol}`)
  }

  /** Forget everything read, as for a fresh connection. */
  clear(): void {
    this.latest.clear()
  }

  /** Record one frame. */
  read(raw: unknown): void {
    const frame = readFrameExtras(raw)
    if (frame) this.latest.set(`${frame.exchange}:${frame.symbol}`, frame.extras)
  }

  /** A factory for the stream client: a browser socket whose frames pass through `read` first. */
  readonly socketFactory = (url: string): SocketLike => {
    const ws = new WebSocket(url)
    const socket: SocketLike = {
      send: (data: string) => ws.send(data),
      close: () => ws.close(),
      onopen: null,
      onclose: null,
      onerror: null,
      onmessage: null,
      get readyState() {
        return ws.readyState
      },
    }
    ws.onopen = () => socket.onopen?.()
    ws.onclose = () => socket.onclose?.()
    ws.onerror = () => socket.onerror?.()
    ws.onmessage = (event: MessageEvent) => {
      this.read(event.data)
      socket.onmessage?.({ data: event.data })
    }
    return socket
  }
}
