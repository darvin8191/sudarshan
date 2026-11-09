/**
 * One Socket.IO connection for the signed-in tab: a provider that remounts in
 * the same commit (a page change, a move between /trading and a normal page)
 * keeps it; nothing left holding it closes it; and a connection that is meant
 * to be open and is not is asked to connect again.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { claimTrip, resetTripsForTests, TRIP_GAP_MS } from '@/sudarshan/trips'
import {
  acquireSharedSocket,
  connectionCount,
  goToLogin,
  REFUSED_RETRY_MS,
  RETRY_MS,
  releaseSharedSocket,
  SESSION_READ_TIMEOUT_MS,
  type SessionState,
  setRefusalHandlersForTests,
  signInNeededNow,
} from './sharedSocket'

type Handler = (...args: unknown[]) => void

class FakeSocket {
  connected = true
  active = true
  connects = 0
  disconnects = 0
  private handlers = new Map<string, Set<Handler>>()
  on(event: string, handler: Handler) {
    this.handlers.set(event, (this.handlers.get(event) ?? new Set()).add(handler))
    return this
  }
  off(event: string, handler: Handler) {
    this.handlers.get(event)?.delete(handler)
    return this
  }
  fire(event: string, ...args: unknown[]) {
    for (const handler of [...(this.handlers.get(event) ?? [])]) handler(...args)
  }
  /** What socket.io-client does with the server's CONNECT_ERROR: it stops for good, then tells. */
  refuse(data?: unknown) {
    this.connected = false
    this.active = false
    this.fire('connect_error', Object.assign(new Error('refused'), { data }))
  }
  connect() {
    this.connects += 1
    return this
  }
  disconnect() {
    this.disconnects += 1
    this.connected = false
    this.active = false
    return this
  }
}

const made: FakeSocket[] = []
const create = () => {
  const socket = new FakeSocket()
  made.push(socket)
  return socket as never
}

async function endOfTask() {
  await Promise.resolve()
}

beforeEach(() => {
  made.length = 0
})

afterEach(async () => {
  for (const socket of made) releaseSharedSocket(socket as never)
  await endOfTask()
  setRefusalHandlersForTests(null)
  resetTripsForTests()
  vi.useRealTimers()
})

describe('shared Socket.IO connection', () => {
  it('is kept by a provider that remounts in the same commit', async () => {
    const first = acquireSharedSocket(create)
    releaseSharedSocket(first)
    const second = acquireSharedSocket(create)
    await endOfTask()

    expect(second).toBe(first)
    expect(made).toHaveLength(1)
    expect(made[0].disconnects).toBe(0)
  })

  it('closes once nothing holds it at the end of the task', async () => {
    const socket = acquireSharedSocket(create)
    releaseSharedSocket(socket)
    await endOfTask()

    expect(made[0].disconnects).toBe(1)
    const next = acquireSharedSocket(create)
    expect(next).not.toBe(socket)
    expect(made).toHaveLength(2)
  })

  it('opens a new one when the one it holds was closed for good', () => {
    const socket = acquireSharedSocket(create)
    made[0].connected = false
    made[0].active = false

    expect(acquireSharedSocket(create)).not.toBe(socket)
  })

  it('asks a dropped connection to connect again every 10 s, and on coming back into view', () => {
    vi.useFakeTimers()
    acquireSharedSocket(create)
    const socket = made[0]
    socket.connected = false
    socket.fire('disconnect')

    vi.advanceTimersByTime(RETRY_MS)
    expect(socket.connects).toBe(1)
    vi.advanceTimersByTime(RETRY_MS)
    expect(socket.connects).toBe(2)

    document.dispatchEvent(new Event('visibilitychange'))
    expect(socket.connects).toBe(3)
    window.dispatchEvent(new Event('online'))
    expect(socket.connects).toBe(4)

    socket.connected = true
    socket.fire('connect')
    vi.advanceTimersByTime(RETRY_MS * 3)
    expect(socket.connects).toBe(4)
  })

  it('recovers by itself from a first connect that failed, past the point Socket.IO gives up', () => {
    vi.useFakeTimers()
    acquireSharedSocket(create)
    const socket = made[0]
    // A tab opened while the server restarts: never connected, every attempt failing.
    socket.connected = false
    for (let attempt = 0; attempt < 5; attempt++) socket.fire('connect_error')
    // Socket.IO's last attempt has failed and it has stopped; nothing else fires.
    vi.advanceTimersByTime(RETRY_MS)
    expect(socket.connects).toBe(1)
    vi.advanceTimersByTime(RETRY_MS * 5)
    expect(socket.connects).toBe(6)

    socket.connected = true
    socket.fire('connect')
    vi.advanceTimersByTime(RETRY_MS * 3)
    expect(socket.connects).toBe(6)
  })

  it('counts how often the connection has come up, so a book can tell a reconnect from the first connect', () => {
    const socket = acquireSharedSocket(create)
    expect(connectionCount(socket)).toBeUndefined()
    made[0].fire('connect')
    expect(connectionCount(socket)).toBe(1)
    made[0].fire('disconnect')
    made[0].fire('connect')
    expect(connectionCount(socket)).toBe(2)
  })
})

