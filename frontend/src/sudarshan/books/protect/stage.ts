import { useSyncExternalStore } from 'react'
import type { ProtectStage } from './protect'

/**
 * The server's Protect switch (`SUDARSHAN_GTT_PROTECT`), as the session facts last said it
 * (components/auth/AuthSync.tsx reads them when the app opens and on return). Off until they
 * say otherwise, and off again for anything that is not one of the three words.
 */
let stage: ProtectStage = 'off'
const listeners = new Set<() => void>()

export function noteProtectStage(said: unknown): void {
  const next: ProtectStage = said === 'on' || said === 'sandbox' ? said : 'off'
  if (next === stage) return
  stage = next
  for (const listener of listeners) listener()
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useProtectStage(): ProtectStage {
  return useSyncExternalStore(subscribe, () => stage)
}
