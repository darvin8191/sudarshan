// SUDARSHAN P6 D4 (M3.1): the member's view of the owner's positions. Read only; the marked
// `readOnly` path of PositionCards carries it.
import { PositionCards } from '@/sudarshan/books/cards/PositionCards'
import type { MaskedPosition } from '../books'
import { useFamilyBoard } from '../useBoard'
import { useFamilyScreen } from '../useBook'

export default function FamilyPositions() {
  useFamilyScreen('positions')
  const board = useFamilyBoard<MaskedPosition>('positions')
  return <PositionCards {...board} />
}
