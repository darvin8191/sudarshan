import { useCallback, useSyncExternalStore } from 'react'
import { useSocketContext } from '@/components/socket/SocketProvider'

/**
 * Whether the app's connection to OpenAlgo is up, so what the server pushes can reach this
 * screen; null where the app holds none (signed out).
 */
export function useSocketUp(): boolean | null {
  const { socket } = useSocketContext()
  const subscribe = useCallback(
    (notify: () => void) => {
      if (!socket) return () => {}
      socket.on('connect', notify)
      socket.on('disconnect', notify)
      return () => {
        socket.off('connect', notify)
        socket.off('disconnect', notify)
      }
    },
    [socket]
  )
  return useSyncExternalStore(
    subscribe,
    () => (socket ? socket.connected : null),
    () => null
  )
}
