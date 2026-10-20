import { render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { compile } from 'tailwindcss'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useAuthStore } from '@/stores/authStore'

vi.mock('@/components/socket/SocketProvider', () => ({
  SocketProvider: ({ children }: { children: ReactNode }) => children,
}))

import { FullWidthLayout } from './FullWidthLayout'

/** The CSS Tailwind writes for a set of class names. */
async function cssFor(classes: string[]): Promise<string> {
  const compiler = await compile('@tailwind utilities;', {
    base: process.cwd(),
    loadStylesheet: async () => ({ content: '', base: process.cwd(), path: '' }),
  })
  return compiler.build(classes)
}

afterEach(() => {
  useAuthStore.setState({ user: null, isAuthenticated: false })
})

describe('FullWidthLayout', () => {
  it('fills the visible screen on a phone, not the taller viewport behind its address bar', async () => {
    useAuthStore.setState({
      isAuthenticated: true,
      user: { username: 'owner', broker: 'upstox' } as never,
    })
    render(
      <MemoryRouter initialEntries={['/chart']}>
        <Routes>
          <Route element={<FullWidthLayout />}>
            <Route path="/chart" element={<p>Page</p>} />
          </Route>
        </Routes>
      </MemoryRouter>
    )
    const root = screen.getByText('Page').parentElement?.parentElement
    if (!root) throw new Error('no layout root')
    const css = await cssFor([...root.classList])
    // The dynamic viewport where the browser has it, the plain one where it does not.
    expect(css).toMatch(/@supports \(height:\s*100dvh\)\s*\{\s*height:\s*100dvh;/)
    expect(css).toMatch(/\.h-screen\s*\{\s*height:\s*100vh;/)
    expect(root).toHaveClass('flex', 'flex-col', 'overflow-hidden')
  })
})
