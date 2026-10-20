import { vi } from 'vitest'
import { PHONE_QUERY } from '@/sudarshan/chart/usePhoneLayout'

type Width = 'phone' | 'wide'

/**
 * Answer the app's phone-layout media query as a phone or a wide screen
 * would, and let a test turn the screen. The setup's own `matchMedia` answers
 * no to every query, which is the wide layout. Undo with `vi.restoreAllMocks()`.
 */
export function fakeScreen(initial: Width): { turn(next: Width): void } {
  let width = initial
  const listeners = new Set<() => void>()
  vi.spyOn(window, 'matchMedia').mockImplementation(
    (query: string) =>
      ({
        get matches() {
          return width === 'phone' && query === PHONE_QUERY
        },
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: (_type: string, listener: () => void) => listeners.add(listener),
        removeEventListener: (_type: string, listener: () => void) => listeners.delete(listener),
        dispatchEvent: vi.fn(),
      }) as unknown as MediaQueryList
  )
  return {
    turn(next: Width) {
      width = next
      for (const listener of [...listeners]) listener()
    },
  }
}