/** The refusal handlers with each pause at its listed length (no spread): random() 0.5. */
function handle(next: Parameters<typeof setRefusalHandlersForTests>[0]) {
  setRefusalHandlersForTests({ random: () => 0.5, ...next })
}

describe('a connect the server refuses (SIO1)', () => {
  it('is asked again after a growing pause while the session holds, from the first pause once up', async () => {
    vi.useFakeTimers()
    const askSession = vi.fn(async (): Promise<SessionState> => 'valid')
    const toLogin = vi.fn()
    handle({ askSession, toLogin })
    acquireSharedSocket(create)
    const socket = made[0]

    socket.refuse()
    await vi.advanceTimersByTimeAsync(REFUSED_RETRY_MS[0] - 1)
    expect(askSession).toHaveBeenCalledTimes(1)
    expect(socket.connects).toBe(0)
    await vi.advanceTimersByTimeAsync(1)
    expect(socket.connects).toBe(1)

    socket.refuse() // refused again: a longer pause
    await vi.advanceTimersByTimeAsync(REFUSED_RETRY_MS[1] - 1)
    expect(socket.connects).toBe(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(socket.connects).toBe(2)

    socket.connected = true
    socket.active = true
    socket.fire('connect')
    socket.refuse() // a refusal after a connect starts from the first pause again
    await vi.advanceTimersByTimeAsync(REFUSED_RETRY_MS[0])
    expect(socket.connects).toBe(3)
    expect(toLogin).not.toHaveBeenCalled()
  })

  it('that says retry is asked again after the pause without asking about the session', async () => {
    vi.useFakeTimers()
    const askSession = vi.fn(async (): Promise<SessionState> => 'ended')
    handle({ askSession, toLogin: vi.fn() })
    acquireSharedSocket(create)

    made[0].refuse({ retry: true })
    await vi.advanceTimersByTimeAsync(REFUSED_RETRY_MS[0])
    expect(made[0].connects).toBe(1)
    expect(askSession).not.toHaveBeenCalled()
  })

  it('for a session that has ended leads to the login page, as a 401 does, and is not asked again', async () => {
    vi.useFakeTimers()
    const toLogin = vi.fn()
    handle({ askSession: async () => 'ended', toLogin })
    acquireSharedSocket(create)

    made[0].refuse()
    await vi.advanceTimersByTimeAsync(0)
    expect(toLogin).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(REFUSED_RETRY_MS.reduce((sum, pause) => sum + pause, 0))
    expect(made[0].connects).toBe(0)
  })

  it('is asked again at once when the page comes back into view or the browser is online', async () => {
    vi.useFakeTimers()
    handle({ askSession: async () => 'unknown', toLogin: vi.fn() })
    acquireSharedSocket(create)
    const socket = made[0]

    socket.refuse()
    await vi.advanceTimersByTimeAsync(0)
    document.dispatchEvent(new Event('visibilitychange'))
    expect(socket.connects).toBe(1)

    socket.refuse()
    await vi.advanceTimersByTimeAsync(0)
    window.dispatchEvent(new Event('online'))
    expect(socket.connects).toBe(2)
  })

  it('closed by the server on purpose gets the same question, and a client drop does not', async () => {
    vi.useFakeTimers()
    const askSession = vi.fn(async (): Promise<SessionState> => 'ended')
    const toLogin = vi.fn()
    handle({ askSession, toLogin })
    acquireSharedSocket(create)
    const socket = made[0]

    socket.connected = false // a network drop: Socket.IO reconnects it, nothing is asked
    socket.fire('disconnect', 'transport close')
    await vi.advanceTimersByTimeAsync(0)
    expect(askSession).not.toHaveBeenCalled()

    socket.active = false // the server closed it (the rollover, a session's end)
    socket.fire('disconnect', 'io server disconnect')
    await vi.advanceTimersByTimeAsync(0)
    expect(askSession).toHaveBeenCalledTimes(1)
    expect(toLogin).toHaveBeenCalledTimes(1)
  })

  it('is left alone once the tab lets the connection go', async () => {
    vi.useFakeTimers()
    handle({ askSession: async () => 'valid', toLogin: vi.fn() })
    const socket = acquireSharedSocket(create)

    made[0].refuse()
    await vi.advanceTimersByTimeAsync(0)
    releaseSharedSocket(socket)
    await endOfTask()
    await vi.advanceTimersByTimeAsync(REFUSED_RETRY_MS[REFUSED_RETRY_MS.length - 1])
    expect(made[0].connects).toBe(0)
  })

  it('asks the server with a read behind the session check, which answers 401 once the session has ended', async () => {
    const fetchMock = vi.fn(async () => ({ ok: false, status: 401 }))
    vi.stubGlobal('fetch', fetchMock)
    const toLogin = vi.fn()
    handle({ toLogin }) // the real question, a stubbed answer
    try {
      acquireSharedSocket(create)
      made[0].refuse()
      await vi.waitFor(() => expect(toLogin).toHaveBeenCalledTimes(1))
      expect(fetchMock).toHaveBeenCalledWith(
        '/auth/active-sessions',
        expect.objectContaining({ credentials: 'include' })
      )
    } finally {
      vi.unstubAllGlobals()
    }
  })
})

describe('a refused connect, kept gentle (SIO1 round 3)', () => {
  it('spreads each pause by up to half either way, so tabs refused together do not come back together', async () => {
    vi.useFakeTimers()
    const draws = [0, 0.99]
    handle({
      askSession: async () => 'valid',
      toLogin: vi.fn(),
      random: () => draws.shift() ?? 0.5,
    })
    acquireSharedSocket(create)
    const socket = made[0]

    socket.refuse() // drawn 0: half the first pause, 500 ms
    await vi.advanceTimersByTimeAsync(499)
    expect(socket.connects).toBe(0)
    await vi.advanceTimersByTimeAsync(1)
    expect(socket.connects).toBe(1)

    socket.refuse() // drawn 0.99: the second pause times 1.49, 2,980 ms
    await vi.advanceTimersByTimeAsync(2_979)
    expect(socket.connects).toBe(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(socket.connects).toBe(2)
  })

  it('counts a session read with no answer in 10 s as unknown, and asks again after the pause', async () => {
    vi.useFakeTimers()
    const askSession = vi.fn(() => new Promise<SessionState>(() => {})) // a read that never answers
    const toLogin = vi.fn()
    handle({ askSession, toLogin })
    acquireSharedSocket(create)
    const socket = made[0]

    socket.refuse()
    await vi.advanceTimersByTimeAsync(SESSION_READ_TIMEOUT_MS + REFUSED_RETRY_MS[0] - 1)
    expect(socket.connects).toBe(0)
    await vi.advanceTimersByTimeAsync(1)
    expect(socket.connects).toBe(1)
    expect(askSession).toHaveBeenCalledTimes(1)
    expect(toLogin).not.toHaveBeenCalled()
  })

  it('lets the session read itself go at the time limit', async () => {
    vi.useFakeTimers()
    let signal: AbortSignal | null | undefined
    vi.stubGlobal(
      'fetch',
      vi.fn((_url: string, init?: RequestInit) => {
        signal = init?.signal
        return new Promise(() => {}) // the server never answers
      })
    )
    handle({ toLogin: vi.fn() }) // the real read
    try {
      acquireSharedSocket(create)
      made[0].refuse()
      await vi.advanceTimersByTimeAsync(SESSION_READ_TIMEOUT_MS - 1)
      expect(signal?.aborted).toBe(false)
      await vi.advanceTimersByTimeAsync(1)
      expect(signal?.aborted).toBe(true)
      await vi.advanceTimersByTimeAsync(REFUSED_RETRY_MS[0])
      expect(made[0].connects).toBe(1)
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('goes to the login page by itself at most once a minute; within it, the line asks the owner instead', async () => {
    vi.useFakeTimers()
    const toLogin = vi.fn()
    handle({ askSession: async () => 'ended', toLogin })
    acquireSharedSocket(create)

    made[0].refuse()
    await vi.advanceTimersByTimeAsync(0)
    expect(toLogin).toHaveBeenCalledTimes(1)
    expect(signInNeededNow()).toBe(false)

    // Sent straight back within the minute (a server that still took the cookie), refused again.
    acquireSharedSocket(create)
    made[1].refuse()
    await vi.advanceTimersByTimeAsync(0)
    expect(toLogin).toHaveBeenCalledTimes(1)
    expect(signInNeededNow()).toBe(true)

    goToLogin() // the line's button: the owner asked, so it is not limited
    expect(toLogin).toHaveBeenCalledTimes(2)

    await vi.advanceTimersByTimeAsync(TRIP_GAP_MS)
    acquireSharedSocket(create)
    made[2].refuse() // a minute on, the tab may go by itself again
    await vi.advanceTimersByTimeAsync(0)
    expect(toLogin).toHaveBeenCalledTimes(3)

    acquireSharedSocket(create)
    made[3].fire('connect') // signed in again: the line has nothing to ask
    expect(signInNeededNow()).toBe(false)
  })

  it('asks the owner nothing on a new connection, as after signing in again with no reload', async () => {
    vi.useFakeTimers()
    handle({ askSession: async () => 'ended', toLogin: vi.fn() })
    expect(claimTrip('login')).toBe(true) // the tab went to the login page a moment ago
    const old = acquireSharedSocket(create)
    made[0].refuse()
    await vi.advanceTimersByTimeAsync(0)
    expect(signInNeededNow()).toBe(true)

    releaseSharedSocket(old) // signed out: the shells let the connection go
    await endOfTask()
    acquireSharedSocket(create) // signed in again inside the app: a new connection, still coming up
    expect(signInNeededNow()).toBe(false)
  })

  it('asks nothing about the session on a transport failure, which Socket.IO retries by itself', async () => {
    vi.useFakeTimers()
    const askSession = vi.fn(async (): Promise<SessionState> => 'ended')
    const toLogin = vi.fn()
    handle({ askSession, toLogin })
    acquireSharedSocket(create)
    const socket = made[0]

    socket.connected = false // still active: Socket.IO's own reconnection is running
    for (let attempt = 0; attempt < 3; attempt++) {
      socket.fire('connect_error', new Error('xhr poll error'))
    }
    await vi.advanceTimersByTimeAsync(RETRY_MS)
    expect(askSession).not.toHaveBeenCalled()
    expect(toLogin).not.toHaveBeenCalled()
  })
})
