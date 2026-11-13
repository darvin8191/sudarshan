// SUDARSHAN P6 D2: the viewer's session, read once by FamilyLayout and shared with its screens.
import { createContext, useContext } from 'react'
import type { Me } from './types'

export const ME_KEY = ['family', 'me'] as const

export const FamilyMeContext = createContext<Me | null>(null)

/** The signed-in member. Only valid below FamilyLayout, which renders nothing until it is known. */
export function useFamilyMe(): Me {
  const me = useContext(FamilyMeContext)
  if (!me) throw new Error('useFamilyMe is used below FamilyLayout only')
  return me
}

/** The member, or null outside the layout (a test of one screen with no shell). */
export function useFamilyMeOptional(): Me | null {
  return useContext(FamilyMeContext)
}
