/**
 * One Socket.IO connection for the signed-in tab: a provider that remounts in
 * the same commit (a page change, a move between /trading and a normal page)
 * keeps it; nothing left holding it closes it; and a connection that is meant
 * to be open and is not is asked to connect again.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { acquireSharedSocket, connectionCount, RETRY_MS, releaseSharedSocket } from './sharedSocket'

class FakeSocket {
  connected = true
  active = true
  connects = 0
  disconnects = 0
  private handlers = new Map<string, Set<() => void>>()
  on(event: string, handler: () => void) {
    this.handlers.set(event, (this.handlers.get(event) ?? new Set()).add(handler))
    return this
  }
  off(event: string, handler: () => void) {
    this.handlers.get(event)?.delete(handler)
    return this
  }
  fire(event: string) {
    for (const handler of [...(this.handlers.get(event) ?? [])]) handler()
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
