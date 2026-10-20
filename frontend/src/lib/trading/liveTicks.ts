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
  /**
   * The exchange's time of the last trade, in UTC seconds: `last_trade_time`,
   * or the short `ltt` a price-only frame carries instead.
   */
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

/** A frame's text as an object, or the object itself; null for anything else. Never throws. */
function parsed(raw: unknown): Record<string, unknown> | null {
  let message: unknown = raw
  if (typeof raw === 'string') {
    try {
      message = JSON.parse(raw)
    } catch {
      return null
    }
  }
  return message && typeof message === 'object' ? (message as Record<string, unknown>) : null
}

/**
 * Read one inbound frame. Returns the instrument and its extras for a market
 * data frame, and null for anything else (acknowledgements, order updates,
 * pings, malformed text). Never throws.
 */
export function readFrameExtras(
  raw: unknown
): { symbol: string; exchange: string; extras: FrameExtras } | null {
  const message = parsed(raw)
  if (!message) return null
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
  const tradeTime = seconds(data.last_trade_time ?? data.ltt)
  if (tradeTime !== undefined) extras.tradeTime = tradeTime
  return { symbol, exchange, extras }
}

/** When the server stamped a market-data frame, in milliseconds, or undefined. */
function frameStamp(message: Record<string, unknown>): number | undefined {
  if (message.type !== 'market_data') return undefined
  const data = (
    message.data && typeof message.data === 'object' ? message.data : message
  ) as Record<string, unknown>
  const at = positive(data.timestamp)
  if (at === undefined) return undefined
  return at > 1e12 ? at : at * 1000
}

/** A reading further than this from the phone's clock is a field that is not a clock. */
export const MAX_CLOCK_SKEW_MS = 10 * 60_000

/** How long a frame's stamp counts toward the server clock. */
const STAMP_WINDOW_MS = 60_000

/**
 * The server's clock, as its frames show it.
 *
 * A phone's clock can be well off, and a candle must open when the exchange's
 * minute turns, not the phone's. Two readings give the difference. A ping's
 * answer carries the server's time, taken between sending and receiving, so
 * the half-way point is within half a round trip of the truth. A market-data
 * frame is stamped before it arrives, so its stamp less the arrival time is a
 * floor under the difference. The estimate is the higher of the answer and
 * the last minute's best floor; with neither, the phone's own clock stands.
 */
export class ServerClock {
  private readonly local: () => number
  /** Server less phone, in milliseconds, from the last ping answered. */
  private answered: number | null = null
  /** The best floor under that difference from recent frames, and when it was read. */
  private floor: { offset: number; at: number } | null = null
  /** The estimate listeners last heard. */
  private told = 0
  private readonly listeners = new Set<() => void>()

  constructor(local: () => number = () => Date.now()) {
    this.local = local
  }

  /**
   * Hear when the estimate moves by more than a quarter of a second: a timer
   * aimed by the old one (the next candle's open) is aimed again.
   */
  onChange(listener: () => void): () => void {
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  /** A frame the server stamped `stampMs` arrived at `arrivedAt` (phone milliseconds). */
  noteStamp(stampMs: number, arrivedAt = this.local()): void {
    const offset = stampMs - arrivedAt
    if (!Number.isFinite(offset) || Math.abs(offset) > MAX_CLOCK_SKEW_MS) return
    const floor = this.floor
    if (!floor || offset >= floor.offset || arrivedAt - floor.at > STAMP_WINDOW_MS)
      this.floor = { offset, at: arrivedAt }
    this.tell()
  }

  /** A ping sent at `sentAt` came back at `arrivedAt` carrying the server's time. */
  notePong(serverMs: number, sentAt: number, arrivedAt = this.local()): void {
    if (!(arrivedAt >= sentAt)) return
    const offset = serverMs - (sentAt + arrivedAt) / 2
    if (!Number.isFinite(offset) || Math.abs(offset) > MAX_CLOCK_SKEW_MS) return
    this.answered = offset
    this.tell()
  }

  private tell(): void {
    const offset = this.offsetMs()
    if (Math.abs(offset - this.told) <= 250) return
    this.told = offset
    for (const listener of [...this.listeners]) listener()
  }

  /** Server less phone, in milliseconds. */
  offsetMs(): number {
    const now = this.local()
    const floor = this.floor && now - this.floor.at <= STAMP_WINDOW_MS ? this.floor.offset : null
    if (this.answered === null) return floor ?? 0
    return floor === null ? this.answered : Math.max(this.answered, floor)
  }

  /** The server's time, in milliseconds. */
  now(): number {
    return this.local() + this.offsetMs()
  }

  /** The server's time, in UTC seconds, with the fraction. */
  seconds(): number {
    return this.now() / 1000
  }

  /** The server's time, in whole UTC seconds. */
  nowSec(): number {
    return Math.floor(this.now() / 1000)
  }
}

/**
 * Away from the page longer than this, and a chart tests its stream on coming
 * back. A phone that slept can hold a socket that is open on paper and dead in
 * fact, and the stream client's own watchdog takes most of a minute to find
 * out.
 */
export const WAKE_RECONNECT_MS = 5000

/** How long a ping on waking may go unanswered before the socket is taken for dead. */
export const PROBE_TIMEOUT_MS = 1500

/**
 * Hands the stream client a socket that reads each frame on its way past.
 *
 * The client calls the socket's message handler synchronously, and its tick
 * and depth callbacks run inside that call, so `get()` from a callback returns
 * the extras of the very frame that callback is reporting.
 */
export class FrameExtrasTap {
  /** The server's clock, read off the frames and the answers to pings. */
  readonly clock = new ServerClock()
  /** Wall clock, in milliseconds, of the last frame of any kind on any socket. */
  lastFrameAt: number | null = null
  private readonly latest = new Map<string, FrameExtras>()
  /** One per socket still open, each closing its socket as a dead connection would. */
  private readonly droppers = new Set<() => void>()
  /** Sockets that have opened and not closed: where a ping goes. */
  private readonly open = new Set<WebSocket>()
  /** Probes waiting for the next frame. */
  private readonly waiters = new Set<() => void>()

