import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  FAMILY_SCOPE,
  inFamilyScope,
  registerFamilyWorker,
  resetRegistrationForTests,
} from './register'

const register = vi.fn()

function setPath(path: string) {
  window.history.replaceState({}, '', path)
}

beforeEach(() => {
  resetRegistrationForTests()
  register.mockReset()
  register.mockResolvedValue({ scope: FAMILY_SCOPE })
  Object.defineProperty(navigator, 'serviceWorker', {
    value: { register },
    configurable: true,
  })
})

afterEach(() => {
  delete (navigator as any).serviceWorker
  setPath('/')
})

describe('the family worker registration', () => {
  it('registers /sw.js with scope /family/, and only that scope', async () => {
    setPath('/family/positions')
    const result = await registerFamilyWorker()
    expect(register).toHaveBeenCalledWith('/sw.js', { scope: '/family/' })
    expect(result).toEqual({ scope: '/family/' })
  })

  it('registers once however often it is asked', async () => {
    setPath('/family')
    await registerFamilyWorker()
    await registerFamilyWorker()
    expect(register).toHaveBeenCalledTimes(1)
  })

  it('does nothing on an owner page', async () => {
    setPath('/dashboard')
    expect(await registerFamilyWorker()).toBeNull()
    setPath('/family-members')
    expect(await registerFamilyWorker()).toBeNull()
    expect(register).not.toHaveBeenCalled()
  })

  it('is null, not an error, when the browser refuses', async () => {
    setPath('/family')
    register.mockRejectedValue(new Error('refused'))
    expect(await registerFamilyWorker()).toBeNull()
  })

  it('tells the family scope from the owner pages that begin with the same letters', () => {
    expect(inFamilyScope('/family')).toBe(true)
    expect(inFamilyScope('/family/login')).toBe(true)
    expect(inFamilyScope('/family-members')).toBe(false)
  })

  it('is not called from main.tsx (the owner never registers it)', async () => {
    const { readFileSync } = await import('node:fs')
    const main = readFileSync(`${process.cwd()}/src/main.tsx`, 'utf8')
    expect(main).not.toContain('register')
  })
})
