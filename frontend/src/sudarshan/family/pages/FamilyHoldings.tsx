// SUDARSHAN P6 D4 (M6.7): the member's view of the owner's holdings. Read only; the marked
// `readOnly` path of HoldingCards carries it.
import { HoldingCards } from '@/sudarshan/books/cards/HoldingCards'
import type { MaskedHolding } from '../books'
import { useFamilyBoard } from '../useBoard'
import { useFamilyScreen } from '../useBook'

export default function FamilyHoldings() {
  useFamilyScreen('holdings')
  const board = useFamilyBoard<MaskedHolding>('holdings')
  return <HoldingCards {...board} />
}