  /** The latest extras for an instrument, or undefined before any frame for it. */
  get(symbol: string, exchange: string): FrameExtras | undefined {
    return this.latest.get(`${exchange}:${symbol}`)
  }

  /** Forget everything read, as for a fresh connection. */
  clear(): void {
    this.latest.clear()
  }

  /**
   * Record one frame. A field the frame leaves out keeps its last value: a
   * price-only update reaching a book subscription carries no volume or open
   * interest, and the bar must not lose them for it. The last trade's
   * quantity is the exception, since it describes that one trade only.
   */
  read(raw: unknown): void {
    const at = Date.now()
    this.lastFrameAt = at
    for (const wake of [...this.waiters]) wake()
    const message = parsed(raw)
    if (!message) return
    if (message.type === 'pong') {
      const server = positive(message.server_timestamp)
      const sent = positive(message.client_timestamp)
      if (server !== undefined && sent !== undefined) this.clock.notePong(server, sent, at)
      return
    }
    const stamp = frameStamp(message)
    if (stamp !== undefined) this.clock.noteStamp(stamp, at)
    const frame = readFrameExtras(message)
    if (!frame) return
    const key = `${frame.exchange}:${frame.symbol}`
    const kept: FrameExtras = { ...this.latest.get(key) }
    delete kept.ltq
    this.latest.set(key, { ...kept, ...frame.extras })
  }

  /**
   * Ask the server for its time on every open socket. The answer sets the
   * clock, and any frame proves the socket alive. False when no socket is
   * open to ask.
   */
  ping(): boolean {
    const sentAt = Date.now()
    const message = JSON.stringify({
      action: 'ping',
      _pingId: `chart-${sentAt}`,
      timestamp: sentAt,
    })
    let sent = false
    for (const ws of this.open) {
      if (ws.readyState !== 1) continue
      try {
        ws.send(message)
        sent = true
      } catch {
        // Closing.
      }
    }
    return sent
  }

  /**
   * Whether the connection still answers: a ping goes out, and any frame back
   * within `timeoutMs` (its answer, a tick, anything) says yes. Silence, or no
   * open socket to ask, says no.
   */
  probe(timeoutMs = PROBE_TIMEOUT_MS): Promise<boolean> {
    return new Promise((resolve) => {
      let timer: ReturnType<typeof setTimeout> | null = null
      const finish = (alive: boolean) => {
        if (!this.waiters.delete(answer)) return
        if (timer) clearTimeout(timer)
        resolve(alive)
      }
      const answer = () => finish(true)
      this.waiters.add(answer)
      if (!this.ping()) {
        finish(false)
        return
      }
      timer = setTimeout(() => finish(false), timeoutMs)
    })
  }

  /**
   * Close every socket this tap opened, at once, as a connection that died
   * would close. The stream client takes it for an unexpected close, so it
   * reconnects within a second and subscribes everything it had again: what
   * a chart wants after the phone wakes, without knowing its subscriptions.
   * The client is told now, not when the browser finishes closing a socket
   * whose far end may be gone.
   */
  drop(): void {
    for (const drop of [...this.droppers]) drop()
  }

  /** A factory for the stream client: a browser socket whose frames pass through `read` first. */
  readonly socketFactory = (url: string): SocketLike => {
    const ws = new WebSocket(url)
    let closed = false
    const socket: SocketLike = {
      send: (data: string) => ws.send(data),
      close: () => ws.close(),
      onopen: null,
      onclose: null,
      onerror: null,
      onmessage: null,
      get readyState() {
        // 3 is CLOSED, read without the constructor a test may have replaced.
        return closed ? 3 : ws.readyState
      },
    }
    const finish = () => {
      if (closed) return
      closed = true
      this.droppers.delete(drop)
      this.open.delete(ws)
      socket.onclose?.()
    }
    const drop = () => {
      ws.onopen = null
      ws.onmessage = null
      ws.onerror = null
      ws.onclose = null
      try {
        ws.close()
      } catch {
        // Already closing.
      }
      finish()
    }
    this.droppers.add(drop)
    ws.onopen = () => {
      this.open.add(ws)
      socket.onopen?.()
    }
    ws.onclose = finish
    ws.onerror = () => socket.onerror?.()
    ws.onmessage = (event: MessageEvent) => {
      this.read(event.data)
      socket.onmessage?.({ data: event.data })
    }
    return socket
  }
}
